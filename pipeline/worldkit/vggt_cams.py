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


def verify_with_vggt(*, items, sharp_by_stem, track_of, ref, verify_frames, marker_masks, coin_cm, cache, fresh, out, verify_fn, rep, no_spark=False) -> dict:
    stems = sorted(sharp_by_stem)
    paths = [sharp_by_stem[s].path for s in stems]
    g = run_vggt(paths, cache / "vggt" / "vggt", fresh)
    E, K, depth, conf, P, imgs = g["extrinsic"], g["intrinsic"], g["depth"].astype(np.float32), g["depth_conf"].astype(np.float32), g["world_points"].astype(np.float32), g["images"]
    idx = {s: i for i, s in enumerate(stems)}
    Rm, tv = E[:, :, :3], E[:, :, 3]
    centers = -np.einsum("nji,nj->ni", Rm, tv)
    sizes = {s: Image.open(sharp_by_stem[s].path).size for s in stems}  # (w, h)

    # scale, origin and plane from the coin frames
    cm_per_unit, origins, plane_pts = [], [], []
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
        raise RuntimeError(f"only {len(cm_per_unit)} coin frame(s) usable for the VGGT scale")
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
                        "vggt_run": g["_meta"], "scale_source": f"coin {coin_cm * 10:g} mm diameter (ellipse major axis, VGGT depth and focal length)"}
    return res
