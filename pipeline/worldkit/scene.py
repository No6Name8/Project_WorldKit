"""Multi-object scene from one video: each object is segmented, reconstructed and finalised on its own, then placed
in a shared scene using the camera geometry of ONE frame in which all of them are visible.

    python -m worldkit.scene <video> --out <dir> --objects mug "round side table" [--mesh-backend modal|trellis|import]
                             [--ref-concept coin --ref-diameter-cm D]

Output (``scene.json`` is the manifest the viewer loads together with the object GLBs):

    <out>/scene.json
    <out>/objects/<id>/{model.glb, object_rgba.png, report.json}
    <out>/layout_check.png        reprojection of the composed scene onto the source frame

How the layout is derived (and what it is not):
- the LARGEST object is the parent and defines the world frame (its finalised mesh units are the scene units);
- each object's camera pose in the chosen frame comes from the silhouette pose fit used for texture projection
  (``texture_project.fit_pose``: weak-perspective, azimuth/elevation/pixels-per-unit/translation);
- a child's scale relative to the parent is the ratio of the two fitted pixels-per-unit values, its yaw is the
  difference of the fitted azimuths, and its position is where its base centre's pixel meets the parent's top
  plane (a ray/plane intersection under the parent's camera). So children rest ON the parent's top surface;
  that is an assumption of this workflow, not something measured for objects that are not on top of the parent;
- the composed scene is re-projected through the parent's camera and compared with the real masks
  (``layout_iou`` in the manifest): that number is the only evidence of layout accuracy.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

from . import appearance, frames, object as obj_cli, reconstruct, scale_cal, segment, texture_project as tp

SCENE_VERSION = 1


class SceneError(RuntimeError):
    pass


# --- geometry helpers (pure, unit-tested) ---------------------------------------------------------------------------

def yaw_matrix(deg: float) -> np.ndarray:
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def top_plane_y(vertices: np.ndarray, top_fraction: float = 0.02) -> float:
    """Height of an object's top surface: median y of the highest ``top_fraction`` of its vertices."""
    y = np.sort(vertices[:, 1])
    k = max(3, int(len(y) * top_fraction))
    return float(np.median(y[-k:]))


def base_center(vertices: np.ndarray) -> np.ndarray:
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    return np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])


def ray_to_plane(pose: tp.CameraPose, pixel: np.ndarray, plane_y: float) -> np.ndarray:
    """World point (X, plane_y, Z) whose projection under ``pose`` is ``pixel`` (weak perspective, exact inverse)."""
    R = pose.rotation()
    a = np.array([[R[0, 0], R[0, 2]], [R[1, 0], R[1, 2]]])
    b = np.array([
        (pixel[0] - pose.tx) / pose.scale - R[0, 1] * plane_y,
        (pose.ty - pixel[1]) / pose.scale - R[1, 1] * plane_y,
    ])
    if abs(np.linalg.det(a)) < 1e-6:
        raise SceneError("camera looks straight down the parent's top plane; cannot place the child from one frame")
    X, Z = np.linalg.solve(a, b)
    return np.array([X, plane_y, Z])


def place_child(parent_pose: tp.CameraPose, parent_vertices: np.ndarray, child_pose: tp.CameraPose,
                child_vertices: np.ndarray) -> dict:
    """Transform (position, yaw_deg, uniform scale) putting the child on the parent's top plane, in parent units."""
    scale = child_pose.scale / parent_pose.scale
    yaw = child_pose.az_deg - parent_pose.az_deg
    bc = base_center(child_vertices)
    pixel = child_pose.project(bc[None, :])[0][0]
    target = ray_to_plane(parent_pose, pixel, top_plane_y(parent_vertices))
    position = target - scale * (yaw_matrix(yaw) @ bc)
    return {"position": [float(v) for v in position], "rotation_deg": [0.0, float(yaw), 0.0], "scale": [float(scale)] * 3}


def transform_vertices(vertices: np.ndarray, t: dict) -> np.ndarray:
    R = yaw_matrix(t["rotation_deg"][1])
    return (np.asarray(vertices) @ R.T) * t["scale"][0] + np.asarray(t["position"])


def aabb_overlap(a: np.ndarray, b: np.ndarray) -> dict:
    """Axis-aligned overlap of two vertex clouds: per-axis penetration depth (<=0 means separated on that axis)."""
    lo = np.maximum(a.min(axis=0), b.min(axis=0))
    hi = np.minimum(a.max(axis=0), b.max(axis=0))
    pen = hi - lo
    return {"penetration": [float(v) for v in pen], "intersects": bool(np.all(pen > 0))}


