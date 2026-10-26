"""Render 4-angle preview PNGs of the final STL, without needing a GPU or display.

Uses matplotlib's Agg backend (headless, software-rendered) with a simple
Poly3DCollection surface plot. This is a preview render for the report, not a
production-quality render -- it exists so you can sanity-check the model's
shape without opening a slicer or 3D viewer.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

PREVIEW_ANGLES_DEG = [0, 90, 180, 270]


def render_preview_angles(
    mesh: trimesh.Trimesh, out_dir: Path, angles_deg: list[int] | None = None
) -> list[Path]:
    """Render ``mesh`` from each angle in ``angles_deg`` (rotation about Z) to PNG."""
    angles_deg = angles_deg or PREVIEW_ANGLES_DEG
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    verts = mesh.vertices
    faces = mesh.faces
    center = mesh.bounds.mean(axis=0)
    radius = float(np.linalg.norm(mesh.extents)) / 2 or 1.0

    paths: list[Path] = []
    for angle in angles_deg:
        fig = plt.figure(figsize=(4, 4), dpi=150)
        ax = fig.add_subplot(111, projection="3d")
        collection = Poly3DCollection(verts[faces], facecolor="#c7cedb", edgecolor="#4a4f5c", linewidths=0.1)
        ax.add_collection3d(collection)

        ax.set_xlim(center[0] - radius, center[0] + radius)
        ax.set_ylim(center[1] - radius, center[1] + radius)
        ax.set_zlim(center[2] - radius, center[2] + radius)
        ax.view_init(elev=20, azim=angle)
        ax.set_box_aspect((1, 1, 1))
        ax.set_axis_off()

        out_path = out_dir / f"preview_{angle:03d}.png"
        fig.savefig(out_path, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        paths.append(out_path)

    return paths
