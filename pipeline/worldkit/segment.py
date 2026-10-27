"""Get a clean mask of the main object (and of an A4 sheet, if present).

Real path: calls the Meta Model API's Responses endpoint with model
"sam-3.1", one short noun-phrase concept per request (per the Meta Model
cookbook's SAM API basics recipe), parsed with the official
``meta_sam_parser`` package rather than hand-parsing the event stream or the
mask wire format.

Fallback path (FALLBACK_LOCAL_MASK): if the Meta API is unavailable (no key,
or a billing/auth rejection), falls back to a local background-removal model
(rembg/u2net, runs on CPU, no network once its weights are cached) to produce
a whole-foreground mask, THEN refines it with ``refine_mask_with_grabcut``
(classical OpenCV, no model/download) to separate the actual object from
whatever larger surface it's sitting on. This never claims to be SAM 3.1 --
every mask produced this way is tagged ``used_fallback_mask=True`` and every
output filename it touches is prefixed ``FALLBACK_``, so it can never be
mistaken for a real result. It also cannot find a named concept (e.g.
"A4 sheet") -- it only separates foreground from background -- so A4
segmentation is skipped when falling back; use --height-cm instead.

Why the refinement step exists: on a real test photo (a mug on a round
table), plain rembg returned the WHOLE TABLE as "foreground" (it has no
concept awareness -- it just finds the largest salient foreground blob, and
the table-plus-mug reads as one blob against the floor). That raw mask is
not usable as reconstruction input. ``refine_mask_with_grabcut`` narrows it:
it finds the sub-region of the rough mask with the highest local texture
(a sitting object's edges/pattern/shadow read as higher-frequency detail
than a plain table surface) that does NOT touch the rough mask's own
boundary (the table's own rim/legs are high-texture too, but they touch that
boundary; an object resting on top does not), then runs classical GrabCut
seeded from that region. On that same test photo this took the mask from
207,736px / 93% of the frame width (the whole table) down to 11,516px / 27%
of the frame width, tight to the mug body, confirmed by eye. It is NOT
pixel-perfect: a mug's plain white glaze can match the table's white marble
closely enough in color to be excluded (seen on the test photo: the mug's
rim/interior was left out), and it cannot recover any geometry a single 2D
frame doesn't show (an occluded handle, for instance) -- no segmentation
method can fix that from one frame.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from openai import AsyncOpenAI
from PIL import Image

from meta_sam_parser import (
    ImageSegmentationResult,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
)

from .meta_api import META_API_BASE_URL, SAM_MODEL, MetaAPIUnavailable, has_api_key, image_to_data_url, load_api_key


class SegmentationError(RuntimeError):
    pass


@dataclass
class SegmentationMaskResult:
    concept: str
    mask: np.ndarray  # bool array, shape (H, W) -- or a SAM-local crop, see box
    box: tuple[float, float, float, float] | None  # left, top, right, bottom
    foreground_pixels: int
    used_fallback: bool = False
    from_cache: bool = False  # True if reused from a prior real SAM 3.1 result


# --- real-result cache -------------------------------------------------------
#
# A real SAM 3.1 call costs money and ~3s. Once we've segmented an exact image
# for an exact concept, there's no reason to pay for it again. Identity is
# verified by SHA-256 of the image bytes (not filename/path), so a cache entry
# can never be silently reused for a different frame or a different video that
# happens to share a filename -- any byte difference is a cache miss, and the
# normal (real-API-or-fallback) path runs as if there were no cache at all.
# Only real (non-fallback) results are ever cached.

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "sam31"


def _image_sha256(image_path: Path) -> str:
    return hashlib.sha256(Path(image_path).read_bytes()).hexdigest()


def _concept_slug(concept: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", concept.strip().lower()).strip("-")
    return slug or "concept"


def _cache_paths(image_hash: str, concept: str) -> tuple[Path, Path]:
    key = f"{image_hash[:16]}_{_concept_slug(concept)}"
    return _CACHE_DIR / f"{key}.json", _CACHE_DIR / f"{key}.npy"


def _load_cached_segmentation(image_path: Path, concept: str) -> SegmentationMaskResult | None:
    image_hash = _image_sha256(image_path)
    meta_path, mask_path = _cache_paths(image_hash, concept)
    if not (meta_path.is_file() and mask_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return None
    # Identity check: the hash is already in the filename, but re-verify the
    # recorded hash and concept inside the file too, so a hand-edited or
    # corrupted cache entry can't be silently trusted.
    if meta.get("image_sha256") != image_hash or meta.get("concept") != concept:
        return None
    mask = np.load(mask_path)
    box = tuple(meta["box"]) if meta.get("box") is not None else None
    return SegmentationMaskResult(
        concept=concept,
        mask=mask,
        box=box,
        foreground_pixels=meta["foreground_pixels"],
        used_fallback=False,
        from_cache=True,
    )


def _store_cached_segmentation(image_path: Path, concept: str, result: SegmentationMaskResult) -> None:
    if result.used_fallback:
        return  # never cache an approximate local fallback as if it were real
    image_hash = _image_sha256(image_path)
    meta_path, mask_path = _cache_paths(image_hash, concept)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(mask_path, result.mask)
    meta_path.write_text(
        json.dumps(
            {
                "image_sha256": image_hash,
                "concept": concept,
                "source_image": str(image_path),
                "box": list(result.box) if result.box is not None else None,
                "foreground_pixels": result.foreground_pixels,
                "mask_shape": list(result.mask.shape),
                "model": SAM_MODEL,
                "cached_at": time.time(),
            },
            indent=2,
        )
    )


def _get_async_client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=META_API_BASE_URL, api_key=load_api_key())


def _mask_record_to_array(record: SegmentationMaskRecord) -> np.ndarray:
    mask = record.mask
    raw = decode_mask_to_raster(mask)
    n_pixels = mask.width * mask.height
    if len(raw) == n_pixels:
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(mask.height, mask.width)
        return arr > 0
    if len(raw) * 8 >= n_pixels:
        bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[:n_pixels]
        return bits.reshape(mask.height, mask.width) > 0
    raise SegmentationError(
        f"could not interpret mask raster: {len(raw)} bytes for "
        f"{mask.width}x{mask.height} pixels"
    )


async def _segment_concept_async(
    image_path: Path, concept: str, client: AsyncOpenAI
) -> ImageSegmentationResult:
    concept = concept.strip()
    if not concept:
        raise SegmentationError("concept text prompt must not be empty")

    data_url = image_to_data_url(image_path)
    stream = await client.responses.create(
        model=SAM_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": concept},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        stream=True,
    )
    parsed = parse_responses_stream(stream, image_segmentation_format())
    try:
        result = await parsed.final_result()
    finally:
        await parsed.aclose()
    return result


def refine_mask_with_grabcut(
    image_bgr: np.ndarray,
    rough_mask: np.ndarray,
    *,
    erosion_px: int = 41,
    texture_percentile: float = 85.0,
    margin_frac: float = 0.15,
) -> np.ndarray | None:
    """Narrow a whole-foreground mask down to the object sitting within it.

    ``rough_mask`` (e.g. from rembg) is assumed to cover a larger surface
    (a table) PLUS the object on it. This locates the object by finding the
    rough mask's highest-local-texture region that is fully interior to it
    (eroded by ``erosion_px``, so the surface's own rim/legs -- which touch
    the rough mask's boundary -- are excluded), then runs classical GrabCut
    seeded from that region (OpenCV builtin, no model download).

    Returns a tighter boolean mask, or None if no interior high-texture
    region is found (e.g. the rough mask IS tight already, or the object has
    no texture/color contrast against its surface) -- callers should fall
    back to ``rough_mask`` in that case rather than treat None as an error.
    """
    h, w = rough_mask.shape
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    texture = cv2.GaussianBlur(np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3)), (0, 0), sigmaX=9)

    interior = cv2.erode(rough_mask.astype("uint8"), np.ones((erosion_px, erosion_px), np.uint8)).astype(bool)
    if not interior.any():
        return None

    texture_masked = texture.copy()
    texture_masked[~interior] = 0
    thresh = np.percentile(texture_masked[interior], texture_percentile)
    high_texture = (texture_masked > thresh).astype("uint8")
    high_texture = cv2.morphologyEx(high_texture, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    high_texture = cv2.morphologyEx(high_texture, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(high_texture, connectivity=8)
    if n_labels <= 1:
        return None
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, _area = stats[largest]

    mx, my = int(bw * margin_frac), int(bh * margin_frac)
    rx0, ry0 = max(0, x - mx), max(0, y - my)
    rx1, ry1 = min(w, x + bw + mx), min(h, y + bh + my)

    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[ry0:ry1, rx0:rx1] = cv2.GC_PR_FGD
    mask[labels == largest] = cv2.GC_FGD
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(image_bgr, mask, None, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_MASK)
    refined = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype("uint8")

    refined = cv2.morphologyEx(refined, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n2, labels2, stats2, _ = cv2.connectedComponentsWithStats(refined, connectivity=8)
    if n2 <= 1:
        return None
    largest2 = 1 + int(np.argmax(stats2[1:, cv2.CC_STAT_AREA]))
    refined = labels2 == largest2
    refined = cv2.morphologyEx(refined.astype("uint8"), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return refined.astype(bool)


def _fallback_mask(image_path: Path) -> SegmentationMaskResult:
    """FALLBACK_LOCAL_MASK: rembg/u2net whole-foreground mask, refined with
    refine_mask_with_grabcut to separate the object from the surface it's on
    (CPU only, no Meta API, no new model download)."""
    from rembg import remove

    img = Image.open(image_path).convert("RGB")
    cutout = remove(img)  # RGBA, alpha = foreground mask
    alpha = np.asarray(cutout)[..., 3]
    rough_mask = alpha > 10

    if not rough_mask.any():
        raise SegmentationError(
            f"FALLBACK_LOCAL_MASK found no foreground in {image_path} (fully transparent result)"
        )

    image_bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    refined = refine_mask_with_grabcut(image_bgr, rough_mask)
    mask = refined if refined is not None else rough_mask
    concept = (
        "(fallback: rembg foreground, GrabCut-refined to isolate the object)"
        if refined is not None
        else "(fallback: whole foreground, no concept prompt -- GrabCut refinement found nothing to narrow)"
    )

    ys, xs = np.where(mask)
    box = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))

    return SegmentationMaskResult(
        concept=concept,
        mask=mask,
        box=box,
        foreground_pixels=int(mask.sum()),
        used_fallback=True,
    )


def segment_concept(
    image_path: Path, concept: str, client: AsyncOpenAI | None = None, use_cache: bool = True
) -> SegmentationMaskResult:
    """Segment ``concept`` (a short noun phrase, e.g. 'coffee mug') in ``image_path``.

    If a real SAM 3.1 result for this exact image (by content hash, not
    filename) and this exact concept was already cached, reuses it instead of
    paying for another call -- set ``from_cache=True`` on the result so
    callers/reports can tell. Otherwise falls back to FALLBACK_LOCAL_MASK
    (whole-foreground cutout, no concept awareness) if the Meta API is
    unavailable. Raises SegmentationError if SAM 3.1 is reachable but reports
    no mask / an error for the concept, or if the fallback itself fails.
    """
    if use_cache:
        cached = _load_cached_segmentation(image_path, concept)
        if cached is not None:
            return cached

    if not has_api_key():
        return _fallback_mask(image_path)

    own_client = client is None
    client = client or _get_async_client()
    try:
        result = asyncio.run(_segment_concept_async(image_path, concept, client))
    except MetaAPIUnavailable:
        return _fallback_mask(image_path)
    except Exception as exc:  # noqa: BLE001 - re-raise with context
        raise SegmentationError(f"SAM 3.1 segmentation failed for concept '{concept}': {exc}") from exc

    errors = [d for d in result.diagnostics if d.severity == "error"] if result.diagnostics else []
    if errors:
        raise SegmentationError(
            f"SAM 3.1 reported errors for concept '{concept}': "
            + "; ".join(f"{d.code}: {d.message}" for d in errors)
        )

    mask_records = [r for r in result.records if isinstance(r, SegmentationMaskRecord)]
    box_records = [r for r in result.records if isinstance(r, SegmentationBoxRecord)]

    if not mask_records:
        raise SegmentationError(
            f"SAM 3.1 found no mask for concept '{concept}' in {image_path}. "
            "Try a different --object name, or check the frame actually shows it."
        )

    mask_record = mask_records[0]
    arr = _mask_record_to_array(mask_record)

    box = None
    matching_boxes = [b for b in box_records if b.object_id == mask_record.object_id]
    if matching_boxes:
        b = matching_boxes[0]
        box = (b.left, b.top, b.right, b.bottom)

    final_result = SegmentationMaskResult(
        concept=concept,
        mask=arr,
        box=box,
        foreground_pixels=int(arr.sum()),
        used_fallback=False,
    )
    if use_cache:
        _store_cached_segmentation(image_path, concept, final_result)
    return final_result


def _place_mask_on_canvas(
    mask: np.ndarray,
    canvas_h: int,
    canvas_w: int,
    box: tuple[float, float, float, float] | None,
) -> np.ndarray:
    """Return a full-canvas-sized boolean mask, correctly placed.

    SAM 3.1 can return a mask raster sized to its own reported bounding box
    (a local crop), not the full frame -- confirmed on a real response: a
    155x140 mask for box (164, 254, 319, 394) on a 480x856 frame, with
    155 == 319-164 and 140 == 394-254 exactly. Naively resizing that crop to
    fill the whole frame (the old behavior here) stretches a tight mug mask
    into a blob smeared across most of the image. If ``box`` is known, the
    mask is pasted at its correct position in a zero canvas instead; only
    without a box does this fall back to the old whole-frame resize (which
    is correct for the fallback path, where the mask IS already full-frame).
    """
    if mask.shape == (canvas_h, canvas_w):
        return mask

    if box is not None:
        left, top, right, bottom = box
        box_w, box_h = int(round(right - left)), int(round(bottom - top))
        resized = mask
        if mask.shape != (box_h, box_w) and box_w > 0 and box_h > 0:
            resized_img = Image.fromarray((mask * 255).astype(np.uint8)).resize(
                (box_w, box_h), Image.NEAREST
            )
            resized = np.asarray(resized_img) > 0

        canvas = np.zeros((canvas_h, canvas_w), dtype=bool)
        x0, y0 = int(round(left)), int(round(top))
        x1, y1 = min(canvas_w, x0 + resized.shape[1]), min(canvas_h, y0 + resized.shape[0])
        x0, y0 = max(0, x0), max(0, y0)
        if x1 > x0 and y1 > y0:
            canvas[y0:y1, x0:x1] = resized[: y1 - y0, : x1 - x0]
        return canvas

    # No box to place it with -- assume it's meant to cover the whole frame,
    # just at a different resolution (true for the fallback path).
    mask_img = Image.fromarray((mask * 255).astype(np.uint8)).resize((canvas_w, canvas_h), Image.NEAREST)
    return np.asarray(mask_img) > 0


def save_mask_overlay(
    image_path: Path,
    mask: np.ndarray,
    out_path: Path,
    color=(255, 64, 64),
    used_fallback: bool = False,
    box: tuple[float, float, float, float] | None = None,
) -> Path:
    """Write a PNG of ``image_path`` with ``mask`` drawn as a translucent overlay.

    If ``used_fallback`` is True, the filename is prefixed FALLBACK_ regardless
    of what the caller asked for, so a fallback result can never look real.
    ``box``, if given (SAM 3.1 results carry one), places a locally-sized
    mask at its correct position instead of stretching it across the whole
    frame -- see ``_place_mask_on_canvas``.
    """
    if used_fallback and out_path.name and not out_path.name.startswith("FALLBACK_"):
        out_path = out_path.with_name(f"FALLBACK_{out_path.name}")

    base = Image.open(image_path).convert("RGBA")
    mask = _place_mask_on_canvas(mask, base.height, base.width, box)

    overlay = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    overlay[mask] = (*color, 110)
    overlay_img = Image.fromarray(overlay, mode="RGBA")
    composed = Image.alpha_composite(base, overlay_img)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composed.convert("RGB").save(out_path)
    return out_path


def save_rgba_with_mask(
    image_path: Path,
    mask: np.ndarray,
    out_path: Path,
    box: tuple[float, float, float, float] | None = None,
) -> Path:
    """Write an RGBA PNG with ``mask`` embedded in the alpha channel.

    This is the input format SAM 3D Objects expects: an RGBA image where
    alpha carries the mask (confirmed in facebookresearch/sam-3d-objects'
    own merge_image_and_mask / merge_mask_to_rgba helpers). ``box``, if
    given, places a locally-sized mask correctly instead of stretching it --
    see ``_place_mask_on_canvas``.
    """
    base = Image.open(image_path).convert("RGB")
    mask = _place_mask_on_canvas(mask, base.height, base.width, box)
    alpha = (mask * 255).astype(np.uint8)
    rgba = np.dstack([np.asarray(base), alpha])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_path)
    return out_path
