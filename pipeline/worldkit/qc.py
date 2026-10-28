"""Reconstruction quality control: measurable agreement between a textured mesh and the real video frames.

What each metric measures, and what it does NOT prove:
- ``silhouette_iou``: overlap of the mesh's projected outline and the SAM mask at the fitted pose. Checks outline/proportions
  only; a wrong interior (rim, texture) or the far side can still score 1.0. It also depends on the pose having been fitted
  to that same mask, so a high value is partly built in.
- ``ncc``: normalised correlation of grayscale pixels between the textured render and the photo over their overlap.
  Illumination-invariant, sensitive to stripe positions/shape, blind to overall colour.
- ``mae``: mean absolute luminance error; depends on exposure/lighting (a photo's white is not an albedo), so it is reported
  but not used for acceptance.
- ``stripe_iou``: overlap of the dark-pattern masks of render and photo (each thresholded relative to its own white level).
  The most direct measure of "are the stripes where and how wide they should be".
Each frame's pose is re-aligned photometrically against the texture being judged (identically for every candidate), so the
scores compare texture quality, not pose-estimation luck. Held-out frames are never used to build the candidate.
Nothing here is ground truth for 3-D shape: all of it is agreement with 2-D evidence from the available viewpoints.
"""

from __future__ import annotations

import numpy as np

from . import texture_project as tp


def _gray(img: np.ndarray) -> np.ndarray:
    return 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]


def _ncc(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> float:
    if m.sum() < 50:
        return float("nan")
    x, y = a[m], b[m]
    x, y = x - x.mean(), y - y.mean()
    d = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / d) if d > 1e-6 else float("nan")


def stripe_iou(render_gray: np.ndarray, photo_gray: np.ndarray, region: np.ndarray, dark_fraction: float = 0.62) -> float:
    def dark(g):
        ref = np.percentile(g[region], 90)
        return (g < dark_fraction * ref) & region

    a, b = dark(render_gray), dark(photo_gray)
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else float("nan")


def texture_agreement(vertices, faces, uvs, texture: np.ndarray, views: list[tp.FrameView], *, refine: bool = True) -> dict:
    """Per-frame {ncc, mae, stripe_iou, silhouette_iou, pose} for ``views`` (poses are only starting points)."""
    import cv2

    out = {}
    for v in views:
        h, w = v.mask.shape
        pose = v.pose
        if refine:
            pose, _ = tp.refine_pose_photometric(vertices, faces, uvs, texture, v.rgb, v.mask, v.pose, az_range=15.0)
        img, cov = tp.render_textured(vertices, faces, uvs, texture, pose, (w, h))
        inter = cv2.erode((cov & v.mask).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        g1, g2 = _gray(img.astype(np.float32)), _gray(v.rgb.astype(np.float32))
        out[v.name] = {
            "ncc": round(_ncc(g1, g2, inter), 3),
            "mae": round(float(np.abs(g1[inter] - g2[inter]).mean()), 1) if inter.any() else float("nan"),
            "stripe_iou": round(stripe_iou(g1, g2, inter), 3) if inter.sum() > 50 else float("nan"),
            "silhouette_iou": round(tp.iou(cov, v.mask), 3),
        }
    return out


def summarize(per_frame: dict) -> dict:
    def mean(key):
        vals = [x[key] for x in per_frame.values() if x.get(key) == x.get(key)]
        return float(np.mean(vals)) if vals else float("nan")

    return {"ncc": mean("ncc"), "mae": mean("mae"), "stripe_iou": mean("stripe_iou"), "silhouette_iou": mean("silhouette_iou"), "n_frames": len(per_frame)}


def accept_candidate(baseline: dict, candidate: dict, *, min_ncc_gain: float = 0.03, min_stripe_iou_gain: float = 0.03,
                     max_frame_ncc_drop: float = 0.10) -> tuple[bool, list[str]]:
    """Explicit acceptance criteria on HELD-OUT frames. Returns (accepted, reasons for the decision)."""
    reasons = []
    b, c = summarize(baseline), summarize(candidate)
    ok = True
    d_ncc, d_iou = c["ncc"] - b["ncc"], c["stripe_iou"] - b["stripe_iou"]
    if not d_ncc >= min_ncc_gain:
        ok = False
        reasons.append(f"held-out NCC gain {d_ncc:+.3f} < required {min_ncc_gain}")
    if not d_iou >= min_stripe_iou_gain:
        ok = False
        reasons.append(f"held-out stripe IoU gain {d_iou:+.3f} < required {min_stripe_iou_gain}")
    for name, cv in candidate.items():
        bv = baseline.get(name)
        if bv and cv["ncc"] == cv["ncc"] and bv["ncc"] == bv["ncc"] and (bv["ncc"] - cv["ncc"]) > max_frame_ncc_drop:
            ok = False
            reasons.append(f"frame {name} got worse by {bv['ncc'] - cv['ncc']:.3f} NCC (> {max_frame_ncc_drop})")
    if ok:
        reasons.append(f"held-out NCC {b['ncc']:.3f} -> {c['ncc']:.3f}, stripe IoU {b['stripe_iou']:.3f} -> {c['stripe_iou']:.3f}")
    return ok, reasons


def rim_contour_error(vertices, faces, view: tp.FrameView, top_fraction: float = 0.30) -> dict:
    """How well the mesh's upper outline (rim + opening) matches the SAM mask's upper outline in one frame.

    Per image column in the top ``top_fraction`` of the object's rows, compares the first row where the mask / the projected
    mesh starts; reports the mean absolute difference in pixels and as a fraction of the object's height. It uses the frame's
    fitted pose, which was itself fitted to this mask, so it is an agreement check on the rim shape under the best pose, not
    an independent measurement; it cannot see the far side of the rim hidden behind the near wall.
    """
    mask = view.mask
    h, w = mask.shape
    sil = tp.silhouette(vertices, faces, view.pose, (w, h))
    ys, xs = np.nonzero(mask)
    y0, y1 = ys.min(), ys.max()
    lim = y0 + int(top_fraction * (y1 - y0))
    diffs = []
    for x in range(xs.min(), xs.max() + 1):
        cm, cs = np.nonzero(mask[y0:lim, x])[0], np.nonzero(sil[y0:lim, x])[0]
        if len(cm) and len(cs):
            diffs.append(abs(int(cm[0]) - int(cs[0])))
    if len(diffs) < 10:
        return {"mean_px": float("nan"), "fraction_of_height": float("nan"), "columns": len(diffs)}
    d = float(np.mean(diffs))
    return {"mean_px": round(d, 2), "fraction_of_height": round(d / (y1 - y0), 4), "columns": len(diffs)}
