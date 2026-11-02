"""Find a named part of a reconstructed object (e.g. the handle of a mug) from SAM 3.1 masks and mark it on the mesh.

    python -m worldkit.parts RUN_DIR [--part handle --concept "mug handle" --object mug]      -> RUN_DIR/parts/parts.json

For every frame where the whole object is segmented and the mesh pose fits the silhouette (IoU >= 0.85), SAM is asked for the part concept. A mesh
vertex belongs to the part if, in the frames that found the part, it faces the camera and projects inside the part mask in at least half of
the frames where it is visible inside the object mask (and at least twice). The result is a vote over 2D masks, so its edges are approximate.
"views_found of views_total" is stored and shown to the user next to any edit that uses the part.

The selection is stored twice: as exact (quantised) GLB vertex positions for the viewer overlay, and transferred into the print mesh's frame
(mm, Z up) for edits. The transfer aligns the GLB to the STL by the scale factor in report.json and a rotation search, and records its error.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

from . import segment, video_appearance
from . import texture_project as tp

KEY_SCALE = 1e4  # GLB positions are matched in the viewer by round(x * 1e4)
MIN_POSE_IOU = 0.85


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def unb64(s: str, dtype, cols: int = 3) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=dtype).reshape(-1, cols)


def vote_part(V, F, normals, frames: list) -> dict:
    """``frames``: dicts with ``obj_mask``, ``part_mask`` (or None when SAM found nothing) and ``pose``. Returns selection + counts."""
    seen = np.zeros(len(V), int)
    hit = np.zeros(len(V), int)
    views_total = views_found = 0
    for fr in frames:
        views_total += 1
        pm = fr["part_mask"]
        if pm is None or pm.sum() < 40:
            continue
        views_found += 1
        pose = fr["pose"]
        xy, _ = pose.project(V)
        h, w = fr["obj_mask"].shape
        ix, iy = np.round(xy[:, 0]).astype(int), np.round(xy[:, 1]).astype(int)
        inside = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        facing = normals @ pose.rotation()[2] > 0.1
        vis = inside & facing
        in_obj = np.zeros(len(V), bool)
        in_part = np.zeros(len(V), bool)
        in_obj[vis] = fr["obj_mask"][iy[vis], ix[vis]]
        in_part[vis] = pm[iy[vis], ix[vis]]
        seen += in_obj
        hit += in_part & in_obj
    need = 2 if views_found >= 3 else 1
    sel = (hit >= need) & (hit >= 0.5 * np.maximum(seen, 1))
    # keep the largest connected group of selected faces (drops stray votes)
    m = trimesh.Trimesh(V, F, process=False)
    face_sel = sel[F].all(axis=1)
    if face_sel.any():
        sub = np.nonzero(face_sel)[0]
        adj = m.face_adjacency
        keep_adj = adj[face_sel[adj[:, 0]] & face_sel[adj[:, 1]]]
        import scipy.sparse as sp
        from scipy.sparse.csgraph import connected_components

        g = sp.coo_matrix((np.ones(len(keep_adj)), (keep_adj[:, 0], keep_adj[:, 1])), shape=(len(F), len(F)))
        _, lab = connected_components(g, directed=False)
        labs, counts = np.unique(lab[sub], return_counts=True)
        big = labs[counts.argmax()]
        face_keep = np.zeros(len(F), bool)
        face_keep[sub[lab[sub] == big]] = True
        sel = np.zeros(len(V), bool)
        sel[np.unique(F[face_keep])] = True
    return {"selected": sel, "views_total": views_total, "views_found": views_found}


def glb_to_stl_transform(V_glb: np.ndarray, stl: trimesh.Trimesh, mm_per_unit: float, body_height_mm: float):
    """Similarity (fixed scale) taking GLB vertices (Y up) into the print mesh frame (mm, Z up, base plate under the object).
    Returns (function points->points, rms error mm). Tries the four Z rotations of the Y-up -> Z-up map."""
    from scipy.spatial import cKDTree

    zmax = float(stl.bounds[1][2])
    plate_top = zmax - body_height_mm
    body = stl.vertices[stl.vertices[:, 2] > plate_top + 0.8]
    tree = cKDTree(body)
    base = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)  # (x, y, z) -> (x, -z, y)
    best = None
    rng = np.random.default_rng(0)
    pick = rng.choice(len(V_glb), min(len(V_glb), 4000), replace=False)
    for k in range(4):
        a = np.radians(90 * k)
        Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        R = Rz @ base
        P = (V_glb * mm_per_unit) @ R.T
        # translation: bounding-box centre in x/y, base on the plate top
        lo, hi = P.min(0), P.max(0)
        t = np.array([(body.min(0)[0] + body.max(0)[0]) / 2 - (lo[0] + hi[0]) / 2, (body.min(0)[1] + body.max(0)[1]) / 2 - (lo[1] + hi[1]) / 2, plate_top - lo[2]])
        Q = P[pick] + t
        Q = Q[Q[:, 2] > plate_top + 0.8]  # compare like with like: the body filter above excludes the foot
        d, _ = tree.query(Q)
        rms = float(np.sqrt(np.mean(d ** 2)))
        if best is None or rms < best[0]:
            best = (rms, R, t)
    rms, R, t = best
    return (lambda pts: (np.asarray(pts, float) * mm_per_unit) @ R.T + t), rms


def find_part(run_dir: Path, part_id: str = "handle", concept: str = "mug handle", object_name: str = "mug", log=print) -> dict:
    run_dir = Path(run_dir)
    glb = trimesh.load(run_dir / "model.glb", force="mesh", process=False)
    V, F = np.asarray(glb.vertices, float), np.asarray(glb.faces)
    normals = np.asarray(trimesh.Trimesh(V, F, process=False).vertex_normals)
    frame_paths = sorted((run_dir / "frames").glob("sharp_*.png"))
    if not frame_paths:
        raise SystemExit(f"{run_dir}/frames has no sharp_*.png frames")
    inputs, _ = video_appearance.build_frame_inputs(frame_paths, object_name)
    frames = []
    for inp in inputs:
        try:
            fit = tp.fit_pose(V, F, inp.mask)
        except tp.TextureProjectionError:
            continue
        if fit.iou < MIN_POSE_IOU:
            continue
        path = next(p for p in frame_paths if p.stem == inp.name)
        part_mask = None
        try:
            r = segment.segment_concept(path, concept)
            if not getattr(r, "used_fallback", False):
                part_mask = segment.full_frame_mask(path, r)
        except segment.SegmentationError:
            part_mask = None
        frames.append({"name": inp.name, "obj_mask": inp.mask, "part_mask": part_mask, "pose": fit.pose})
        log(f"{inp.name}: pose IoU {fit.iou:.2f}, SAM {'found' if part_mask is not None and part_mask.sum() >= 40 else 'did not find'} '{concept}'")
    if not frames:
        raise SystemExit("no frame had both an object mask and a good pose fit")
    v = vote_part(V, F, normals, frames)
    sel = v["selected"]
    out = {"id": part_id, "name": part_id, "concept": concept, "views_total": v["views_total"], "views_found": v["views_found"], "vertex_count": int(sel.sum()),
           "method": "SAM 3.1 part masks voted onto the mesh vertices (2D votes, edges approximate)", "glb_keys_b64": _b64(np.round(V[sel] * KEY_SCALE).astype(np.int32))}
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8")) if (run_dir / "report.json").is_file() else {}
    pp, stl_path = rep.get("print_prep") or {}, run_dir / "model_print.stl"
    if sel.any() and pp.get("scale_factor") and stl_path.is_file():
        stl = trimesh.load(stl_path, force="mesh")
        body_h = float((rep.get("measurement") or {}).get("height_mm") or pp.get("upright_height_cm", 0) * 10)
        fn, rms = glb_to_stl_transform(V, stl, float(pp["scale_factor"]) * 10.0, body_h)
        out["stl_alignment_rms_mm"] = round(rms, 3)
        if rms <= 1.5:
            out["stl_points_b64"] = _b64(fn(V[sel]).astype(np.float32))
        else:
            out["stl_note"] = f"GLB to STL alignment error {rms:.2f} mm is too large; edits on this part are disabled"
    (run_dir / "parts").mkdir(exist_ok=True)
    (run_dir / "parts" / "parts.json").write_text(json.dumps({"object": object_name, "parts": [out]}, indent=1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Find a named part with SAM 3.1 and store it for the viewer and AI Edit")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--part", default="handle")
    ap.add_argument("--concept", default="mug handle")
    ap.add_argument("--object", default="mug")
    a = ap.parse_args(argv)
    r = find_part(a.run_dir, a.part, a.concept, a.object)
    print(f"{a.part}: SAM found it in {r['views_found']} of {r['views_total']} views, {r['vertex_count']} vertices"
          + (f", STL alignment {r['stl_alignment_rms_mm']} mm" if "stl_alignment_rms_mm" in r else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
