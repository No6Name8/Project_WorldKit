"""Map mode: a video of a desk with several objects and a coin -> one scene at real scale, verified against the frames.

    python -m worldkit.scene <video> --out <dir> [--coin saudi-25-halalas | --ref-diameter-cm D]

Stages (each timed in scene_report.json; any single object failing is skipped and listed, it never kills the scene):
  discover  Muse Spark lists the physical objects (not the coin, hands, walls, background, or the surface itself) and names the surface
  track     SAM 3.1 segments every object in every kept frame; one track = one object (tracks that cover the same pixels are merged)
  build     best view per object (large, sharp, un-clipped, unoccluded) -> SAM 3D Objects on Modal, all objects in parallel; mesh QC
  place     real scale and position from the coin: the coin's ellipse gives the desk plane's scale and elevation in a reference frame; each
            object's silhouette pose fit gives its size (cm per mesh unit), yaw and the pixel where its base touches the desk, which is
            intersected with the desk plane; footprints that overlap are pushed apart. Objects rest on the plane by construction.
  verify    render the scene from 3 of the original camera viewpoints, per-object silhouette IoU against the SAM masks, Muse Spark names
            concrete errors, corrections are applied only where IoU improves (max 2 rounds), side-by-side images saved
  export    per-object print-ready STL (mm, via printprep) and one watertight diorama on a base plate scaled to a 256 mm bed

Layout method used: weak-perspective pose fits of one reference frame (no VGGT). Units in the scene are centimetres on the desk plane.
"""

from __future__ import annotations

import io
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

from . import appearance, frames as frames_mod, printprep, reconstruct, scale_cal, segment
from . import scene as sc
from . import texture_project as tp
from .meta_api import MetaAPIUnavailable, ask_muse_spark_with_usage

BED_MM = 256.0
MIN_WALL_MM = 1.2


class MapError(RuntimeError):
    pass


# --- 1. discover ------------------------------------------------------------------------------------------------------------

DISCOVER_PROMPT = """These {n} photos come from one phone video of objects resting on a desk or table.
List every distinct PHYSICAL OBJECT sitting on the surface that someone might want to 3D-reconstruct or 3D-print.
Do NOT list: coins (a coin is the scale reference), hands or people, walls, floor, background furniture, cables, shadows, or the surface itself.
Give each object a short noun phrase (1-3 words) that an image segmenter can find, and make the phrases distinct (add a colour if two objects are similar).
Also name the surface the objects rest on.
Reply with ONLY one JSON object: {{"objects": [{{"name": "white mug", "description": "short"}}], "surface": "desk"}}"""


def discover_objects(image_paths: list[Path], ask=ask_muse_spark_with_usage) -> dict:
    text, usage = ask(list(image_paths), DISCOVER_PROMPT.format(n=len(image_paths)))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise MapError(f"Muse Spark did not return a JSON object list: {text[:200]}")
    d = json.loads(m.group(0))
    names, seen = [], set()
    for o in d.get("objects", []):
        n = str(o.get("name", "")).strip()
        low = n.lower()
        if not n or low in seen or re.search(r"\b(coin|hand|wall|floor|background|person)\b", low):
            continue
        seen.add(low)
        names.append({"name": n, "description": str(o.get("description", ""))})
    return {"objects": names, "surface": str(d.get("surface") or "desk"), "usage": usage}


# --- 2. track ---------------------------------------------------------------------------------------------------------------

@dataclass
class Track:
    name: str
    masks: dict = field(default_factory=dict)  # frame stem -> full-frame bool mask


def build_tracks(frame_list, names: list[str], segment_fn=segment.segment_concept) -> tuple[list[Track], dict]:
    tracks, errors = [], {}
    for name in names:
        t = Track(name)
        for fr in frame_list:
            try:
                r = segment_fn(fr.path, name)
                if getattr(r, "used_fallback", False):
                    continue
                m = segment.full_frame_mask(fr.path, r)
            except segment.SegmentationError as exc:
                errors.setdefault(name, []).append(f"{Path(fr.path).stem}: {str(exc)[:80]}")
                continue
            if m.sum() > 150:
                t.masks[Path(fr.path).stem] = m
        tracks.append(t)
    return tracks, errors


