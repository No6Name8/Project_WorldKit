"""Precise geometry edits on the PRINT mesh (trimesh + manifold3d). Each tool returns a NEW watertight mesh.

Conventions: millimetres, Z up, the base rests on z = 0 (as produced by printprep), x/y free. Every tool raises ``EditError``
with a reason instead of returning a broken mesh, and ``apply_tool`` returns a short machine-readable summary (dimensions,
volume change, watertight) that the agent loop feeds back to the model and to the verifier.
"""

from __future__ import annotations

import numpy as np
import trimesh

TOOL_NAMES = ["scale_to", "emboss_text", "drill_hole", "flatten_base", "split", "thicken", "mirror", "thicken_part"]


class EditError(ValueError):
    pass


# --- helpers -----------------------------------------------------------------------------------------------------------

def _manifold_to_trimesh(m) -> trimesh.Trimesh:
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False)


def _finish(mesh: trimesh.Trimesh, what: str, *, reseat: bool = True) -> trimesh.Trimesh:
    mesh = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), process=False)
    if not mesh.is_watertight:  # (merging an already-watertight manifold output can pinch near-duplicate vertices into non-manifold edges)
        mesh.merge_vertices()
        mesh.remove_unreferenced_vertices()
        trimesh.repair.fill_holes(mesh)
        trimesh.repair.fix_normals(mesh)
    if not mesh.is_watertight or len(mesh.faces) == 0 or mesh.volume <= 0:
        raise EditError(f"{what}: the result is not a valid watertight solid")
    if reseat:
        mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
    return mesh


def _boolean(op: str, a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    fn = trimesh.boolean.union if op == "union" else trimesh.boolean.difference
    try:
        return fn([a, b], engine="manifold")
    except Exception as exc:  # noqa: BLE001 - manifold raises generic errors on bad input
        raise EditError(f"boolean {op} failed: {exc}") from exc


def _sample_thickness(mesh: trimesh.Trimesh, n: int = 1500, seed: int = 0) -> np.ndarray:
    pts, fidx = trimesh.sample.sample_surface(mesh, n, seed=seed)
    nrm = mesh.face_normals[fidx]
    origins = pts - nrm * 1e-3
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, -nrm, multiple_hits=False)
    t = np.full(n, np.inf)
    t[ray_idx] = np.linalg.norm(locs - pts[ray_idx], axis=1)
    return t[np.isfinite(t)]


def body_footprint(mesh: trimesh.Trimesh):
    """(centre xy, size xy) of what actually stands on the base. printprep adds a flat plate wider than the object, so the footprint is
    measured just above that plate (first height where the cross-section shrinks well below the plate's) instead of at z=0."""
    def box(z):
        sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        if sec is None:
            return None
        c = sec.vertices[:, :2]
        return (c.min(0) + c.max(0)) / 2, c.max(0) - c.min(0)

    base = box(0.3)
    if base is None:
        return None
    plate_t = None
    for z in np.arange(0.6, 0.15 * mesh.extents[2], 0.4):
        b = box(float(z))
        if b is not None and b[1].max() < 0.8 * base[1].max():
            plate_t = float(z)
            break
    if plate_t is None:
        return base
    return box(plate_t + 1.0) or base


def floor_thickness(mesh: trimesh.Trimesh) -> float | None:
    """Thickness of the base at the centre of its footprint (distance up to the first inner surface), or None."""
    sec = mesh.section(plane_origin=[0, 0, 0.3], plane_normal=[0, 0, 1])
    if sec is None:
        return None
    c = sec.vertices[:, :2]
    cx, cy = (c.min(0) + c.max(0)) / 2
    locs, _, _ = mesh.ray.intersects_location([[cx, cy, 0.01]], [[0, 0, 1]])
    return float(np.sort(locs[:, 2])[0]) if len(locs) else None


