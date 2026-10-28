"""Synthetic scenes with KNOWN real-world sizes, rendered with a true perspective (pinhole) camera.

Used to validate scale calibration against ground truth: the mesh has a known height in cm, a coin of known diameter
lies on the table, and the camera orbits at a known distance and elevation. The calibration code only sees masks.
"""

from __future__ import annotations

import numpy as np
import trimesh
from PIL import Image, ImageDraw

W, H = 480, 856


def mug_mesh(height_cm: float = 9.0, radius_cm: float = 4.0) -> trimesh.Trimesh:
    """Cylinder body + ring handle, Y-up, standing on y=0, units = cm."""
    body = trimesh.creation.cylinder(radius=radius_cm, height=height_cm, sections=48)
    body.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))  # axis z -> y
    body.apply_translation([0, height_cm / 2, 0])
    handle = trimesh.creation.torus(major_radius=2.2, minor_radius=0.5, major_sections=32, minor_sections=12)
    handle.apply_translation([radius_cm + 1.2, height_cm * 0.55, 0])
    return trimesh.util.concatenate([body, handle])


def _camera(az_deg: float, el_deg: float, dist_cm: float, target: np.ndarray):
    az, el = np.radians(az_deg), np.radians(el_deg)
    eye = target + dist_cm * np.array([np.sin(az) * np.cos(el), np.sin(el), np.cos(az) * np.cos(el)])
    fwd = target - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 1, 0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    return eye, right, up, fwd


def project(points: np.ndarray, eye, right, up, fwd, focal_px: float) -> np.ndarray:
    rel = points - eye
    z = rel @ fwd
    return np.stack([W / 2 + focal_px * (rel @ right) / z, H / 2 - focal_px * (rel @ up) / z], axis=1)


def render_masks(
    mesh_cm: trimesh.Trimesh, coin_diameter_cm: float, coin_offset_cm: tuple[float, float],
    az_deg: float, el_deg: float, dist_cm: float, focal_px: float = 700.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Full-frame bool masks (mug, coin) under a pinhole camera orbiting the mug."""
    target = np.array([0.0, 4.0, 0.0])
    eye, right, up, fwd = _camera(az_deg, el_deg, dist_cm, target)

    xy = project(np.asarray(mesh_cm.vertices), eye, right, up, fwd, focal_px)
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    for t in xy[np.asarray(mesh_cm.faces)]:
        d.polygon([tuple(p) for p in t], fill=1)
    mug = np.array(img, dtype=bool)

    ang = np.linspace(0, 2 * np.pi, 90, endpoint=False)
    r = coin_diameter_cm / 2
    circle = np.stack([coin_offset_cm[0] + r * np.cos(ang), np.full_like(ang, 0.05), coin_offset_cm[1] + r * np.sin(ang)], axis=1)
    cxy = project(circle, eye, right, up, fwd, focal_px)
    img = Image.new("1", (W, H), 0)
    ImageDraw.Draw(img).polygon([tuple(p) for p in cxy], fill=1)
    return mug, np.array(img, dtype=bool)