def dedup_tracks(tracks: list[Track], iou_thresh: float = 0.5, min_frames: int = 2) -> tuple[list[Track], list[dict]]:
    """One track = one object: tracks whose masks coincide in the frames they share are the same object (keep the better-observed)."""
    dropped = []
    keep = [t for t in tracks if len(t.masks) >= min_frames]
    for t in tracks:
        if len(t.masks) < min_frames:
            dropped.append({"name": t.name, "reason": f"seen in only {len(t.masks)} frame(s) (< {min_frames})"})
    keep.sort(key=lambda t: -sum(m.sum() for m in t.masks.values()))
    out = []
    for t in keep:
        dup = None
        for k in out:
            shared = set(t.masks) & set(k.masks)
            if shared and np.mean([tp.iou(t.masks[s], k.masks[s]) for s in shared]) >= iou_thresh:
                dup = k
                break
        if dup is None:
            out.append(t)
        else:
            dropped.append({"name": t.name, "reason": f"same object as '{dup.name}' (mask IoU >= {iou_thresh} in shared frames)"})
    return out, dropped


def best_view(track: Track, others: list[Track], sharpness: dict[str, float]) -> tuple[str, dict]:
    """Largest, sharpest, least clipped and least occluded mask of one track."""
    best, info = None, {}
    for stem, m in track.masks.items():
        ys, xs = np.nonzero(m)
        h, w = m.shape
        clipped = xs.min() <= 1 or ys.min() <= 1 or xs.max() >= w - 2 or ys.max() >= h - 2
        other = np.zeros_like(m)
        for o in others:
            if stem in o.masks:
                other |= o.masks[stem]
        import cv2

        ring = cv2.dilate(m.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ~m
        occl = float((ring & other).sum() / max(ring.sum(), 1))
        score = float(m.sum()) * float(sharpness.get(stem, 1.0)) * (0.3 if clipped else 1.0) * (1.0 - 0.8 * occl)
        if best is None or score > best[0]:
            best, info = (score, stem), {"area_px": int(m.sum()), "sharpness": round(float(sharpness.get(stem, 0)), 1), "clipped": bool(clipped), "occlusion": round(occl, 3)}
    return best[1], info


# --- 3. build ---------------------------------------------------------------------------------------------------------------

def object_qc(glb: bytes) -> tuple[bool, list[str], dict]:
    """Does this mesh look like a usable object? Watertight, not tiny, not a sliver, one dominant piece."""
    reasons = []
    try:
        v, f = sc._load_mesh(glb)
    except Exception as exc:  # noqa: BLE001
        return False, [f"unreadable mesh: {exc}"], {}
    m = trimesh.Trimesh(v, f, process=False)
    stats = {"faces": int(len(f)), "extents": [round(float(x), 3) for x in m.extents]}
    mm = trimesh.Trimesh(m.vertices, m.faces, process=True)
    mm.merge_vertices()
    if len(f) < 300:
        reasons.append(f"only {len(f)} faces")
    if not mm.is_watertight:
        reasons.append("not watertight")
    elif mm.volume <= 0:
        reasons.append("non-positive volume")
    ext = np.sort(m.extents)
    if ext[0] / max(ext[2], 1e-9) < 0.02:
        reasons.append("degenerate (a flat sliver)")
    comps = mm.split(only_watertight=False)
    if len(comps) > 1:
        big = max(len(c.faces) for c in comps) / max(len(mm.faces), 1)
        stats["largest_component_share"] = round(big, 3)
        if big < 0.85:
            reasons.append(f"fragmented ({len(comps)} pieces, largest has {big:.0%} of the faces)")
    return not reasons, reasons, stats


def crop_rgba(frame_rgb: np.ndarray, visible_mask: np.ndarray) -> bytes:
    rgba = np.dstack([frame_rgb, (visible_mask * 255).astype(np.uint8)])
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return reconstruct.crop_to_alpha_bbox(buf.getvalue())


# --- 4. place ---------------------------------------------------------------------------------------------------------------

def yaw_deg_of(a: float, b: float) -> float:
    return float((a - b + 180) % 360 - 180)


def circ_mean_deg(angles: list[float]) -> float:
    a = np.radians(angles)
    return float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())))


@dataclass
class Placed:
    id: str
    name: str
    glb: bytes
    verts: np.ndarray  # mesh units, Y up (finalised GLB)
    faces: np.ndarray
    transform: dict
    best_frame: str
    fit_az: dict = field(default_factory=dict)  # frame stem -> silhouette-fit azimuth
    size_cm: list | None = None

    def world(self, t: dict | None = None) -> np.ndarray:
        return sc.transform_vertices(self.verts, t or self.transform)


def coin_in_frame(path: Path, concept: str = "coin"):
    try:
        r = segment.segment_concept(path, concept)
        if getattr(r, "used_fallback", False):
            return None
        mask = segment.full_frame_mask(path, r)
        return scale_cal.fit_disk(mask)
    except (segment.SegmentationError, scale_cal.ScaleCalibrationError):
        return None