def handle_info(mesh: trimesh.Trimesh) -> dict | None:
    """Outermost point of the handle, the normal of the plane the handle ring lies in, and the bar thickness along it."""
    fp = body_footprint(mesh)  # the body's footprint is handle-free (and not the wider print plate): its centre is the body axis
    if fp is None:
        return None
    centre, fsize = fp
    base_r = float(fsize.max()) / 2
    up_mask = mesh.vertices[:, 2] > 0.15 * mesh.extents[2]  # ignore the print plate near z=0
    idx = np.nonzero(up_mask)[0]
    horiz = mesh.vertices[idx, :2] - centre
    dist = np.linalg.norm(horiz, axis=1)
    j = int(np.argmax(dist))
    k = int(idx[j])
    if dist[j] < 1.2 * base_r:
        return None  # nothing sticks out noticeably: no handle
    d = np.array([horiz[j][0], horiz[j][1], 0.0]) / dist[j]
    outer = mesh.vertices[k]
    normal = np.array([-d[1], d[0], 0.0])
    # bar centre: step in from the outermost point to the middle of the bar (distance to its inner surface / 2)
    radial = None
    try:
        hits, _, _ = mesh.ray.intersects_location([outer - d * 0.5], [-d])
        if len(hits):
            radial = 0.5 + float(np.linalg.norm(hits - (outer - d * 0.5), axis=1).min())
    except Exception:  # noqa: BLE001
        radial = None
    center = outer - d * (radial / 2 if radial else 2.0)
    th = None
    try:
        a_, _, _ = mesh.ray.intersects_location([center], [normal])
        b_, _, _ = mesh.ray.intersects_location([center], [-normal])
        if len(a_) and len(b_):
            th = float(np.linalg.norm(a_ - center, axis=1).min() + np.linalg.norm(b_ - center, axis=1).min())
    except Exception:  # noqa: BLE001
        th = None
    return {"bar_center_point_mm": [round(float(x), 2) for x in center], "outermost_point_mm": [round(float(x), 2) for x in outer],
            "plane_normal": [round(float(x), 4) for x in normal], "direction_from_body_axis": [round(float(x), 4) for x in d],
            "bar_thickness_along_normal_mm": None if th is None else round(th, 2),
            "hint": "drill_hole(position=bar_center_point_mm, direction=plane_normal) goes through the handle bar sideways"}


def model_facts(mesh: trimesh.Trimesh) -> dict:
    ext = mesh.extents
    return {
        "units": "mm", "up_axis": "z", "base_plane_z_mm": 0.0,
        "size_mm": {"x": round(float(ext[0]), 2), "y": round(float(ext[1]), 2), "z_height": round(float(ext[2]), 2)},
        "bounding_box_mm": {"min": [round(float(v), 2) for v in mesh.bounds[0]], "max": [round(float(v), 2) for v in mesh.bounds[1]]},
        "volume_mm3": round(float(mesh.volume), 1), "watertight": bool(mesh.is_watertight),
        "base_floor_thickness_mm": None if floor_thickness(mesh) is None else round(floor_thickness(mesh), 2),
        "handle": handle_info(mesh),
        "named_parts": [{"id": k, "sam_views_found": v.get("views_found"), "sam_views_total": v.get("views_total"), "usable_for_thicken_part": v.get("stl_points") is not None} for k, v in PARTS.items()],
        "front_face": "the -y side", "base_face": "the underside (z = 0)",
    }


# --- tools -------------------------------------------------------------------------------------------------------------

def scale_to(mesh: trimesh.Trimesh, height_cm: float | None = None, factor: float | None = None) -> trimesh.Trimesh:
    """Uniformly scale (about the footprint centre, base stays on z=0) to a total height in cm, or by a factor."""
    if (height_cm is None) == (factor is None):
        raise EditError("scale_to needs exactly one of height_cm or factor")
    f = factor if factor is not None else height_cm * 10.0 / mesh.extents[2]
    if not (0.05 <= f <= 20):
        raise EditError(f"scale factor {f:.3f} is outside the sane range 0.05..20")
    out = mesh.copy()
    cx, cy = (out.bounds[0][:2] + out.bounds[1][:2]) / 2
    out.apply_translation([-cx, -cy, 0])
    out.apply_scale(f)
    out.apply_translation([cx, cy, 0])
    return _finish(out, "scale_to")


def _text_polygons(text: str, mirror_x: bool):
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    tp = TextPath((0, 0), text, size=10, prop=FontProperties(family="DejaVu Sans", weight="bold"))
    polys = [np.asarray(p) for p in tp.to_polygons() if len(p) >= 3]
    if not polys:
        raise EditError("the text produced no printable outline")
    allp = np.vstack(polys)
    lo, hi = allp.min(0), allp.max(0)
    polys = [(p - (lo + hi) / 2) for p in polys]
    if mirror_x:
        polys = [p * np.array([-1, 1]) for p in polys]
    return polys, float(hi[0] - lo[0]), float(hi[1] - lo[1])


def _extrude_text(polys, scale: float, z0: float, z1: float) -> trimesh.Trimesh:
    from manifold3d import CrossSection, FillRule, Manifold

    cs = CrossSection([p * scale for p in polys], fillrule=FillRule.EvenOdd)
    if cs.area() <= 0:
        raise EditError("text outline has zero area")
    m = Manifold.extrude(cs, z1 - z0).translate((0.0, 0.0, z0))
    return _manifold_to_trimesh(m)


