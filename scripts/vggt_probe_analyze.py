"""Analyse vggt_out.npz (experiment): camera plausibility, confidence/noise, plane fits, room size relative to the mug reference."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
HERE = Path(__file__).resolve().parents[1] / "pipeline" / "runs" / "vggt_probe"
z = np.load(HERE / "vggt_out.npz")
E, K, conf, P, imgs = z["extrinsic"], z["intrinsic"], z["depth_conf"].astype(np.float32), z["world_points"].astype(np.float32), z["images"]
N, H, W = P.shape[:3]
print("frames", N, "points per frame", H, W)
names = sorted((HERE / "frames").glob("*.jpg"))
tsec = [int(n.stem.split("_")[1][:-1]) for n in names]

R, t = E[:, :, :3], E[:, :, 3]
C = -np.einsum("nji,nj->ni", R, t)  # camera centres (world)
step = np.linalg.norm(np.diff(C, axis=0), axis=1)
up = -np.mean(R[:, 1, :], axis=0)
up /= np.linalg.norm(up)
out = {"n_frames": int(N), "grid": [int(H), int(W)]}

# camera plausibility
out["camera"] = {"path_length_units": float(step.sum()), "median_step": float(np.median(step)), "max_step": float(step.max()),
                 "max_step_over_median": float(step.max() / np.median(step)),
                 "focal_px_median": float(np.median(K[:, 0, 0])),
                 "fov_deg_horizontal_median": float(np.degrees(2 * np.arctan(W / 2 / np.median(K[:, 0, 0])))),
                 "camera_height_above_first_cam_along_up_std": float(np.std(C @ up))}
# loop closure: the video starts at the table (0 s) and returns to it (~42 s)
i0, i1 = tsec.index(0), tsec.index(42)
out["loop_closure"] = {"dist_cam0_to_cam_at_42s": float(np.linalg.norm(C[i0] - C[i1])), "in_median_steps": float(np.linalg.norm(C[i0] - C[i1]) / np.median(step)),
                       "dist_cam_at_0_to_cam_at_20s_(far_side_of_room)": float(np.linalg.norm(C[i0] - C[tsec.index(20)])),
                       "viewing_dir_angle_0s_vs_42s_deg": float(np.degrees(np.arccos(np.clip(R[i0, 2] @ R[i1, 2], -1, 1))))}

# confidence / point cloud
thr = float(np.percentile(conf, 50))
out["confidence"] = {"median": float(np.median(conf)), "p10": float(np.percentile(conf, 10)), "p90": float(np.percentile(conf, 90)), "threshold_used_p50": thr}
mask = conf > thr
pts = P[mask]
pts = pts[np.isfinite(pts).all(1)]
rng = np.random.default_rng(0)
sub = pts[rng.choice(len(pts), min(len(pts), 400000), replace=False)]
out["points_used"] = int(len(pts))
h = sub @ up  # height along up
# floor plane via RANSAC restricted to near-horizontal planes
def ransac_plane(X, normal_hint=None, ang_tol=None, iters=1500, tol=None):
    best = (0, None)
    n = len(X)
    for _ in range(iters):
        a, b, c = X[rng.choice(n, 3, replace=False)]
        nv = np.cross(b - a, c - a)
        l = np.linalg.norm(nv)
        if l < 1e-9:
            continue
        nv /= l
        if normal_hint is not None and ang_tol is not None:
            cosang = abs(nv @ normal_hint)
            if (ang_tol[0] == "near" and cosang < np.cos(np.radians(ang_tol[1]))) or (ang_tol[0] == "perp" and cosang > np.sin(np.radians(ang_tol[1]))):
                continue
        d = -nv @ a
        cnt = int((np.abs(X @ nv + d) < tol).sum())
        if cnt > best[0]:
            best = (cnt, (nv, d))
    nv, d = best[1]
    inl = np.abs(X @ nv + d) < tol
    # refine with SVD on inliers
    Y = X[inl]
    cen = Y.mean(0)
    nv2 = np.linalg.svd(Y - cen, full_matrices=False)[2][2]
    return nv2, -nv2 @ cen, inl

span = float(np.percentile(h, 99) - np.percentile(h, 1))
tol = span * 0.01
out["scene_vertical_span_units"] = span
fl_n, fl_d, fl_in = ransac_plane(sub, up, ("near", 12), tol=tol)
if fl_n @ up < 0:
    fl_n, fl_d = -fl_n, -fl_d
floor_h = float(np.median(sub[fl_in] @ up))
resid = np.abs(sub[fl_in] @ fl_n + fl_d)
out["floor"] = {"inlier_fraction": float(fl_in.mean()), "normal_vs_up_deg": float(np.degrees(np.arccos(abs(fl_n @ up)))), "rms_residual_units": float(np.sqrt((resid ** 2).mean())),
                "height_along_up": floor_h}
# is it the floor (lowest big horizontal plane) or the table? list the two largest horizontal planes
rest = sub[~fl_in]
planes = [("horizontal_1", floor_h, float(fl_in.mean()))]
n2, d2, in2 = ransac_plane(rest, up, ("near", 12), tol=tol)
planes.append(("horizontal_2", float(np.median(rest[in2] @ up)), float(in2.sum() / len(sub))))
out["horizontal_planes_(height,share_of_points)"] = planes

# walls: vertical planes, iteratively removing inliers
walls = []
rem = sub[(sub @ up) > floor_h + 0.15 * span] if planes[0][1] <= planes[1][1] else sub[(sub @ up) > min(planes[0][1], planes[1][1]) + 0.15 * span]
for k in range(6):
    if len(rem) < 5000:
        break
    wn, wd, win = ransac_plane(rem, up, ("perp", 10), tol=tol)
    share = float(win.sum() / len(rem))
    if share < 0.04:
        break
    walls.append({"normal": [round(float(x), 3) for x in wn], "offset": float(wd), "inliers": int(win.sum()), "rms_residual": float(np.sqrt(((rem[win] @ wn + wd) ** 2).mean()))})
    rem = rem[~win]
out["vertical_planes_found"] = len(walls)
out["vertical_planes"] = walls
# pairs of parallel walls -> room extents
dims = []
for i in range(len(walls)):
    for j in range(i + 1, len(walls)):
        ni, nj = np.array(walls[i]["normal"]), np.array(walls[j]["normal"])
        if abs(ni @ nj) > 0.97:
            if ni @ nj < 0:
                nj = -nj
                dj = -walls[j]["offset"]
            else:
                dj = walls[j]["offset"]
            dims.append(abs(walls[i]["offset"] - dj))
out["parallel_wall_pair_distances_units"] = [float(d) for d in dims]

# mug reference: world points under the SAM 'striped mug' mask in frames that show it
from worldkit import segment

mug_heights, used = [], []
for fi, nm in enumerate(names):
    try:
        r = segment.segment_concept(str(nm), "striped mug")
        if getattr(r, "used_fallback", False):
            continue
        m = segment.full_frame_mask(str(nm), r)
    except Exception:
        continue
    import cv2

    full_h = int(round(m.shape[0] * (518 / m.shape[1])))
    mm = cv2.resize(m.astype(np.uint8), (518, full_h), interpolation=cv2.INTER_NEAREST).astype(bool)
    if full_h > H:  # center crop used by VGGT's preprocessing
        off = (full_h - H) // 2
        mm = mm[off:off + H]
    elif full_h < H:
        continue
    sel = mm[:H, :W] & (conf[fi] > np.percentile(conf[fi], 30))
    if sel.sum() < 80:
        continue
    q = P[fi][sel]
    q = q[np.isfinite(q).all(1)]
    hh = q @ up
    mug_heights.append(float(np.percentile(hh, 97) - np.percentile(hh, 3)))
    used.append(names[fi].stem)
out["mug_reference"] = {"frames_used": used, "height_units_per_frame": mug_heights}
if mug_heights:
    med = float(np.median(mug_heights))
    s_mm = 70.0 / med
    out["mug_reference"].update({"median_height_units": med, "mm_per_unit": s_mm, "spread_rel": float(np.std(mug_heights) / med)})
    out["room_relative_to_mug_mm"] = {"parallel_wall_pair_distances_mm": [round(d * s_mm) for d in dims],
                                      "vertical_span_of_points_mm": round(span * s_mm), "camera_height_above_floor_mm": round(float(np.median(C @ up) - floor_h) * s_mm),
                                      "note": "scale from the user's 70 mm mug height measured on VGGT points; ONLY an experiment"}
(HERE / "vggt_metrics.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1)[:3500])

# pictures: top-down cloud + trajectory, side view
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

a = np.cross(up, [1, 0, 0] if abs(up[0]) < 0.9 else [0, 1, 0])
a /= np.linalg.norm(a)
b = np.cross(up, a)
fig, ax = plt.subplots(1, 2, figsize=(13, 6))
sc = sub[::6]
col = np.clip((sc @ up - np.percentile(h, 1)) / span, 0, 1)
ax[0].scatter(sc @ a, sc @ b, c=col, s=0.2, cmap="viridis")
ax[0].plot(C @ a, C @ b, "r.-", lw=1)
for i in range(N):
    ax[0].annotate(str(tsec[i]), (C[i] @ a, C[i] @ b), fontsize=6, color="r")
ax[0].set_aspect("equal"); ax[0].set_title("top-down: points (colour = height) and camera path (seconds)")
ax[1].scatter(sc @ a, sc @ up, c="k", s=0.2); ax[1].set_aspect("equal"); ax[1].set_title("side view")
plt.tight_layout(); plt.savefig(HERE / "vggt_overview.png", dpi=110)