def desk_camera(ref: scale_cal.DiskObservation, coin_diameter_cm: float, coin_center_px, el_override=None) -> tp.CameraPose:
    """Camera of a frame in desk coordinates (cm, Y up, world origin = the coin's centre on the desk plane, camera azimuth 0)."""
    el = el_override if el_override is not None else float(np.clip(ref.elevation_deg, 12.0, 80.0))
    return tp.CameraPose(0.0, el, ref.major_px / coin_diameter_cm, float(coin_center_px[0]), float(coin_center_px[1]))


def coin_center(mask_path: Path, concept: str = "coin"):
    r = segment.segment_concept(mask_path, concept)
    m = segment.full_frame_mask(mask_path, r)
    ys, xs = np.nonzero(m)
    return np.array([xs.mean(), ys.mean()])


def resolve_overlaps(items: list[Placed], gap_cm: float = 0.4, iters: int = 20) -> list[dict]:
    """Objects stand on the desk plane by construction; push footprints apart (smallest axis shift) until no AABB overlap remains."""
    moved = []
    for _ in range(iters):
        changed = False
        boxes = [(it, it.world()) for it in items]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (a, va), (b, vb) = boxes[i], boxes[j]
                lo = np.maximum(va.min(0), vb.min(0))
                hi = np.minimum(va.max(0), vb.max(0))
                pen = hi - lo
                if pen[0] > 0 and pen[2] > 0 and pen[1] > 0.05:  # overlap in x and z while sharing height
                    ax = 0 if pen[0] < pen[2] else 2
                    ca = (va.min(0)[ax] + va.max(0)[ax]) / 2
                    cb = (vb.min(0)[ax] + vb.max(0)[ax]) / 2
                    sgn = 1.0 if cb >= ca else -1.0
                    shift = (pen[ax] + gap_cm) / 2
                    for it, s in ((a, -sgn), (b, sgn)):
                        pos = list(it.transform["position"])
                        pos[ax] += s * shift
                        it.transform = {**it.transform, "position": pos}
                    moved += [{"a": a.id, "b": b.id, "axis": "xz"[ax // 2], "shift_cm": round(shift * 2, 2)}]
                    changed = True
                    boxes = [(it, it.world()) for it in items]
        if not changed:
            break
    return moved


# --- 5. verify --------------------------------------------------------------------------------------------------------------

def object_ious(items: list[Placed], cams: dict, masks: dict, size_of: dict, overrides: dict | None = None) -> dict:
    """{object id: {frame: IoU}} between the rendered silhouette and the SAM mask, same canvas."""
    out = {}
    for it in items:
        t = (overrides or {}).get(it.id, it.transform)
        w = it.world(t)
        out[it.id] = {}
        for stem, pose in cams.items():
            m = masks[it.id].get(stem)
            if m is None:
                continue
            sil = tp.silhouette(w, it.faces, pose, size_of[stem])
            out[it.id][stem] = round(float(tp.iou(sil, m)), 3)
    return out


def mean_iou(ious: dict) -> float:
    vals = [v for per in ious.values() for v in per.values()]
    return float(np.mean(vals)) if vals else float("nan")


PALETTE = [(230, 60, 60), (60, 160, 230), (80, 200, 100), (240, 180, 40), (180, 90, 220), (60, 210, 200)]


def render_overlay(frame_rgb, items: list[Placed], pose, size, label: str, overrides=None) -> Image.Image:
    img = frame_rgb.astype(float).copy()
    draw_names = []
    for k, it in enumerate(items):
        sil = tp.silhouette(it.world((overrides or {}).get(it.id, it.transform)), it.faces, pose, size)
        col = np.array(PALETTE[k % len(PALETTE)], float)
        img[sil] = 0.45 * img[sil] + 0.55 * col
        ys, xs = np.nonzero(sil)
        if len(xs):
            draw_names.append((it.name, (int(xs.mean()), int(ys.min()) - 10), tuple(int(c) for c in col)))
    im = Image.fromarray(img.astype(np.uint8))
    d = ImageDraw.Draw(im)
    for name, xy, col in draw_names:
        d.text((xy[0] - 20, max(xy[1], 2)), name, fill=col)
    d.text((6, 6), label, fill=(255, 255, 255))
    return im


CORRECT_PROMPT = """Image 1 is a REAL video frame; image 2 is a computed render of the reconstructed scene from the same camera (coloured silhouettes, one colour per object, labelled).
Objects: {objs}. Measured silhouette overlap with the real objects (IoU, 1.0 = perfect): {ious}.
In this view +x is image-right and +z is toward the camera (down the image on the desk); units are centimetres on the desk.
Name CONCRETE errors you can see (an object too far left/right/forward/back, too small/large, rotated) and give a correction for each.
Reply with ONLY JSON: {{"corrections": [{{"object": "name", "dx_cm": 0.0, "dz_cm": 0.0, "scale": 1.0, "yaw_deg": 0.0, "reason": "mug 2 cm too far left"}}]}}
Use an empty list if nothing is visibly wrong. Keep |dx|,|dz| <= 6 cm, 0.8 <= scale <= 1.25, |yaw| <= 40."""


def parse_corrections(text: str, names: dict[str, str]) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0)).get("corrections", [])
    except json.JSONDecodeError:
        return []
    out = []
    lower = {n.lower(): i for i, n in names.items()}
    for c in data:
        oid = lower.get(str(c.get("object", "")).lower())
        if oid is None:
            continue
        out.append({"id": oid, "dx_cm": float(np.clip(c.get("dx_cm", 0) or 0, -6, 6)), "dz_cm": float(np.clip(c.get("dz_cm", 0) or 0, -6, 6)),
                    "scale": float(np.clip(c.get("scale", 1) or 1, 0.8, 1.25)), "yaw_deg": float(np.clip(c.get("yaw_deg", 0) or 0, -40, 40)),
                    "reason": str(c.get("reason", ""))[:140]})
    return out


def apply_correction(t: dict, c: dict) -> dict:
    pos = list(t["position"])
    pos[0] += c["dx_cm"]
    pos[2] += c["dz_cm"]
    s = t["scale"][0] * c["scale"]
    return {"position": pos, "rotation_deg": [0.0, t["rotation_deg"][1] + c["yaw_deg"], 0.0], "scale": [s, s, s]}


# --- 6. export --------------------------------------------------------------------------------------------------------------

def to_print_space(world_cm_yup: np.ndarray) -> np.ndarray:
    """Desk coordinates (cm, Y up) -> print coordinates (mm, Z up)."""
    return np.stack([world_cm_yup[:, 0], -world_cm_yup[:, 2], world_cm_yup[:, 1]], axis=1) * 10.0


def build_diorama(items: list[Placed], desk_margin_mm: float = 6.0, plate_mm: float = 2.5) -> tuple[trimesh.Trimesh, dict]:
    """All objects unioned onto a base plate, scaled (never up) to fit a 256 mm bed. Returns (mesh in mm, Z up, base on z=0; info)."""
    parts, repairs = [], []
    for it in items:
        m = trimesh.Trimesh(to_print_space(it.world()), it.faces, process=False)  # (x, -z, y) is a proper rotation: winding is kept
        if not m.is_watertight:  # raw generated meshes are often open shells: same repair ladder as Object-mode print prep
            m, how = printprep.make_watertight(m)
            repairs.append(f"{it.id}: {how}")
        parts.append(m)
    lo = np.min([p.bounds[0] for p in parts], axis=0)
    hi = np.max([p.bounds[1] for p in parts], axis=0)
    zbase = lo[2]
    plate = trimesh.creation.box(extents=[hi[0] - lo[0] + 2 * desk_margin_mm, hi[1] - lo[1] + 2 * desk_margin_mm, plate_mm + 0.5])
    plate.apply_translation([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, zbase - plate_mm / 2 + 0.25])  # overlaps the objects' feet by 0.5 mm so the union fuses
    try:
        fused = trimesh.boolean.union(parts + [plate], engine="manifold")
    except Exception as exc:  # noqa: BLE001
        raise MapError(f"diorama union failed: {exc}") from exc
    fused.apply_translation([0, 0, -fused.bounds[0][2]])
    ratio = min(1.0, BED_MM / float(fused.extents.max()))
    fused.apply_scale(ratio)
    info = {"scale_ratio": round(ratio, 4), "scale_text": "1:1" if ratio >= 0.999 else f"1:{1 / ratio:.1f}", "size_mm": [round(float(x), 1) for x in fused.extents],
            "bed_mm": [BED_MM] * 3, "watertight": bool(fused.is_watertight), "objects": [it.id for it in items], "repairs": repairs, "warnings": []}
    from .edit_tools import _sample_thickness

    thin = _sample_thickness(fused)
    frac = float((thin < MIN_WALL_MM).mean()) if len(thin) else 0.0
    info["thin_feature_fraction"] = round(frac, 3)
    if frac > 0.02:
        info["warnings"].append(f"{frac:.0%} of sampled surface points are thinner than {MIN_WALL_MM} mm at this scale: thin walls/handles may not print")
    if ratio < 0.999:
        info["warnings"].append(f"scaled to {info['scale_text']} to fit the 256 mm bed: real sizes are {1 / ratio:.1f}x larger")
    if not fused.is_watertight:
        info["warnings"].append("the union is not watertight")
    return fused, info


def desk_glb(items: list[Placed], margin_cm: float = 6.0, thick_cm: float = 1.0) -> tuple[bytes, dict]:
    lo = np.min([it.world().min(0) for it in items], axis=0)
    hi = np.max([it.world().max(0) for it in items], axis=0)
    w, d = max(hi[0] - lo[0] + 2 * margin_cm, 30.0), max(hi[2] - lo[2] + 2 * margin_cm, 22.0)
    box = trimesh.creation.box(extents=[w, thick_cm, d])
    box.apply_translation([(lo[0] + hi[0]) / 2, -thick_cm / 2, (lo[2] + hi[2]) / 2])  # top face at y = 0
    box.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(baseColorFactor=[168, 140, 108, 255], metallicFactor=0.0, roughnessFactor=0.8))
    return trimesh.Scene(box).export(file_type="glb"), {"size_cm": [round(float(w), 1), thick_cm, round(float(d), 1)]}


