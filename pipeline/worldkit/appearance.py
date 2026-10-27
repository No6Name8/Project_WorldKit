"""Make a generated, textured GLB display correctly everywhere (viewer, Blender, Unity...).

Two real defects found by inspecting actual generator output:

* glTF's default ``metallicFactor`` is 1.0. TRELLIS omits it, so its materials read as fully
  metallic and render near-black without an environment map (the unlit sides of the mug were
  almost black in the viewer). A ceramic mug is dielectric, and the generator never *measured*
  metalness -- the omission is an exporter default, not captured appearance. An explicitly
  authored value is left untouched; only the absent/implicit default is replaced.
* No vertex normals, so viewers fall back to faceted shading.

Optionally the mesh is re-centred and uniformly rescaled to the viewer's working size (the
viewer's collision probe and step sizes are in scene units tuned for a ~2-unit object; TRELLIS
emits ~0.4). That is an arbitrary-unit change: it does NOT calibrate real-world scale.
"""

from __future__ import annotations

import io

import numpy as np
import trimesh


class AppearanceError(RuntimeError):
    pass


def glb_is_textured(glb_bytes: bytes) -> bool:
    """True if the GLB's (single) geometry has a UV-mapped base colour texture."""
    try:
        scene = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    except Exception:  # noqa: BLE001 - unparseable input is simply "not a usable textured GLB"
        return False
    geoms = list(scene.geometry.values()) if hasattr(scene, "geometry") else [scene]
    return len(geoms) == 1 and getattr(getattr(geoms[0].visual, "material", None), "baseColorTexture", None) is not None


def finalize_textured_glb(
    glb_bytes: bytes, *, normalize_scale: bool = True, target_extent: float = 2.0
) -> tuple[bytes, dict]:
    """Return (display-ready GLB, report of exactly what was changed)."""
    scene = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    geoms = list(scene.geometry.values()) if hasattr(scene, "geometry") else [scene]
    if not geoms:
        raise AppearanceError("GLB contains no geometry")

    report: dict = {"metallic_factor_set_to_zero": [], "vertex_normals_added": False, "scale_normalized": None}
    for i, g in enumerate(geoms):
        material = getattr(g.visual, "material", None)
        if hasattr(material, "metallicFactor") and material.metallicFactor is None:
            material.metallicFactor = 0.0
            report["metallic_factor_set_to_zero"].append(i)
        _ = g.vertex_normals  # computing it caches it, so the exporter writes a NORMAL attribute
        report["vertex_normals_added"] = True

    if normalize_scale:
        bounds = np.array([g.bounds for g in geoms])
        lo, hi = bounds[:, 0].min(axis=0), bounds[:, 1].max(axis=0)
        extent = float((hi - lo).max())
        if extent <= 0:
            raise AppearanceError("mesh has zero extent")
        factor = target_extent / extent
        centre = (lo + hi) / 2.0
        for g in geoms:
            g.vertices = (np.asarray(g.vertices) - centre) * factor
        report["scale_normalized"] = {
            "from_max_extent": round(extent, 5), "to_max_extent": target_extent,
            "note": "arbitrary scene units for viewer compatibility; NOT a real-world calibration",
        }

    return scene.export(file_type="glb"), report
