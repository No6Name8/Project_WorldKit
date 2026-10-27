"""Turn a reconstructed mesh (GLB) into a print-ready STL.

All work happens in centimeters. Steps, in order:
  1. scale to a real-world size FIRST (user-given --height-cm, or an A4-sheet
     pixel estimate, clearly labeled as an estimate)
  2. repair into a watertight, correctly-wound manifold (trimesh repair,
     falling back to a manifold3d self-union for meshes repair can't fix)
  3. add a flat base so the model can stand on a print bed
  4. orient into the most stable resting pose
  5. check minimum wall thickness by inward ray casting, flagging thin spots
  6. export STL

Note on "height": ``upright_height_cm`` (captured right after step 1, before
any reorientation) is the real-world height along the object's original
upright axis -- this is what should be compared against a tape-measured
height. ``dimensions_cm`` (captured at the end) is the final bounding box
AFTER orienting for the most stable print pose, which may lay the object on
its side -- useful for bed-fit, not for "how tall is this".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

DEFAULT_MIN_WALL_MM = 1.2
BASE_THICKNESS_MM = 1.6
A4_WIDTH_CM = 21.0  # short edge of an A4 sheet


class PrintPrepError(RuntimeError):
    pass


@dataclass
class ThinWallWarning:
    point_cm: tuple[float, float, float]
    thickness_mm: float


@dataclass
class PrintPrepReport:
    scale_factor: float
    target_height_cm: float
    upright_height_cm: float  # height along the original upright axis, post-scale, pre-reorientation
    height_source: str  # "user" | "a4_estimate" | "default_fallback"
    is_estimate: bool
    dimensions_cm: tuple[float, float, float]  # final bbox, after print-orientation
    volume_cm3: float
    watertight: bool
    watertight_repair_used: str  # "none" | "trimesh_repair" | "manifold_union" | "convex_hull_fallback"
    min_wall_mm_checked: float
    thin_wall_warnings: list[ThinWallWarning] = field(default_factory=list)
    thin_wall_sample_count: int = 0

    @property
    def print_ready(self) -> bool:
        """Real defect found via integration testing (2026-10-27): object.py's
        CLI report never surfaced a print_ready verdict at all -- the viewer's
        provenance panel expected one and silently showed "(not recorded)".
        Single source of truth here (not duplicated ad hoc per caller):
        watertight AND zero sampled points below the minimum wall threshold.
        """
        return self.watertight and len(self.thin_wall_warnings) == 0

    def to_dict(self) -> dict:
        return {
            "scale_factor": round(self.scale_factor, 5),
            "target_height_cm": round(self.target_height_cm, 3),
            "upright_height_cm": round(self.upright_height_cm, 3),
            "height_source": self.height_source,
            "is_estimate": self.is_estimate,
            "dimensions_cm_after_print_orientation": [round(d, 3) for d in self.dimensions_cm],
            "volume_cm3": round(self.volume_cm3, 3),
            "watertight": self.watertight,
            "watertight_repair_used": self.watertight_repair_used,
            "min_wall_mm_checked": self.min_wall_mm_checked,
            "thin_wall_warning_count": len(self.thin_wall_warnings),
            "thin_wall_sample_count": self.thin_wall_sample_count,
            "thin_wall_warnings": [
                {"point_cm": list(w.point_cm), "thickness_mm": round(w.thickness_mm, 3)}
                for w in self.thin_wall_warnings[:20]
            ],
            "print_ready": self.print_ready,
        }


def estimate_height_cm_from_a4(
    object_pixel_height: float, a4_pixel_width: float
) -> float:
    """Estimate the object's real-world height from an A4 sheet in the same photo.

    Assumes the object and the A4 sheet are roughly the same distance from
    the camera (e.g. both resting on the same table), so pixel sizes scale
    the same way. This is a rough estimate, not a calibrated measurement --
    callers must label it as such.
    """
    if a4_pixel_width <= 0:
        raise PrintPrepError("a4_pixel_width must be > 0 to estimate scale")
    return object_pixel_height / a4_pixel_width * A4_WIDTH_CM


def load_mesh(path: Path) -> trimesh.Trimesh:
    path = Path(path)
    if not path.is_file():
        raise PrintPrepError(f"mesh file not found: {path}")
    loaded = trimesh.load(path, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh):
        raise PrintPrepError(f"{path} did not load as a single mesh (got {type(loaded)})")
    if len(loaded.vertices) == 0 or len(loaded.faces) == 0:
        raise PrintPrepError(f"{path} loaded but is empty (0 vertices/faces)")
    return loaded


def scale_to_height(
    mesh: trimesh.Trimesh, target_height_cm: float
) -> tuple[trimesh.Trimesh, float]:
    """Uniformly scale ``mesh`` so its bounding-box height (Z extent) equals target_height_cm."""
    current_height = mesh.extents[2]
    if current_height <= 0:
        raise PrintPrepError("mesh has zero height; cannot scale")
    factor = target_height_cm / current_height
    mesh = mesh.copy()
    mesh.apply_scale(factor)
    return mesh, factor


def make_watertight(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, str]:
    """Repair ``mesh`` into a watertight manifold, trying increasingly strong fixes."""
    mesh = mesh.copy()
    if mesh.is_watertight:
        return mesh, "none"

    mesh.merge_vertices()
    mesh.remove_duplicate_faces()
    mesh.remove_degenerate_faces()
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)
    if mesh.is_watertight:
        return mesh, "trimesh_repair"

    try:
        unioned = trimesh.boolean.union([mesh], engine="manifold")
        if unioned.is_watertight:
            return unioned, "manifold_union"
    except Exception:  # noqa: BLE001 - fall through to last-resort fallback
        pass

    hull = mesh.convex_hull
    return hull, "convex_hull_fallback"


def add_flat_base(mesh: trimesh.Trimesh, base_thickness_mm: float = BASE_THICKNESS_MM) -> trimesh.Trimesh:
    """Union a thin flat slab under ``mesh`` so it can stand on a print bed."""
    thickness_cm = base_thickness_mm / 10.0
    min_corner, max_corner = mesh.bounds
    extents_xy = (max_corner[0] - min_corner[0], max_corner[1] - min_corner[1])
    pad = 1.05  # slightly oversize the base footprint so it fully covers the model's base
    center_xy = ((min_corner[0] + max_corner[0]) / 2, (min_corner[1] + max_corner[1]) / 2)
    base_center_z = min_corner[2] - thickness_cm / 2  # top face sits at the model's lowest point
    base = trimesh.creation.box(extents=(extents_xy[0] * pad, extents_xy[1] * pad, thickness_cm))
    base.apply_translation((center_xy[0], center_xy[1], base_center_z))

    try:
        combined = trimesh.boolean.union([mesh, base], engine="manifold")
    except Exception as exc:  # noqa: BLE001
        raise PrintPrepError(f"failed to union flat base onto mesh: {exc}") from exc
    return combined


def orient_for_printing(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Rotate ``mesh`` into its most physically stable resting pose."""
    mesh = mesh.copy()
    try:
        transforms, probabilities = trimesh.poses.compute_stable_poses(mesh)
        if len(transforms) > 0:
            mesh.apply_transform(transforms[int(np.argmax(probabilities))])
    except Exception:  # noqa: BLE001 - keep original orientation if this fails
        pass
    mesh.apply_translation((0, 0, -mesh.bounds[0][2]))  # re-settle onto the Z=0 plane
    return mesh