def emboss_text(mesh: trimesh.Trimesh, text: str, face: str = "base", depth_mm: float = 0.6, mode: str | None = None) -> trimesh.Trimesh:
    """Text on the base (default: ENGRAVED so the object still sits flat; reads correctly when viewed from below) or on the
    front (-y side, RAISED). ``mode`` may be forced to "raise"/"engrave". Text is sized to fit the available surface."""
    text = text.strip()
    if not text or len(text) > 40:
        raise EditError("text must be 1-40 characters")
    if face not in ("base", "front"):
        raise EditError("face must be 'base' or 'front'")
    if not (0.1 <= depth_mm <= 3.0):
        raise EditError("depth_mm must be between 0.1 and 3.0")
    notes: list[str] = []
    mode = mode or ("engrave" if face == "base" else "raise")
    if mode not in ("raise", "engrave"):
        raise EditError("mode must be 'raise' or 'engrave'")
    if face == "base":
        fp = body_footprint(mesh)
        if fp is None:
            raise EditError("could not find the base footprint")
        center, size = fp
        polys, tw, th = _text_polygons(text, mirror_x=True)  # mirrored: reads correctly when the object is flipped over
        scale = min(0.62 * size.min() / tw, 0.30 * size.min() / th)
        if mode == "engrave":
            ft = floor_thickness(mesh)
            if ft is not None and depth_mm > 0.6 * ft:
                notes.append(f"depth reduced from {depth_mm} to {round(0.6 * ft, 2)} mm: the base is only {ft:.2f} mm thick")
                depth_mm = round(0.6 * ft, 2)  # never cut through the floor
            cutter = _extrude_text(polys, scale, -0.5, depth_mm)
        else:
            cutter = _extrude_text(polys, scale, -depth_mm, 0.4)
        cutter.apply_translation([center[0], center[1], 0])
    else:
        polys, tw, th = _text_polygons(text, mirror_x=False)
        zmid = 0.5 * mesh.extents[2]
        sec = mesh.section(plane_origin=[0, 0, zmid], plane_normal=[0, 0, 1])
        c = sec.vertices[:, :2]
        center, width = (c.min(0) + c.max(0)) / 2, float(c.max(0)[0] - c.min(0)[0])
        scale = min(0.45 * width / tw, 0.35 * mesh.extents[2] / th)
        ymin = float(c.min(0)[1])
        sag = (0.5 * tw * scale) ** 2 / max(width, 1.0)  # body curves away from the text plane at its edges
        flat = _extrude_text(polys, scale, 0.0, depth_mm + sag + 0.5)  # extruded along +z; rotate so +z -> -y
        flat.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))  # +z -> -y, text upright
        flat.apply_translation([center[0], ymin + sag + 0.5, zmid])
        cutter = flat
    out = _boolean("union" if mode == "raise" else "difference", mesh, cutter)
    if abs(out.volume - mesh.volume) < 0.2:
        raise EditError("the text did not change the solid (it missed the surface)")
    res = _finish(out, "emboss_text")
    res.metadata["edit_notes"] = notes + [f"text '{text}' ({mode}, {face}, {depth_mm} mm deep)"]
    return res


def drill_hole(mesh: trimesh.Trimesh, diameter_mm: float, position, direction=(0, 0, 1), through: bool = True, depth_mm: float | None = None) -> trimesh.Trimesh:
    """Cylindrical hole of ``diameter_mm`` whose axis passes through ``position`` (mm) along ``direction``."""
    if not (0.5 <= diameter_mm <= 60):
        raise EditError("diameter_mm must be between 0.5 and 60")
    pos = np.asarray(position, float)
    d = np.asarray(direction, float)
    if pos.shape != (3,) or d.shape != (3,) or np.linalg.norm(d) < 1e-9:
        raise EditError("position and direction must be 3-vectors (direction non-zero)")
    d = d / np.linalg.norm(d)
    length = 4.0 * float(np.linalg.norm(mesh.extents)) if through else float(depth_mm or 0)
    if not through and not (0.2 <= length <= 500):
        raise EditError("a blind hole needs depth_mm in 0.2..500")
    cyl = trimesh.creation.cylinder(radius=diameter_mm / 2, height=length, sections=64)
    cyl.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], d))
    cyl.apply_translation(pos if through else pos + d * length / 2)
    out = _boolean("difference", mesh, cyl)
    removed = mesh.volume - out.volume
    if removed < 0.2:
        raise EditError("the hole removed no material: the axis does not pass through the object (check position/direction)")
    return _finish(out, "drill_hole")


