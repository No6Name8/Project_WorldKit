"""Coin-grounded scale: reshape maths, and the accuracy-table measurements on a synthetic tapered cup of known size."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

from worldkit import scale_cal

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import accuracy_table as at  # noqa: E402


def test_reshape_is_identity_at_k1_t0_and_stretches_and_tapers_about_the_reference():
    v = np.array([[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0], [1.0, 2.0, 0.0], [-1.0, 2.0, 0.0]])
    ref = scale_cal.shape_reference(v)
    assert np.allclose(scale_cal.reshape_vertices(v, 1.0, 0.0, ref), v)
    w = scale_cal.reshape_vertices(v, 0.5, -0.2, ref)  # shorter, wider at the top
    assert w[2, 1] == pytest.approx(1.0) and w[0, 1] == 0.0  # y stretched about the lowest point
    assert w[2, 0] == pytest.approx(1.0 * (1 + 0.2 * 0.5)) and w[0, 0] == pytest.approx(1.0 * (1 - 0.2 * 0.5))  # top wider, base narrower


def test_circle_fit_ignores_a_handle_and_measure_excludes_the_base_plate():
    cup = trimesh.creation.cylinder(radius=40.0, height=70.0, sections=96)  # 80 mm wide, 70 mm tall, centred on the origin
    cup.apply_translation([0, 0, 35.0 + 3.0])  # sits on a 3 mm plate
    plate = trimesh.creation.box(extents=[110, 110, 3.0]).apply_translation([0, 0, 1.5])
    handle = trimesh.creation.box(extents=[30, 6, 30]).apply_translation([50, 0, 50])
    m = trimesh.util.concatenate([cup, plate, handle])
    d = at.measure_mesh(m, 70.0)
    assert d["height_mm"] == pytest.approx(70.0, abs=0.01)
    assert d["rim_diameter_mm"] == pytest.approx(80.0, abs=0.8) and d["base_diameter_mm"] == pytest.approx(80.0, abs=0.8)
    assert d["_plate_mm"] == pytest.approx(3.0, abs=0.01)
