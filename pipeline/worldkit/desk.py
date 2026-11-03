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

from . import appearance, carddetect, coindetect, printprep, refscale
from . import scene as sc
from .frames import _laplacian_sharpness, extract_all_frames
from .mapmode import Placed, cached_mask, crop_rgba, mean_iou, modal_metered, object_ious, object_qc, render_overlay, to_print_space, verify_loop
from .vggt_cams import VGGT_SIZE, PinholeCam, mask_to_vggt, ransac_plane, run_vggt, to_vggt_px

STAGES = ["frames", "vggt", "table", "objects", "place", "verify", "export"]
CONCEPTS = {"table": "white marble table top", "mug": "striped mug", "case": "black case", "holder": "red candle holder", "candle": "white candle",
            "burner": "incense burner", "bottle": "water bottle", "legs": "gold metal table frame"}
NAMES = {"mug": "striped mug", "case": "black case", "candle": "candle in red holder", "burner": "incense burner (bokhoor stand)", "bottle": "water bottle"}
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


def refine_coin(rgb: np.ndarray, m: np.ndarray) -> np.ndarray:
    """The colour detector keeps only the coin's bright core (its dark rim is lost, ~15-20% of the diameter at ~22 px). Re-segment at
    half intensity between the coin's centre and the table around it (the unbiased edge position), keep the piece under the centre,
    and open it with a 3x3 kernel so thin marble veins touching the coin are dropped."""
    ys, xs = np.nonzero(m)
    cy, cx = int(ys.mean()), int(xs.mean())
    r = int(max(np.ptp(xs), np.ptp(ys), 4))
    y0, y1, x0, x1 = max(cy - 2 * r, 0), min(cy + 2 * r + 1, rgb.shape[0]), max(cx - 2 * r, 0), min(cx + 2 * r + 1, rgb.shape[1])
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(float)[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rr = np.hypot(yy - cy, xx - cx)
    inner, bg = np.median(g[rr < 0.3 * r]), np.median(g[(rr > 1.3 * r) & (rr < 2 * r)])
    half = ((g < (inner + bg) / 2) if inner < bg else (g > (inner + bg) / 2)).astype(np.uint8)
    half = cv2.morphologyEx(half, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    _, lab = cv2.connectedComponents(half)
    c = lab[cy - y0, cx - x0]
    out = np.zeros_like(m, bool)
    if c == 0 or abs(inner - bg) < 25:
        return m
    out[y0:y1, x0:x1] = lab == c
    return out


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
    frames = extract_all_frames(Path(args.video), out / "frames", fps=args.fps)
    stems = [p.stem for p in frames]
    path_of = {p.stem: p for p in frames}
    rgb_frame = lambda s: np.array(Image.open(path_of[s]).convert("RGB"))  # noqa: E731
    w, h = Image.open(frames[0]).size
    sharp = {p.stem: float(_laplacian_sharpness(p)) for p in frames}
    coin_px = {}  # reference detections: the gold coin by colour, or the card by its rectangle (worldkit.carddetect)
    for p in frames:
        rgb_ = np.array(Image.open(p).convert("RGB"))
        m = coindetect.find_gold_coin(rgb_) if args.ref == "coin" else carddetect.find_card(rgb_)
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
    rep["frames"] = {"video": Path(args.video).name, "n_frames": len(stems), "fps": args.fps, "video_seconds": round(len(stems) / args.fps), "reference": args.ref, "reference_detector_frames": sorted(coin_px),
                     "fit_frames": fit_frames, "verify_frames": verify_frames, "segments": [[stems[i[0]], stems[i[-1]]] for i in seg],
                     "selection": f"per third of the video: sharp frames with the {args.ref} visible, >= 3 frames apart; fit and verify frames disjoint"}
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

    if args.ref == "coin":
    # coin diameter on the plane (contour of the colour-detected coin, projected): only coins on the table, clustered
        coin_obs = []
        for s, m in coin_px.items():
            m = refine_coin(rgb_frame(s), m)
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
        coin_uv = np.median([o["uv"] for o in good], 0)
    
        ref_uv, ref_yaw_vec, k_ref = coin_uv, None, k_coin
        ref_info = {"kind": "coin", "frames": len(good), "frames_detected": len(coin_obs), "cm_per_unit_plane": k_coin, "cm_per_unit_depth": k_depth,
                    "plane_vs_depth_disagreement_pct": round(100 * (k_depth - k_coin) / k_coin, 1), "relative_spread": round(spread_coin, 3),
                    "median_px": round(float(np.median([o["px"] for o in good])), 1)}
    else:
        # the card: its 4 corners (refscale.card_quad on the rectangle detection) projected onto the table plane with the VGGT camera;
        # a detection counts only if it lies on the table, is a rectangle there (opposite sides within 6%, corners within 6 deg of 90)
        # and has the ID-1 aspect (85.6/54 = 1.585, within 6%): an occluded or wrong rectangle fails these tests
        card_obs = []
        for s_, m in coin_px.items():
            q = refscale.card_quad(m)
            if q is None:
                continue
            vx, _ = to_vggt_px(q + 0.5, (w, h))
            Pp, ok = plane_hits(vg, s_, vx, n, p0)
            if not ok.all():
                continue
            Q = uv(Pp)
            sides = [float(np.linalg.norm(Q[i] - Q[(i + 1) % 4])) for i in range(4)]
            a1, a2 = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
            opp = max(abs(sides[0] - sides[2]) / a1, abs(sides[1] - sides[3]) / a2)
            angs = []
            for i in range(4):
                u1, u2 = Q[(i + 1) % 4] - Q[i], Q[i - 1] - Q[i]
                angs.append(abs(90 - np.degrees(np.arccos(np.clip(u1 @ u2 / np.linalg.norm(u1) / np.linalg.norm(u2), -1, 1)))))
            Lu, Su = max(a1, a2), min(a1, a2)
            long_vec = (Q[1] - Q[0]) if a1 >= a2 else (Q[2] - Q[1])
            cen = Q.mean(0)
            qc = loc(cen[None], cx, cy, ang)[0]
            card_obs.append({"frame": s_, "long_u": Lu, "short_u": Su, "aspect": Lu / Su, "opp_side_diff": opp, "max_corner_dev_deg": float(max(angs)),
                             "uv": cen.tolist(), "long_vec": (long_vec / np.linalg.norm(long_vec)).tolist(), "on_table": bool(in_rounded_rect(qc[0], qc[1], L, W, r))})
        card_l, card_s = args.card_mm[0] / 10, args.card_mm[1] / 10
        good = [o for o in card_obs if o["on_table"] and abs(o["aspect"] / (card_l / card_s) - 1) < 0.06 and o["opp_side_diff"] < 0.06 and o["max_corner_dev_deg"] < 6]
        if good:
            med = np.median([o["uv"] for o in good], 0)
            dist = np.array([np.linalg.norm(np.array(o["uv"]) - med) for o in good])
            good = [o for o, d in zip(good, dist) if d < max(3 * np.median(dist), 0.02 * L)]
        if len(good) < 3:
            raise SystemExit(f"the card passed the rectangle checks in only {len(good)} frames (need 3); detections: {len(card_obs)}")
        per = [(card_l / o["long_u"] + card_s / o["short_u"]) / 2 for o in good]
        k_card = float(np.median(per))
        spread_card = float(np.median(np.abs(np.array(per) - k_card)) / k_card)
        lv = np.array([o["long_vec"] for o in good])
        lv = lv * np.sign(lv @ lv[0])[:, None]
        ref_uv, ref_yaw_vec, k_ref = np.median([o["uv"] for o in good], 0), lv.mean(0), k_card
        ref_info = {"kind": "card", "frames": len(good), "frames_detected": len(coin_px), "frames_quad": len(card_obs), "cm_per_unit_plane": k_card,
                    "relative_spread": round(spread_card, 3), "aspect_median": round(float(np.median([o["aspect"] for o in good])), 3),
                    "rejected": [{k2: (round(v2, 3) if isinstance(v2, float) else v2) for k2, v2 in o.items() if k2 in ("frame", "aspect", "opp_side_diff", "max_corner_dev_deg", "on_table")} for o in card_obs if o not in good][:20]}
    if args.table_cm:
        k = args.table_cm[0] / L
        scale_src = f"table length {args.table_cm[0]} cm (user-measured)"
    else:
        k = k_ref
        scale_src = (f"coin {args.coin_mm} mm, measured on the table plane in {ref_info['frames']} frames (VGGT cameras)" if args.ref == "coin" else
                     f"card {args.card_mm[0]} x {args.card_mm[1]} mm, 4 corners measured on the table plane in {ref_info['frames']} frames (VGGT cameras)")

    # world frame: origin = table centre, X = table long axis, Y = up (plane normal), Z = X x Y; centimetres
    o_w = p0 + cx * e1 + cy * e2
    ex = np.cos(ang) * e1 + np.sin(ang) * e2
    ez = np.cross(ex, n)
    B = np.c_[ex, n, ez]
    to_world = lambda P: ((np.atleast_2d(P) - o_w) @ B) * k  # noqa: E731
    cams = {s: PinholeCam(vg.R(s) @ B / k, vg.R(s) @ o_w + vg.t(s), *vg.K[vg.idx[s]][[0, 1, 0, 1], [0, 1, 2, 2]]) for s in stems}
    ref_w = to_world(p0 + ref_uv[0] * e1 + ref_uv[1] * e2)[0]
    ref_yaw = 0.0
    if ref_yaw_vec is not None:  # card long side in the world: yaw for the known-geometry card (x axis = long side)
        d3 = ref_yaw_vec[0] * e1 + ref_yaw_vec[1] * e2
        dw = d3 @ B
        ref_yaw = float(np.degrees(np.arctan2(-dw[2], dw[0])))  # yaw_matrix turns +x toward -z for positive angles
    ref_info["position_cm"] = [round(float(v), 2) for v in ref_w]
    ref_info["yaw_deg"] = round(ref_yaw, 1)
    tcol = np.median(np.vstack([rgb_frame(s)[cached_mask(cache / "masks", path_of[s], CONCEPTS["table"])] for s in list(tmask)[:6]]), 0)  # median colour of the table masks
    table = {"plane_inlier_share": round(inl, 3), "masks": len(tmask), "fused_cells": int(filled.sum()), "stadium_fit_iou": round(stadium_iou, 3),
             "length_cm": round(L * k, 1), "width_cm": round(W * k, 1), "corner_radius_cm": round(r * k, 1), "is_full_stadium": bool(r > 0.95 * W / 2),
             "thickness_cm": args.table_thick_cm, "thickness_source": "user-measured" if args.table_thick_given else "estimated (not measured)",
             "colour_rgb": [int(c) for c in tcol], "scale_source": scale_src,
             "reference": ref_info, "length_ref_cm": round(L * k_ref, 1), "width_ref_cm": round(W * k_ref, 1)}
    if args.ref == "coin":
        table["coin"] = ref_info  # older key, kept for the first desk run's report
    if args.table_cm:
        table["reference_vs_table_disagreement_pct"] = round(100 * (k_ref - k) / k, 1)
    legs_mesh = None
    if args.legs:
        table["legs"], legs_mesh = build_legs(vg, sam_frames, path_of, cache, n, p0, to_world, k, L * k, W * k, r * k, args.table_thick_cm, rgb_frame, args.legs_frames)
    rep["table"] = table
    jdump(out / "desk_report.json", rep)
    np.save(out / "table_fused.npy", filled)
    Image.fromarray((filled * 255).astype(np.uint8)).save(out / "table_fused.png")
    if until == 2:
        return rep

    # -- objects: masks, SAM 3D builds, QC, parametric candle fallback
    OBJS = list(args.objects)
    masks = {o: {} for o in OBJS}
    masks_o = {o: {} for o in OBJS}
    for s in sam_frames:
        for o in [x for x in OBJS if x != "candle"]:
            m = cached_mask(cache / "masks", path_of[s], CONCEPTS[o])
            if m is not None and m.sum() >= 60:
                masks_o[o][s] = m
        if "candle" not in OBJS:
            continue
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
    for o in OBJS:
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
        elif o == "burner":
            glb, prof = revolved_object(vg, masks[o], masks_o[o], sorted(masks[o]), P, n, p0, k, rgb_frame, w, h)
            if glb is None:
                rep["warnings"].append(f"burner: revolved profile failed ({prof})")
                continue
            v, f = sc._load_mesh(glb)
            how = "revolved profile, estimated (radius vs height from the SAM silhouettes in several views, VGGT scale)"
            info["profile"] = prof
            built.setdefault(o, {})["glb"] = glb
        elif o != "candle":
            rep["warnings"].append(f"{o}: no usable mesh (SAM 3D failed or mesh QC failed: {built.get(o, {}).get('qc')}); skipped")
            continue
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
        if how.startswith(("parametric", "revolved")):
            s0 = 1.0  # already in cm
        def score(sc_, yaw):
            t = {"position": pos, "rotation_deg": [0, yaw, 0], "scale": [sc_] * 3}
            wv = sc.transform_vertices(v, t)
            return float(np.mean([iou(fast_sil(wv, f, cams[s]), masks[o][s]) for s in fs]))
        fixed = how.startswith(("parametric", "revolved"))
        yaws = [0.0] if fixed else list(range(0, 360, 15))
        scales = [1.0] if fixed else list(s0 * np.linspace(0.6, 1.5, 19))
        if o == "case" and not fixed:  # yaw from the principal axis of the case's footprint on the plane
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
        if not fixed:
            a, b = best_sc[1], best_sc[2]
            best_sc = max(((score(a * f1, (b + d) % 360), a * f1, (b + d) % 360) for f1 in np.linspace(0.95, 1.05, 5) for d in (-6, -3, 0, 3, 6)), key=lambda x: x[0])
        fit_iou, scl, yaw = best_sc
        if o == "burner" and how.startswith("SAM") and fit_iou < 0.5:  # a generated burner that does not match its silhouettes: revolve instead
            rep["warnings"].append(f"burner: SAM 3D mesh fit IoU {fit_iou:.2f} < 0.5 on the fit frames; replaced by the revolved profile")
            glb, prof = revolved_object(vg, masks[o], masks_o[o], sorted(masks[o]), P, n, p0, k, rgb_frame, w, h)
            if glb is not None:
                v, f = sc._load_mesh(glb)
                how = "revolved profile, estimated (radius vs height from the SAM silhouettes in several views, VGGT scale)"
                info["profile"] = prof
                built[o]["glb"] = glb
                fit_iou = float(np.mean([iou(fast_sil(sc.transform_vertices(v, {"position": pos, "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}), f, cams[s_]), masks[o][s_]) for s_ in fs]))
                scl, yaw = 1.0, 0.0
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
    res = verify_loop(items, vcams, vmasks, size_of, rgbs, vf, out / "verify", "vggt", vrep, no_spark=args.no_spark, max_rounds=args.spark_rounds)
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
    export(out, rep, items, L * k, W * k, r * k, args, table, ref_w, ref_yaw, legs_mesh)
    rep["total_wall_s"] = round(time.time() - T0, 1)
    jdump(out / "desk_report.json", rep)
    return rep


# ---------------------------------------------------------------- table base and revolved objects
def rr_sdf(x, z, L, W, r):
    """Signed distance to a centred rounded rectangle (negative inside)."""
    r = min(r, W / 2, L / 2)
    qx, qz = np.abs(x) - (L / 2 - r), np.abs(z) - (W / 2 - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qz, 0)) + np.minimum(np.maximum(qx, qz), 0) - r


def ring(L, W, r, width, y0, y1) -> trimesh.Trimesh:
    outer = extrude(stadium_outline(L, W, r), y0, y1)
    inner = extrude(stadium_outline(L - 2 * width, W - 2 * width, max(r - width, 0.5)), y0 - 0.1, y1 + 0.1)
    return trimesh.boolean.difference([outer, inner], engine="manifold")


def build_legs(vg, frames_, path_of, cache, n, p0, to_world, k, L, W, r, thick, rgb_frame, n_frames):
    """The gold table base, from the frames: floor plane = the densest height band of VGGT points well below the top; SAM 3.1 masks of the
    frame give 3D points (VGGT) in the world; a lower ring is fitted to the points near the floor (rounded rectangle, concentric with the
    top), posts are placed at clusters of points between the rings, and a rim band under the top if points are there. Ring and post
    thicknesses are estimates. Returns (info, mesh in world cm, Y up) or (info with an error, None)."""
    from scipy.optimize import minimize

    stems = list(vg.idx)
    Hs, Ps = [], []
    for s in stems[::max(1, len(stems) // 40)]:
        i = vg.idx[s]
        sel = vg.valid & (vg.conf[i] >= np.percentile(vg.conf[i][vg.valid], 50))  # >=: VGGT confidence is often saturated at 1.0
        P = vg.P[i][sel][::23]
        Ps.append(P)
        Hs.append(((P - p0) @ n) * k)
    hh, PP = np.concatenate(Hs), np.vstack(Ps)
    low = hh < -10
    if low.sum() < 500:
        return {"error": f"only {int(low.sum())} confident points below the top: no floor"}, None
    hist, edges = np.histogram(hh[low], bins=np.arange(np.percentile(hh[low], 1), -10, 0.5))
    fl = float(edges[np.argmax(hist)] + 0.25)
    F = to_world(PP[np.abs(hh - fl) < 2.0])
    nf = np.linalg.svd(F - F.mean(0), full_matrices=False)[2][2]
    tilt = float(np.degrees(np.arccos(min(1.0, abs(nf[1])))))
    H = -fl
    fs = frames_[:: max(1, len(frames_) // n_frames)][:n_frames]
    pts, cols = [], []
    for s in fs:
        m = cached_mask(cache / "masks", path_of[s], CONCEPTS["legs"])
        if m is None or m.sum() < 80:
            continue
        cols.append(rgb_frame(s)[m])
        vm = mask_to_vggt(m)
        i = vg.idx[s]
        sel = vm & (vg.conf[i] >= np.percentile(vg.conf[i][vm], 40))
        pts.append(to_world(vg.P[i][sel]))
    if not pts:
        return {"error": "SAM 3.1 found no table frame", "floor_height_cm": round(H, 1)}, None
    Pw = np.vstack(pts)
    # VGGT depth below the table is compressed (the carpet's points stop ~30 cm down), so the floor height comes from the cameras instead:
    # a plane sweep. The lowest frame pixel in each image column is where the base meets the floor; projected onto a horizontal plane at
    # the true floor height, these rays from all views agree on one rounded-rectangle footprint (smallest residual).
    rays_c, rays_d = [], []
    for s in fs:
        m = cached_mask(cache / "masks", path_of[s], CONCEPTS["legs"])
        if m is None or m.sum() < 80:
            continue
        vm = mask_to_vggt(m)
        bottom_valid = np.nonzero(vg.valid.any(1))[0].max()
        for x in range(0, VGGT_SIZE, 2):
            ys_ = np.nonzero(vm[:, x])[0]
            if len(ys_) and ys_.max() < bottom_valid - 2:
                rays_c.append(vg.center(s)); rays_d.append(vg.rays(s, np.array([[x, ys_.max()]], float))[0])
    sweep = []
    if len(rays_d) >= 60:
        Cr, Dr = np.array(rays_c), np.array(rays_d)
        for hcm in np.arange(8, 90, 1.0):
            off = -hcm / k
            den = Dr @ n
            t = (off - (Cr - p0) @ n) / np.where(np.abs(den) > 1e-6, den, 1e-6)
            ok = t > 0
            Xw = to_world(Cr[ok] + Dr[ok] * t[ok, None])
            def cst2(pv, X=Xw):
                L2, W2, r2 = pv
                if not (5 < W2 <= L2 and 0 < r2 <= W2 / 2 + 0.01):
                    return 1e9
                return float(np.median(np.abs(rr_sdf(X[:, 0], X[:, 2], L2, W2, r2))))
            res = minimize(cst2, [L * 0.9, W * 0.9, W * 0.45], method="Nelder-Mead", options={"maxiter": 300})
            sweep.append((float(res.fun) / max(float(res.x[1]), 1e-6), hcm, res.x))  # scale-free: residual / footprint width
        best_sw = min(sweep, key=lambda q: q[0])
        fl_cam = -best_sw[1]
        if best_sw[1] <= sweep[0][1] + 5:  # minimum at (or flat near) the lower end of the range: no floor was found, the sweep is not used
            sweep_note = f"plane sweep inconclusive (residual rises from the lowest height tried, {sweep[0][1]:.0f} cm): the lowest frame pixels sit near the table edge"
            best_sw, fl_cam = None, None
        else:
            sweep_note = None
    else:
        best_sw, fl_cam, sweep_note = None, None, "too few frame-bottom rays for a plane sweep"
    if best_sw is not None:
        fl, H = fl_cam, -fl_cam
    inside = (Pw[:, 1] < -thick - 0.3) & (rr_sdf(Pw[:, 0], Pw[:, 2], L, W, r) < 8.0)
    Pw = Pw[inside]
    # height bands from the (compressed) depth points, relative to their own lowest frame points; used only to find post positions
    dlo = float(np.percentile(Pw[:, 1], 2)) if len(Pw) else fl
    yrel = (Pw[:, 1] - dlo) / max(-thick - dlo, 1e-6)
    lowb, midb, upb = Pw[yrel < 0.3], Pw[(yrel >= 0.3) & (yrel < 0.85)], Pw[yrel >= 0.85]
    colour = tuple(int(c) for c in np.median(np.vstack(cols), 0))
    info = {"floor_height_cm": round(-fl, 1), "table_height_cm": round(H, 1), "floor_tilt_vs_top_deg": round(tilt, 1), "frame_masks": len(pts),
            "points": {"low": int(len(lowb)), "mid": int(len(midb)), "upper": int(len(upb))}, "colour_rgb": list(colour),
            "source": "estimated from frames (VGGT points under SAM 3.1 'gold metal table frame' masks)",
            "height_method": "camera plane sweep" if best_sw is not None else "densest band of VGGT depth points below the top (depth below the top looks compressed: likely an underestimate)",
            "sweep_note": sweep_note}
    parts = []
    if len(lowb) >= 60:
        def cst(pv):
            L2, W2, r2 = pv
            if not (5 < W2 <= L2 and 0 < r2 <= W2 / 2 + 0.01):
                return 1e9
            return float(np.median(np.abs(rr_sdf(lowb[:, 0], lowb[:, 2], L2, W2, r2))))
        res = min((minimize(cst, [L * f, W * f, W * f / 2], method="Nelder-Mead", options={"maxiter": 800}) for f in (0.8, 0.9)), key=lambda q: q.fun)
        L2, W2, r2 = res.x
        y_lo, y_hi = np.percentile(lowb[:, 1], [15, 85])
        y_lo, y_hi = float(y_lo), float(max(y_hi, y_lo + 2.0))
        if best_sw is not None:  # the camera plane sweep: footprint and height of the ring at the floor
            L2, W2, r2 = best_sw[2]
            y_lo, y_hi = fl, fl + 3.0
            res = type("R", (), {"fun": best_sw[0]})()
            info["floor_from"] = "camera plane sweep of the frame's lowest pixels (VGGT depth below the top is compressed)"
            info["sweep_relative_residual"] = [[round(q[1]), round(q[0], 4)] for q in sweep[::3]]
        parts.append(ring(L2, W2, r2, 2.0, y_lo, y_hi))
        info["lower_ring_cm"] = {"length": round(L2, 1), "width": round(W2, 1), "radius": round(r2, 1), "y": [round(y_lo, 1), round(y_hi, 1)],
                                 "median_fit_residual": round(res.fun, 2), "ring_width": "2.0 (estimated)"}
        # posts: clusters of mid-height points near the ring line
        if len(midb) >= 40:
            near = midb[np.abs(rr_sdf(midb[:, 0], midb[:, 2], L2, W2, r2)) < 8.0]
            cell = 3.0
            if len(near):
                gx = np.floor((near[:, 0] - near[:, 0].min()) / cell).astype(int)
                gz = np.floor((near[:, 2] - near[:, 2].min()) / cell).astype(int)
                occ = np.zeros((gz.max() + 1, gx.max() + 1), np.int32)
                np.add.at(occ, (gz, gx), 1)
                nlab, lab, st, cen = cv2.connectedComponentsWithStats((occ >= 2).astype(np.uint8), 8)
                posts = []
                for j in range(1, nlab):
                    sel = lab[gz, gx] == j
                    if sel.sum() >= max(15, 0.03 * len(near)):
                        posts.append(near[sel][:, [0, 2]].mean(0))
                for (px_, pz_) in posts:
                    c = trimesh.creation.cylinder(radius=1.2, height=max(-thick - y_hi, 2.0) + 0.6, sections=24)
                    c.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
                    c.apply_translation([px_, (y_hi - 0.3 + (-thick + 0.3)) / 2, pz_])
                    parts.append(c)
                info["posts"] = [[round(float(a), 1), round(float(b), 1)] for a, b in posts]
                info["post_radius"] = "1.2 cm (estimated)"
    if len(upb) >= 40:
        parts.append(ring(L - 1.0, W - 1.0, max(r - 0.5, 0.5), 1.5, -thick - 2.5, -thick + 0.2))
        info["upper_rim"] = "band 2.5 cm under the top, 1.5 cm wide (estimated)"
    if not parts:
        return {**info, "error": "too few frame points to place a ring or posts"}, None
    mesh = trimesh.boolean.union(parts, engine="manifold") if len(parts) > 1 else parts[0]
    info["watertight"] = bool(mesh.is_watertight)
    return info, (mesh, colour)


def revolved_object(vg, vmasks, omasks, frames_, P, n, p0, k, rgb_frame, w, h):
    """Surface of revolution from silhouettes: for each view, every mask row's width at the depth of the object's vertical axis gives a
    radius, and the ray through the row's centre meets the axis at a height; the median radius per 0.5 cm of height over the views is
    revolved. Estimated, not measured."""
    base = P - ((P - p0) @ n) * n
    prof, used = [], 0
    for s in frames_:
        m = vmasks.get(s)
        if m is None:
            continue
        i = vg.idx[s]
        K = vg.K[i]
        C = vg.center(s)
        ys = np.nonzero(m.any(1))[0]
        if len(ys) < 20:
            continue
        rows = []
        for y in ys[::2]:
            xs = np.nonzero(m[y])[0]
            if len(xs) < 2:
                continue
            xc, wpx = (xs.min() + xs.max()) / 2, xs.max() - xs.min() + 1
            d = vg.rays(s, np.array([[xc, y]], float))[0]
            w0 = C - base
            a_, b_, dd, e = d @ d, d @ n, d @ w0, n @ w0
            den = a_ - b_ * b_
            if abs(den) < 1e-9:
                continue
            t = (a_ * e - b_ * dd) / den
            z = vg.depth_of(s, base + t * n)
            rows.append((t * k, wpx / 2 * z / K[0, 0] * k))
        if len(rows) > 10:
            prof.append(np.array(rows))
            used += 1
    if used < 3:
        return None, f"only {used} usable views"
    allp = np.vstack(prof)
    allp = allp[allp[:, 0] > -1]
    hmax = float(np.percentile(allp[:, 0], 98))
    bins = np.arange(0, hmax, 0.5)
    rad = np.array([np.median(allp[(allp[:, 0] >= b) & (allp[:, 0] < b + 0.5), 1]) if ((allp[:, 0] >= b) & (allp[:, 0] < b + 0.5)).sum() >= 3 else np.nan for b in bins])
    ok = ~np.isnan(rad)
    if ok.sum() < 5:
        return None, "profile too sparse"
    rad = np.interp(bins, bins[ok], rad[ok])
    rad = np.convolve(np.pad(rad, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    line = np.r_[[[0.0, 0.0]], np.c_[rad, bins + 0.25], [[0.0, hmax]]]
    mesh = trimesh.creation.revolve(line, sections=48)
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    if mesh.volume < 0:
        mesh.invert()
    col = np.median(np.vstack([rgb_frame(s_)[omasks[s_]] for s_ in frames_[:4] if s_ in omasks]), 0)
    return colored_glb([(mesh, tuple(col))]), {"views": used, "height_cm": round(hmax, 1), "max_diameter_cm": round(float(2 * rad.max()), 1),
                                                "watertight": bool(mesh.is_watertight)}


def table_mesh_vf(L, W, r, thick):
    m = extrude(stadium_outline(L, W, r), -thick, 0.0)
    return np.asarray(m.vertices), np.asarray(m.faces)


def export(out: Path, rep: dict, items: list, L: float, W: float, r: float, args, table: dict, ref_w, ref_yaw: float = 0.0, legs=None) -> None:
    od = out / "objects"
    od.mkdir(exist_ok=True)
    tmesh = extrude(stadium_outline(L, W, r), -args.table_thick_cm, 0.0)
    (od / "table").mkdir(exist_ok=True)
    tparts = [(tmesh, table["colour_rgb"])] + ([legs] if legs is not None else [])
    (od / "table" / "model.glb").write_bytes(colored_glb(tparts))
    if args.ref == "coin":
        ref_t = 0.15
        coin = trimesh.creation.cylinder(radius=args.coin_mm / 20.0, height=ref_t, sections=48)
        coin.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        ref_col, ref_name, ref_size, ref_note = (196, 160, 70), f"reference: coin {args.coin_mm} mm", [args.coin_mm / 10, ref_t, args.coin_mm / 10], "thickness 1.5 mm assumed"
    else:
        ref_t = 0.076
        coin = trimesh.creation.box(extents=[args.card_mm[0] / 10, ref_t, args.card_mm[1] / 10])
        ref_col, ref_name = (40, 120, 140), f"reference: card {args.card_mm[0]:g} x {args.card_mm[1]:g} mm"
        ref_size, ref_note = [args.card_mm[0] / 10, ref_t, args.card_mm[1] / 10], "ID-1 card, 0.76 mm thick (standard)"
    coin.apply_translation([0, ref_t / 2, 0])
    (od / "reference").mkdir(exist_ok=True)
    (od / "reference" / "model.glb").write_bytes(colored_glb([(coin, ref_col)]))
    objs = [{"id": "table", "name": "table top", "mesh": "objects/table/model.glb", "role": "parent", "printable": False,
             "reconstruction_model": {"name": "stadium fitted to fused SAM 3.1 table masks on the VGGT plane", "vendor": "WorldKit", "is_meta_model": False},
             "transform": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]},
             "size_cm": [round(L, 1), round(table["legs"]["table_height_cm"], 1) if legs is not None else args.table_thick_cm, round(W, 1)],
             "note": (f"top: stadium from the fused masks, thickness {args.table_thick_cm} cm {table['thickness_source']}; "
                      + ("base (rings, posts) estimated from frames" if legs is not None else "tabletop only (no legs)") + "; colours = median of the masks")},
            {"id": "reference", "name": ref_name, "mesh": "objects/reference/model.glb", "role": "child", "printable": False, "is_scale_reference": True,
             "reconstruction_model": {"name": "known geometry (not reconstructed)", "vendor": "WorldKit", "is_meta_model": False},
             "transform": {"position": [float(ref_w[0]), 0.0, float(ref_w[2])], "rotation_deg": [0, float(ref_yaw), 0], "scale": [1, 1, 1]},
             "size_cm": ref_size, "note": ref_note}]
    if legs is not None:
        objs[0]["name"] = "table (top and base)"
    parts = [trimesh.Trimesh(to_print_space(np.asarray(tmesh.vertices)), tmesh.faces), trimesh.Trimesh(to_print_space(sc.transform_vertices(np.asarray(coin.vertices), objs[1]["transform"])), coin.faces)]
    if legs is not None:
        parts.append(trimesh.Trimesh(to_print_space(np.asarray(legs[0].vertices)), legs[0].faces))
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
    ratio = min(1.0, BED_MM * 0.98 / float(max(fused.extents)))
    fused.apply_scale(ratio)
    fused.export(out / "diorama.stl")
    dio = {"stl": "diorama.stl", "scale_ratio": round(ratio, 4), "scale_text": f"1:{1 / ratio:.1f}", "size_mm": [round(float(x), 1) for x in fused.extents],
           "bed_mm": [BED_MM] * 3, "watertight": bool(fused.is_watertight), "objects": [o["id"] for o in objs], "warnings": [] if fused.is_watertight else ["the union is not watertight"]}
    jdump(out / "diorama.json", dio)
    v = rep["verify"]
    scene = {"worldkit_scene": 1, "mode": "map", "source_video": rep["frames"]["video"], "source_frames": rep["frames"]["n_frames"],
             "video_seconds": rep["frames"].get("video_seconds"), "units": "centimetres on the table plane (Y up, origin at the table centre)",
             "cm_per_scene_unit": 1.0,
             "scale_source": {"text": "scale source: " + table["scale_source"], "kind": args.ref, "name": args.ref,
                              "size_mm": args.coin_mm if args.ref == "coin" else list(args.card_mm), "object_id": "reference"},
             "layout_method": rep["placement"]["method"], "objects": objs,
             "verify": {"mean_iou_before": v["mean_iou_before"], "mean_iou_after": v["mean_iou_after"], "images": v["images"], "cameras": "VGGT, held-out frames"},
             "diorama": dio, "skipped": ([] if "burner" in args.objects else [{"name": "silver incense burner", "reason": "not part of this scene build"}])
                                        + [{"name": w_.split(":")[0], "reason": w_.split(":", 1)[1].strip()} for w_ in rep["warnings"] if w_.endswith("skipped")]}
    jdump(out / "scene.json", scene)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--coin-mm", type=float, default=21.5, help="coin diameter (default: Saudi 25 halalas preset, looked up)")
    ap.add_argument("--table-cm", type=float, nargs=2, metavar=("LENGTH", "WIDTH"), help="measured table top size; scales the scene instead of the coin")
    ap.add_argument("--table-thick-cm", type=float, default=None)
    ap.add_argument("--per-segment", type=int, default=5, help="SAM frames per third of the video")
    ap.add_argument("--fps", type=float, default=2.0, help="frame rate for extraction and VGGT (1.0 keeps a 2-minute video under ~120 frames)")
    ap.add_argument("--ref", choices=["coin", "card"], default="coin", help="scale reference on the table")
    ap.add_argument("--card-mm", type=float, nargs=2, default=[85.6, 54.0], metavar=("LONG", "SHORT"))
    ap.add_argument("--objects", nargs="+", default=["mug", "case", "candle"], choices=["mug", "case", "candle", "burner", "bottle"])
    ap.add_argument("--legs", action="store_true", help="also build the table base (floor plane, rings, posts) from SAM masks and VGGT points")
    ap.add_argument("--legs-frames", type=int, default=8)
    ap.add_argument("--concept", action="append", default=[], metavar="KEY=PHRASE", help="SAM 3.1 phrase for an object, e.g. burner='silver lantern'")
    ap.add_argument("--until", choices=STAGES, default="export")
    ap.add_argument("--no-spark", action="store_true")
    ap.add_argument("--spark-rounds", type=int, default=2, help="Muse Spark correction rounds in verify (each kept only if IoU improves)")
    ap.add_argument("--no-gpu", action="store_true")
    a = ap.parse_args(argv)
    for kv in a.concept:
        key, phrase = kv.split("=", 1)
        CONCEPTS[key.strip()] = phrase.strip()
    a.table_thick_given = a.table_thick_cm is not None
    if a.table_thick_cm is None:
        a.table_thick_cm = 2.0
    rep = run(a)
    print(json.dumps({k: rep[k] for k in rep if k in ("frames", "vggt", "table", "objects", "placement", "verify")}, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
