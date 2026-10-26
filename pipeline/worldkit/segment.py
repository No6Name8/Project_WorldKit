"""Get a clean mask of the main object (and of an A4 sheet, if present).

Real path: calls the Meta Model API's Responses endpoint with model
"sam-3.1", one short noun-phrase concept per request (per the Meta Model
cookbook's SAM API basics recipe), parsed with the official
``meta_sam_parser`` package rather than hand-parsing the event stream or the
mask wire format.

Fallback path (FALLBACK_LOCAL_MASK): if the Meta API is unavailable (no key,
or a billing/auth rejection), falls back to a local background-removal model
(rembg/u2net, runs on CPU, no network once its weights are cached) to produce
a foreground mask. This never claims to be SAM 3.1 -- every mask produced
this way is tagged ``used_fallback_mask=True`` and every output filename it
touches is prefixed ``FALLBACK_``, so it can never be mistaken for a real
result. It also cannot find a named concept (e.g. "A4 sheet") -- it only
separates foreground from background -- so A4 segmentation is skipped when
falling back; use --height-cm instead.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

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
    mask: np.ndarray  # bool array, shape (H, W)
    box: tuple[float, float, float, float] | None  # left, top, right, bottom
    foreground_pixels: int
    used_fallback: bool = False


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


def _fallback_mask(image_path: Path) -> SegmentationMaskResult:
    """FALLBACK_LOCAL_MASK: local background removal via rembg/u2net (CPU, no Meta API)."""
    from rembg import remove

    img = Image.open(image_path).convert("RGB")
    cutout = remove(img)  # RGBA, alpha = foreground mask
    alpha = np.asarray(cutout)[..., 3]
    mask = alpha > 10

    if not mask.any():
        raise SegmentationError(
            f"FALLBACK_LOCAL_MASK found no foreground in {image_path} (fully transparent result)"
        )

    ys, xs = np.where(mask)
    box = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))

    return SegmentationMaskResult(
        concept="(fallback: whole foreground, no concept prompt)",
        mask=mask,
        box=box,
        foreground_pixels=int(mask.sum()),
        used_fallback=True,
    )


def segment_concept(
    image_path: Path, concept: str, client: AsyncOpenAI | None = None
) -> SegmentationMaskResult:
    """Segment ``concept`` (a short noun phrase, e.g. 'coffee mug') in ``image_path``.

    Falls back to FALLBACK_LOCAL_MASK (whole-foreground cutout, no concept
    awareness) if the Meta API is unavailable. Raises SegmentationError if
    SAM 3.1 is reachable but reports no mask / an error for the concept, or
    if the fallback itself fails.
    """
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

    return SegmentationMaskResult(
        concept=concept,
        mask=arr,
        box=box,
        foreground_pixels=int(arr.sum()),
        used_fallback=False,
    )


def save_mask_overlay(
    image_path: Path, mask: np.ndarray, out_path: Path, color=(255, 64, 64), used_fallback: bool = False
) -> Path:
    """Write a PNG of ``image_path`` with ``mask`` drawn as a translucent overlay.

    If ``used_fallback`` is True, the filename is prefixed FALLBACK_ regardless
    of what the caller asked for, so a fallback result can never look real.
    """
    if used_fallback and out_path.name and not out_path.name.startswith("FALLBACK_"):
        out_path = out_path.with_name(f"FALLBACK_{out_path.name}")

    base = Image.open(image_path).convert("RGBA")
    if mask.shape != (base.height, base.width):
        mask_img = Image.fromarray((mask * 255).astype(np.uint8)).resize(
            (base.width, base.height), Image.NEAREST
        )
        mask = np.asarray(mask_img) > 0

    overlay = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    overlay[mask] = (*color, 110)
    overlay_img = Image.fromarray(overlay, mode="RGBA")
    composed = Image.alpha_composite(base, overlay_img)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composed.convert("RGB").save(out_path)
    return out_path


def save_rgba_with_mask(image_path: Path, mask: np.ndarray, out_path: Path) -> Path:
    """Write an RGBA PNG with ``mask`` embedded in the alpha channel.

    This is the input format SAM 3D Objects expects: an RGBA image where
    alpha carries the mask (confirmed in facebookresearch/sam-3d-objects'
    own merge_image_and_mask / merge_mask_to_rgba helpers).
    """
    base = Image.open(image_path).convert("RGB")
    if mask.shape != (base.height, base.width):
        mask_img = Image.fromarray((mask * 255).astype(np.uint8)).resize(
            (base.width, base.height), Image.NEAREST
        )
        mask = np.asarray(mask_img) > 0
    alpha = (mask * 255).astype(np.uint8)
    rgba = np.dstack([np.asarray(base), alpha])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(out_path)
    return out_path
