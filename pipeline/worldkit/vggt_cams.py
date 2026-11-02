"""Per-frame camera poses from VGGT, aligned to the desk world (scale from the coin), for the Map-mode verify step.

VGGT gives a pinhole camera (R, t, K) per frame in its own arbitrary-scale world. To use it for the desk scene:
- scale: a coin of known diameter D seen with major axis ``a`` px (ellipse major axis = true diameter whatever the tilt) at camera depth z with focal
  fx has diameter ``a * z / fx`` VGGT units, so cm per unit = D / that (median over the coin frames; spread reported);
- origin: the coin centre (median 3D point under the coin mask);
- up: normal of the table plane fitted (RANSAC) to VGGT points around the coin;
- axes: as in the weak-perspective desk world: X = the reference camera's right vector projected on the plane, Z = toward that camera.
Objects keep the transforms of the weak-perspective placement, so the VGGT verify tests that placement from perspective cameras.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

VGGT_SIZE = 518


@dataclass
class PinholeCam:
    """Camera acting on desk-world points (cm): x_cam = A x + b, then a pinhole projection with the VGGT intrinsics."""
    A: np.ndarray
    b: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float

    def project(self, points: np.ndarray):
        pc = np.asarray(points, float) @ self.A.T + self.b
        z = np.where(pc[:, 2] > 1e-6, pc[:, 2], 1e-6)
        return np.stack([self.fx * pc[:, 0] / z + self.cx, self.fy * pc[:, 1] / z + self.cy], axis=1), -pc[:, 2]


def run_vggt(frame_paths: list, cache_file: Path, fresh: bool = False) -> dict:
    """VGGT on the frames (padded to 518x518, nothing cropped); cached on disk by hash of the frames."""
    data = [Path(p).read_bytes() for p in frame_paths]
    key = hashlib.sha1(b"".join(data)).hexdigest()[:16]
    f = cache_file.with_name(f"{cache_file.stem}_{key}.npz")
    meta = {"from_cache": True}
    if not f.is_file() or fresh:
        from modal_app.vggt_probe import app, run_vggt as remote

        jpgs = []
        for p in frame_paths:
            ok, buf = cv2.imencode(".jpg", cv2.imread(str(p)), [cv2.IMWRITE_JPEG_QUALITY, 95])
            jpgs.append(buf.tobytes())
        with app.run():
            r = remote.remote(jpgs, "pad")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r["npz"])
        meta = {"from_cache": False, **{k: v for k, v in r.items() if k != "npz"}}
    z = np.load(f)
    out = {k: z[k] for k in z.files}
    out["_meta"] = meta
    return out


def to_vggt_px(xy, orig_wh):
    w, h = orig_wh
    sc = VGGT_SIZE / max(w, h)
    return np.asarray(xy, float) * sc + np.array([(VGGT_SIZE - w * sc) / 2, (VGGT_SIZE - h * sc) / 2]), sc


def mask_to_vggt(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    sc = VGGT_SIZE / max(w, h)
    nw, nh = max(int(round(w * sc)), 1), max(int(round(h * sc)), 1)
    m = cv2.resize(mask.astype(np.uint8), (nw, nh), interpolation=cv2.INTER_NEAREST).astype(bool)
    out = np.zeros((VGGT_SIZE, VGGT_SIZE), bool)
    x0, y0 = (VGGT_SIZE - nw) // 2, (VGGT_SIZE - nh) // 2
    out[y0:y0 + nh, x0:x0 + nw] = m
    return out


def ransac_plane(X: np.ndarray, tol: float, iters: int = 800, seed: int = 0):
    rng = np.random.default_rng(seed)
    best = (0, None)
    for _ in range(iters):
        a, b, c = X[rng.choice(len(X), 3, replace=False)]
        n = np.cross(b - a, c - a)
        l = np.linalg.norm(n)
        if l < 1e-12:
            continue
        n /= l
        cnt = int((np.abs((X - a) @ n) < tol).sum())
        if cnt > best[0]:
            best = (cnt, (n, a))
    n, a = best[1]
    inl = np.abs((X - a) @ n) < tol
    Y = X[inl]
    cen = Y.mean(0)
    n2 = np.linalg.svd(Y - cen, full_matrices=False)[2][2]
    return n2, cen, float(inl.mean())


def perspective_corrections(*, sharp_by_stem, marker_masks, obj_masks, cache, fresh) -> dict:
    """{object id: {frame: (z_marker / z_object, elevation_object - elevation_marker in degrees)}} from VGGT (medians over each mask).

    A weak-perspective reading of a coin or card holds at the marker only. An object farther from the camera looks smaller by the depth
    ratio (px/cm at the object = px/cm at the marker x ratio) and is seen at a shallower angle to the table: the elevation difference is
    measured between the camera rays to the two masks' 3D points and the table plane fitted around the marker. Same VGGT run (and cache)
    as ``verify_with_vggt``."""
    stems = sorted(sharp_by_stem)
    g = run_vggt([sharp_by_stem[s].path for s in stems], cache / "vggt" / "vggt", fresh)
    D = g["depth"].astype(np.float32)
    if D.ndim == 4:
        D = D[..., 0]
    P = g["world_points"].astype(np.float32)
    Rm, tv = g["extrinsic"][:, :, :3], g["extrinsic"][:, :, 3]
    centers = -np.einsum("nji,nj->ni", Rm, tv)
    idx = {s: i for i, s in enumerate(stems)}
    planes = {}

    def elev(i, m, st):
        mv = mask_to_vggt(m)
        if st not in planes:
            ys, xs = np.nonzero(mask_to_vggt(marker_masks[st]))
            v, u = int(ys.mean()), int(xs.mean())
            win = P[i][max(v - 60, 0):v + 60, max(u - 60, 0):u + 60].reshape(-1, 3)
            planes[st] = ransac_plane(win[np.isfinite(win).all(1)], tol=0.005)[0]
        p = np.median(P[i][mv], axis=0)
        d = p - centers[i]
        return float(np.degrees(np.arcsin(abs(d @ planes[st]) / np.linalg.norm(d))))

    def zmed(i, m):
        mv = mask_to_vggt(m)
        v = D[i][mv]
        return float(np.median(v)) if v.size else None

    out = {}
    for oid, ms in obj_masks.items():
        for st, m in ms.items():
            if st not in marker_masks or st not in idx:
                continue
            i = idx[st]
            zm, zo = zmed(i, marker_masks[st]), zmed(i, m)
            if zm and zo and zm > 0 and zo > 0:
                out.setdefault(oid, {})[st] = (zm / zo, elev(i, m, st) - elev(i, marker_masks[st], st))
    return out


def card_scale_3d(P_i: np.ndarray, mask: np.ndarray, size_wh, card_cm) -> tuple[float, float] | None:
    """(cm per VGGT unit, 3D long/short ratio) from the card's four corners looked up in VGGT's world-point map (no weak perspective).
    The ratio should be close to 85.6/54 = 1.585 if the mask really is the card."""
    from . import refscale

    q = refscale.card_quad(mask)
    if q is None:
        return None
    qv, _ = to_vggt_px(q, size_wh)
    X = []
    for u, v in qv:
        u, v = int(round(u)), int(round(v))
        if not (2 <= u < VGGT_SIZE - 2 and 2 <= v < VGGT_SIZE - 2):
            return None
        X.append(np.median(P_i[v - 1:v + 2, u - 1:u + 2].reshape(-1, 3), axis=0))
    X = np.array(X)
    e = [np.linalg.norm(X[(i + 1) % 4] - X[i]) for i in range(4)]
    p0, p1 = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
    lo, sh = max(p0, p1), min(p0, p1)
    if sh <= 0:
        return None
    L, S = card_cm
    return float((L / lo + S / sh) / 2), float(lo / sh)


def verify_with_vggt(*, items, sharp_by_stem, track_of, ref, verify_frames, marker_masks, coin_cm, cache, fresh, out, verify_fn, rep, no_spark=False,
                     card_cm=None) -> dict:
    """``card_cm=(long, short)``: the marker is a card, scaled from its corners' 3D points; otherwise a coin of diameter ``coin_cm``."""
    stems = sorted(sharp_by_stem)
    paths = [sharp_by_stem[s].path for s in stems]
    g = run_vggt(paths, cache / "vggt" / "vggt", fresh)
    E, K, depth, conf, P, imgs = g["extrinsic"], g["intrinsic"], g["depth"].astype(np.float32), g["depth_conf"].astype(np.float32), g["world_points"].astype(np.float32), g["images"]
    idx = {s: i for i, s in enumerate(stems)}
    Rm, tv = E[:, :, :3], E[:, :, 3]
    centers = -np.einsum("nji,nj->ni", Rm, tv)
    sizes = {s: Image.open(sharp_by_stem[s].path).size for s in stems}  # (w, h)

    # scale, origin and plane from the coin frames
    cm_per_unit, origins, plane_pts, card_ratios = [], [], [], []
    for s, m in marker_masks.items():
        if s not in idx:
            continue
        i = idx[s]
        ys, xs = np.nonzero(m)
        if len(xs) < 20:
            continue
        ctr, sc = to_vggt_px([[xs.mean(), ys.mean()]], sizes[s])
        u, v = int(round(ctr[0][0])), int(round(ctr[0][1]))
        if not (4 <= u < VGGT_SIZE - 4 and 4 <= v < VGGT_SIZE - 4):
            continue
        d = float(np.median(depth[i][v - 1:v + 2, u - 1:u + 2]))
        pt = np.median(P[i][v - 1:v + 2, u - 1:u + 2].reshape(-1, 3), axis=0)
        if card_cm is not None:
            cs = card_scale_3d(P[i], m, sizes[s], card_cm)
            if cs is None:
                continue
            cm_per_unit.append(cs[0])
            card_ratios.append(cs[1])
        else:
            # coin ellipse major axis in VGGT pixels (area-equivalent, as scale_cal.fit_disk)
            from . import scale_cal

            try:
                major = scale_cal.fit_disk(m, min_area_px=30).major_px * sc
            except scale_cal.ScaleCalibrationError:
                continue
            # the camera-space depth of the coin = depth map value (VGGT depth is z along the optical axis)
            d_units = major * d / float(K[i][0, 0])
            cm_per_unit.append(coin_cm / d_units)
        origins.append(pt)
        win = P[i][max(v - 70, 0):v + 70, max(u - 70, 0):u + 70].reshape(-1, 3)
        plane_pts.append(win[np.isfinite(win).all(1)])
    if len(cm_per_unit) < 2:
        raise RuntimeError(f"only {len(cm_per_unit)} {'card' if card_cm else 'coin'} frame(s) usable for the VGGT scale")
    c = float(np.median(cm_per_unit))
    o = np.median(np.array(origins), axis=0)
    cloud = np.vstack(plane_pts)
    cloud = cloud[np.linalg.norm(cloud - o, axis=1) < 30.0 / c]  # within ~30 cm of the coin
    n, cen, inl = ransac_plane(cloud, tol=0.6 / c)  # 6 mm
    ri = idx[ref]
    if (centers[ri] - o) @ n < 0:
        n = -n
    Rr = Rm[ri]
    right = Rr[0]
    fwd = Rr[2]
    Xax = right - (right @ n) * n
    Xax /= np.linalg.norm(Xax)
    Yax = n / np.linalg.norm(n)
    Zax = np.cross(Xax, Yax)
    if Zax @ (-(fwd - (fwd @ n) * n)) < 0:  # Z must point toward the reference camera (opposite its forward direction)
        Xax, Zax = -Xax, -Zax
    M = np.stack([Xax, Yax, Zax], axis=1)
    cams, masks, size_of, rgbs = {}, {it.id: {} for it in items}, {}, {}
    for s in verify_frames:
        i = idx[s]
        A = Rm[i] @ M / c
        b = Rm[i] @ o + tv[i]
        cams[s] = PinholeCam(A, b, float(K[i][0, 0]), float(K[i][1, 1]), float(K[i][0, 2]), float(K[i][1, 2]))
        size_of[s] = (VGGT_SIZE, VGGT_SIZE)
        rgbs[s] = np.transpose(imgs[i], (1, 2, 0)).astype(np.uint8)
        for it in items:
            m = track_of[it.id].masks.get(s)
            if m is not None:
                masks[it.id][s] = mask_to_vggt(m)
    res = verify_fn(items, cams, masks, size_of, rgbs, verify_frames, out / "verify", "vggt", rep, no_spark)
    res["alignment"] = {"cm_per_vggt_unit": c, "scale_spread_rel": float(np.std(cm_per_unit) / c), "coin_frames_used": len(cm_per_unit),
                        "coin_centre_spread_cm": float(np.linalg.norm((np.array(origins) - o) * c, axis=1).max()), "table_plane_inlier_fraction": inl,
                        "table_plane_points": int(len(cloud)), "camera_height_above_table_cm_ref_frame": float((centers[ri] - o) @ Yax * c),
                        "vggt_run": g["_meta"], "scale_source": (f"card {card_cm[0] * 10:g} x {card_cm[1] * 10:g} mm (corner 3D points from VGGT)" if card_cm else
                                                                 f"coin {coin_cm * 10:g} mm diameter (ellipse major axis, VGGT depth and focal length)"),
                        "per_frame_cm_per_unit": [round(x, 5) for x in cm_per_unit], "card_3d_long_short_ratio": [round(x, 3) for x in card_ratios] or None}
    return res