def flatten_base(mesh: trimesh.Trimesh, depth_mm: float = 0.5) -> trimesh.Trimesh:
    """Shave ``depth_mm`` off the underside so the base is one exactly flat plane; the object gets that much shorter."""
    if not (0.05 <= depth_mm <= 0.25 * mesh.extents[2]):
        raise EditError("depth_mm must be between 0.05 and 25% of the height")
    box = trimesh.creation.box(extents=[10 * mesh.extents[0], 10 * mesh.extents[1], 2 * mesh.extents[2]])
    box.apply_translation([*(mesh.bounds[0][:2] + mesh.bounds[1][:2]) / 2, -mesh.extents[2] + depth_mm])
    return _finish(_boolean("difference", mesh, box), "flatten_base")


def split(mesh: trimesh.Trimesh, axis: str = "z", offset_mm: float | None = None, keep: str = "upper") -> trimesh.Trimesh:
    """Cut by the plane axis = offset_mm (default: the middle) and KEEP one half, capped and watertight."""
    ax = {"x": 0, "y": 1, "z": 2}.get(axis)
    if ax is None or keep not in ("upper", "lower"):
        raise EditError("axis must be x/y/z and keep must be upper/lower")
    lo, hi = mesh.bounds[0][ax], mesh.bounds[1][ax]
    off = (lo + hi) / 2 if offset_mm is None else float(offset_mm)
    if not (lo + 0.5 < off < hi - 0.5):
        raise EditError(f"offset {off:.2f} mm is outside the model's {axis}-range {lo:.2f}..{hi:.2f}")
    big = 10.0 * float(np.linalg.norm(mesh.extents))
    ext = np.full(3, big)
    ext[ax] = big
    box = trimesh.creation.box(extents=ext)
    c = (mesh.bounds[0] + mesh.bounds[1]) / 2
    c[ax] = off + (big / 2 if keep == "upper" else -big / 2)
    box.apply_translation(c)
    try:
        part = trimesh.boolean.intersection([mesh, box], engine="manifold")
    except Exception as exc:  # noqa: BLE001
        raise EditError(f"split failed: {exc}") from exc
    return _finish(part, "split")


def thicken(mesh: trimesh.Trimesh, min_wall_mm: float = 1.5) -> trimesh.Trimesh:
    """Grow walls thinner than ``min_wall_mm``: Minkowski-sum the solid with a sphere of half the deficit, so every wall gains
    that much on both faces. The outer size grows by that margin and cavities/holes shrink by it; the model is returned
    unchanged (as a copy) if no wall is thinner than requested."""
    from manifold3d import Manifold, Mesh

    if not (0.4 <= min_wall_mm <= 10):
        raise EditError("min_wall_mm must be between 0.4 and 10")
    t = _sample_thickness(mesh)
    t_min = float(np.percentile(t, 5)) if len(t) else min_wall_mm
    if t_min >= min_wall_mm:
        return mesh.copy()
    r = (min_wall_mm - t_min) / 2 + 0.05
    mm = Manifold(Mesh(vert_properties=mesh.vertices.astype("float32"), tri_verts=mesh.faces.astype("uint32")))
    out = _manifold_to_trimesh(mm.minkowski_sum(Manifold.sphere(r, 12)))
    return _finish(out, "thicken")


def mirror(mesh: trimesh.Trimesh, axis: str = "x") -> trimesh.Trimesh:
    """Reflect the model about the mid-plane of its bounding box along ``axis`` (x/y/z); winding is corrected."""
    ax = {"x": 0, "y": 1, "z": 2}.get(axis)
    if ax is None:
        raise EditError("axis must be x, y or z")
    out = mesh.copy()
    s = np.ones(3)
    s[ax] = -1
    out.vertices = out.vertices * s
    out.faces = out.faces[:, ::-1]
    mid = (mesh.bounds[0][ax] + mesh.bounds[1][ax]) / 2
    shift = np.zeros(3)
    shift[ax] = 2 * mid
    out.apply_translation(shift)
    return _finish(out, "mirror")


# Named parts found by worldkit.parts (SAM 3.1 masks voted onto the mesh), loaded by edit.run_edit: id -> {"stl_points", "views_found", "views_total"}
PARTS: dict = {}
MIN_PART_COVERAGE = 0.5  # SAM must have found the part in at least half of the usable views


def set_parts(parts: dict | None) -> None:
    PARTS.clear()
    PARTS.update(parts or {})