def layout_iou(parent_pose, parent_v, parent_f, children, size, masks) -> dict:
    """Re-project the composed scene through the parent's camera; IoU against the real masks of every object."""
    out = {}
    merged = tp.silhouette(parent_v, parent_f, parent_pose, size)
    out["parent"] = tp.iou(merged, masks["parent"])
    union_pred = merged.copy()
    union_true = masks["parent"].copy()
    for cid, (v, f, t) in children.items():
        w = transform_vertices(v, t)
        sil = tp.silhouette(w, f, parent_pose, size)
        out[cid] = tp.iou(sil, masks[cid])
        union_pred |= sil
        union_true |= masks[cid]
    out["all"] = tp.iou(union_pred, union_true)
    return {k: round(float(v), 3) for k, v in out.items()}


# --- pipeline -------------------------------------------------------------------------------------------------------

def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or "object"


def _load_mesh(glb_bytes: bytes):
    import trimesh

    sc = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    geoms = list(sc.geometry.values()) if hasattr(sc, "geometry") else [sc]
    m = trimesh.util.concatenate(geoms) if len(geoms) > 1 else geoms[0]
    return np.asarray(m.vertices, float), np.asarray(m.faces)


def choose_frame(sharp_frames: list, concepts: list[str], forced: str | None):
    """First frame (sharpest first) in which SAM 3.1 finds every concept; returns (frame, {concept: (mask, result)})."""
    candidates = [f for f in sharp_frames if forced is None or Path(f.path).stem == forced]
    if not candidates:
        raise SceneError(f"--frame {forced} is not one of the extracted frames")
    last_err = "no frames"
    for fr in sorted(candidates, key=lambda f: -f.sharpness):
        found = {}
        try:
            for c in concepts:
                r = segment.segment_concept(fr.path, c)
                if r.used_fallback:
                    raise segment.SegmentationError("only the local fallback mask is available (not concept-aware)")
                found[c] = (segment.full_frame_mask(fr.path, r), r)
            return fr, found
        except segment.SegmentationError as exc:
            last_err = f"{Path(fr.path).stem}: {exc}"
    raise SceneError(f"no frame shows all of {concepts} with a genuine SAM 3.1 mask ({last_err})")