def check_min_wall_thickness(
    mesh: trimesh.Trimesh, min_wall_mm: float = DEFAULT_MIN_WALL_MM, n_samples: int = 1500
) -> tuple[list[ThinWallWarning], int]:
    """Sample the surface and cast rays inward to estimate local wall thickness.

    For each sampled surface point, casts a ray along the inverse of its
    normal and measures the distance to the next surface hit (the opposite
    interior wall). Points below ``min_wall_mm`` are returned as warnings.
    This is a sampling-based estimate, not an exhaustive thickness map.
    """
    min_wall_cm = min_wall_mm / 10.0
    try:
        points, face_indices = trimesh.sample.sample_surface(mesh, n_samples)
    except Exception as exc:  # noqa: BLE001
        raise PrintPrepError(f"surface sampling failed: {exc}") from exc

    normals = mesh.face_normals[face_indices]
    origins = points - normals * 1e-4  # nudge inside to avoid self-hit
    directions = -normals

    locations, index_ray, _ = mesh.ray.intersects_location(origins, directions)
    warnings: list[ThinWallWarning] = []
    if len(locations) > 0:
        distances = np.linalg.norm(locations - origins[index_ray], axis=1)
        for dist, ray_idx in zip(distances, index_ray):
            if dist < min_wall_cm:
                pt = tuple(float(c) for c in points[ray_idx])
                warnings.append(ThinWallWarning(point_cm=pt, thickness_mm=float(dist * 10.0)))

    return warnings, n_samples


def export_stl(mesh: trimesh.Trimesh, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path, file_type="stl")
    return path


def prepare_for_print(
    mesh_path: Path,
    out_stl_path: Path,
    *,
    height_cm: float | None = None,
    estimated_height_cm: float | None = None,
    min_wall_mm: float = DEFAULT_MIN_WALL_MM,
) -> PrintPrepReport:
    """Run the full print-prep pipeline on the mesh at ``mesh_path``.

    Exactly one of ``height_cm`` (user-measured, exact) or
    ``estimated_height_cm`` (derived from the A4 sheet, approximate) should
    be given; if both are None, falls back to a flagged default height.
    """
    mesh = load_mesh(mesh_path)

    if height_cm is not None:
        target_height_cm, height_source, is_estimate = height_cm, "user", False
    elif estimated_height_cm is not None:
        target_height_cm, height_source, is_estimate = estimated_height_cm, "a4_estimate", True
    else:
        target_height_cm, height_source, is_estimate = 10.0, "default_fallback", True

    mesh, scale_factor = scale_to_height(mesh, target_height_cm)
    upright_height_cm = float(mesh.extents[2])  # captured BEFORE reorientation

    mesh, repair_used = make_watertight(mesh)
    mesh = add_flat_base(mesh)
    mesh = orient_for_printing(mesh)

    warnings, n_samples = check_min_wall_thickness(mesh, min_wall_mm=min_wall_mm)

    export_stl(mesh, out_stl_path)

    return PrintPrepReport(
        scale_factor=scale_factor,
        target_height_cm=target_height_cm,
        upright_height_cm=upright_height_cm,
        height_source=height_source,
        is_estimate=is_estimate,
        dimensions_cm=tuple(float(e) for e in mesh.extents),
        volume_cm3=float(mesh.volume),
        watertight=bool(mesh.is_watertight),
        watertight_repair_used=repair_used,
        min_wall_mm_checked=min_wall_mm,
        thin_wall_warnings=warnings,
        thin_wall_sample_count=n_samples,
    )