def thicken_part(mesh: trimesh.Trimesh, part: str, by_mm: float = 2.0) -> trimesh.Trimesh:
    """Grow one named part (e.g. the handle) by ``by_mm`` in cross-section: its surface moves outward by by_mm / 2 on every side, fading to zero
    4 mm away from the part so it blends into the body. The rest of the model does not change."""
    from scipy.spatial import cKDTree

    p = PARTS.get(part)
    if p is None or p.get("stl_points") is None:
        raise EditError(f"no usable part {part!r}; known parts with a print-mesh selection: {[k for k, v in PARTS.items() if v.get('stl_points') is not None]}")
    found, total = p.get("views_found") or 0, p.get("views_total") or 0
    if total == 0 or found / total < MIN_PART_COVERAGE:
        raise EditError(f"SAM found the {part} in only {found} of {total} views, too few to edit it reliably. Nothing was changed.")
    if not 0.2 <= by_mm <= 6:
        raise EditError("by_mm must be between 0.2 and 6")
    d, _ = cKDTree(p["stl_points"]).query(mesh.vertices)
    w = np.clip(1.0 - np.maximum(d - 2.0, 0.0) / 2.5, 0.0, 1.0)  # full effect within 2 mm of a selected GLB vertex (their spacing), none beyond 4.5 mm
    out = mesh.copy()
    out.vertices = mesh.vertices + mesh.vertex_normals * (by_mm / 2.0) * w[:, None]
    from manifold3d import Manifold, Mesh

    if str(Manifold(Mesh(vert_properties=out.vertices.astype("float32"), tri_verts=out.faces.astype("uint32"))).status()) != "Error.NoError":
        raise EditError(f"thicken_part: growing the {part} made the surface self-intersect; try a smaller by_mm")
    res = _finish(out, "thicken_part")
    res.metadata["part_info"] = {"part": part, "by_mm": float(by_mm), "views_found": p.get("views_found"), "views_total": p.get("views_total")}
    return res


_TOOLS = {"scale_to": scale_to, "emboss_text": emboss_text, "drill_hole": drill_hole, "flatten_base": flatten_base,
          "split": split, "thicken": thicken, "mirror": mirror, "thicken_part": thicken_part}


def sentence_for(name: str, args: dict, before: trimesh.Trimesh, after: trimesh.Trimesh, info: dict | None) -> str:
    """One plain sentence describing an applied tool call (no tool names, no JSON)."""
    f = lambda v: f"{float(v):g}"
    if info:
        return f"Thickened {info['part']} by {info['by_mm']:g} mm (SAM found {info['views_found']} of {info['views_total']} views)"
    if name == "scale_to":
        return f"Scaled to {after.extents[2]:.0f} mm tall" if abs(after.extents[2] - before.extents[2]) > 0.05 else "Scaled (size unchanged)"
    if name == "emboss_text":
        verb = "Raised" if (args.get("mode") == "raise" or (args.get("mode") is None and args.get("face") == "front")) else "Engraved"
        return f'{verb} "{args.get("text", "")}" on the {"front" if args.get("face") == "front" else "base"}'
    if name == "drill_hole":
        return f"Drilled a {f(args.get('diameter_mm', 0))} mm hole" + (" all the way through" if args.get("through", True) else "")
    if name == "flatten_base":
        return f"Flattened the base by {f(args.get('depth_mm', 0.5))} mm"
    if name == "split":
        return f"Cut at {args.get('axis', 'z')} = {f(args.get('offset_mm', 0)) if args.get('offset_mm') is not None else 'the middle'} mm and kept the {args.get('keep', 'upper')} half"
    if name == "thicken":
        return f"Thickened thin walls to at least {f(args.get('min_wall_mm', 0))} mm"
    if name == "mirror":
        return f"Mirrored along {args.get('axis', 'x')}"
    return f"Applied {name}"


def apply_tool(mesh: trimesh.Trimesh, name: str, args: dict) -> tuple[trimesh.Trimesh, dict]:
    """Run one tool by name; returns (new mesh, summary). Unknown tools / bad arguments raise EditError."""
    fn = _TOOLS.get(name)
    if fn is None:
        raise EditError(f"unknown tool {name!r}; available: {TOOL_NAMES}")
    try:
        new = fn(mesh, **args)
    except TypeError as exc:
        raise EditError(f"bad arguments for {name}: {exc}") from exc
    info = new.metadata.get("part_info")
    return new, {
        "sentence": sentence_for(name, args, mesh, new, info), **({"part": info} if info else {}),
        "ok": True, "tool": name, "notes": new.metadata.get("edit_notes", []), "size_mm_before": [round(float(v), 2) for v in mesh.extents], "size_mm_after": [round(float(v), 2) for v in new.extents],
        "volume_change_mm3": round(float(new.volume - mesh.volume), 1), "watertight": bool(new.is_watertight), "faces": int(len(new.faces)),
    }