# --- orchestration ----------------------------------------------------------------------------------------------------------

def modal_metered() -> float | None:
    import subprocess

    try:
        out = subprocess.run(["modal", "billing", "summary"], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"Metered Cost:\s*\|\s*([0-9.]+)", out)
        return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _unique_ids(names: list[str]) -> dict[str, str]:
    ids, used = {}, set()
    for n in names:
        base = sc._slug(n)
        i, k = base, 2
        while i in used:
            i, k = f"{base}_{k}", k + 1
        used.add(i)
        ids[n] = i
    return ids


def run(args) -> dict:
    from . import object as obj_cli

    t_all = time.time()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"mode": "map", "warnings": [], "timings_sec": {}, "spark_calls": 0,
                 "layout_method": "weak-perspective pose fits in one reference frame (the coin gives the desk plane's scale and elevation); VGGT not used"}
    T = rep["timings_sec"]

    def timed(name, fn, *a, **k):
        t0 = time.time()
        r = fn(*a, **k)
        T[name] = round(time.time() - t0, 1)
        return r

    sharp = timed("frames", frames_mod.extract_sharp_frames, args.video, out, fps=args.fps, keep_n=args.keep_frames)
    sharp_by_stem = {Path(f.path).stem: f for f in sharp}
    sharp_map = {s: f.sharpness for s, f in sharp_by_stem.items()}
    rgb = {s: np.array(Image.open(f.path).convert("RGB")) for s, f in sharp_by_stem.items()}
    size_of = {s: (a.shape[1], a.shape[0]) for s, a in rgb.items()}

    # 1. discover
    picks = [sharp[i] for i in np.linspace(0, len(sharp) - 1, min(6, len(sharp))).round().astype(int)]
    if args.objects:
        found = {"objects": [{"name": n, "description": "given on the command line"} for n in args.objects], "surface": "desk", "usage": None}
    else:
        found = timed("discover", discover_objects, [f.path for f in picks])
        rep["spark_calls"] += 1
    names = [o["name"] for o in found["objects"]]
    rep["discovered"] = {"objects": names, "surface": found["surface"], "spark_tokens": found.get("usage")}
    if not names:
        raise MapError("Muse Spark found no objects on the surface")

    # 2. track
    tracks, track_errors = timed("track", build_tracks, sharp, names)
    tracks, dropped = dedup_tracks(tracks)
    rep["tracks"] = {t.name: len(t.masks) for t in tracks}
    rep["skipped"] = list(dropped)
    if not tracks:
        raise MapError(f"SAM 3.1 could not track any of {names}")

    # 3. build
    ids = _unique_ids([t.name for t in tracks])
    inputs, meta = [], []
    for t in tracks:
        others = [o for o in tracks if o is not t]
        stem, info = best_view(t, others, sharp_map)
        vis = t.masks[stem].copy()
        for o in others:
            if stem in o.masks:
                vis &= ~o.masks[stem]
        odir = out / "objects" / ids[t.name]
        odir.mkdir(parents=True, exist_ok=True)
        crop = crop_rgba(rgb[stem], vis)
        (odir / "object_rgba.png").write_bytes(crop)
        inputs.append(crop)
        meta.append({"track": t, "id": ids[t.name], "stem": stem, "view": info, "odir": odir})
    rep["build"] = {"backend": args.mesh_backend, "parallel_containers_max": 3 if args.mesh_backend == "modal" else 1}
    metered0 = modal_metered() if args.mesh_backend == "modal" else None
    t0 = time.time()
    if args.mesh_backend == "modal":
        try:
            from modal_app.sam3d import run_remote_many

            results = run_remote_many(inputs)
        except Exception as exc:  # noqa: BLE001
            raise MapError(f"SAM 3D on Modal failed for the whole batch: {type(exc).__name__}: {str(exc)[:200]}") from exc
    elif args.mesh_backend == "import":
        mf = dict(kv.split("=", 1) for kv in (args.mesh_file or []))
        results = [reconstruct.load_external_mesh(Path(mf[m["id"]])) if m["id"] in mf else RuntimeError(f"no --mesh-file {m['id']}=...") for m in meta]
    else:
        results = []
        for crop in inputs:
            try:
                results.append(reconstruct.reconstruct_via_trellis_space(crop, space_id=reconstruct.DEFAULT_TRELLIS_SPACE_ID))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)
    T["build_wall"] = round(time.time() - t0, 1)
    metered1 = modal_metered() if args.mesh_backend == "modal" else None
    rep["build"]["modal_wall_seconds"] = T["build_wall"]
    rep["build"]["modal_metered_cost_usd_delta"] = None if (metered0 is None or metered1 is None) else round(metered1 - metered0, 3)
    items: list[Placed] = []
    model_info = dict(obj_cli.RECONSTRUCTION_MODELS["modal" if args.mesh_backend == "modal" else args.mesh_backend])
    for m, res in zip(meta, results):
        if isinstance(res, BaseException):
            rep["skipped"].append({"name": m["track"].name, "reason": f"reconstruction failed: {type(res).__name__}: {str(res)[:160]}"})
            continue
        glb = res
        if appearance.glb_is_textured(glb):
            glb, _ = appearance.finalize_textured_glb(glb)
        ok, why, stats = object_qc(glb)
        m["qc"] = {"ok": ok, "reasons": why, **stats}
        if not ok:
            rep["skipped"].append({"name": m["track"].name, "reason": "mesh QC failed: " + "; ".join(why)})
            continue
        v, f = sc._load_mesh(glb)
        (m["odir"] / "model.glb").write_bytes(glb)
        items.append(Placed(m["id"], m["track"].name, glb, v, f, {"position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}, m["stem"]))
    rep["built"] = [{"id": it.id, "name": it.name} for it in items]
    if not items:
        raise MapError("no object survived reconstruction and mesh QC")
    track_of = {ids[t.name]: t for t in tracks}

    # 4. place
    diam = args.ref_diameter_cm
    coin_obs = {}
    t0 = time.time()
    if diam:
        for s, f in sharp_by_stem.items():
            d = coin_in_frame(Path(f.path), args.ref_concept)
            if d is not None:
                coin_obs[s] = d
    rep["coin"] = {"diameter_cm": diam, "frames_with_coin": sorted(coin_obs)}
    scale_known = bool(coin_obs)
    if not scale_known:
        rep["warnings"].append("no coin found (or no --coin/--ref-diameter-cm): the scene is NOT at real scale (placeholder 20 px/cm)")
    vis_counts = {s: sum(1 for it in items if s in track_of[it.id].masks) for s in sharp_by_stem}
    pool = list(coin_obs) if scale_known else list(sharp_by_stem)
    ref = max(pool, key=lambda s: (vis_counts[s], coin_obs[s].major_px if scale_known else sharp_map[s]))
    rep["reference_frame"] = ref

    def camera_for(stem):
        if scale_known:
            return desk_camera(coin_obs[stem], diam, coin_center(Path(sharp_by_stem[stem].path), args.ref_concept))
        return tp.CameraPose(0.0, 40.0, 20.0, size_of[stem][0] / 2, size_of[stem][1] * 0.7)

    cam_ref = camera_for(ref)
    fits_ref = {}
    for it in items:
        mask = track_of[it.id].masks.get(ref)
        if mask is None:
            rep["skipped"].append({"name": it.name, "reason": f"not visible in the reference frame {ref}"})
            continue
        try:
            fits_ref[it.id] = tp.fit_pose(it.verts, it.faces, mask)
        except tp.TextureProjectionError as exc:
            rep["skipped"].append({"name": it.name, "reason": f"pose fit failed in the reference frame: {exc}"})
    items = [it for it in items if it.id in fits_ref]
    if not items:
        raise MapError("no object could be placed in the reference frame")
    for it in items:
        fit = fits_ref[it.id]
        s = float(fit.pose.scale / cam_ref.scale)  # cm per mesh unit
        yaw = float(fit.pose.az_deg)  # the reference camera has azimuth 0
        bc = sc.base_center(it.verts)
        px = fit.pose.project(bc[None, :])[0][0]
        target = sc.ray_to_plane(cam_ref, px, 0.0)
        pos = target - s * (sc.yaw_matrix(yaw) @ bc)
        it.transform = {"position": [float(x) for x in pos], "rotation_deg": [0.0, yaw, 0.0], "scale": [s, s, s]}
        it.fit_az[ref] = yaw
    rep["overlap_resolution"] = resolve_overlaps(items)
    T["place"] = round(time.time() - t0, 1)

    # 5. verify
    t0 = time.time()
    others = sorted((x for x in pool if x != ref), key=lambda x: -(vis_counts[x] * 1000 + sharp_map[x]))
    verify_frames = [ref] + others[:2]
    cams = {ref: cam_ref}
    for s in verify_frames[1:]:
        base_cam = camera_for(s)
        diffs = []
        for it in items:
            m = track_of[it.id].masks.get(s)
            if m is None:
                continue
            try:
                diffs.append(yaw_deg_of(tp.fit_pose(it.verts, it.faces, m).pose.az_deg, it.fit_az[ref]))
            except tp.TextureProjectionError:
                continue
        cams[s] = tp.CameraPose(circ_mean_deg(diffs) if diffs else 0.0, base_cam.el_deg, base_cam.scale, base_cam.tx, base_cam.ty)
    masks = {it.id: {s: track_of[it.id].masks[s] for s in verify_frames if s in track_of[it.id].masks} for it in items}
    ious0 = object_ious(items, cams, masks, size_of)
    before_t = {it.id: dict(it.transform) for it in items}
    vdir = out / "verify"
    vdir.mkdir(exist_ok=True)
    rounds, ious_cur = [], ious0
    names_of = {it.id: it.name for it in items}
    for rnd in range(1, 3):
        worst = min(verify_frames, key=lambda s: np.nanmean([ious_cur[i].get(s, np.nan) for i in ious_cur]))
        p1, p2 = vdir / f"round{rnd}_real.png", vdir / f"round{rnd}_render.png"
        Image.fromarray(rgb[worst]).save(p1)
        render_overlay(rgb[worst], items, cams[worst], size_of[worst], f"render before round {rnd}").save(p2)
        if getattr(args, "no_spark_verify", False):
            break
        try:
            text, usage = ask_muse_spark_with_usage([p1, p2], CORRECT_PROMPT.format(objs=", ".join(names_of.values()), ious=json.dumps({names_of[i]: v for i, v in ious_cur.items()})))
            rep["spark_calls"] += 1
        except MetaAPIUnavailable as exc:
            rep["warnings"].append(f"Muse Spark unavailable for verification: {exc}")
            break
        corr = parse_corrections(text, names_of)
        accepted, rejected = [], []
        for c in corr:
            it = next(x for x in items if x.id == c["id"])
            new_t = apply_correction(it.transform, c)
            trial = object_ious([it], cams, masks, size_of, {it.id: new_t})
            old = mean_iou({it.id: ious_cur[it.id]})
            if mean_iou(trial) > old + 0.005:
                it.transform = new_t
                accepted.append({**c, "iou_before": round(old, 3), "iou_after": round(mean_iou(trial), 3)})
                ious_cur = {**ious_cur, it.id: trial[it.id]}
            else:
                rejected.append({**c, "reason": "IoU did not improve"})
        rounds.append({"round": rnd, "worst_view": worst, "spark_corrections": corr, "applied": accepted, "rejected": rejected, "usage": usage})
        if not accepted:
            break
    ious1 = object_ious(items, cams, masks, size_of)
    for i, s in enumerate(verify_frames):
        real = Image.fromarray(rgb[s])
        b0 = np.nanmean([ious0[k].get(s, np.nan) for k in ious0])
        b1 = np.nanmean([ious1[k].get(s, np.nan) for k in ious1])
        bimg = render_overlay(rgb[s], items, cams[s], size_of[s], f"BEFORE verify  mean IoU {b0:.2f}", overrides=before_t)
        aimg = render_overlay(rgb[s], items, cams[s], size_of[s], f"AFTER verify  mean IoU {b1:.2f}")
        comp = Image.new("RGB", (real.width * 3 + 16, real.height), (255, 255, 255))
        comp.paste(real, (0, 0))
        comp.paste(bimg, (real.width + 8, 0))
        comp.paste(aimg, (2 * real.width + 16, 0))
        ImageDraw.Draw(comp).text((6, 6), "REAL", fill=(255, 255, 255))
        comp.save(vdir / f"view{i}_{s}.png")
    T["verify"] = round(time.time() - t0, 1)
    rep["verify"] = {"frames": verify_frames, "iou_before": ious0, "iou_after": ious1, "mean_iou_before": round(mean_iou(ious0), 3), "mean_iou_after": round(mean_iou(ious1), 3),
                     "rounds": rounds, "images": sorted(p.relative_to(out).as_posix() for p in vdir.glob("view*.png")),
                     "note": "IoU compares the rendered silhouette (weak-perspective camera from the coin) with the SAM 3.1 mask; the camera model is approximate"}

    # 6. export
    t0 = time.time()
    desk_bytes, desk_info = desk_glb(items)
    ddir = out / "objects" / "desk"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "model.glb").write_bytes(desk_bytes)
    plane = {"name": "procedural plane", "vendor": "WorldKit", "is_meta_model": False}
    (ddir / "report.json").write_text(json.dumps({"object_name": found["surface"], "reconstruction_model": plane, "note": "the support surface is a flat slab, not reconstructed", "size_cm": desk_info["size_cm"]}, indent=1))
    manifest_objects = [{"id": "desk", "name": found["surface"], "mesh": "objects/desk/model.glb", "report": "objects/desk/report.json", "role": "parent", "reconstruction_model": plane,
                         "transform": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}, "size_cm": desk_info["size_cm"], "printable": False}]
    meta_of = {m["id"]: m for m in meta}
    for it in items:
        w = it.world()
        it.size_cm = [round(float(x), 2) for x in (w.max(0) - w.min(0))]
        odir = out / "objects" / it.id
        prep, prep_err = None, None
        try:
            prep = printprep.prepare_for_print(odir / "model.glb", odir / "model_print.stl", estimated_height_cm=it.size_cm[1] if scale_known else None, estimated_height_source="reference_calibrated")
        except Exception as exc:  # noqa: BLE001
            prep_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            rep["warnings"].append(f"print prep failed for {it.name}: {prep_err}")
        orep = {"object_name": it.name, "reconstruction_model": model_info, "best_view": meta_of[it.id]["view"], "best_frame": it.best_frame, "mesh_qc": meta_of[it.id].get("qc"),
                "size_cm": it.size_cm, "print_ready": bool(prep.print_ready) if prep else False, "print_prep": prep.to_dict() if prep else None, "print_prep_error": prep_err,
                "measurement": {"height_cm": it.size_cm[1], "method": "coin (scene reference frame)" if scale_known else "placeholder", "uncertainty_note": "scene-level estimate (coin edge, weak perspective); not a measurement of this object"}}
        (odir / "report.json").write_text(json.dumps(orep, indent=1, default=float))
        manifest_objects.append({"id": it.id, "name": it.name, "mesh": f"objects/{it.id}/model.glb", "report": f"objects/{it.id}/report.json", "role": "child", "reconstruction_model": model_info,
                                 "transform": it.transform, "size_cm": it.size_cm, "stl": f"objects/{it.id}/model_print.stl" if prep else None, "printable": bool(prep)})
    dio, dio_info = build_diorama(items)
    dio.export(out / "diorama.stl")
    (out / "diorama.json").write_text(json.dumps(dio_info, indent=1))
    T["export"] = round(time.time() - t0, 1)
    rep["diorama"] = dio_info
    manifest = {"worldkit_scene": sc.SCENE_VERSION, "mode": "map", "source_video": Path(args.video).name, "frame": ref,
                "units": "centimetres on the desk plane (Y up, origin at the coin)" if scale_known else "arbitrary (no coin found)", "cm_per_scene_unit": 1.0 if scale_known else None,
                "scale_calibration": {"reference": args.ref_concept, "reference_diameter_cm": diam, "relative_spread_mad": None, "frames_used": len(coin_obs)} if scale_known else None,
                "objects": manifest_objects, "layout_method": rep["layout_method"],
                "verify": {"mean_iou_before": rep["verify"]["mean_iou_before"], "mean_iou_after": rep["verify"]["mean_iou_after"], "images": rep["verify"]["images"]},
                "diorama": {"stl": "diorama.stl", **dio_info}, "skipped": rep["skipped"]}
    (out / "scene.json").write_text(json.dumps(manifest, indent=1, default=float))
    rep["total_seconds"] = round(time.time() - t_all, 1)
    (out / "scene_report.json").write_text(json.dumps(rep, indent=1, default=float))
    return rep