def run(args: argparse.Namespace) -> dict:
    from PIL import Image

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"warnings": [], "timings_sec": {}}
    t0 = time.time()
    if len(args.objects) < 2:
        raise SceneError("a scene needs at least two --objects")

    sharp = frames.extract_sharp_frames(args.video, out, fps=args.fps, keep_n=args.keep_frames)
    fr, found = choose_frame(sharp, list(args.objects), args.frame)
    frame_rgb = np.array(Image.open(fr.path).convert("RGB"))
    h, w = frame_rgb.shape[:2]
    report["frame"] = Path(fr.path).stem

    # biggest object = parent; smaller objects are subtracted from larger masks (they are in front of / on top of them)
    order = sorted(args.objects, key=lambda c: -int(found[c][0].sum()))
    parent_c = order[0]
    ids = {c: _slug(c) for c in args.objects}
    if len(set(ids.values())) != len(ids):
        raise SceneError("object names must be distinct")
    full = {c: found[c][0] for c in args.objects}
    visible = {}
    for c in args.objects:
        m = full[c].copy()
        for o in args.objects:
            if o != c and full[o].sum() < full[c].sum():
                m &= ~full[o]
        visible[c] = m

    mesh_files = dict(kv.split("=", 1) for kv in (args.mesh_file or []))
    objects, meshes, masks_for_fit = [], {}, {}
    for c in order:
        oid = ids[c]
        odir = out / "objects" / oid
        odir.mkdir(parents=True, exist_ok=True)
        rgba = np.dstack([frame_rgb, (visible[c] * 255).astype(np.uint8)])
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, "PNG")
        (odir / "object_rgba.png").write_bytes(buf.getvalue())
        crop = obj_cli.prepare_modal_input(buf.getvalue())

        t1 = time.time()
        if args.mesh_backend == "modal":
            glb, method = obj_cli.run_sam3d_on_modal(crop), "modal_sam3d_builtin_mesh_postprocess_and_texture_baking"
        elif args.mesh_backend == "trellis":
            import os

            glb = reconstruct.reconstruct_via_trellis_space(crop, space_id=reconstruct.DEFAULT_TRELLIS_SPACE_ID, hf_token=os.environ.get("HF_TOKEN"))
            method = f"hosted_space_trellis:{reconstruct.DEFAULT_TRELLIS_SPACE_ID}"
        elif args.mesh_backend == "import":
            if oid not in mesh_files:
                raise SceneError(f"--mesh-backend import needs --mesh-file {oid}=<path>")
            glb, method = reconstruct.load_external_mesh(Path(mesh_files[oid])), f"imported_external_mesh:{Path(mesh_files[oid]).name}"
        else:
            raise SceneError(f"unknown --mesh-backend {args.mesh_backend}")
        recon_s = time.time() - t1

        info: dict = {"textured": appearance.glb_is_textured(glb), "texture_source": "generated by the reconstruction model"}
        if info["textured"]:
            glb, fin = appearance.finalize_textured_glb(glb)
            info["finalize"] = fin
        (odir / "model.glb").write_bytes(glb)
        model = dict(obj_cli.RECONSTRUCTION_MODELS[args.mesh_backend])
        orep = {
            "object_name": c, "frame": report["frame"], "mesh_backend": args.mesh_backend, "mesh_method": method,
            "reconstruction_model": model, "appearance": info,
            "segment_source": "sam-3.1_cached" if found[c][1].from_cache else "sam-3.1_live",
            "mask_pixels_visible": int(visible[c].sum()), "reconstruct_seconds": round(recon_s, 1),
            "print_ready": False, "print_ready_note": "not evaluated per object in scene mode",
            "model_mesh": str(odir / "model.glb"),
        }
        (odir / "report.json").write_text(json.dumps(orep, indent=2))
        v, f = _load_mesh(glb)
        meshes[c] = (v, f)
        masks_for_fit[c] = full[c] if c == parent_c else visible[c]
        objects.append({"id": oid, "name": c, "mesh": f"objects/{oid}/model.glb", "report": f"objects/{oid}/report.json",
                        "role": "parent" if c == parent_c else "child", "reconstruction_model": model})

    # --- layout from the shared frame ------------------------------------------------------------------------------
    pv, pf = meshes[parent_c]
    pfit = tp.fit_pose(pv, pf, masks_for_fit[parent_c])
    if pfit.iou < 0.8:
        report["warnings"].append(f"parent silhouette fit is loose (IoU {pfit.iou:.2f}); layout will be rough")
    fits = {parent_c: pfit}
    children = {}
    for o in objects:
        c = o["name"]
        if c == parent_c:
            o["transform"] = {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}
            o["layout"] = {"method": "parent defines the world frame", "silhouette_iou": round(pfit.iou, 3)}
            continue
        cv, cf = meshes[c]
        cfit = tp.fit_pose(cv, cf, masks_for_fit[c])
        fits[c] = cfit
        t = place_child(pfit.pose, pv, cfit.pose, cv)
        o["transform"] = t
        o["layout"] = {"method": "scale=px/unit ratio, yaw=azimuth difference, position=base-centre ray meets parent top plane",
                       "silhouette_iou": round(cfit.iou, 3), "assumption": "object rests on the top surface of the parent"}
        children[ids[c]] = (cv, cf, t)
    masks = {"parent": masks_for_fit[parent_c], **{ids[c]: masks_for_fit[c] for c in order[1:]}}
    report["layout_iou"] = layout_iou(pfit.pose, pv, pf, children, (w, h), masks)

    # overlap / contact table in the world frame
    world = {parent_c: pv}
    for o in objects:
        if o["role"] == "child":
            world[o["name"]] = transform_vertices(meshes[o["name"]][0], o["transform"])
    pairs = []
    names = list(world)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            ov = aabb_overlap(world[names[i]], world[names[j]])
            pairs.append({"a": ids[names[i]], "b": ids[names[j]], **ov})
    report["aabb_overlaps"] = pairs

    manifest = {
        "worldkit_scene": SCENE_VERSION, "source_video": Path(args.video).name, "frame": report["frame"],
        "units": "scene units = the parent object's finalised mesh units (arbitrary, not a real-world unit)",
        "cm_per_scene_unit": None, "scale_calibration": None, "objects": objects,
        "layout_iou": report["layout_iou"], "aabb_overlaps": pairs,
        "layout_note": "Children are placed on the parent's top plane using one frame's weak-perspective pose fits.",
    }

    # --- optional real-world scale ---------------------------------------------------------------------------------
    if args.ref_diameter_cm is not None:
        scale_target = order[1]  # the reference sits next to the object it was measured with; use the first child
        frames_in = []
        for sf in sharp:
            try:
                om = segment.segment_concept(sf.path, scale_target)
                if om.used_fallback:
                    continue
                om_mask = segment.full_frame_mask(sf.path, om)
            except segment.SegmentationError:
                continue
            ref_mask = None
            try:
                rr = segment.segment_concept(sf.path, args.ref_concept)
                ref_mask = None if rr.used_fallback else segment.full_frame_mask(sf.path, rr)
            except segment.SegmentationError:
                pass
            frames_in.append((Path(sf.path).stem, om_mask, ref_mask))
        try:
            cal = scale_cal.calibrate(frames_in, *meshes[scale_target], args.ref_concept, args.ref_diameter_cm)
            child_scale = next(o for o in objects if o["name"] == scale_target)["transform"]["scale"][0]
            manifest["cm_per_scene_unit"] = cal.cm_per_unit / child_scale
            manifest["scale_calibration"] = {**cal.to_dict(), "measured_on_object": ids[scale_target],
                                             "note_scene": "cm_per_scene_unit = cm_per_unit(child) / child scale in parent units"}
        except scale_cal.ScaleCalibrationError as exc:
            report["warnings"].append(f"scale calibration failed; scene stays in arbitrary units: {exc}")
            manifest["scale_calibration"] = {"status": "failed", "reason": str(exc)}

    (out / "scene.json").write_text(json.dumps(manifest, indent=2))
    _save_layout_check(out / "layout_check.png", frame_rgb, pfit.pose, pv, pf, children, masks)
    report["total_seconds"] = round(time.time() - t0, 1)
    report["scene"] = str(out / "scene.json")
    return report


