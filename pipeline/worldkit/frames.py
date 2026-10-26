"""Extract frames from a video and keep the sharpest ones.

Frame extraction is done with a real ffmpeg binary, obtained via the
``imageio-ffmpeg`` package (it ships a static ffmpeg build so this works
without a system-wide ffmpeg install). Sharpness is scored with the
variance of the Laplacian, a standard focus-measure: a well-focused image
has strong high-frequency edge content, which shows up as a high-variance
Laplacian response; a blurry image is smoother and scores low.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import imageio_ffmpeg


class FrameExtractionError(RuntimeError):
    """Raised when ffmpeg fails to extract frames from a video."""


@dataclass
class Frame:
    """One extracted video frame."""

    path: Path
    index: int
    timestamp_sec: float
    sharpness: float

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "index": self.index,
            "timestamp_sec": round(self.timestamp_sec, 3),
            "sharpness": round(self.sharpness, 2),
        }


def _ffmpeg_binary() -> str:
    """Return the path to the bundled ffmpeg binary, downloading it on first use."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def _laplacian_sharpness(image_path: Path) -> float:
    """Focus measure: variance of the Laplacian. Higher = sharper."""
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FrameExtractionError(f"could not read extracted frame: {image_path}")
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def extract_all_frames(video_path: Path, work_dir: Path, fps: float = 2.0) -> list[Path]:
    """Extract frames from ``video_path`` at a fixed sampling rate using ffmpeg.

    Returns the extracted frame paths in order. Raises ``FrameExtractionError``
    with ffmpeg's own stderr if extraction fails (bad video, unsupported
    codec, etc.) so the failure is never silent.
    """
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FrameExtractionError(f"video file not found: {video_path}")

    work_dir.mkdir(parents=True, exist_ok=True)
    pattern = work_dir / "frame_%06d.png"
    ffmpeg = _ffmpeg_binary()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        "-qscale:v",
        "2",
        str(pattern),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FrameExtractionError(
            f"ffmpeg failed (exit {proc.returncode}) on {video_path}:\n{proc.stderr.strip()}"
        )

    frame_paths = sorted(work_dir.glob("frame_*.png"))
    if not frame_paths:
        raise FrameExtractionError(
            f"ffmpeg ran but produced no frames from {video_path}. "
            f"stderr:\n{proc.stderr.strip()}"
        )
    return frame_paths


def score_and_select_sharpest(
    frame_paths: list[Path], fps: float, keep_n: int = 12
) -> list[Frame]:
    """Score every extracted frame for sharpness and keep the ``keep_n`` sharpest.

    Returned frames are sorted by sharpness, descending (best first), but each
    keeps its original ``index``/``timestamp_sec`` so downstream stages can
    still reason about temporal order if needed.
    """
    scored: list[Frame] = []
    for i, path in enumerate(frame_paths):
        sharpness = _laplacian_sharpness(path)
        scored.append(
            Frame(path=path, index=i, timestamp_sec=i / fps, sharpness=sharpness)
        )
    scored.sort(key=lambda f: f.sharpness, reverse=True)
    return scored[:keep_n]


def extract_sharp_frames(
    video_path: Path,
    out_dir: Path,
    fps: float = 2.0,
    keep_n: int = 12,
) -> list[Frame]:
    """Extract frames from ``video_path`` and return the ``keep_n`` sharpest.

    All raw extracted frames are written to ``out_dir/_all_frames`` (kept for
    debugging); the selected sharp frames are copied to ``out_dir/frames`` and
    returned, best (sharpest) first.
    """
    out_dir = Path(out_dir)
    raw_dir = out_dir / "_all_frames"
    sharp_dir = out_dir / "frames"
    sharp_dir.mkdir(parents=True, exist_ok=True)

    all_frames = extract_all_frames(video_path, raw_dir, fps=fps)
    selected = score_and_select_sharpest(all_frames, fps=fps, keep_n=keep_n)

    final: list[Frame] = []
    for rank, frame in enumerate(selected):
        dest = sharp_dir / f"sharp_{rank:02d}.png"
        dest.write_bytes(frame.path.read_bytes())
        final.append(
            Frame(
                path=dest,
                index=frame.index,
                timestamp_sec=frame.timestamp_sec,
                sharpness=frame.sharpness,
            )
        )
    return final
