"""Tests for video_appearance.py: only genuine, correctly placed segmentations become projection inputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from worldkit import segment, video_appearance


def _frame(tmp_path: Path, name: str, size=(120, 80), colour=(10, 20, 30)) -> Path:
    path = tmp_path / f"{name}.png"
    Image.new("RGB", size, colour).save(path)
    return path


def _result(mask: np.ndarray, box, fallback=False) -> segment.SegmentationMaskResult:
    return segment.SegmentationMaskResult(
        concept="mug", mask=mask, box=box, foreground_pixels=int(mask.sum()), used_fallback=fallback
    )


def test_a_box_local_sam_mask_is_placed_at_its_real_position_in_the_full_frame(tmp_path: Path):
    # SAM 3.1 rasterises its mask to its own bounding box (a real defect fixed earlier in segment.py);
    # projection needs it at the right place in the WHOLE frame.
    path = _frame(tmp_path, "sharp_01", size=(120, 80))
    local = np.ones((20, 30), dtype=bool)  # height 20, width 30
    box = (40.0, 10.0, 70.0, 30.0)  # left, top, right, bottom

    inputs, skipped = video_appearance.build_frame_inputs([path], "mug", segment_fn=lambda p, c: _result(local, box))

    assert skipped == []
    assert inputs[0].name == "sharp_01"
    assert inputs[0].rgb.shape == (80, 120, 3) and inputs[0].mask.shape == (80, 120)
    assert inputs[0].mask[10:30, 40:70].all() and inputs[0].mask.sum() == 20 * 30


def test_fallback_masks_are_never_used_for_projection(tmp_path: Path):
    path = _frame(tmp_path, "sharp_02")
    full = np.ones((80, 120), dtype=bool)

    inputs, skipped = video_appearance.build_frame_inputs([path], "mug", segment_fn=lambda p, c: _result(full, None, fallback=True))

    assert inputs == []
    assert skipped == [{"frame": "sharp_02", "reason": "only the local fallback mask was available (not concept-aware)"}]


def test_segmentation_errors_skip_that_frame_but_keep_the_rest(tmp_path: Path):
    good, bad = _frame(tmp_path, "good"), _frame(tmp_path, "bad")
    full = np.ones((80, 120), dtype=bool)

    def fake(path, concept):
        if path.stem == "bad":
            raise segment.SegmentationError("SAM 3.1 found no mask")
        return _result(full, None)

    inputs, skipped = video_appearance.build_frame_inputs([good, bad], "mug", segment_fn=fake)

    assert [i.name for i in inputs] == ["good"]
    assert skipped[0]["frame"] == "bad" and "no mask" in skipped[0]["reason"]


def test_empty_masks_are_skipped(tmp_path: Path):
    path = _frame(tmp_path, "empty")

    inputs, skipped = video_appearance.build_frame_inputs(
        [path], "mug", segment_fn=lambda p, c: _result(np.zeros((80, 120), dtype=bool), None)
    )

    assert inputs == [] and skipped[0]["reason"] == "empty mask"
