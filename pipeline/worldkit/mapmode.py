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

import hashlib
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

from . import appearance, frames as frames_mod, printprep, reconstruct, refscale, scale_cal, segment
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
List each object ONCE, as a whole: a cap, lid or label belongs to its bottle or container, and a holder or stand belongs to the candle, lamp or ornament it holds (name the whole thing, e.g. "candle in red holder"); never list parts separately.
Do NOT list: coins or cards used as scale references, hands or people, walls, floor, background furniture, cables, shadows, or the surface itself.
Give each object a short noun phrase (1-3 words) that an image segmenter can find, and make the phrases distinct (add a colour if two objects are similar).
Also name the surface the objects rest on.
For each object also give "parts": segmenter phrases for pieces that belong to it and should be merged into it (e.g. the red holder of a candle), and "aliases": up to 2 alternative phrases for the same whole object (e.g. "incense burner" for "silver ornament").
Reply with ONLY one JSON object: {{"objects": [{{"name": "white candle", "parts": ["red candle holder"], "aliases": ["candle"], "description": "short"}}], "surface": "desk"}}"""


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
        if not n or low in seen or re.search(r"\b(coin|hand|wall|floor|background|person|sofa|couch|carpet|rug|cushion)\b", low):
            continue
        seen.add(low)
        names.append({"name": n, "description": str(o.get("description", "")), "parts": [str(x) for x in (o.get("parts") or []) if str(x).strip()][:3],
                      "aliases": [str(x) for x in (o.get("aliases") or []) if str(x).strip()][:2]})
    part_names = {x.lower() for o in names for x in o["parts"]}
    names = [o for o in names if o["name"].lower() not in part_names]
    return {"objects": names, "surface": str(d.get("surface") or "desk"), "usage": usage}


# --- 2. track ---------------------------------------------------------------------------------------------------------------

@dataclass
class Track:
    name: str
    masks: dict = field(default_factory=dict)  # frame stem -> full-frame bool mask


def cached_mask(cache: Path | None, path, concept: str, segment_fn=segment.segment_concept, fresh: bool = False):
    """Full-frame SAM mask of ``concept`` in ``path`` (None if not found), cached on disk by hash of the frame and the concept."""
    f = None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        f = cache / (hashlib.sha1(Path(path).read_bytes() + concept.encode()).hexdigest()[:20] + ".npz")
        if f.is_file() and not fresh:
            z = np.load(f)
            return z["m"] if bool(z["ok"]) else None
    m = None
    try:
        r = segment_fn(path, concept)
        if not getattr(r, "used_fallback", False):
            m = segment.full_frame_mask(path, r)
    except segment.SegmentationError:
        m = None
    if f is not None:
        np.savez_compressed(f, ok=m is not None, m=m if m is not None else np.zeros((1, 1), bool))
    return m


def build_tracks(frame_list, names: list, segment_fn=segment.segment_concept, cache: Path | None = None, fresh: bool = False, extra: dict | None = None) -> tuple[list[Track], dict]:
    """``extra``: name -> {"parts": [...], "aliases": [...]}; the first concept (name, then aliases) with a mask is used, parts are merged into it."""
    tracks, errors = [], {}
    for name in names:
        t = Track(name)
        ex = (extra or {}).get(name, {})
        for fr in frame_list:
            m = None
            words = name.split()
            tails = [" ".join(words[-2:]), words[-1]] if len(words) > 1 else []  # "black glasses case" -> "glasses case" -> "case"
            for concept in dict.fromkeys([name] + list(ex.get("aliases", [])) + tails):
                m = cached_mask(cache, fr.path, concept, segment_fn, fresh)
                if m is not None:
                    break
            if m is None:
                errors.setdefault(name, []).append(f"{Path(fr.path).stem}: no mask")
                continue
            for part in ex.get("parts", []):
                pm = cached_mask(cache, fr.path, part, segment_fn, fresh)
                if pm is not None and pm.shape == m.shape:
                    m = m | pm
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
        dup, part_of = None, None
        for k in out:
            shared = set(t.masks) & set(k.masks)
            if not shared:
                continue
            if np.mean([tp.iou(t.masks[s], k.masks[s]) for s in shared]) >= iou_thresh:
                dup = k
                break
            inside = np.mean([(t.masks[s] & k.masks[s]).sum() / max(t.masks[s].sum(), 1) for s in shared])
            if inside >= 0.7:
                part_of = k
                break
        if dup is None and part_of is None:
            out.append(t)
        elif dup is not None:
            dropped.append({"name": t.name, "reason": f"same object as '{dup.name}' (mask IoU >= {iou_thresh} in shared frames)"})
        else:
            dropped.append({"name": t.name, "reason": f"part of '{part_of.name}' (>= 70% of its mask lies inside that object's mask)"})
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

def fit_frame_camera(items: list, targets: dict, init: tp.CameraPose) -> tuple[tp.CameraPose, float] | None:
    """Weak-perspective camera of one frame from the base-centre pixels of the placed objects (>= 3 needed): the frame's own viewpoint, which a
    single reference-based camera cannot give once the phone moves close or around. Returns (camera, rms reprojection error in cm)."""
    from scipy.optimize import least_squares

    pts, px = [], []
    for it in items:
        if it.id in targets:
            w = it.world()
            pts.append([(w.min(0)[0] + w.max(0)[0]) / 2, w.min(0)[1], (w.min(0)[2] + w.max(0)[2]) / 2])
            px.append(targets[it.id])
    if len(pts) < 3:
        return None
    pts, px = np.asarray(pts), np.asarray(px)

    def resid(v):
        return (tp.CameraPose(v[0], v[1], v[2], v[3], v[4]).project(pts)[0] - px).ravel()

    x0 = np.array([init.az_deg, init.el_deg, init.scale, init.tx, init.ty])
    lo = np.array([-360.0, 10.0, init.scale * 0.4, -1e5, -1e5])
    hi = np.array([360.0, 80.0, init.scale * 2.5, 1e5, 1e5])
    best = None
    for az0 in (x0[0], x0[0] + 90, x0[0] - 90, x0[0] + 180):  # azimuth is multi-modal
        r = least_squares(resid, np.clip([az0, *x0[1:]], lo, hi), bounds=(lo, hi), loss="soft_l1", f_scale=8.0)
        if best is None or r.cost < best.cost:
            best = r
    v = best.x
    cam = tp.CameraPose(float(v[0]), float(v[1]), float(v[2]), float(v[3]), float(v[4]))
    return cam, float(np.sqrt(np.mean(resid(v) ** 2)) / cam.scale)


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
                 "layout_method": "weak-perspective pose fits in one reference frame (the scale reference gives the desk plane's scale and elevation); VGGT not used"}
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
        dkey = hashlib.sha1(b"".join(Path(f.path).read_bytes() for f in picks) + DISCOVER_PROMPT.encode()).hexdigest()[:20]
        dfile = out / "cache" / f"discover_{dkey}.json"
        if dfile.is_file() and not getattr(args, "fresh", False):
            found = json.loads(dfile.read_text(encoding="utf-8"))
            rep["discover_from_cache"] = True
        else:
            found = timed("discover", discover_objects, [f.path for f in picks])
            rep["spark_calls"] += 1
            dfile.parent.mkdir(parents=True, exist_ok=True)
            dfile.write_text(json.dumps(found, default=str))
    names = [o["name"] for o in found["objects"]]
    specs = refscale.specs_from_args(getattr(args, "coin", None), getattr(args, "ref_diameter_cm", None), bool(getattr(args, "card", False)), getattr(args, "ref", None),
                                     getattr(args, "ref_concept", "coin"))
    fresh = bool(getattr(args, "fresh", False))
    cache = out / "cache"
    for sp in specs:
        if sp.kind == "object" and not any(refscale.name_matches(n, sp.name) for n in names):
            names.append(sp.name)  # the reference object must be found even if Spark phrased it differently
    rep["discovered"] = {"objects": names, "surface": found["surface"], "spark_tokens": found.get("usage")}
    if not names:
        raise MapError("Muse Spark found no objects on the surface")

    # 2. track
    tracks, track_errors = timed("track", build_tracks, sharp, names, cache=cache / "masks", fresh=fresh, extra={o["name"]: o for o in found["objects"]})
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
    mesh_cache = cache / "mesh"
    mesh_cache.mkdir(parents=True, exist_ok=True)
    keys = [hashlib.sha1(c).hexdigest()[:20] for c in inputs]
    if args.mesh_backend == "modal":
        results = [None] * len(inputs)
        todo = []
        for i, k in enumerate(keys):
            f = mesh_cache / f"{k}.glb"
            if f.is_file() and not fresh:
                results[i] = f.read_bytes()
            else:
                todo.append(i)
        rep["build"]["from_cache"] = len(inputs) - len(todo)
        if todo:
            try:
                from modal_app.sam3d import run_remote_many

                got = run_remote_many([inputs[i] for i in todo])
            except Exception as exc:  # noqa: BLE001
                raise MapError(f"SAM 3D on Modal failed for the whole batch: {type(exc).__name__}: {str(exc)[:200]}") from exc
            for i, g in zip(todo, got):
                results[i] = g
                if not isinstance(g, BaseException):
                    (mesh_cache / f"{keys[i]}.glb").write_bytes(g)
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
        (out / "partial_report.json").write_text(json.dumps({**rep, "error": "no object survived reconstruction and mesh QC"}, indent=1, default=float))
        raise MapError("no object survived reconstruction and mesh QC")

    # 4. place: the most reliable reference visible (coin > card > user-given object) defines scale, camera elevation and the desk plane
    t0 = time.time()
    track_of_early = {ids[t.name]: t for t in tracks}
    ref_obs: dict = {}
    marker_masks: dict = {}
    scale_src, scale_note, fits_all = None, [], {}
    for spec in specs:
        if spec.kind in ("coin", "card"):
            for st, f in sharp_by_stem.items():
                m = cached_mask(cache / "masks", f.path, spec.name, fresh=fresh)
                if m is None or m.sum() < 120:
                    continue
                try:
                    if spec.kind == "coin":
                        d = scale_cal.fit_disk(m)
                        ppc, el = d.major_px / spec.size_cm, d.elevation_deg
                    else:
                        ppc, el = refscale.card_observation(m, spec.size_cm, spec.short_cm)
                except (scale_cal.ScaleCalibrationError, refscale.RefError):
                    continue
                ys, xs = np.nonzero(m)
                ref_obs[st] = refscale.FrameRef(float(ppc), float(np.clip(el, 12.0, 80.0)), (float(xs.mean()), float(ys.mean())))
                marker_masks[st] = m
            if ref_obs:
                scale_src = spec
                break
            scale_note.append(f"{spec.name} reference not found in any frame")
        else:
            ritem = next((it for it in items if refscale.name_matches(it.name, spec.name)), None)
            if ritem is None:
                scale_note.append(f"reference object '{spec.name}' was not built (failed or skipped), cannot use it for scale")
                continue
            cpu = spec.size_cm / float(ritem.verts[:, 1].max() - ritem.verts[:, 1].min())  # cm per mesh unit, from the supplied height
            for st, tk in track_of_early[ritem.id].masks.items():
                try:
                    fit = tp.fit_pose(ritem.verts, ritem.faces, tk)
                except tp.TextureProjectionError:
                    continue
                fits_all[(ritem.id, st)] = fit
                px = fit.pose.project(sc.base_center(ritem.verts)[None, :])[0][0]
                ref_obs[st] = refscale.FrameRef(float(fit.pose.scale / cpu), float(np.clip(fit.pose.el_deg, 12.0, 80.0)), (float(px[0]), float(px[1])))
            if ref_obs:
                scale_src = spec
                rep["reference_object_id"] = ritem.id
                break
            scale_note.append(f"reference object '{spec.name}' could not be pose-fitted in any frame")
    scale_known = scale_src is not None
    rep["scale_source"] = ({"text": scale_src.describe(), "kind": scale_src.kind, "name": scale_src.name, "size_mm": round(scale_src.size_cm * 10, 2), "frames_used": sorted(ref_obs),
                            "object_id": rep.get("reference_object_id"), "considered": [sp.describe() for sp in specs], "notes": scale_note} if scale_known else
                           {"text": "scale source: none (placeholder scale, NOT real size)", "considered": [sp.describe() for sp in specs], "notes": scale_note})
    if not scale_known:
        rep["warnings"].append("no scale reference found (or none given): the scene is NOT at real scale (placeholder 20 px/cm)")
    rep["coin"] = {"frames_with_reference": sorted(ref_obs)}

    # grounded reshape (coin-elevation-fixed silhouette fit of height aspect and taper) is only possible when a coin fixes the camera elevation
    rep["grounded_reshape"] = {}
    if scale_known and scale_src.kind == "coin":
        from . import object as obj_cli_reshape

        t1 = time.time()
        for it in items:
            tk = track_of_early[it.id].masks
            cand = sorted((st for st in marker_masks if st in tk), key=lambda st: -int(tk[st].sum()))[:4]
            if len(cand) < scale_cal.MIN_FRAMES:
                rep["grounded_reshape"][it.id] = {"applied": False, "reason": f"only {len(cand)} frame(s) show both the object and the coin"}
                continue
            try:
                g = scale_cal.calibrate_grounded([(st, tk[st], marker_masks[st]) for st in cand], it.verts, it.faces, scale_src.size_cm)
            except scale_cal.ScaleCalibrationError as exc:
                rep["grounded_reshape"][it.id] = {"applied": False, "reason": str(exc)}
                continue
            rep["grounded_reshape"][it.id] = g.to_dict()
            if g.applied:
                gp = next(m_["odir"] for m_ in meta if m_["id"] == it.id) / "model.glb"
                obj_cli_reshape._reshape_glb(gp, g.aspect_k, g.taper_t)
                it.verts, it.faces = sc._load_mesh(gp.read_bytes())
                it.glb = gp.read_bytes()
        T["reshape"] = round(time.time() - t1, 1)
    vis_counts = {s_: sum(1 for it in items if s_ in track_of_early[it.id].masks) for s_ in sharp_by_stem}
    pool = list(ref_obs) if scale_known else list(sharp_by_stem)
    ref = max(pool, key=lambda s_: (vis_counts[s_], ref_obs[s_].px_per_cm if scale_known else sharp_map[s_]))
    rep["reference_frame"] = ref
    if scale_src is not None and scale_src.kind == "object":
        # the reference object's own mesh was reshaped by nothing: its height IS the supplied height by construction
        rep["scale_source"]["note"] = "the reference object's reconstructed height is set to the supplied value, so it cannot be checked against itself"
    track_of = track_of_early

    def camera_for(stem):
        if scale_known:
            o = ref_obs[stem]
            return tp.CameraPose(0.0, o.elevation_deg, o.px_per_cm, o.anchor_px[0], o.anchor_px[1])
        return tp.CameraPose(0.0, 40.0, 20.0, size_of[stem][0] / 2, size_of[stem][1] * 0.7)

    cam_ref = camera_for(ref)
    fits_ref = {}
    for it in items:
        mask = track_of[it.id].masks.get(ref)
        if mask is None:
            rep["skipped"].append({"name": it.name, "reason": f"not visible in the reference frame {ref}"})
            continue
        try:
            fits_ref[it.id] = fits_all.get((it.id, ref)) or tp.fit_pose(it.verts, it.faces, mask)
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
    cam_az_from, cam_fit = {}, {}
    for s in verify_frames[1:]:
        base_cam = camera_for(s)
        # camera azimuth relative to the reference frame: from the object whose azimuth the silhouette pins down best in BOTH frames
        # (a mug with a handle does; a bottle or candle is rotationally symmetric, its fitted azimuth is noise)
        cand = []
        for it in items:
            m = track_of[it.id].masks.get(s)
            if m is None or it.id not in fits_ref:
                continue
            try:
                f_s = fits_all.get((it.id, s)) or tp.fit_pose(it.verts, it.faces, m)
            except tp.TextureProjectionError:
                continue
            cand.append((min(fits_ref[it.id].azimuth_sensitivity, f_s.azimuth_sensitivity) * min(fits_ref[it.id].iou, f_s.iou) ** 2, yaw_deg_of(f_s.pose.az_deg, it.fit_az[ref]), it.id))
        cand.sort(reverse=True)
        d_az = cand[0][1] if cand else 0.0
        cams[s] = tp.CameraPose(d_az, base_cam.el_deg, base_cam.scale, base_cam.tx, base_cam.ty)
        cam_az_from[s] = cand[0][2] if cand else None
        targets = {}
        for it in items:
            m_ = track_of[it.id].masks.get(s)
            if m_ is None:
                continue
            try:
                f_ = fits_all.get((it.id, s)) or tp.fit_pose(it.verts, it.faces, m_)
            except tp.TextureProjectionError:
                continue
            targets[it.id] = f_.pose.project(sc.base_center(it.verts)[None, :])[0][0]
        ff = fit_frame_camera(items, targets, cams[s])
        if ff is not None:
            cams[s], err_cm = ff
            cam_fit[s] = {"method": "weak-perspective camera fitted to the objects' base-centre pixels", "objects": sorted(targets), "rms_reprojection_cm": round(err_cm, 2)}
        else:
            cam_fit[s] = {"method": "azimuth from the best-pinned object (fewer than 3 objects visible)"}
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
                     "camera_azimuth_from_object": cam_az_from, "camera_fit": cam_fit, "rounds": rounds, "images": sorted(p.relative_to(out).as_posix() for p in vdir.glob("view*.png")),
                     "note": "IoU compares the rendered silhouette with the SAM 3.1 mask. The reference frame uses the scale reference camera; other frames use a camera fitted to the objects' base-centre pixels, so there IoU tests size, shape and yaw but NOT position (see camera_fit residuals). Weak perspective is approximate."}

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
                "measurement": {"height_cm": it.size_cm[1], "size_mm_whd": [round(it.size_cm[0] * 10, 1), round(it.size_cm[1] * 10, 1), round(it.size_cm[2] * 10, 1)],
                        "method": rep["scale_source"]["text"], "scale_source": rep["scale_source"]["text"],
                        "is_scale_reference": it.id == rep.get("reference_object_id"),
                        "uncertainty_note": "scene-level estimate (reference edge/height, weak perspective, generated proportions); not a measurement of this object"}}
        (odir / "report.json").write_text(json.dumps(orep, indent=1, default=float))
        manifest_objects.append({"id": it.id, "name": it.name, "mesh": f"objects/{it.id}/model.glb", "report": f"objects/{it.id}/report.json", "role": "child", "reconstruction_model": model_info,
                                 "transform": it.transform, "size_cm": it.size_cm, "size_mm": [round(x * 10, 1) for x in it.size_cm], "stl": f"objects/{it.id}/model_print.stl" if prep else None,
                                 "printable": bool(prep), "is_scale_reference": it.id == rep.get("reference_object_id")})
    dio, dio_info = build_diorama(items)
    dio.export(out / "diorama.stl")
    (out / "diorama.json").write_text(json.dumps(dio_info, indent=1))
    T["export"] = round(time.time() - t0, 1)
    rep["diorama"] = dio_info
    manifest = {"worldkit_scene": sc.SCENE_VERSION, "mode": "map", "source_video": Path(args.video).name, "frame": ref,
                "units": "centimetres on the desk plane (Y up, origin at the scale reference)" if scale_known else "arbitrary (no scale reference found)", "cm_per_scene_unit": 1.0 if scale_known else None,
                "scale_source": rep["scale_source"],
                "scale_calibration": {"reference": rep["scale_source"].get("name"), "reference_size_mm": rep["scale_source"].get("size_mm"), "relative_spread_mad": None, "frames_used": len(ref_obs)} if scale_known else None,
                "objects": manifest_objects, "layout_method": rep["layout_method"],
                "verify": {"mean_iou_before": rep["verify"]["mean_iou_before"], "mean_iou_after": rep["verify"]["mean_iou_after"], "images": rep["verify"]["images"]},
                "diorama": {"stl": "diorama.stl", **dio_info}, "skipped": rep["skipped"]}
    (out / "scene.json").write_text(json.dumps(manifest, indent=1, default=float))
    rep["total_seconds"] = round(time.time() - t_all, 1)
    (out / "scene_report.json").write_text(json.dumps(rep, indent=1, default=float))
    return rep
