"""Scale calibration against synthetic scenes with known real-world sizes (true perspective rendering)."""
from __future__ import annotations

import numpy as np
import pytest

from tests import synth_scene as ss
from worldkit import scale_cal

COIN_CM = 2.5
MUG_H_CM = 9.0


def _frames(mesh, azs, el, dist, coin_offset):
    out = []
    for i, az in enumerate(azs):
        mug, coin = ss.render_masks(mesh, COIN_CM, coin_offset, az, el, dist)
        out.append((f"f{i}", mug, coin))
    return out


def _estimate_height(mesh_cm, azs, el, dist, coin_offset):
    # the pipeline never sees cm: give it the mesh in arbitrary units (cm / 3.7) and check it recovers the cm scale
    k = 3.7
    verts_units = np.asarray(mesh_cm.vertices) / k
    cal = scale_cal.calibrate(_frames(mesh_cm, azs, el, dist, coin_offset), verts_units, np.asarray(mesh_cm.faces), "coin", COIN_CM)
    height_units = verts_units[:, 1].max() - verts_units[:, 1].min()
    return cal, cal.cm_per_unit * height_units


def test_fit_disk_recovers_axes_of_an_ellipse():
    yy, xx = np.mgrid[0:200, 0:200]
    a, b = 40.0, 24.0
    mask = ((xx - 100) / a) ** 2 + ((yy - 100) / b) ** 2 <= 1.0
    obs = scale_cal.fit_disk(mask)
    assert obs.major_px == pytest.approx(2 * a, rel=0.03)
    assert obs.minor_px == pytest.approx(2 * b, rel=0.03)
    assert obs.elevation_deg == pytest.approx(np.degrees(np.arcsin(b / a)), abs=2.0)


def test_fit_disk_rejects_tiny_mask():
    mask = np.zeros((100, 100), bool)
    mask[50:53, 50:53] = True
    with pytest.raises(scale_cal.ScaleCalibrationError):
        scale_cal.fit_disk(mask)


def test_recovers_known_height_within_a_few_percent_when_reference_is_beside_the_object():
    mesh = ss.mug_mesh(MUG_H_CM)
    cal, h = _estimate_height(mesh, [0, 60, 120, 180, 240, 300], el=40.0, dist=70.0, coin_offset=(0.0, 7.0))
    assert abs(h - MUG_H_CM) / MUG_H_CM < 0.08, f"estimated {h:.2f} cm vs true {MUG_H_CM}"
    assert cal.consistent


def test_height_estimate_does_not_depend_on_the_arbitrary_mesh_units():
    mesh = ss.mug_mesh(MUG_H_CM)
    cal, h = _estimate_height(mesh, [0, 90, 180, 270], el=35.0, dist=80.0, coin_offset=(6.0, 0.0))
    assert h == pytest.approx(MUG_H_CM, rel=0.10)


def test_too_few_frames_with_the_reference_is_an_error_not_a_guess():
    mesh = ss.mug_mesh(MUG_H_CM)
    frames = _frames(mesh, [0, 90, 180, 270], 40.0, 70.0, (0.0, 7.0))
    frames = [(n, m, c if i < 2 else None) for i, (n, m, c) in enumerate(frames)]  # coin visible in only 2 frames
    with pytest.raises(scale_cal.ScaleCalibrationError):
        scale_cal.calibrate(frames, np.asarray(mesh.vertices) / 3.7, np.asarray(mesh.faces), "coin", COIN_CM)


def test_wrong_reference_diameter_scales_the_estimate_proportionally():
    """The calibration trusts the supplied diameter exactly: doubling it doubles the height (no hidden prior)."""
    mesh = ss.mug_mesh(MUG_H_CM)
    frames = _frames(mesh, [0, 90, 180, 270], 40.0, 70.0, (0.0, 7.0))
    v, f = np.asarray(mesh.vertices) / 3.7, np.asarray(mesh.faces)
    a = scale_cal.calibrate(frames, v, f, "coin", COIN_CM).cm_per_unit
    b = scale_cal.calibrate(frames, v, f, "coin", 2 * COIN_CM).cm_per_unit
    assert b / a == pytest.approx(2.0, rel=1e-6)
