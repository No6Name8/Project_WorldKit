"""Turn extracted video frames into inputs for texture re-projection.

Only GENUINE concept-aware segmentations are used. The local fallback mask (no concept awareness)
can include table, shadow or neighbouring objects; projecting those pixels onto the object would
paint background onto it, so such frames are skipped and reported rather than used.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from . import segment
from .texture_project import FrameInput


def build_frame_inputs(
    frame_paths: list[Path],
    concept: str,
    *,
    segment_fn: Callable[..., segment.SegmentationMaskResult] = segment.segment_concept,
) -> tuple[list[FrameInput], list[dict]]:
    """Segment every frame and return (usable FrameInputs, records of skipped frames + why)."""
    inputs: list[FrameInput] = []
    skipped: list[dict] = []
    for path in frame_paths:
        path = Path(path)
        try:
            result = segment_fn(path, concept)
        except segment.SegmentationError as exc:
            skipped.append({"frame": path.stem, "reason": f"segmentation failed: {exc}"})
            continue
        if result.used_fallback:
            skipped.append({"frame": path.stem, "reason": "only the local fallback mask was available (not concept-aware)"})
            continue
        mask = segment.full_frame_mask(path, result)
        if not mask.any():
            skipped.append({"frame": path.stem, "reason": "empty mask"})
            continue
        with Image.open(path) as im:
            rgb = np.array(im.convert("RGB"))
        inputs.append(FrameInput(path.stem, rgb, mask))
    return inputs, skipped
