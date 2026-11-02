"""Shaded views of a GLB from several angles (painter's algorithm, Lambert shading), for checking a mesh by eye without a GPU or browser.

    python scripts/quick_views.py MODEL.glb|MODEL.stl OUT.png [--el 25] [--n 4] [--zup]
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw


def render(mesh: trimesh.Trimesh, az: float, el: float, size: int = 320) -> Image.Image:
    a, e = np.radians(az), np.radians(el)
    R = np.array([[np.cos(a), 0, -np.sin(a)], [0, 1, 0], [np.sin(a), 0, np.cos(a)]])
    R = np.array([[1, 0, 0], [0, np.cos(e), -np.sin(e)], [0, np.sin(e), np.cos(e)]]) @ R  # tilt the view down by el
    v = (mesh.vertices - mesh.bounds.mean(0)) @ R.T
    s = 0.8 * size / max(np.ptp(v[:, 0]), np.ptp(v[:, 1]))
    xy = np.c_[v[:, 0] * s + size / 2, -v[:, 1] * s + size / 2]
    n = mesh.face_normals @ R.T
    light = np.array([0.4, 0.6, 0.7]) / np.linalg.norm([0.4, 0.6, 0.7])
    shade = np.clip(np.abs(n @ light), 0.12, 1.0)
    order = np.argsort(v[mesh.faces][:, :, 2].mean(1))  # far (low z) first, viewer looks down -z
    im = Image.new("RGB", (size, size), (21, 23, 26))
    d = ImageDraw.Draw(im)
    for f in order:
        c = int(60 + 180 * shade[f])
        d.polygon([tuple(p) for p in xy[mesh.faces[f]]], fill=(c, c, c))
    d.text((4, 4), f"az {az:.0f} el {el:.0f}", fill=(255, 200, 0))
    return im


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("glb", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--el", type=float, default=25.0)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--zup", action="store_true", help="the file is Z-up (print STLs): turn it Y-up before rendering")
    a = ap.parse_args()
    m = trimesh.load(io.BytesIO(a.glb.read_bytes()), file_type=a.glb.suffix.lstrip(".").lower(), force="mesh")
    if a.zup:
        m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    ims = [render(m, az, a.el) for az in np.linspace(0, 360, a.n, endpoint=False)] + [render(m, 0, 80)]
    sheet = Image.new("RGB", (320 * len(ims), 320))
    for i, im in enumerate(ims):
        sheet.paste(im, (320 * i, 0))
    sheet.save(a.out)


if __name__ == "__main__":
    main()
