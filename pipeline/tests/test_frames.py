"""Tests for frames.py using a tiny synthetic video (no real phone footage needed)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from worldkit import frames


def _make_synthetic_video(path: Path, width: int = 128, height: int = 128, fps: float = 2.0) -> None:
    """Write an 8-frame video alternating a sharp checkerboard and a flat blur."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    assert writer.isOpened(), "OpenCV could not open a VideoWriter for the test video"
    for i in range(8):
        if i % 2 == 0:
            frame = np.zeros((height, width, 3), dtype="uint8")
            frame[::8, :] = 255
            frame[:, ::8] = 255
        else:
            frame = np.full((height, width, 3), 128, dtype="uint8")
        writer.write(frame)
    writer.release()


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    video_path = tmp_path / "test.mp4"
    _make_synthetic_video(video_path)
    return video_path


def test_extract_all_frames_missing_file_raises(tmp_path: Path):
    with pytest.raises(frames.FrameExtractionError):
        frames.extract_all_frames(tmp_path / "nope.mp4", tmp_path / "work")


def test_extract_all_frames_produces_expected_count(synthetic_video: Path, tmp_path: Path):
    extracted = frames.extract_all_frames(synthetic_video, tmp_path / "work", fps=2.0)
    assert len(extracted) == 8
    for p in extracted:
        assert p.is_file()


def test_score_and_select_sharpest_prefers_checkerboard_over_blur(synthetic_video: Path, tmp_path: Path):
    extracted = frames.extract_all_frames(synthetic_video, tmp_path / "work", fps=2.0)
    selected = frames.score_and_select_sharpest(extracted, fps=2.0, keep_n=4)

    assert len(selected) == 4
    # Even-indexed source frames are the sharp checkerboards; all 4 kept
    # frames should come from those even indices.
    assert all(f.index % 2 == 0 for f in selected)
    # Sorted sharpest-first.
    sharpness_values = [f.sharpness for f in selected]
    assert sharpness_values == sorted(sharpness_values, reverse=True)


def test_extract_sharp_frames_end_to_end_writes_selected_copies(synthetic_video: Path, tmp_path: Path):
    out_dir = tmp_path / "out"
    selected = frames.extract_sharp_frames(synthetic_video, out_dir, fps=2.0, keep_n=3)

    assert len(selected) == 3
    for f in selected:
        assert f.path.is_file()
        assert f.path.parent == out_dir / "frames"
    # Raw frames are also kept for debugging.
    assert len(list((out_dir / "_all_frames").glob("*.png"))) == 8
