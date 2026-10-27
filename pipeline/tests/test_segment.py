"""Regression test for the local GrabCut mask-refinement step (segment.py).

Deliberately does NOT touch rembg or the Meta API: it feeds a synthetic
image and a synthetic "rough" foreground mask straight into
refine_mask_with_grabcut, so this test needs no network, no model download,
and no GPU -- just OpenCV, already a project dependency.

The synthetic scene mirrors the real failure case found on the actual test
video (a textured mug on a large plain table, with rembg returning the whole
table as foreground): a plain "table" square containing a smaller, distinctly
textured "object" patch well inside it.
"""

from __future__ import annotations

import numpy as np

from worldkit import segment

CANVAS = 400
TABLE_SLICE = slice(50, 350)  # 300x300 plain table, well inside the canvas
OBJECT_SLICE = slice(170, 230)  # 60x60 textured object, centered on the table


def _synthetic_table_and_object_image() -> np.ndarray:
    """BGR image: dark background, plain mid-gray table, striped object on it."""
    img = np.full((CANVAS, CANVAS, 3), 30, dtype="uint8")  # dark background
    img[TABLE_SLICE, TABLE_SLICE] = (200, 200, 200)  # plain, low-texture table
    # striped object: alternating bands give high local Laplacian response,
    # same mechanism the real mug's pattern exploits.
    obj = img[OBJECT_SLICE, OBJECT_SLICE]
    obj[::6, :] = (40, 40, 160)  # dark red/navy-ish stripes
    return img


def _rough_table_mask() -> np.ndarray:
    """Simulates rembg returning the whole table as "foreground"."""
    mask = np.zeros((CANVAS, CANVAS), dtype=bool)
    mask[TABLE_SLICE, TABLE_SLICE] = True
    return mask


def test_refine_mask_with_grabcut_isolates_object_from_table():
    img = _synthetic_table_and_object_image()
    rough_mask = _rough_table_mask()

    refined = segment.refine_mask_with_grabcut(img, rough_mask)

    assert refined is not None
    assert refined.dtype == bool
    # The whole point of refinement: much smaller than the rough table mask.
    assert refined.sum() < rough_mask.sum() * 0.5
    # A point deep in the plain table (far from the object) must be excluded.
    assert not refined[60, 60]
    # The object's own center must be included.
    assert refined[200, 200]


def test_refine_mask_with_grabcut_returns_none_without_texture_contrast():
    # A perfectly uniform image (no edges anywhere, so Laplacian is exactly
    # zero everywhere, including at the rough mask's own boundary) -- there's
    # no object to find, so refinement should say so (None) rather than
    # hallucinate a sub-region.
    img = np.full((CANVAS, CANVAS, 3), 200, dtype="uint8")
    rough_mask = _rough_table_mask()

    refined = segment.refine_mask_with_grabcut(img, rough_mask)

    assert refined is None


def test_refine_mask_with_grabcut_handles_empty_rough_mask():
    img = _synthetic_table_and_object_image()
    empty_mask = np.zeros((CANVAS, CANVAS), dtype=bool)

    refined = segment.refine_mask_with_grabcut(img, empty_mask)

    assert refined is None


# --- _place_mask_on_canvas: regression test for a real bug found against the
# actual Meta API. SAM 3.1 returned a mask raster sized to its own bounding
# box (e.g. 155x140 for box (164, 254, 319, 394) on a 480x856 frame -- width
# and height match the box exactly), not the full frame. The old code
# stretched any differently-shaped mask to fill the WHOLE image, which took a
# small, correct, mug-only mask and smeared it across most of the frame
# (visually confirmed before the fix: the overlay covered floor, table and
# background, not just the mug). Fixed by placing a box-shaped mask at its
# reported position instead of stretching it.


def test_place_mask_on_canvas_positions_a_local_crop_at_its_box():
    # A small fully-True mask representing a SAM-style local crop.
    local_mask = np.ones((40, 30), dtype=bool)  # height=40, width=30
    box = (100.0, 50.0, 130.0, 90.0)  # left, top, right, bottom -> 30x40

    canvas = segment._place_mask_on_canvas(local_mask, canvas_h=200, canvas_w=300, box=box)

    assert canvas.shape == (200, 300)
    # The mask must land exactly at the box, not be stretched over the canvas.
    assert canvas[50:90, 100:130].all()
    assert canvas.sum() == 40 * 30
    # Nothing outside the box should be set -- this is exactly what the old
    # whole-canvas-resize behavior got wrong.
    assert not canvas[0, 0]
    assert not canvas[199, 299]
    assert not canvas[0:50, :].any()
    assert not canvas[90:, :].any()


def test_place_mask_on_canvas_passthrough_when_already_full_size():
    full_mask = np.zeros((200, 300), dtype=bool)
    full_mask[10:20, 10:20] = True

    canvas = segment._place_mask_on_canvas(full_mask, canvas_h=200, canvas_w=300, box=None)

    assert canvas is full_mask  # no copy/resize needed


def test_place_mask_on_canvas_falls_back_to_stretch_without_a_box():
    # No box available: old behavior (stretch to fill the canvas) is the
    # only option, and is correct for the fallback path, where the mask IS
    # already meant to cover the whole frame.
    small_mask = np.ones((10, 10), dtype=bool)

    canvas = segment._place_mask_on_canvas(small_mask, canvas_h=100, canvas_w=100, box=None)

    assert canvas.shape == (100, 100)
    assert canvas.all()  # an all-True small mask stretches to an all-True canvas
