"""Evidence-gated fidelity improvement for striped cups/mugs, with a held-out quality-control loop.

    understand (Muse Spark, structured) -> plan -> geometry QC / targeted correction -> pose refinement ->
    cylindrical texture fusion + stripe model -> HELD-OUT frame comparison against the generated texture -> accept or keep original

Nothing here trusts a generator or an LLM: the vision analysis only *selects* which deterministic technique may be tried, and
every technique must beat the unmodified reconstruction on frames it was not built from, by explicit margins (``qc.py``).
If anything is not applicable, not validated or not better, the original GLB is returned unchanged and the report says why.
Geometry is never replaced; the only corrections are a rigid axis alignment and a rim levelling, each gated by measurement.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np

from . import cyl_texture as ct
from . import mesh_qc, pattern_model, qc
from . import texture_project as tp
from .understand import Understanding


class FidelityError(RuntimeError):
    pass


@dataclass
class Plan:
    texture_model: bool
    rim_ok: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"texture_model": self.texture_model, "rim_constraint_permitted": self.rim_ok, "reasons": self.reasons}


def decide_plan(und: Understanding | None, geometry: dict | None, *, min_conf: float = 0.6, force: bool = False) -> Plan:
    """Which techniques may be attempted. The vision analysis gates semantics; the geometry measurement gates validity."""
    reasons: list[str] = []
    if geometry is None:
        return Plan(False, False, ["mesh is not a measurable cup-like body (analysis failed)"])
    circ = geometry["rim"]["circularity_std_over_radius"]
    geom_ok = geometry["axis_tilt_from_y_deg"] < 20 and circ < 0.12
    reasons.append(f"geometry: axis tilt {geometry['axis_tilt_from_y_deg']:.1f} deg, rim circularity {circ:.3f} -> {'body of revolution' if geom_ok else 'NOT a clean body of revolution'}")
    if und is None or und.used_fallback or not und.data:
        reasons.append("no vision analysis available -> texture model " + ("forced on" if force else "not attempted (needs semantic confirmation)"))
        return Plan(bool(force and geom_ok), False, reasons)
    d = und.data
    cat_ok = und.conf("category_confidence") >= min_conf
    bor = d.get("body_of_revolution") is True and und.conf("body_of_revolution_confidence") >= min_conf
    pat = und.pattern
    stripes = pat.get("present") is True and "vertical" in str(pat.get("type", "")).lower() and "stripe" in str(pat.get("type", "")).lower() and float(pat.get("confidence") or 0) >= min_conf
    reasons.append(f"vision: category '{und.category}' (conf {und.conf('category_confidence'):.2f}), body of revolution={d.get('body_of_revolution')}, pattern={pat.get('type')} (conf {pat.get('confidence')})")
    texture = bool((cat_ok and bor and stripes and geom_ok) or (force and geom_ok))
    if not texture:
        reasons.append("texture model not attempted: " + ", ".join(
            n for n, ok in (("category unsure", cat_ok), ("not a body of revolution", bor), ("no vertical stripes", stripes), ("geometry check failed", geom_ok)) if not ok))
    rim = d.get("rim") or {}
    rim_ok = rim.get("shape") == "circular" and rim.get("level") == "level" and float(rim.get("confidence") or 0) >= min_conf
    return Plan(texture, rim_ok, reasons)


@dataclass
class Candidate:
    texture: np.ndarray
    info: dict


def _build_texture(V, F, UV, base, views: list[tp.FrameView], body: ct.Body, order: list[str]) -> Candidate:
    by = {v.name: v for v in views}
    uws = [ct.unwrap_frame(n, by[n].rgb, by[n].mask, by[n].pose, V, F, body) for n in order]
    fused = ct.fuse(uws)
    info: dict = {"registration": fused.registration, "frames_unwrapped": order}
    try:
        model = pattern_model.fit_stripe_model(uws, fused)
        alb = ct.albedo_from_pattern(model.synthesize(), fused, base, {"method": "stripe lattice + canonical waveform (measured from the frames)"})
        info["pattern_model"] = model.checks
        info["pattern_centres_deg"] = model.centres_deg
        info["pattern_n_stripes"] = model.n_stripes
        info["pattern_extent_h"] = [model.row_lo / ct.N_H, model.row_hi / ct.N_H]
    except pattern_model.PatternModelError as exc:
        info["pattern_model_rejected"] = str(exc)
        alb = ct.albedo(fused, base)
        alb.info["method"] = "fused observations (stripe model rejected: " + str(exc) + ")"
    tex, stats = ct.to_uv(V, F, UV, base[..., :3], body, alb)
    info["albedo"] = alb.info
    info["uv_stats"] = stats
    return Candidate(tex, info)


def _views_with_refined_poses(V, F, UV, base, frames: list[tp.FrameInput], *, min_iou: float = 0.85):
    """Silhouette pose, then photometric refinement against the GENERATED texture (it has the right stripe heights, which
    the silhouette alone cannot see: elevation was off by up to 30 deg on the benchmark mug)."""
    views, skipped = [], {}
    sens = {}
    for fr in frames:
        try:
            fit = tp.fit_pose(V, F, fr.mask)
        except tp.TextureProjectionError as exc:
            skipped[fr.name] = f"pose fit failed: {exc}"
            continue
        if fit.iou < min_iou:
            skipped[fr.name] = f"silhouette fit too loose (IoU {fit.iou:.2f})"
            continue
        pose, score = tp.refine_pose_photometric(V, F, UV, base[..., :3], fr.rgb, fr.mask, fit.pose, az_range=12.0)
        views.append(tp.FrameView(fr.name, fr.rgb, fr.mask, pose))
        sens[fr.name] = (fit.azimuth_sensitivity, pose.scale, float(score))
    return views, skipped, sens


def improve_striped_cup(
    glb_bytes: bytes, frames: list[tp.FrameInput], understanding: Understanding | None = None, *,
    force: bool = False, min_train_frames: int = 4, holdout_every: int = 3,
    min_ncc_gain: float = 0.03, min_stripe_iou_gain: float = 0.03,
) -> tuple[bytes, dict]:
    """Return (possibly improved GLB, report). The original bytes come back untouched unless every gate passes."""
    import trimesh

    report: dict = {"applied": False, "geometry": {}, "decisions": []}
    scene = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    geoms = list(scene.geometry.values()) if hasattr(scene, "geometry") else [scene]
    if len(geoms) != 1:
        report["decisions"].append(f"not applicable: expected one geometry, found {len(geoms)}")
        return glb_bytes, report
    mesh = geoms[0]
    mat = getattr(mesh.visual, "material", None)
    tex_img = getattr(mat, "baseColorTexture", None)
    if tex_img is None or getattr(mesh.visual, "uv", None) is None:
        report["decisions"].append("not applicable: mesh has no UV-mapped base colour texture")
        return glb_bytes, report
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces)
    UV = np.asarray(mesh.visual.uv, float)
    base = np.array(tex_img.convert("RGB"))

    # --- geometry measurement and (gated) targeted corrections --------------------------------------------------------------
    try:
        geom = mesh_qc.analyze_cup(V)
    except Exception as exc:  # noqa: BLE001 - an unmeasurable mesh simply is not eligible
        geom = None
        report["geometry"]["analysis_error"] = str(exc)
    if geom:
        report["geometry"]["before"] = _brief(geom)
    plan = decide_plan(understanding, geom, force=force)
    report["plan"] = plan.to_dict()
    if understanding is not None:
        report["understanding"] = understanding.to_dict()
    if not plan.texture_model:
        report["decisions"].append("texture model not attempted (see plan.reasons); original reconstruction kept")
        return glb_bytes, report

    new_V = V
    ax = mesh_qc.plan_axis_alignment(geom)
    report["geometry"]["axis_alignment"] = ax
    if ax["apply"]:
        cand_V, R = mesh_qc.apply_axis_alignment(V, ax["axis"])
        after = mesh_qc.analyze_cup(cand_V)
        if after["axis_tilt_from_y_deg"] < geom["axis_tilt_from_y_deg"]:
            new_V = cand_V
            report["geometry"]["axis_alignment"]["applied"] = True
            report["geometry"]["axis_alignment"]["tilt_after_deg"] = after["axis_tilt_from_y_deg"]
        else:
            report["geometry"]["axis_alignment"]["applied"] = False
    rim_plan = mesh_qc.plan_rim_regularization(mesh_qc.analyze_cup(new_V), plan.rim_ok)
    report["geometry"]["rim_regularization"] = rim_plan
    if rim_plan["apply"]:
        report["geometry"]["rim_regularization"]["note"] = "applied only if silhouette agreement does not drop (checked below)"

    # --- poses ---------------------------------------------------------------------------------------------------------------
    views, skipped, sens = _views_with_refined_poses(new_V, F, UV, base, frames)
    report["frames_skipped"] = skipped
    if len(views) < min_train_frames + 1:
        report["decisions"].append(f"not validated: only {len(views)} usable frames (need >= {min_train_frames + 1} to hold some out); original kept")
        return glb_bytes, report
    views.sort(key=lambda v: v.pose.az_deg)
    hold = [v for i, v in enumerate(views) if i % holdout_every == holdout_every - 1]
    train = [v for v in views if v not in hold]
    if len(train) < min_train_frames or not hold:
        report["decisions"].append("not validated: not enough frames for a train/held-out split; original kept")
        return glb_bytes, report
    if rim_plan["apply"]:
        rim_V = mesh_qc.regularize_rim(new_V, mesh_qc.analyze_cup(new_V))
        iou_before = float(np.mean([tp.iou(tp.silhouette(new_V, F, v.pose, (v.mask.shape[1], v.mask.shape[0])), v.mask) for v in train]))
        iou_after = float(np.mean([tp.iou(tp.silhouette(rim_V, F, v.pose, (v.mask.shape[1], v.mask.shape[0])), v.mask) for v in train]))
        rim_plan["silhouette_iou_before"], rim_plan["silhouette_iou_after"] = iou_before, iou_after
        if iou_after >= iou_before - 0.005:
            new_V = rim_V
            rim_plan["applied"] = True
            rim_plan["geometry_after"] = _brief(mesh_qc.analyze_cup(new_V))
        else:
            rim_plan["applied"] = False
            rim_plan["note"] = "reverted: the frames' silhouettes agree less with the levelled rim (the rim is not as level as assumed)"
    body = ct.fit_body(new_V)

    def order_of(vs):  # anchor first = the frame whose pose is best constrained (handle visible) and largest
        return [v.name for v in sorted(vs, key=lambda v: -(sens[v.name][0] * sens[v.name][1]))]

    # --- QC loop: build from training frames only, judge on held-out frames -----------------------------------------------------
    base_hold = qc.texture_agreement(new_V, F, UV, base, [tp.FrameView(v.name, v.rgb, v.mask, v.pose) for v in hold])
    try:
        cand = _build_texture(new_V, F, UV, base, train, body, order_of(train))
    except (ct.CylTextureError, pattern_model.PatternModelError) as exc:
        report["decisions"].append(f"no texture candidate could be built from the frames ({exc}); original reconstruction kept")
        return glb_bytes, report
    cand_hold = qc.texture_agreement(new_V, F, UV, cand.texture, hold)
    accepted, reasons = qc.accept_candidate(base_hold, cand_hold, min_ncc_gain=min_ncc_gain, min_stripe_iou_gain=min_stripe_iou_gain)
    report["qc"] = {
        "held_out_frames": [v.name for v in hold], "train_frames": [v.name for v in train],
        "baseline_held_out": base_hold, "candidate_held_out": cand_hold,
        "baseline_summary": qc.summarize(base_hold), "candidate_summary": qc.summarize(cand_hold),
        "accepted": accepted, "reasons": reasons,
        "criteria": {"min_ncc_gain": min_ncc_gain, "min_stripe_iou_gain": min_stripe_iou_gain, "max_single_frame_ncc_drop": 0.10},
        "metric_notes": "NCC/stripe IoU/silhouette IoU: see worldkit/qc.py; MAE is reported but not used (exposure dependent)",
    }
    if not accepted:
        report["decisions"].append("candidate rejected by the held-out check; original reconstruction kept: " + "; ".join(reasons))
        return glb_bytes, report

    # --- final build from every usable frame ----------------------------------------------------------------------------------
    final = _build_texture(new_V, F, UV, base, views, body, order_of(views))
    final_all = qc.texture_agreement(new_V, F, UV, final.texture, views)
    report["final"] = {"info": final.info, "all_frames_agreement": final_all, "summary": qc.summarize(final_all)}

    from PIL import Image

    mat.baseColorTexture = Image.fromarray(final.texture)
    if new_V is not V:
        mesh.vertices = new_V
    exported = scene.export(file_type="glb")
    report["applied"] = True
    report["decisions"].append("applied: held-out agreement improved; geometry unchanged except as listed under geometry")
    report["geometry"]["modified"] = bool(new_V is not V)
    if understanding is not None and not understanding.used_fallback:
        report["understanding_crosscheck"] = crosscheck(understanding, final.info)
    report["texture_provenance"] = {
        "observed_fraction_of_surface": final.info["uv_stats"]["observed_fraction"],
        "inferred_fraction_of_surface": final.info["uv_stats"]["inferred_fraction"],
        "generated_fraction_of_surface": final.info["uv_stats"]["generated_fraction"],
        "meaning": (
            "observed = body texels whose angle was seen by real frames (pattern parameters measured from them; the albedo is a "
            "two-colour pattern model, lighting not baked in); inferred = body texels at unseen angles filled from the measured "
            "lattice; generated = everything else (interior, rim, handle, base) keeps the reconstruction model's texture"
        ),
    }
    return exported, report


def _brief(g: dict) -> dict:
    return {
        "axis_tilt_from_y_deg": g["axis_tilt_from_y_deg"], "rim_tilt_vs_base_deg": g["rim_tilt_vs_base_deg"],
        "rim_planarity_resid_over_radius": g["rim"]["planarity_resid_over_radius"], "rim_circularity_std_over_radius": g["rim"]["circularity_std_over_radius"],
        "base_circularity_std_over_radius": g["base"]["circularity_std_over_radius"], "height": g["height"],
    }


def crosscheck(und: Understanding, info: dict) -> dict:
    """Compare the vision analysis with what was MEASURED. The measurement always wins; disagreements are flagged."""
    out: dict = {"used_for": ["decided whether the stripe model may be attempted", "decided whether a rim constraint is permitted"], "flags": []}
    pat = und.pattern
    n_meas = info.get("pattern_n_stripes")
    n_llm = pat.get("estimated_total_stripes_around")
    if n_meas and n_llm:
        out["stripe_count"] = {"measured": n_meas, "vision_estimate": n_llm}
        if abs(n_meas - n_llm) / n_meas > 0.25:
            out["flags"].append(f"stripe count disagrees: measured {n_meas}, vision estimate {n_llm} (measurement used)")
    ext = info.get("pattern_extent_h")
    top = pat.get("region_top_fraction_of_body_height")
    if ext and top is not None:
        out["stripe_top_fraction"] = {"measured": ext[1], "vision_estimate": top}
        if abs(ext[1] - float(top)) > 0.25:
            out["flags"].append(f"stripe region top disagrees: measured {ext[1]:.2f}, vision estimate {top} (measurement used)")
    return out
