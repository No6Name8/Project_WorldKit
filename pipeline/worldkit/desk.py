"""Desk scene v2: VGGT cameras, a fused stadium table, real-size reference objects, triangulated placement.

    python -m worldkit.desk VIDEO --out runs/desk_v2 [--coin-mm 21.5] [--table-cm L W] [--table-thick-cm T] [--until STAGE] [--no-spark]

Stages (each cached; rerunning only redoes the cheap maths):
  frames   all frames at 2 fps; SAM frames = sharp frames with the coin visible, spread over three equal time segments;
           per segment, fit frames (triangulation, scale/yaw fit) and verify frames are kept DISJOINT so the verify IoU is held out
  vggt     VGGT-1B (Modal A100) on every frame: cameras, depth, world points
  table    SAM 3.1 table-top masks in every SAM frame -> plane (RANSAC on VGGT points under the masks) -> every mask projected onto the
           plane with its camera and fused by voting -> stadium (rounded rectangle) fit; coin diameter measured on the plane gives cm
  objects  SAM 3.1 masks for the mug, the case and the candle (holder + candle as ONE object); SAM 3D Objects from each object's largest
           view; mesh QC; the candle falls back to a parametric holder + candle (sizes from the masks) if its mesh fails QC
  place    position = the object's mask-centroid rays triangulated over the fit frames, dropped onto the table plane; scale and yaw
           fitted to the SAM silhouettes in the fit frames (IoU only, never ground truth); the case's yaw from its mask's principal axis
  verify   held-out verify frames, VGGT cameras, at most 2 Muse Spark correction rounds (kept only if IoU improves)
  export   scene.json for the viewer, per-object print STLs (mm), the stadium table, the coin, and a diorama scaled to the 256 mm bed
Everything generated is labelled as generated; the coin is known geometry; the table size comes from the coin unless --table-cm is given.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import trimesh
from PIL import Image

from . import appearance, coindetect, printprep
from . import scene as sc
from .frames import _laplacian_sharpness, extract_all_frames
from .mapmode import Placed, cached_mask, crop_rgba, mean_iou, modal_metered, object_ious, object_qc, render_overlay, to_print_space, verify_loop
from .vggt_cams import VGGT_SIZE, PinholeCam, mask_to_vggt, ransac_plane, run_vggt, to_vggt_px

STAGES = ["frames", "vggt", "table", "objects", "place", "verify", "export"]
CONCEPTS = {"table": "white marble table top", "mug": "striped mug", "case": "black case", "holder": "red candle holder", "candle": "white candle"}
NAMES = {"mug": "striped mug", "case": "black case", "candle": "candle in red holder"}
BED_MM = 256.0


def jdump(p: Path, d) -> None:
    p.write_text(json.dumps(d, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)), encoding="utf-8")


# ---------------------------------------------------------------- VGGT geometry helpers
class VG:
    """VGGT output for a list of frame stems: cameras (OpenCV: x_cam = R X + t), intrinsics on the 518 px padded canvas, world points."""

    def __init__(self, z: dict, stems: list[str], orig_wh):
        self.E, self.K = z["extrinsic"].astype(float), z["intrinsic"].astype(float)
        self.P = z["world_points"].astype(np.float32)
        self.conf = z["depth_conf"].astype(np.float32)
        self.img = np.transpose(z["images"], (0, 2, 3, 1))
        self.idx = {s: i for i, s in enumerate(stems)}
        self.wh = orig_wh
        self.valid = mask_to_vggt(np.ones((orig_wh[1], orig_wh[0]), bool))  # the padded canvas: real pixels only

    def R(self, s): return self.E[self.idx[s], :, :3]
    def t(self, s): return self.E[self.idx[s], :, 3]
    def center(self, s): return -self.R(s).T @ self.t(s)

    def rays(self, s, px: np.ndarray) -> np.ndarray:
        K = self.K[self.idx[s]]
        h = np.c_[px, np.ones(len(px))]
        d = (np.linalg.inv(K) @ h.T).T @ self.R(s)  # = (R^T d_cam^T)^T
        return d / np.linalg.norm(d, axis=1, keepdims=True)

    def depth_of(self, s, X: np.ndarray) -> float:
        return float((self.R(s) @ X + self.t(s))[2])


def plane_hits(vg: VG, s: str, px: np.ndarray, n, p0):
    """Ray-plane intersections of pixels (VGGT canvas) in frame s; returns (points, ok mask)."""
    C, D = vg.center(s), vg.rays(s, px)
    den = D @ n
    tt = ((p0 - C) @ n) / np.where(np.abs(den) > 1e-9, den, 1e-9)
    ok = (tt > 0) & (np.abs(den) > 0.05)
    return C + D * tt[:, None], ok


def triangulate(Cs, Ds) -> np.ndarray:
    A, b = np.zeros((3, 3)), np.zeros(3)
    for c, d in zip(Cs, Ds):
        M = np.eye(3) - np.outer(d, d)
        A += M
        b += M @ c
    return np.linalg.solve(A, b)


def fast_sil(world_cm: np.ndarray, faces: np.ndarray, cam: PinholeCam, size=(VGGT_SIZE, VGGT_SIZE)) -> np.ndarray:
    xy, _ = cam.project(world_cm)
    m = np.zeros((size[1], size[0]), np.uint8)
    tri = np.round(xy[faces] * 4).astype(np.int32)  # 2 bits of sub-pixel precision
    cv2.fillPoly(m, list(tri), 1, lineType=cv2.LINE_8, shift=2)
    return m.astype(bool)


def iou(a, b) -> float:
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


# ---------------------------------------------------------------- shapes
def stadium_outline(L: float, W: float, r: float, n_arc: int = 24) -> np.ndarray:
    """Rounded rectangle (stadium when r = W/2) centred at 0, long side along x; counter-clockwise points (x, z)."""
    r = min(r, W / 2, L / 2)
    hx, hz = L / 2 - r, W / 2 - r
    pts = []
    for cx, cz, a0 in [(hx, -hz, -90), (hx, hz, 0), (-hx, hz, 90), (-hx, -hz, 180)]:
        for a in np.linspace(a0, a0 + 90, n_arc):
            pts.append((cx + r * np.cos(np.radians(a)), cz + r * np.sin(np.radians(a))))
    return np.array(pts)


def in_rounded_rect(x, z, L, W, r):
    r = np.minimum(r, np.minimum(W, L) / 2)
    qx, qz = np.abs(x) - (L / 2 - r), np.abs(z) - (W / 2 - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qz, 0)) + np.minimum(np.maximum(qx, qz), 0) <= r


def extrude(outline_xz: np.ndarray, y0: float, y1: float) -> trimesh.Trimesh:
    """Prism from a convex outline (x, z) between heights y0 < y1; watertight, outward normals."""
    n = len(outline_xz)
    top = np.c_[outline_xz[:, 0], np.full(n, y1), outline_xz[:, 1]]
    bot = np.c_[outline_xz[:, 0], np.full(n, y0), outline_xz[:, 1]]
    c_top, c_bot = np.array([[*outline_xz.mean(0)[:1], y1, outline_xz.mean(0)[1]]]), np.array([[*outline_xz.mean(0)[:1], y0, outline_xz.mean(0)[1]]])
    V = np.vstack([top, bot, c_top, c_bot])
    it, ib = 2 * n, 2 * n + 1
    F = []
    for i in range(n):
        j = (i + 1) % n
        F += [[it, j, i], [ib, n + i, n + j], [i, j, n + j], [i, n + j, n + i]]
    m = trimesh.Trimesh(V, np.array(F), process=True)
    m.fix_normals()
    return m


def colored_glb(meshes: list[tuple[trimesh.Trimesh, tuple]], ) -> bytes:
    scene = trimesh.Scene()
    for i, (m, rgb) in enumerate(meshes):
        m = m.copy()
        m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(baseColorFactor=[*[int(c) for c in rgb], 255], metallicFactor=0.0, roughnessFactor=0.6))
        scene.add_geometry(m, node_name=f"part{i}")
    return scene.export(file_type="glb")


def base_centered_glb(glb: bytes) -> bytes:
    """Bake node transforms, then move the mesh so its base centre sits at the origin (Y up)."""
    s = trimesh.load(io.BytesIO(glb), file_type="glb")
    geoms = s.dump() if hasattr(s, "dump") else [s]
    allv = np.vstack([g.vertices for g in geoms])
    lo, hi = allv.min(0), allv.max(0)
    shift = -np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    out = trimesh.Scene()
    for i, g in enumerate(geoms):
        g = g.copy()
        g.apply_translation(shift)
        out.add_geometry(g, node_name=f"g{i}")
    return out.export(file_type="glb")


# ---------------------------------------------------------------- the run
def run(args) -> dict:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    rep = json.loads((out / "desk_report.json").read_text(encoding="utf-8")) if (out / "desk_report.json").is_file() else {}
    rep.setdefault("warnings", [])
    until = STAGES.index(args.until)
    T0 = time.time()

    # -- frames
    frames = extract_all_frames(Path(args.video), out / "frames", fps=2.0)
    stems = [p.stem for p in frames]
    path_of = {p.stem: p for p in frames}
    w, h = Image.open(frames[0]).size
    sharp = {p.stem: float(_laplacian_sharpness(p)) for p in frames}
    coin_px = {}
    for p in frames:
        m = coindetect.find_gold_coin(np.array(Image.open(p).convert("RGB")))
        if m is not None:
            coin_px[p.stem] = m
    seg = np.array_split(np.arange(len(stems)), 3)
    fit_frames, verify_frames = [], []
    for k, idxs in enumerate(seg):
        cand = sorted(idxs, key=lambda i: -(np.log(sharp[stems[i]] + 1) + (1.0 if stems[i] in coin_px else 0.0)))
        chosen = []
        for i in cand:
            if all(abs(i - j) >= 3 for j in chosen):
                chosen.append(i)
            if len(chosen) == args.per_segment:
                break
        chosen.sort()
        ver = chosen[1::2][:2] if len(chosen) >= 4 else chosen[-1:]
        verify_frames += [stems[i] for i in ver]
        fit_frames += [stems[i] for i in chosen if i not in ver]
    sam_frames = sorted(fit_frames + verify_frames)
    rep["frames"] = {"video": Path(args.video).name, "n_frames": len(stems), "fps": 2.0, "coin_detector_frames": sorted(coin_px),
                     "fit_frames": fit_frames, "verify_frames": verify_frames, "segments": [[stems[i[0]], stems[i[-1]]] for i in seg],
                     "selection": "per third of the video: sharp frames with the coin visible, >= 3 frames apart; fit and verify frames disjoint"}
    jdump(out / "desk_report.json", rep)
    if until == 0:
        return rep

    # -- vggt
    m0 = modal_metered()
    t0 = time.time()
    z = run_vggt(frames, cache / "vggt" / "vggt")
    vg = VG(z, stems, (w, h))
    if not z["_meta"]["from_cache"]:
        rep["vggt"] = {"frames": len(stems), "wall_s": round(time.time() - t0, 1), **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in z["_meta"].items()},
                       "modal_cost_usd_delta": None if m0 is None else round((modal_metered() or m0) - m0, 3)}
    rep.setdefault("vggt", {"frames": len(stems)})
    jdump(out / "desk_report.json", rep)
    if until == 1:
        return rep

    # -- table: masks, plane, fused outline, stadium, coin scale
    def vmask(concept, s):
        m = cached_mask(cache / "masks", path_of[s], concept)
        return None if m is None or m.sum() < 50 else mask_to_vggt(m)

    tmask = {s: vmask(CONCEPTS["table"], s) for s in sam_frames}
    tmask = {s: m for s, m in tmask.items() if m is not None}
    if len(tmask) < 3:
        raise SystemExit(f"table found in only {len(tmask)} frames")
    pts = []
    for s, m in tmask.items():
        i = vg.idx[s]
        sel = m & (vg.conf[i] > np.percentile(vg.conf[i][m], 40))
        P = vg.P[i][sel]
        pts.append(P[np.random.default_rng(0).choice(len(P), min(len(P), 4000), replace=False)])
    X = np.vstack(pts)
    spread = float(np.median(np.linalg.norm(X - np.median(X, 0), axis=1)))
    n, p0, inl = ransac_plane(X, tol=0.01 * spread, iters=1500)
    n = n / np.linalg.norm(n)
    if np.mean([(vg.center(s) - p0) @ n for s in stems]) < 0:
        n = -n
    e1 = np.cross(n, [0, 0, 1.0]); e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    uv = lambda P: np.c_[(P - p0) @ e1, (P - p0) @ e2]  # noqa: E731
    # fuse: every table mask projected onto the plane, voting per cell (visible in the frame vs inside the mask)
    Ut = uv(X)
    lo, hi = np.percentile(Ut, 1, 0), np.percentile(Ut, 99, 0)
    pad = 0.35 * (hi - lo)
    lo, hi = lo - pad, hi + pad
    G = 360
    cell = float(max(hi - lo) / G)
    gx = np.arange(lo[0], hi[0], cell)
    gy = np.arange(lo[1], hi[1], cell)
    GX, GY = np.meshgrid(gx, gy)
    Pg = p0 + GX.reshape(-1, 1) * e1 + GY.reshape(-1, 1) * e2
    vis = np.zeros(len(Pg)); hit = np.zeros(len(Pg))
    for s, m in tmask.items():
        cam = vg.R(s) @ Pg.T + vg.t(s)[:, None]
        K = vg.K[vg.idx[s]]
        zc = cam[2]
        x = (K[0, 0] * cam[0] / np.maximum(zc, 1e-9) + K[0, 2]).round().astype(int)
        y = (K[1, 1] * cam[1] / np.maximum(zc, 1e-9) + K[1, 2]).round().astype(int)
        ok = (zc > 0) & (x >= 0) & (x < VGGT_SIZE) & (y >= 0) & (y < VGGT_SIZE)
        ok[ok] = vg.valid[y[ok], x[ok]]
        vis += ok
        hit[ok] += m[y[ok], x[ok]]
    frac = np.where(vis >= 2, hit / np.maximum(vis, 1), 0).reshape(GX.shape)
    occ = (frac >= 0.5).astype(np.uint8)
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(occ, 8)
    if nlab < 2:
        raise SystemExit("fused table outline is empty")
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    occ = (lab == big).astype(np.uint8)
    occ = cv2.morphologyEx(occ, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(occ, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(occ)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    ys, xs = np.nonzero(filled)
    U = np.c_[gx[xs], gy[ys]]
    c0 = U.mean(0)
    ev, evec = np.linalg.eigh(np.cov((U - c0).T))
    ax = evec[:, 1]
    ang0 = float(np.arctan2(ax[1], ax[0]))
    loc = lambda P2, cx, cy, a: (np.c_[(P2[:, 0] - cx) * np.cos(a) + (P2[:, 1] - cy) * np.sin(a), -(P2[:, 0] - cx) * np.sin(a) + (P2[:, 1] - cy) * np.cos(a)])  # noqa: E731
    Lc = loc(U, *c0, ang0)
    L0, W0 = float(np.ptp(Lc[:, 0])), float(np.ptp(Lc[:, 1]))
    allc = np.c_[GX.ravel(), GY.ravel()]
    fillv = filled.ravel().astype(bool)
    sub = np.random.default_rng(1).choice(len(allc), min(len(allc), 60000), replace=False)

    def cost(p):
        cx, cy, a, L, W, r = p
        if L <= 0 or W <= 0 or r <= 0:
            return 1.0
        q = loc(allc[sub], cx, cy, a)
        inside = in_rounded_rect(q[:, 0], q[:, 1], L, W, r)
        return 1.0 - iou(inside, fillv[sub])

    from scipy.optimize import minimize

    best = min((minimize(cost, [*c0, ang0, L0, W0, W0 * f], method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-5, "maxiter": 1500}) for f in (0.3, 0.5)), key=lambda r: r.fun)
    cx, cy, ang, L, W, r = best.x
    if L < W:
        L, W, ang = W, L, ang + np.pi / 2
    r = min(r, W / 2)
    stadium_iou = 1 - best.fun

    # coin diameter on the plane (contour of the colour-detected coin, projected): only coins on the table, clustered
    coin_obs = []
    for s, m in coin_px.items():
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cpts = max(cs, key=cv2.contourArea)[:, 0, :].astype(float) + 0.5
        vx, _ = to_vggt_px(cpts, (w, h))
        Pp, ok = plane_hits(vg, s, vx, n, p0)
        if ok.sum() < 6:
            continue
        q = uv(Pp[ok])
        d_plane = 2 * np.sqrt(abs(cv2.contourArea(q.astype(np.float32))) / np.pi)
        cen = q.mean(0)
        # second method: ellipse major axis (px) x depth / focal length
        (ex_, ey_), (ma, mi), _ = cv2.fitEllipse(cpts.astype(np.float32)) if len(cpts) >= 5 else ((0, 0), (0, 0), 0)
        _, scl = to_vggt_px(np.zeros((1, 2)), (w, h))
        Pc = p0 + cen[0] * e1 + cen[1] * e2
        d_depth = max(ma, mi) * scl * vg.depth_of(s, Pc) / vg.K[vg.idx[s]][0, 0]
        qc = loc(cen[None], cx, cy, ang)[0]
        coin_obs.append({"frame": s, "d_plane": float(d_plane), "d_depth": float(d_depth), "uv": cen.tolist(), "on_table": bool(in_rounded_rect(qc[0], qc[1], L, W, r)), "px": float(max(ma, mi))})
    good = [o for o in coin_obs if o["on_table"]]
    if good:
        med = np.median([o["uv"] for o in good], 0)
        dist = np.array([np.linalg.norm(np.array(o["uv"]) - med) for o in good])
        good = [o for o, d in zip(good, dist) if d < max(3 * np.median(dist), 0.02 * L)]
    if len(good) < 2:
        raise SystemExit(f"the coin was measured in only {len(good)} frames on the table")
    coin_cm = args.coin_mm / 10.0
    d_units = float(np.median([o["d_plane"] for o in good]))
    k_coin = coin_cm / d_units
    k_depth = coin_cm / float(np.median([o["d_depth"] for o in good]))
    spread_coin = float(np.median([abs(o["d_plane"] - d_units) for o in good]) / d_units)
    if args.table_cm:
        k = args.table_cm[0] / L
        scale_src = f"table length {args.table_cm[0]} cm (user-measured)"
    else:
        k = k_coin
        scale_src = f"coin {args.coin_mm} mm, measured on the table plane in {len(good)} frames (VGGT cameras)"
    coin_uv = np.median([o["uv"] for o in good], 0)

    # world frame: origin = table centre, X = table long axis, Y = up (plane normal), Z = X x Y; centimetres
    o_w = p0 + cx * e1 + cy * e2
    ex = np.cos(ang) * e1 + np.sin(ang) * e2
    ez = np.cross(ex, n)
    B = np.c_[ex, n, ez]
    to_world = lambda P: ((np.atleast_2d(P) - o_w) @ B) * k  # noqa: E731
    cams = {s: PinholeCam(vg.R(s) @ B / k, vg.R(s) @ o_w + vg.t(s), *vg.K[vg.idx[s]][[0, 1, 0, 1], [0, 1, 2, 2]]) for s in stems}
    coin_w = to_world(p0 + coin_uv[0] * e1 + coin_uv[1] * e2)[0]
    rgb_frame = lambda s: np.array(Image.open(path_of[s]).convert("RGB"))  # noqa: E731
    tcol = np.median(np.vstack([rgb_frame(s)[cached_mask(cache / "masks", path_of[s], CONCEPTS["table"])] for s in list(tmask)[:6]]), 0)  # median colour of the table masks
    table = {"plane_inlier_share": round(inl, 3), "masks": len(tmask), "fused_cells": int(filled.sum()), "stadium_fit_iou": round(stadium_iou, 3),
             "length_cm": round(L * k, 1), "width_cm": round(W * k, 1), "corner_radius_cm": round(r * k, 1), "is_full_stadium": bool(r > 0.95 * W / 2),
             "thickness_cm": args.table_thick_cm, "thickness_source": "user-measured" if args.table_thick_given else "estimated (not measured)",
             "colour_rgb": [int(c) for c in tcol], "scale_source": scale_src,
             "coin": {"frames": len(good), "frames_detected": len(coin_obs), "cm_per_unit_plane": k_coin, "cm_per_unit_depth": k_depth,
                      "plane_vs_depth_disagreement_pct": round(100 * (k_depth - k_coin) / k_coin, 1), "relative_spread": round(spread_coin, 3),
                      "median_px": round(float(np.median([o["px"] for o in good])), 1), "position_cm": [round(float(v), 2) for v in coin_w]},
             "length_coin_cm": round(L * k_coin, 1), "width_coin_cm": round(W * k_coin, 1)}
    if args.table_cm:
        table["coin_vs_table_disagreement_pct"] = round(100 * (k_coin - k) / k, 1)
    rep["table"] = table
    jdump(out / "desk_report.json", rep)
    np.save(out / "table_fused.npy", filled)
    Image.fromarray((filled * 255).astype(np.uint8)).save(out / "table_fused.png")
    if until == 2:
        return rep

    # -- objects: masks, SAM 3D builds, QC, parametric candle fallback
    masks = {o: {} for o in ("mug", "case", "candle")}
    masks_o = {o: {} for o in ("mug", "case", "candle")}
    for s in sam_frames:
        for o in ("mug", "case"):
            m = cached_mask(cache / "masks", path_of[s], CONCEPTS[o])
            if m is not None and m.sum() >= 60:
                masks_o[o][s] = m
        hm = cached_mask(cache / "masks", path_of[s], CONCEPTS["holder"])
        cm_ = cached_mask(cache / "masks", path_of[s], CONCEPTS["candle"])
        parts = [x for x in (hm, cm_) if x is not None and x.sum() >= 30]
        if parts:
            masks_o["candle"][s] = np.logical_or.reduce(parts)
            masks_o.setdefault("_holder", {})[s] = hm if hm is not None else np.zeros_like(parts[0])
            masks_o.setdefault("_wick", {})[s] = cm_ if cm_ is not None else np.zeros_like(parts[0])
    for o in masks:
        masks[o] = {s: mask_to_vggt(m) for s, m in masks_o[o].items()}
    rep["masks"] = {o: sorted(masks[o]) for o in masks}

    def best_view(o):
        def score(s):
            m = masks_o[o][s]
            ys, xs = np.nonzero(m)
            touches = xs.min() <= 1 or ys.min() <= 1 or xs.max() >= w - 2 or ys.max() >= h - 2
            return m.sum() * (0.3 if touches else 1.0) * np.log(sharp[s] + 1)
        return max(masks_o[o], key=score) if masks_o[o] else None

    views = {o: best_view(o) for o in masks}
    inputs = {o: crop_rgba(rgb_frame(s), masks_o[o][s]) for o, s in views.items() if s}
    mesh_dir = cache / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    keys = {o: hashlib.sha1(b).hexdigest()[:20] for o, b in inputs.items()}
    todo = [o for o in inputs if not (mesh_dir / f"{keys[o]}.glb").is_file()]
    if todo:
        if args.no_gpu:
            raise SystemExit(f"meshes not cached for {todo} and --no-gpu given")
        from modal_app.sam3d import run_remote_many

        m0, t0 = modal_metered(), time.time()
        got = run_remote_many([inputs[o] for o in todo])
        for o, g in zip(todo, got):
            if isinstance(g, BaseException):
                rep["warnings"].append(f"SAM 3D failed for {o}: {g}")
            else:
                (mesh_dir / f"{keys[o]}.glb").write_bytes(g)
        rep.setdefault("gpu", []).append({"what": "SAM 3D " + ", ".join(todo), "wall_s": round(time.time() - t0, 1),
                                          "modal_cost_usd_delta": None if m0 is None else round((modal_metered() or m0) - m0, 3)})
    built = {}
    for o in inputs:
        f = mesh_dir / f"{keys[o]}.glb"
        if not f.is_file():
            continue
        glb = f.read_bytes()
        if appearance.glb_is_textured(glb):
            glb, _ = appearance.finalize_textured_glb(glb)
        ok, why, stats = object_qc(glb)
        built[o] = {"view": views[o], "qc_ok": ok, "qc": why, **stats}
        if ok:
            built[o]["glb"] = base_centered_glb(glb)
    rep["objects"] = {o: {k2: v for k2, v in b.items() if k2 != "glb"} for o, b in built.items()}
    jdump(out / "desk_report.json", rep)
    if until == 3:
        return rep

    # -- place: triangulated base point, scale + yaw from silhouettes in the fit frames
    def tri_point(o, frames_):
        Cs, Ds = [], []
        for s in frames_:
            m = masks[o].get(s)
            if m is None:
                continue
            ys, xs = np.nonzero(m)
            Cs.append(vg.center(s)); Ds.append(vg.rays(s, np.array([[xs.mean(), ys.mean()]]))[0])
        return (triangulate(Cs, Ds), len(Cs)) if len(Cs) >= 2 else (None, len(Cs))

    def tri_top(mset, frames_):
        Cs, Ds = [], []
        for s in frames_:
            m = mset.get(s)
            if m is None or m.sum() < 10:
                continue
            m = mask_to_vggt(m) if m.shape != (VGGT_SIZE, VGGT_SIZE) else m
            ys, xs = np.nonzero(m)
            y0 = ys.min()
            Cs.append(vg.center(s)); Ds.append(vg.rays(s, np.array([[xs[ys <= y0 + 1].mean(), y0]]))[0])
        return triangulate(Cs, Ds) if len(Cs) >= 2 else None

    def width_cm(m, s, Pv):
        ys, xs = np.nonzero(m)
        return float(np.ptp(xs)) * vg.depth_of(s, Pv) / vg.K[vg.idx[s]][0, 0] * k

    items, place = [], {}
    fitset = lambda o: [s for s in fit_frames if s in masks[o]]  # noqa: E731
    for o in ("mug", "case", "candle"):
        P, n_used = tri_point(o, fitset(o))
        if P is None:
            P, n_used = tri_point(o, sorted(masks[o]))
            if P is None:
                rep["warnings"].append(f"{o}: seen in fewer than 2 frames, not placed")
                continue
            rep["warnings"].append(f"{o}: triangulated with verify frames too (only {len(fitset(o))} fit frames)")
        base_w = to_world(P - ((P - p0) @ n) * n)[0]
        pos = [float(base_w[0]), 0.0, float(base_w[2])]
        info = {"triangulated_from": n_used, "center_height_cm": round(float(((P - p0) @ n) * k), 2)}
        if o in built and built[o].get("qc_ok"):
            glb = built[o]["glb"]
            v, f = sc._load_mesh(glb)
            how = "SAM 3D Objects (generated mesh)"
        else:
            # parametric holder + candle: diameters from mask widths at the triangulated depth, heights from triangulated top points
            hm, wm = masks_o.get("_holder", {}), masks_o.get("_wick", {})
            fr = [s for s in sorted(masks[o])]
            ht = tri_top(hm, fr); ct = tri_top(wm, fr)
            h_hold = float(((ht - p0) @ n) * k) if ht is not None else 5.0
            h_cand = float(((ct - p0) @ n) * k) if ct is not None else h_hold + 15.0
            d_hold = float(np.median([width_cm(mask_to_vggt(hm[s]), s, P) for s in fr if s in hm and hm[s].sum() > 30] or [7.0]))
            d_cand = float(np.median([width_cm(mask_to_vggt(wm[s]), s, P) for s in fr if s in wm and wm[s].sum() > 30] or [2.0]))
            d_cand = min(d_cand, 0.6 * d_hold)
            def colour(mset):
                px_ = [rgb_frame(s)[mset[s]] for s in fr[:4] if s in mset and mset[s].sum() > 30]
                return tuple(np.median(np.vstack(px_), 0)) if px_ else (200, 60, 60)
            holder = trimesh.creation.cylinder(radius=d_hold / 2, height=h_hold, sections=48); holder.apply_translation([0, 0, h_hold / 2])
            candle = trimesh.creation.cylinder(radius=d_cand / 2, height=max(h_cand - 0.5 * h_hold, 1.0), sections=32)
            candle.apply_translation([0, 0, 0.5 * h_hold + max(h_cand - 0.5 * h_hold, 1.0) / 2])
            rot = trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0])
            for mm in (holder, candle):
                mm.apply_transform(rot)
            glb = colored_glb([(holder, colour(hm)), (candle, colour(wm))])
            v, f = sc._load_mesh(glb)
            how = "parametric (holder + candle cylinders, sizes from SAM masks on the VGGT plane scale)"
            info["parametric_cm"] = {"holder_height": round(h_hold, 1), "holder_diameter": round(d_hold, 1), "candle_top_height": round(h_cand, 1), "candle_diameter": round(d_cand, 1)}
            built.setdefault(o, {})["glb"] = glb
        # scale + yaw: fit the silhouettes in the fit frames (IoU against SAM masks only)
        fs = fitset(o) or sorted(masks[o])
        horiz = float(np.mean([np.ptp(v[:, 0]), np.ptp(v[:, 2])]))
        s0 = float(np.median([width_cm(masks[o][s], s, P) for s in fs])) / max(horiz, 1e-9)
        if how.startswith("parametric"):
            s0 = 1.0  # already in cm
        def score(sc_, yaw):
            t = {"position": pos, "rotation_deg": [0, yaw, 0], "scale": [sc_] * 3}
            wv = sc.transform_vertices(v, t)
            return float(np.mean([iou(fast_sil(wv, f, cams[s]), masks[o][s]) for s in fs]))
        yaws = [0.0] if how.startswith("parametric") else list(range(0, 360, 15))
        scales = [1.0] if how.startswith("parametric") else list(s0 * np.linspace(0.6, 1.5, 19))
        if o == "case" and not how.startswith("parametric"):  # yaw from the principal axis of the case's footprint on the plane
            Q = []
            for s in fs:
                ys, xs = np.nonzero(masks[o][s][::2, ::2])
                Pp, ok = plane_hits(vg, s, np.c_[xs * 2, ys * 2].astype(float), n, p0)
                Q.append(to_world(Pp[ok])[:, [0, 2]])
            Q = np.vstack(Q)
            evq = np.linalg.eigh(np.cov((Q - Q.mean(0)).T))[1][:, 1]
            a_plane = np.degrees(np.arctan2(evq[1], evq[0]))
            evm = np.linalg.eigh(np.cov((v[:, [0, 2]] - v[:, [0, 2]].mean(0)).T))[1][:, 1]
            a_mesh = np.degrees(np.arctan2(evm[1], evm[0]))
            base_yaw = (a_mesh - a_plane) % 180  # yaw_matrix rotates x toward -z for positive angles
            yaws = sorted({round((base_yaw + d + f2) % 360, 1) for d in (-10, -5, 0, 5, 10) for f2 in (0, 180)})
            info["principal_axis_deg"] = round(float(a_plane), 1)
        best_sc = max(((score(a, b), a, b) for a in scales for b in yaws), key=lambda x: x[0])
        if not how.startswith("parametric"):
            a, b = best_sc[1], best_sc[2]
            best_sc = max(((score(a * f1, (b + d) % 360), a * f1, (b + d) % 360) for f1 in np.linspace(0.95, 1.05, 5) for d in (-6, -3, 0, 3, 6)), key=lambda x: x[0])
        fit_iou, scl, yaw = best_sc
        t = {"position": pos, "rotation_deg": [0.0, float(yaw), 0.0], "scale": [float(scl)] * 3}
        it = Placed(o, NAMES[o], built[o]["glb"], v, f, t, views.get(o) or "")
        ext = it.world().max(0) - it.world().min(0)
        it.size_cm = [round(float(x), 2) for x in ext]
        items.append(it)
        place[o] = {**info, "built": how, "position_cm": [round(x, 2) for x in pos], "yaw_deg": round(float(yaw), 1), "scale": round(float(scl), 4),
                    "fit_frames": fs, "fit_iou": round(fit_iou, 3), "size_cm": it.size_cm}
    rep["placement"] = {"method": "mask-centroid rays triangulated over the fit frames (VGGT cameras), dropped onto the fitted table plane; scale and yaw "
                                  "fitted to the SAM silhouettes in the fit frames; the case yaw seeded by its footprint's principal axis",
                        "objects": place}
    jdump(out / "desk_report.json", rep)
    if until == 4:
        return rep

    # -- verify on the held-out frames with the VGGT cameras
    vf = [s for s in verify_frames if any(s in masks[it.id] for it in items)]
    rgbs = {s: vg.img[vg.idx[s]] for s in vf}
    vcams = {s: cams[s] for s in vf}
    vmasks = {it.id: {s: masks[it.id][s] for s in vf if s in masks[it.id]} for it in items}
    size_of = {s: (VGGT_SIZE, VGGT_SIZE) for s in vf}
    vrep = {"warnings": []}
    res = verify_loop(items, vcams, vmasks, size_of, rgbs, vf, out / "verify", "vggt", vrep, no_spark=args.no_spark)
    tbl_item = Placed("table", "table top", b"", *table_mesh_vf(L * k, W * k, r * k, args.table_thick_cm), {"position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}, "")
    tbl_iou = object_ious([tbl_item], vcams, {"table": {s: tmask[s] for s in vf if s in tmask}}, size_of)
    res["table_iou"] = tbl_iou["table"]
    res["table_mean_iou"] = round(mean_iou(tbl_iou), 3)
    res["warnings"] = vrep["warnings"]
    res.pop("before_transforms", None)
    rep["verify"] = res
    jdump(out / "desk_report.json", rep)
    if until == 5:
        return rep

    # -- export
    export(out, rep, items, L * k, W * k, r * k, args, table, coin_w)
    rep["total_wall_s"] = round(time.time() - T0, 1)
    jdump(out / "desk_report.json", rep)
    return rep


def table_mesh_vf(L, W, r, thick):
    m = extrude(stadium_outline(L, W, r), -thick, 0.0)
    return np.asarray(m.vertices), np.asarray(m.faces)


def export(out: Path, rep: dict, items: list, L: float, W: float, r: float, args, table: dict, coin_w) -> None:
    od = out / "objects"
    od.mkdir(exist_ok=True)
    tmesh = extrude(stadium_outline(L, W, r), -args.table_thick_cm, 0.0)
    (od / "table").mkdir(exist_ok=True)
    (od / "table" / "model.glb").write_bytes(colored_glb([(tmesh, table["colour_rgb"])]))
    coin_r, coin_t = args.coin_mm / 20.0, 0.15
    coin = trimesh.creation.cylinder(radius=coin_r, height=coin_t, sections=48)
    coin.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    coin.apply_translation([0, coin_t / 2, 0])
    (od / "coin").mkdir(exist_ok=True)
    (od / "coin" / "model.glb").write_bytes(colored_glb([(coin, (196, 160, 70))]))
    objs = [{"id": "table", "name": "table top", "mesh": "objects/table/model.glb", "role": "parent", "printable": False,
             "reconstruction_model": {"name": "stadium fitted to fused SAM 3.1 table masks on the VGGT plane", "vendor": "WorldKit", "is_meta_model": False},
             "transform": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}, "size_cm": [round(L, 1), args.table_thick_cm, round(W, 1)],
             "note": f"tabletop only (no legs); thickness {args.table_thick_cm} cm {table['thickness_source']}; colour = median of the table masks"},
            {"id": "coin", "name": f"reference: coin {args.coin_mm} mm", "mesh": "objects/coin/model.glb", "role": "child", "printable": False, "is_scale_reference": True,
             "reconstruction_model": {"name": "known geometry (not reconstructed)", "vendor": "WorldKit", "is_meta_model": False},
             "transform": {"position": [float(coin_w[0]), 0.0, float(coin_w[2])], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]},
             "size_cm": [args.coin_mm / 10, coin_t, args.coin_mm / 10], "note": "thickness 1.5 mm assumed"}]
    parts = [trimesh.Trimesh(to_print_space(np.asarray(tmesh.vertices)), tmesh.faces), trimesh.Trimesh(to_print_space(sc.transform_vertices(np.asarray(coin.vertices), objs[1]["transform"])), coin.faces)]
    for it in items:
        d = od / it.id
        d.mkdir(exist_ok=True)
        (d / "model.glb").write_bytes(it.glb)
        local = trimesh.Trimesh(to_print_space(np.asarray(it.verts) * it.transform["scale"][0]), it.faces, process=True)
        if not local.is_watertight:
            local, _ = printprep.make_watertight(local)
        local.apply_translation([0, 0, -local.bounds[0][2]])
        local.export(d / "model_print.stl")
        how = rep["placement"]["objects"][it.id]["built"]
        objs.append({"id": it.id, "name": it.name, "mesh": f"objects/{it.id}/model.glb", "role": "child", "printable": True, "stl": f"objects/{it.id}/model_print.stl",
                     "reconstruction_model": {"name": "SAM 3D Objects" if how.startswith("SAM") else "parametric", "vendor": "Meta" if how.startswith("SAM") else "WorldKit",
                                              "is_meta_model": how.startswith("SAM")},
                     "built": how, "transform": it.transform, "size_cm": it.size_cm, "size_mm": [round(x * 10, 1) for x in it.size_cm], "is_scale_reference": False})
        w_ = trimesh.Trimesh(to_print_space(it.world()), it.faces, process=True)
        if not w_.is_watertight:
            w_, _ = printprep.make_watertight(w_)
        parts.append(w_)
    try:
        fused = trimesh.boolean.union(parts, engine="manifold")
    except Exception as exc:  # noqa: BLE001
        rep["warnings"].append(f"diorama union failed ({exc}); parts concatenated")
        fused = trimesh.util.concatenate(parts)
    fused.apply_translation([0, 0, -fused.bounds[0][2]])
    ratio = min(1.0, BED_MM * 0.98 / float(max(fused.extents[:2])))
    fused.apply_scale(ratio)
    fused.export(out / "diorama.stl")
    dio = {"stl": "diorama.stl", "scale_ratio": round(ratio, 4), "scale_text": f"1:{1 / ratio:.1f}", "size_mm": [round(float(x), 1) for x in fused.extents],
           "bed_mm": [BED_MM] * 3, "watertight": bool(fused.is_watertight), "objects": [o["id"] for o in objs], "warnings": [] if fused.is_watertight else ["the union is not watertight"]}
    jdump(out / "diorama.json", dio)
    v = rep["verify"]
    scene = {"worldkit_scene": 1, "mode": "map", "source_video": rep["frames"]["video"], "units": "centimetres on the table plane (Y up, origin at the table centre)",
             "cm_per_scene_unit": 1.0,
             "scale_source": {"text": "scale source: " + table["scale_source"], "kind": "coin", "name": "coin", "size_mm": args.coin_mm, "object_id": "coin"},
             "layout_method": rep["placement"]["method"], "objects": objs,
             "verify": {"mean_iou_before": v["mean_iou_before"], "mean_iou_after": v["mean_iou_after"], "images": v["images"], "cameras": "VGGT, held-out frames"},
             "diorama": dio, "skipped": [{"name": "silver incense burner", "reason": "not part of this scene build (shiny metal and mirror panels)"}]}
    jdump(out / "scene.json", scene)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--coin-mm", type=float, default=21.5, help="coin diameter (default: Saudi 25 halalas preset, looked up)")
    ap.add_argument("--table-cm", type=float, nargs=2, metavar=("LENGTH", "WIDTH"), help="measured table top size; scales the scene instead of the coin")
    ap.add_argument("--table-thick-cm", type=float, default=None)
    ap.add_argument("--per-segment", type=int, default=5, help="SAM frames per third of the video")
    ap.add_argument("--until", choices=STAGES, default="export")
    ap.add_argument("--no-spark", action="store_true")
    ap.add_argument("--no-gpu", action="store_true")
    a = ap.parse_args(argv)
    a.table_thick_given = a.table_thick_cm is not None
    if a.table_thick_cm is None:
        a.table_thick_cm = 2.0
    rep = run(a)
    print(json.dumps({k: rep[k] for k in rep if k in ("frames", "vggt", "table", "objects", "placement", "verify")}, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
