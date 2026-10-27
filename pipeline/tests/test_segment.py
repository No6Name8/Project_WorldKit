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

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

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


# --- real-result cache: reuse a valid existing SAM 3.1 result for the same
# exact image (by content hash), never for a different image, a different
# concept, or an approximate fallback result. No network/API calls here --
# everything is seeded/read directly against a temp cache dir.


@pytest.fixture
def two_distinct_images(tmp_path: Path) -> tuple[Path, Path]:
    img_a = Image.fromarray(np.zeros((20, 20, 3), dtype="uint8"))
    img_b = Image.fromarray(np.full((20, 20, 3), 255, dtype="uint8"))
    path_a, path_b = tmp_path / "a.png", tmp_path / "b.png"
    img_a.save(path_a)
    img_b.save(path_b)
    return path_a, path_b


@pytest.fixture
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(segment, "_CACHE_DIR", cache_dir)
    return cache_dir


def test_cache_roundtrip_hits_on_same_image_and_concept(
    two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path
):
    image_path, _ = two_distinct_images
    mask = np.ones((10, 10), dtype=bool)
    real_result = segment.SegmentationMaskResult(
        concept="mug", mask=mask, box=(1.0, 2.0, 11.0, 12.0), foreground_pixels=100, used_fallback=False
    )

    segment._store_cached_segmentation(image_path, "mug", real_result)
    cached = segment._load_cached_segmentation(image_path, "mug")

    assert cached is not None
    assert cached.from_cache is True
    assert cached.used_fallback is False
    assert np.array_equal(cached.mask, mask)
    assert cached.box == (1.0, 2.0, 11.0, 12.0)
    assert cached.foreground_pixels == 100


def test_cache_misses_on_different_image_content(
    two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path
):
    image_a, image_b = two_distinct_images
    mask = np.ones((10, 10), dtype=bool)
    real_result = segment.SegmentationMaskResult(
        concept="mug", mask=mask, box=None, foreground_pixels=100, used_fallback=False
    )

    segment._store_cached_segmentation(image_a, "mug", real_result)

    # Different image content (even if a caller passed the "wrong" file by
    # mistake, or a video's frame changed) must never be served image_a's
    # cached mask.
    assert segment._load_cached_segmentation(image_b, "mug") is None


def test_cache_misses_on_different_concept(two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path):
    image_path, _ = two_distinct_images
    mask = np.ones((10, 10), dtype=bool)
    real_result = segment.SegmentationMaskResult(
        concept="mug", mask=mask, box=None, foreground_pixels=100, used_fallback=False
    )

    segment._store_cached_segmentation(image_path, "mug", real_result)

    assert segment._load_cached_segmentation(image_path, "coffee cup") is None


def test_cache_never_stores_fallback_results(two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path):
    image_path, _ = two_distinct_images
    mask = np.ones((10, 10), dtype=bool)
    fallback_result = segment.SegmentationMaskResult(
        concept="(fallback: whole foreground, no concept prompt)",
        mask=mask,
        box=None,
        foreground_pixels=100,
        used_fallback=True,
    )

    segment._store_cached_segmentation(image_path, "mug", fallback_result)

    assert not isolated_cache_dir.exists() or not any(isolated_cache_dir.iterdir())
    assert segment._load_cached_segmentation(image_path, "mug") is None


def test_cache_rejects_corrupted_metadata(two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path):
    image_path, _ = two_distinct_images
    mask = np.ones((10, 10), dtype=bool)
    real_result = segment.SegmentationMaskResult(
        concept="mug", mask=mask, box=None, foreground_pixels=100, used_fallback=False
    )
    segment._store_cached_segmentation(image_path, "mug", real_result)

    # Tamper with the cached metadata's recorded hash -- simulates a hand-
    # edited or corrupted cache entry. Must not be trusted even though the
    # filename itself still matches.
    image_hash = segment._image_sha256(image_path)
    meta_path, _ = segment._cache_paths(image_hash, "mug")
    tampered = meta_path.read_text().replace(image_hash, "0" * 64)
    meta_path.write_text(tampered)

    assert segment._load_cached_segmentation(image_path, "mug") is None


def test_segment_concept_uses_cache_without_any_network_call(
    two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path
):
    image_path, _ = two_distinct_images
    mask = np.ones((10, 10), dtype=bool)
    real_result = segment.SegmentationMaskResult(
        concept="mug", mask=mask, box=(0.0, 0.0, 10.0, 10.0), foreground_pixels=100, used_fallback=False
    )
    segment._store_cached_segmentation(image_path, "mug", real_result)

    # If this fell through to the real API or the local fallback, it would
    # either hang/error on a fake 20x20 PNG with no real content, or require
    # rembg's model -- reaching a clean, instant, correct result proves the
    # cache path was taken, not either of those.
    result = segment.segment_concept(image_path, "mug")

    assert result.from_cache is True
    assert result.used_fallback is False
    assert np.array_equal(result.mask, mask)


def test_segment_concept_cache_bypass_flag(
    two_distinct_images: tuple[Path, Path], isolated_cache_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    image_path, _ = two_distinct_images
    cached_mask = np.ones((10, 10), dtype=bool)
    real_result = segment.SegmentationMaskResult(
        concept="mug", mask=cached_mask, box=None, foreground_pixels=100, used_fallback=False
    )
    segment._store_cached_segmentation(image_path, "mug", real_result)

    # use_cache=False must skip the cache entirely and take the normal
    # no-key-available path instead. Patch the name segment.py actually
    # calls (it imported has_api_key directly, so patching worldkit.meta_api
    # wouldn't affect it) and stub out _fallback_mask so this stays
    # network/model-free rather than invoking real rembg.
    monkeypatch.setattr(segment, "has_api_key", lambda: False)
    stub_mask = np.zeros((5, 5), dtype=bool)
    monkeypatch.setattr(
        segment,
        "_fallback_mask",
        lambda path: segment.SegmentationMaskResult(
            concept="(stub fallback)", mask=stub_mask, box=None, foreground_pixels=0, used_fallback=True
        ),
    )

    result = segment.segment_concept(image_path, "mug", use_cache=False)

    assert result.from_cache is False
    assert result.used_fallback is True
    assert not np.array_equal(result.mask, cached_mask)  # proves the cache was NOT used
