"""Fidelity stack on SYNTHETIC cups with known ground truth (the real-video benchmark lives in runs/fidelity_*).

Covers: geometry QC (tilt, rim defects, rim levelling), cylindrical unwrapping/registration, the stripe model (and its
refusals), the held-out acceptance rule (must reject a bad candidate), understanding parsing/planning, and provenance.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from worldkit import cyl_texture as ct
from worldkit import fidelity, mesh_qc, pattern_model, qc, understand
from worldkit import texture_project as tp

N_STRIPES = 12
W_IMG, H_IMG = 300, 360


def make_cup(nth=96, nh=28, taper=0.15, tilt_deg=0.0, rim_wave=0.0):
    """Body of revolution (open top rendered as a thin wall), Y-up, with cylindrical UVs (u = theta, v = height)."""
    th = np.linspace(0, 2 * np.pi, nth + 1)
    hh = np.linspace(0, 1, nh)
    T, Hh = np.meshgrid(th, hh)
    r = 1.0 - taper * Hh
    X, Z = r * np.cos(T), r * np.sin(T)
    Y = 1.6 * Hh - 0.8
    Y = Y + rim_wave * np.cos(3 * T) * (Hh > 0.97)
    verts = np.stack([X, Y, Z], axis=-1).reshape(-1, 3)
    uv = np.stack([T / (2 * np.pi), Hh], axis=-1).reshape(-1, 2)
    faces = []
    for j in range(nh - 1):
        for i in range(nth):
            a, b, c, d = j * (nth + 1) + i, j * (nth + 1) + i + 1, (j + 1) * (nth + 1) + i, (j + 1) * (nth + 1) + i + 1
            faces += [[a, b, c], [b, d, c]]
    # inner wall (so rim/axis analysis sees a thin wall) at 92% radius
    verts2 = verts * np.array([0.92, 1.0, 0.92])
    off = len(verts)
    faces += [[f[0] + off, f[2] + off, f[1] + off] for f in faces[: len(faces)]]
    verts = np.vstack([verts, verts2])
    uv = np.vstack([uv, uv])
    if tilt_deg:
        R = trimesh.transformations.rotation_matrix(np.radians(tilt_deg), [1, 0, 1])[:3, :3]
        verts = verts @ R.T
    return verts, np.array(faces), uv


def stripe_texture(n=N_STRIPES, amp_deg=7.0, width_deg=11.0, shape=(256, 720)):
    """Texture whose columns are theta (u) and rows are height (v, flipped like OpenGL UVs): white body, dark wavy stripes."""
    h, w = shape
    u = (np.arange(w) + 0.5) / w * 360.0
    v = 1.0 - (np.arange(h) + 0.5) / h  # row 0 = top = v=1
    U, Vv = np.meshgrid(u, v)
    img = np.full((h, w, 3), 215, np.uint8)
    for i in range(n):
        c = i * 360.0 / n + amp_deg * np.sin(2 * np.pi * Vv * 1.5)
        d = (U - c + 180) % 360 - 180
        m = (np.abs(d) < width_deg / 2) & (Vv > 0.08) & (Vv < 0.65)
        img[m] = (28, 30, 52)
    return img


def make_frames(verts, faces, uv, tex, azs, el=35.0, scale=90.0):
    frames, views = [], []
    for k, az in enumerate(azs):
        pose = tp.CameraPose(float(az), el, scale, W_IMG / 2, H_IMG / 2 + 10)
        img, cov = tp.render_textured(verts, faces, uv, tex, pose, (W_IMG, H_IMG))
        img = img.copy()
        img[~cov] = 255
        frames.append(tp.FrameInput(f"f{k}", img, cov))
        views.append(tp.FrameView(f"f{k}", img, cov, pose))
    return frames, views


# --------------------------------------------------------------------------- geometry QC

def test_analyze_cup_reports_tilt_and_level_rim():
    v, f, uv = make_cup(tilt_deg=4.0)
    rep = mesh_qc.analyze_cup(v)
    assert rep["axis_tilt_from_y_deg"] == pytest.approx(4.0, abs=0.8)
    assert rep["rim_tilt_vs_base_deg"] < 1.0  # rim and base stay parallel under a rigid tilt
    assert rep["rim"]["planarity_resid_over_radius"] < 0.02


def test_axis_alignment_is_planned_only_when_rim_base_and_axis_agree_and_removes_the_tilt():
    v, f, uv = make_cup(tilt_deg=5.0)
    rep = mesh_qc.analyze_cup(v)
    plan = mesh_qc.plan_axis_alignment(rep)
    assert plan["apply"]
    v2, _ = mesh_qc.apply_axis_alignment(v, plan["axis"])
    assert mesh_qc.analyze_cup(v2)["axis_tilt_from_y_deg"] < 0.8
    # a well aligned cup is left alone
    assert not mesh_qc.plan_axis_alignment(mesh_qc.analyze_cup(make_cup()[0]))["apply"]


def test_wavy_rim_is_measured_and_levelled_only_when_vision_permits():
    v, f, uv = make_cup(rim_wave=0.06)
    rep = mesh_qc.analyze_cup(v)
    assert rep["rim"]["planarity_resid_over_radius"] > 0.02
    assert not mesh_qc.plan_rim_regularization(rep, understanding_rim_ok=False)["apply"]
    plan = mesh_qc.plan_rim_regularization(rep, understanding_rim_ok=True)
    assert plan["apply"]
    v2 = mesh_qc.regularize_rim(v, rep)
    after = mesh_qc.analyze_cup(v2)
    assert after["rim"]["planarity_resid_over_radius"] < 0.5 * rep["rim"]["planarity_resid_over_radius"]
    assert np.abs(v2[:, [0, 2]] - v[:, [0, 2]]).max() == 0  # only heights move


def test_a_level_rim_is_not_touched():
    v, f, uv = make_cup()
    rep = mesh_qc.analyze_cup(v)
    assert not mesh_qc.plan_rim_regularization(rep, understanding_rim_ok=True)["apply"]


# --------------------------------------------------------------------------- cylindrical texture + stripe model

@pytest.fixture(scope="module")
def striped():
    v, f, uv = make_cup()
    tex = stripe_texture()
    frames, views = make_frames(v, f, uv, tex, azs=[0, 50, 100, 150, 200, 250, 300, 340])
    return v, f, uv, tex, frames, views


def test_unwrap_recovers_the_stripes_in_the_cylinder_domain(striped):
    v, f, uv, tex, frames, views = striped
    body = ct.fit_body(v)
    uw = ct.unwrap_frame("f0", views[0].rgb, views[0].mask, views[0].pose, v, f, body)
    assert (uw.weight > 0.05).mean() > 0.2  # roughly the visible half of the surface
    pat, obs = pattern_model.pattern_map(uw.colour, uw.weight, 0.15)
    assert pat[obs].max() > 0.8 and (pat[obs] < 0.1).mean() > 0.3  # both dark stripes and white body are seen


def test_stripe_model_recovers_count_spacing_and_phase(striped):
    v, f, uv, tex, frames, views = striped
    body = ct.fit_body(v)
    uws = [ct.unwrap_frame(w.name, w.rgb, w.mask, w.pose, v, f, body) for w in views]
    fused = ct.fuse(uws)
    model = pattern_model.fit_stripe_model(uws, fused)
    assert model.n_stripes == N_STRIPES
    truth = np.arange(N_STRIPES) * 360.0 / N_STRIPES
    # the body's theta=0 is the mesh x axis; compare lattices modulo the period, allowing the (unknown) gauge offset
    P = 360.0 / N_STRIPES
    got = np.array(model.centres_deg)
    d = (got - got[0] + P / 2) % P - P / 2
    assert np.abs(d).max() < 2.5  # centres equally spaced within 2.5 degrees
    assert model.checks["waveform_amplitude_cols"] > 5  # the wave was measured, not assumed straight


def test_pattern_model_refuses_irregular_stripes(striped):
    v, f, uv, tex, frames, views = striped
    rng = np.random.default_rng(3)
    # same stripes at jittered, irregular angles
    h, w = tex.shape[:2]
    img = np.full_like(tex, 215)
    ang = np.sort(rng.uniform(0, 360, 12))
    u = (np.arange(w) + 0.5) / w * 360.0
    vv = 1.0 - (np.arange(h) + 0.5) / h
    U, V_ = np.meshgrid(u, vv)
    for c in ang:
        m = (np.abs((U - c + 180) % 360 - 180) < 5.5) & (V_ > 0.08) & (V_ < 0.65)
        img[m] = (28, 30, 52)
    _, vws = make_frames(v, f, uv, img, azs=[0, 50, 100, 150, 200, 250, 300, 340])
    body = ct.fit_body(v)
    uws = [ct.unwrap_frame(w_.name, w_.rgb, w_.mask, w_.pose, v, f, body) for w_ in vws]
    fused = ct.fuse(uws)
    with pytest.raises(pattern_model.PatternModelError):
        pattern_model.fit_stripe_model(uws, fused)


def test_no_pattern_is_an_error_not_an_invention():
    v, f, uv = make_cup()
    plain = np.full((256, 720, 3), 215, np.uint8)
    _, vws = make_frames(v, f, uv, plain, azs=[0, 90, 180, 270])
    body = ct.fit_body(v)
    uws = [ct.unwrap_frame(w.name, w.rgb, w.mask, w.pose, v, f, body) for w in vws]
    fused = ct.fuse(uws)
    with pytest.raises(pattern_model.PatternModelError):
        pattern_model.fit_stripe_model(uws, fused)


# --------------------------------------------------------------------------- QC acceptance rule

def _frame(ncc, iou):
    return {"ncc": ncc, "mae": 30.0, "stripe_iou": iou, "silhouette_iou": 0.95}


def test_acceptance_requires_a_real_heldout_gain():
    base = {"a": _frame(0.60, 0.50), "b": _frame(0.62, 0.52)}
    better = {"a": _frame(0.75, 0.70), "b": _frame(0.80, 0.72)}
    same = {"a": _frame(0.61, 0.51), "b": _frame(0.63, 0.53)}
    assert qc.accept_candidate(base, better)[0]
    ok, why = qc.accept_candidate(base, same)
    assert not ok and any("gain" in w for w in why)


def test_acceptance_rejects_a_candidate_that_ruins_one_view():
    base = {"a": _frame(0.60, 0.50), "b": _frame(0.62, 0.52), "c": _frame(0.60, 0.50)}
    cand = {"a": _frame(0.95, 0.9), "b": _frame(0.95, 0.9), "c": _frame(0.45, 0.4)}  # mean improves, one view collapses
    ok, why = qc.accept_candidate(base, cand)
    assert not ok and any("frame c" in w for w in why)


def test_corrupted_texture_scores_worse_than_the_truth_on_synthetic_frames(striped):
    """The metrics must be able to tell a wrong pattern from the right one (QC has to detect mismatches)."""
    v, f, uv, tex, frames, views = striped
    wrong = np.roll(tex, 30, axis=1)  # stripes shifted by 15 degrees = half a period: wrong positions
    hold = views[1:8:3]
    good = qc.texture_agreement(v, f, uv, tex, hold, refine=False)
    bad = qc.texture_agreement(v, f, uv, wrong, hold, refine=False)
    assert qc.summarize(good)["stripe_iou"] > qc.summarize(bad)["stripe_iou"] + 0.3
    assert qc.summarize(good)["ncc"] > qc.summarize(bad)["ncc"] + 0.3


# --------------------------------------------------------------------------- vision analysis: parsing + planning

GOOD = {
    "object_category": "mug", "category_confidence": 0.95, "body_of_revolution": True, "body_of_revolution_confidence": 0.9,
    "has_handle": True, "rim": {"visible": True, "shape": "circular", "level": "level", "confidence": 0.9},
    "pattern": {"present": True, "type": "vertical stripes", "stripe_shape": "wavy", "confidence": 0.9,
                "estimated_total_stripes_around": 12, "region_top_fraction_of_body_height": 0.65},
}


def _und(data, fallback=False):
    return understand.Understanding(data, ["a.png"], used_fallback=fallback)


def _geom(tilt=0.3, circ=0.02):
    return {"axis_tilt_from_y_deg": tilt, "rim": {"circularity_std_over_radius": circ}}


def test_plan_enables_the_stripe_model_only_with_semantic_and_geometric_agreement():
    assert fidelity.decide_plan(_und(GOOD), _geom()).texture_model
    no_stripes = {**GOOD, "pattern": {**GOOD["pattern"], "type": "none", "present": False}}
    assert not fidelity.decide_plan(_und(no_stripes), _geom()).texture_model
    assert not fidelity.decide_plan(_und({**GOOD, "body_of_revolution": False}), _geom()).texture_model
    assert not fidelity.decide_plan(_und(GOOD), _geom(circ=0.3)).texture_model  # geometry says it is not a clean cup
    assert not fidelity.decide_plan(_und(GOOD, ), None).texture_model


def test_plan_is_conservative_without_a_vision_analysis():
    assert not fidelity.decide_plan(None, _geom()).texture_model
    assert not fidelity.decide_plan(_und({}, fallback=True), _geom()).texture_model
    assert fidelity.decide_plan(None, _geom(), force=True).texture_model  # explicit user override only


def test_rim_constraint_needs_the_vision_analysis_to_call_the_rim_circular_and_level():
    assert fidelity.decide_plan(_und(GOOD), _geom()).rim_ok
    tilted = {**GOOD, "rim": {"shape": "oval", "level": "tilted", "confidence": 0.9}}
    assert not fidelity.decide_plan(_und(tilted), _geom()).rim_ok


def test_understanding_rejects_malformed_answers_and_uses_the_cache(tmp_path, monkeypatch):
    img = tmp_path / "a.png"
    Image.new("RGB", (8, 8), "white").save(img)
    monkeypatch.setattr(understand, "CACHE_DIR", tmp_path / "cache")
    calls = []

    def fake_ask(images, prompt):
        calls.append(1)
        return "noise " + json.dumps(GOOD) + " trailing", {"total_tokens": 1}

    a = understand.analyse_frames([img], ask=fake_ask)
    assert a.data["object_category"] == "mug" and not a.from_cache
    b = understand.analyse_frames([img], ask=fake_ask)
    assert b.from_cache and len(calls) == 1
    with pytest.raises(understand.UnderstandingError):
        understand.analyse_frames([img], use_cache=False, ask=lambda i, p: ("{}", None))
    with pytest.raises(understand.UnderstandingError):
        understand.analyse_frames([img], use_cache=False, ask=lambda i, p: ("no json here", None))


def test_unavailable_api_yields_an_empty_understanding_not_invented_facts(tmp_path, monkeypatch):
    from worldkit.meta_api import MetaAPIUnavailable

    img = tmp_path / "a.png"
    Image.new("RGB", (8, 8), "black").save(img)
    monkeypatch.setattr(understand, "CACHE_DIR", tmp_path / "c2")

    def boom(images, prompt):
        raise MetaAPIUnavailable("no key")

    u = understand.analyse_frames([img], ask=boom)
    assert u.used_fallback and u.data == {}


def test_crosscheck_flags_disagreement_and_lets_the_measurement_win():
    info = {"pattern_n_stripes": 13, "pattern_extent_h": [0.04, 0.73]}
    ok = fidelity.crosscheck(_und(GOOD), info)
    assert not ok["flags"] and ok["stripe_count"]["measured"] == 13
    off = {**GOOD, "pattern": {**GOOD["pattern"], "estimated_total_stripes_around": 6, "region_top_fraction_of_body_height": 0.2}}
    bad = fidelity.crosscheck(_und(off), info)
    assert len(bad["flags"]) == 2 and all("measurement used" in f for f in bad["flags"])


# --------------------------------------------------------------------------- end to end on a synthetic textured cup

def _glb_of(v, f, uv, tex):
    m = trimesh.Trimesh(vertices=v, faces=f, process=False)
    m.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(tex)))
    return trimesh.Scene(m).export(file_type="glb")


def test_improve_striped_cup_without_vision_keeps_the_original_bytes():
    v, f, uv = make_cup()
    glb = _glb_of(v, f, uv, stripe_texture())
    frames, _ = make_frames(v, f, uv, stripe_texture(), azs=[0, 90, 180, 270])
    out, rep = fidelity.improve_striped_cup(glb, frames, None)
    assert out == glb and not rep["applied"] and "not attempted" in " ".join(rep["decisions"])


def test_improve_striped_cup_on_a_plain_cup_never_invents_stripes():
    v, f, uv = make_cup()
    plain = np.full((256, 720, 3), 215, np.uint8)
    glb = _glb_of(v, f, uv, plain)
    frames, _ = make_frames(v, f, uv, plain, azs=[0, 40, 80, 120, 160, 200, 240, 280, 320])
    out, rep = fidelity.improve_striped_cup(glb, frames, _und(GOOD), force=False)
    assert not rep["applied"] or rep["final"]["info"].get("pattern_model_rejected")
    if not rep["applied"]:
        assert out == glb


def test_rim_contour_error_is_tiny_for_the_true_mesh_and_large_for_a_tilted_rim():
    v, f, uv = make_cup()
    pose = tp.CameraPose(30.0, 35.0, 90.0, W_IMG / 2, H_IMG / 2)
    mask = tp.silhouette(v, f, pose, (W_IMG, H_IMG))
    view = tp.FrameView("x", np.zeros((H_IMG, W_IMG, 3), np.uint8), mask, pose)
    assert qc.rim_contour_error(v, f, view)["fraction_of_height"] < 0.005
    tilted = v.copy()
    tilted[:, 1] += 0.25 * v[:, 0] * (v[:, 1] > 0.6)  # one side of the rim much higher than the other
    assert qc.rim_contour_error(tilted, f, view)["fraction_of_height"] > 0.02