def _save_layout_check(path: Path, frame_rgb, pose, pv, pf, children, masks) -> None:
    """Source frame with the composed scene's silhouettes drawn over it (red=parent, green=children)."""
    from PIL import Image

    h, w = frame_rgb.shape[:2]
    img = frame_rgb.astype(float)
    sil = tp.silhouette(pv, pf, pose, (w, h))
    img[sil] = 0.6 * img[sil] + 0.4 * np.array([255, 0, 0])
    for cid, (v, f, t) in children.items():
        s = tp.silhouette(transform_vertices(v, t), f, pose, (w, h))
        img[s] = 0.5 * img[s] + 0.5 * np.array([0, 255, 0])
    Image.fromarray(img.astype(np.uint8)).save(path)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reconstruct several objects from one video and compose them into a scene")
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--objects", nargs="+", default=None, help='SAM 3.1 concepts, e.g. mug "round side table". Omit for MAP MODE: Muse Spark discovers the objects')
    p.add_argument("--coin", default=None, help="a known reference coin (see scale_cal.COINS), e.g. saudi-25-halalas")
    p.add_argument("--ref", action="append", help='scale from an object of known height, e.g. --ref "striped mug=70mm" (repeatable; a coin or card, if visible, wins)')
    p.add_argument("--card", action="store_true", help="a credit card (85.6 x 54 mm) lies on the surface: use it as a scale reference (approximate, untested on real footage)")
    p.add_argument("--fresh", action="store_true", help="map mode: ignore the cache (masks, meshes) and recompute")
    p.add_argument("--no-spark-verify", action="store_true", help="map mode: skip the Muse Spark correction rounds")
    p.add_argument("--mesh-backend", choices=["modal", "trellis", "import"], default="modal")
    p.add_argument("--mesh-file", action="append", help="with import: <object-id>=<glb path> (repeatable)")
    p.add_argument("--frame", default=None, help="force the shared frame, e.g. sharp_11")
    p.add_argument("--fps", type=float, default=2.0)
    p.add_argument("--keep-frames", type=int, default=12)
    p.add_argument("--ref-concept", default="coin")
    p.add_argument("--ref-diameter-cm", type=float, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.coin and args.ref_diameter_cm is None:
        args.ref_diameter_cm = scale_cal.coin_diameter_cm(args.coin)
    try:
        if args.objects is None:
            from . import mapmode

            report = mapmode.run(args)
            report.setdefault("scene", str(Path(args.out) / "scene.json"))
        else:
            report = run(args)
    except (SceneError, *( [__import__('worldkit.mapmode', fromlist=['MapError']).MapError] ), segment.SegmentationError, frames.FrameExtractionError, reconstruct.ReconstructionError, tp.TextureProjectionError) as exc:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "scene_report.json").write_text(json.dumps({"error": str(exc), "error_type": type(exc).__name__}, indent=2))
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    (Path(args.out) / "scene_report.json").write_text(json.dumps(report, indent=2))
    print(f"Done. Scene: {report['scene']}")
    print(f"View it:  python viewer/server.py --open {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
