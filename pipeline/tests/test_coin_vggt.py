"""Colour coin finder (synthetic) and the VGGT pinhole camera / mask mapping."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from worldkit import coindetect, vggt_cams


def test_gold_coin_on_white_is_found_but_a_beige_carpet_blob_is_not():
    img = np.full((200, 160, 3), 235, np.uint8)  # pale surface
    cv2.circle(img, (80, 100), 7, (200, 150, 40), -1)  # saturated gold disc
    m = coindetect.find_gold_coin(img)
    assert m is not None and 100 < m.sum() < 220
    ys, xs = np.nonzero(m)
    assert abs(xs.mean() - 80) < 1.5 and abs(ys.mean() - 100) < 1.5
    carpet = np.full((200, 160, 3), (190, 175, 150), np.uint8)  # beige, low contrast, ring is not pale enough
    cv2.circle(carpet, (80, 100), 7, (170, 140, 90), -1)
    assert coindetect.find_gold_coin(carpet) is None


def test_pinhole_camera_projects_desk_points_and_flags_depth():
    cam = vggt_cams.PinholeCam(np.eye(3), np.array([0.0, 0.0, 100.0]), 500.0, 500.0, 259.0, 259.0)  # looking down +z, 100 cm away
    xy, depth = cam.project(np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 0.0, 50.0]]))
    assert np.allclose(xy[0], [259, 259]) and xy[1][0] == pytest.approx(259 + 500 * 10 / 100)
    assert depth[2] < depth[0]  # larger = closer, so the farther point (z=50 -> 150 cm away) has the smaller value


def test_mask_to_vggt_pads_a_portrait_frame_into_the_square():
    m = np.ones((856, 480), bool)
    v = vggt_cams.mask_to_vggt(m)
    assert v.shape == (518, 518) and v.sum() > 0.5 * 518 * 291 and not v[:, :100].any() and not v[:, -100:].any()
    xy, sc = vggt_cams.to_vggt_px([[240, 428]], (480, 856))
    assert sc == pytest.approx(518 / 856) and np.allclose(xy[0], [259, 259], atol=1.0)
