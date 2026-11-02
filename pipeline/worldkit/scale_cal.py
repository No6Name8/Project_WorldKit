"""Real-world scale from a known-size circular reference (a coin) seen in the same frames as the object.

Why a disk: a circle of known diameter D seen obliquely is an ellipse whose *major* axis is D in pixels whatever
the viewing angle, so ``px_per_cm = major_axis_px / D`` needs no knowledge of the camera tilt. The object's own
pixels-per-mesh-unit comes from the silhouette pose fit already used for texture projection
(``texture_project.fit_pose``; ``pose.scale`` is image pixels per mesh unit), so

    cm_per_mesh_unit = pose.scale / px_per_cm

per frame. Estimates from several frames are aggregated (median) and their spread is reported as the
self-consistency of the method.

Model assumptions, all of which add error that the report records instead of hiding:
- weak perspective: the reference and the object are at about the same distance from the camera. A depth
  difference of dz at camera distance Z biases the scale by roughly dz/Z; because the camera circles the scene the
  sign changes between frames and the median partly cancels it;
- the reference's real diameter is exactly what the user supplied (the pipeline never guesses a coin);
- ellipse/mask accuracy: a ~30 px coin has a +-1 px edge error, i.e. a few percent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# a scale estimate is only reported if at least this many frames contributed
MIN_FRAMES = 3
# self-consistency gate: median absolute deviation / median must be below this to call the estimate "consistent"
MAX_REL_SPREAD = 0.10


# Known reference coins (diameter in cm). The pipeline never guesses a coin: pick one with --coin or give --ref-diameter-cm.
COINS = {
    "saudi-25-halalas": {"diameter_cm": 2.15, "source": "Numista (https://en.numista.com/101221): 2016 brass issue, 21.5 mm, gold-coloured; the 1972 copper-nickel issue is silver-coloured and 23 mm"},
}


def coin_diameter_cm(name: str) -> float:
    if name not in COINS:
        raise ScaleCalibrationError(f"unknown coin {name!r}; known: {sorted(COINS)}; or pass --ref-diameter-cm")
    return COINS[name]["diameter_cm"]


class ScaleCalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiskObservation:
    major_px: float
    minor_px: float
    angle_deg: float
    area_px: int

    @property
    def elevation_deg(self) -> float:
        """Camera elevation above the table plane implied by the ellipse: minor/major = sin(elevation)."""
        return float(np.degrees(np.arcsin(np.clip(self.minor_px / self.major_px, 0.0, 1.0))))


def fit_disk(mask: np.ndarray, *, min_area_px: int = 120) -> DiskObservation:
    """Ellipse of a segmented circular reference. ``mask`` is a full-frame bool array."""
    import cv2

    area = int(mask.sum())
    if area < min_area_px:
        raise ScaleCalibrationError(f"reference mask too small ({area} px < {min_area_px})")
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cnt = max(contours, key=cv2.contourArea)
    if len(cnt) < 5:
        raise ScaleCalibrationError("reference contour has too few points to fit an ellipse")
    (_, _), (d1, d2), angle = cv2.fitEllipse(cnt)
    major, minor = max(d1, d2), min(d1, d2)
    # fitEllipse on a pixel contour is biased slightly low (it traces pixel centres); area-equivalent axes are
    # unbiased: pi/4 * major * minor must equal the mask area. Keep the fitted ratio, rescale to the area.
    ratio = minor / major
    major_area = float(np.sqrt(4.0 * area / (np.pi * ratio)))
    return DiskObservation(major_px=major_area, minor_px=major_area * ratio, angle_deg=float(angle), area_px=area)


@dataclass(frozen=True)
class FrameScale:
    frame: str
    cm_per_unit: float
    px_per_cm: float
    object_px_per_unit: float
    pose_iou: float
    ref_elevation_deg: float
    pose_elevation_deg: float


@dataclass
class ScaleCalibration:
    reference: str
    reference_diameter_cm: float
    cm_per_unit: float
    rel_spread: float  # median absolute deviation / median across frames
    frames: list[FrameScale] = field(default_factory=list)
    frames_rejected: dict = field(default_factory=dict)
    consistent: bool = False

    def to_dict(self) -> dict:
        return {
            "method": "known-diameter disk reference + silhouette pose-fit scale (weak perspective)",
            "reference": self.reference,
            "reference_diameter_cm": self.reference_diameter_cm,
            "reference_diameter_source": "user-supplied (never guessed)",
            "cm_per_mesh_unit": self.cm_per_unit,
            "frames_used": len(self.frames),
            "relative_spread_mad": self.rel_spread,
            "self_consistent": self.consistent,
            "per_frame": [f.__dict__ for f in self.frames],
            "frames_rejected": self.frames_rejected,
            "note": (
                "An estimate, not a measurement: assumes the reference and the object are at similar distance from "
                "the camera (weak perspective). The spread shows self-consistency across frames, not accuracy."
            ),
        }


def combine(per_frame: list[FrameScale], reference: str, diameter_cm: float, rejected: dict | None = None) -> ScaleCalibration:
    """Aggregate per-frame estimates. Raises if fewer than MIN_FRAMES frames contributed."""
    if len(per_frame) < MIN_FRAMES:
        raise ScaleCalibrationError(
            f"only {len(per_frame)} frame(s) usable for scale calibration (need {MIN_FRAMES}); rejected: {rejected or {}}"
        )
    vals = np.array([f.cm_per_unit for f in per_frame])
    med = float(np.median(vals))
    spread = float(np.median(np.abs(vals - med)) / med)
    return ScaleCalibration(
        reference=reference, reference_diameter_cm=diameter_cm, cm_per_unit=med, rel_spread=spread,
        frames=per_frame, frames_rejected=rejected or {}, consistent=spread <= MAX_REL_SPREAD,
    )


def frame_scale(name: str, ref: DiskObservation, diameter_cm: float, pose, pose_iou: float) -> FrameScale:
    px_per_cm = ref.major_px / diameter_cm
    return FrameScale(
        frame=name, cm_per_unit=float(pose.scale / px_per_cm), px_per_cm=float(px_per_cm),
        object_px_per_unit=float(pose.scale), pose_iou=float(pose_iou),
        ref_elevation_deg=ref.elevation_deg, pose_elevation_deg=float(pose.el_deg),
    )


def calibrate(
    frames: list[tuple[str, np.ndarray, np.ndarray | None]],
    vertices: np.ndarray,
    faces: np.ndarray,
    reference: str,
    diameter_cm: float,
    *,
    min_pose_iou: float = 0.85,
) -> ScaleCalibration:
    """``frames``: (name, object_mask, reference_mask or None) full-frame bool masks; mesh in its own units."""
    from . import texture_project as tp

    if diameter_cm <= 0:
        raise ScaleCalibrationError("reference diameter must be > 0")
    per_frame, rejected = [], {}
    for name, obj_mask, ref_mask in frames:
        if ref_mask is None or not ref_mask.any():
            rejected[name] = "reference not found in this frame"
            continue
        try:
            ref = fit_disk(ref_mask)
        except ScaleCalibrationError as exc:
            rejected[name] = str(exc)
            continue
        try:
            fit = tp.fit_pose(vertices, faces, obj_mask)
        except tp.TextureProjectionError as exc:
            rejected[name] = f"object pose fit failed: {exc}"
            continue
        if fit.iou < min_pose_iou:
            rejected[name] = f"object silhouette fit too loose (IoU {fit.iou:.2f} < {min_pose_iou})"
            continue
        per_frame.append(frame_scale(name, ref, diameter_cm, fit.pose, fit.iou))
    return combine(per_frame, reference, diameter_cm, rejected)


# --- coin-grounded calibration: elevation from the coin, shape from the silhouettes ---------------------------------------------------
# calibrate() above fits the camera elevation from the object's own silhouette, which is weakly determined (up to ~30 deg off) and leaks
# into the scale. The coin lies on the same surface as the object, so ITS ellipse gives the camera elevation. With elevation fixed, the
# silhouettes can also say how tall (aspect k) and how tapered (t) the generated mesh should be: generated proportions are not measured.
# Both are chosen only by silhouette IoU against SAM masks; no external measurement is involved.

ASPECT_GRID = (0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10)
TAPER_GRID = (-0.20, -0.15, -0.10, -0.05, 0.0, 0.05, 0.10)  # radial scale 1 + t*(0.5 - y/h): t < 0 = wider at the top
ASPECT_MIN_GAIN = 0.004  # mean IoU gain over the generated shape needed before the mesh is restretched
TAPER_MIN_GAIN = 0.003
ASPECT_FLAT_TOL = 0.003  # values within this IoU of the best are not distinguishable: their span is the reported uncertainty


def shape_reference(vertices: np.ndarray, up_axis: int = 1) -> tuple:
    h = [i for i in range(3) if i != up_axis]
    lo, hi = vertices.min(0), vertices.max(0)
    return float(lo[up_axis]), float(hi[up_axis]), float((lo[h[0]] + hi[h[0]]) / 2), float((lo[h[1]] + hi[h[1]]) / 2)


def reshape_vertices(vertices: np.ndarray, k: float, t: float, ref: tuple, up_axis: int = 1) -> np.ndarray:
    """Vertical stretch k about the lowest point and a linear taper t about the vertical axis; ``ref`` comes from the ORIGINAL mesh."""
    y0, y1, c0, c1 = ref
    h = [i for i in range(3) if i != up_axis]
    v = np.asarray(vertices, float).copy()
    yn = (v[:, up_axis] - y0) / max(y1 - y0, 1e-12)
    f = 1.0 + t * (0.5 - yn)
    v[:, h[0]] = c0 + (v[:, h[0]] - c0) * f
    v[:, h[1]] = c1 + (v[:, h[1]] - c1) * f
    v[:, up_axis] = y0 + (v[:, up_axis] - y0) * k
    return v


def fit_pose_fixed_elevation(vertices, faces, mask, elevation_deg: float, *, az_step: float = 10.0):
    from . import texture_project as tp

    size = (mask.shape[1], mask.shape[0])
    best = None
    for az in np.arange(0.0, 360.0, az_step):
        p = tp._anchored_pose(vertices, float(az), float(elevation_deg), mask)
        s = tp.iou(tp.silhouette(vertices, faces, p, size), mask)
        if best is None or s > best[0]:
            best = (s, p)
    best_iou, bp = best
    steps = np.array([4.0, 0.0, 0.03, 3.0, 3.0])  # az, el (fixed), rel-scale, tx, ty
    for _ in range(3):
        improved = True
        while improved:
            improved = False
            for ax in (0, 2, 3, 4):
                for sg in (-1.0, 1.0):
                    v = [bp.az_deg, bp.el_deg, bp.scale, bp.tx, bp.ty]
                    v[ax] += sg * steps[ax] * (bp.scale if ax == 2 else 1.0)
                    c = tp.CameraPose(*v)
                    s = tp.iou(tp.silhouette(vertices, faces, c, size), mask)
                    if s > best_iou + 1e-4:
                        best_iou, bp, improved = s, c, True
        steps = steps / 2.0
    return best_iou, bp


@dataclass
class GroundedScale:
    aspect_k: float          # vertical stretch to apply to the generated mesh (1.0 = as generated)
    taper_t: float           # linear taper to apply (0.0 = as generated)
    cm_per_unit: float       # lateral scale (cm per mesh unit) of the reshaped mesh
    height_cm: float
    height_range_cm: tuple   # span of aspect values the silhouettes cannot tell apart (within ASPECT_FLAT_TOL of the best IoU)
    mean_iou_by_k: dict
    mean_iou_by_taper: dict
    mean_iou_generated: float
    mean_iou_final: float
    applied: bool
    frames: list
    reference_diameter_cm: float
    up_axis: int = 1

    def to_dict(self) -> dict:
        return {"method": "the scale reference (coin ellipse or card corners) fixes the camera elevation; height aspect and taper fitted to the silhouettes at that elevation; lateral scale from the reference",
                "aspect_k": self.aspect_k, "taper_t": self.taper_t, "applied": self.applied, "cm_per_mesh_unit": self.cm_per_unit, "height_cm": self.height_cm,
                "height_range_cm": list(self.height_range_cm), "mean_iou_by_k": self.mean_iou_by_k, "mean_iou_by_taper": self.mean_iou_by_taper,
                "mean_iou_generated": self.mean_iou_generated, "mean_iou_final": self.mean_iou_final, "frames": self.frames,
                "reference_diameter_cm": self.reference_diameter_cm}


@dataclass
class _RefObs:
    elevation_deg: float
    px_per_cm: float


def calibrate_grounded(frames, vertices, faces, diameter_cm: float, *, up_axis: int = 1, observe=None, min_frames: int = MIN_FRAMES,
                       reference_name: str = "coin") -> GroundedScale:
    """``frames``: (name, object_mask, reference_mask or None). Needs >= ``min_frames`` frames with a usable reference.

    ``observe(mask) -> (px_per_cm, elevation_deg)`` reads the reference; the default is a coin of diameter ``diameter_cm`` (ellipse fit).
    A card passes ``refscale.card_observation`` here (``diameter_cm`` is then its long side, used only in the report)."""
    ref_box = shape_reference(vertices, up_axis)
    h0 = ref_box[1] - ref_box[0]
    obs = []
    for name, obj_mask, ref_mask in frames:
        if ref_mask is None or not ref_mask.any():
            continue
        try:
            if observe is None:
                d = fit_disk(ref_mask)
                ref = _RefObs(d.elevation_deg, d.major_px / diameter_cm)
            else:
                ppc, el = observe(ref_mask)
                ref = _RefObs(float(el), float(ppc))
        except (ScaleCalibrationError, ValueError):
            continue
        obs.append((name, obj_mask, ref))
    if len(obs) < min_frames:
        raise ScaleCalibrationError(f"only {len(obs)} frame(s) with a usable {reference_name} for the grounded calibration (need {min_frames})")

    def score(k: float, t: float):
        vk = reshape_vertices(vertices, k, t, ref_box, up_axis)
        res = [(fit_pose_fixed_elevation(vk, faces, m, r.elevation_deg), r.px_per_cm) for _, m, r in obs]
        return float(np.mean([i for (i, _), _ppc in res])), float(np.mean([p.scale / ppc for (_, p), ppc in res]))

    ks, ts = np.array(ASPECT_GRID), np.array(TAPER_GRID)
    by_k = [score(k, 0.0) for k in ASPECT_GRID]
    iou_k, cm_k = np.array([a for a, _ in by_k]), np.array([b for _, b in by_k])
    j = int(iou_k.argmax())
    k_best = float(ks[j])
    if 0 < j < len(ks) - 1:  # parabolic refinement around the peak
        a, b, c = iou_k[j - 1], iou_k[j], iou_k[j + 1]
        den = a - 2 * b + c
        if den < 0:
            k_best = float(ks[j] + 0.5 * (a - c) / den * (ks[1] - ks[0]))
    i_gen = float(iou_k[list(ks).index(1.0)])
    k_ok = bool(iou_k.max() - i_gen >= ASPECT_MIN_GAIN and abs(k_best - 1.0) >= 0.02)
    k_use = k_best if k_ok else 1.0
    by_t = [score(k_use, t) for t in TAPER_GRID]
    iou_t, cm_t = np.array([a for a, _ in by_t]), np.array([b for _, b in by_t])
    i_t0 = float(iou_t[list(ts).index(0.0)])
    jt = int(iou_t.argmax())
    t_ok = bool(iou_t.max() - i_t0 >= TAPER_MIN_GAIN and ts[jt] != 0.0)
    t_use = float(ts[jt]) if t_ok else 0.0
    i_fin, cm_fin = score(k_use, t_use)
    flat = ks[iou_k >= iou_k.max() - ASPECT_FLAT_TOL]
    h = lambda k: float(np.interp(k, ks, cm_k) * h0 * k)
    return GroundedScale(aspect_k=float(k_use), taper_t=t_use, cm_per_unit=cm_fin, height_cm=float(cm_fin * h0 * k_use),
                         height_range_cm=(h(float(flat.min())), h(float(flat.max()))),
                         mean_iou_by_k={f"{k:.2f}": round(float(v), 4) for k, v in zip(ks, iou_k)},
                         mean_iou_by_taper={f"{t:+.2f}": round(float(v), 4) for t, v in zip(ts, iou_t)},
                         mean_iou_generated=i_gen, mean_iou_final=i_fin, applied=bool(k_ok or t_ok),
                         frames=[{"frame": n, f"{reference_name}_elevation_deg": r.elevation_deg, "px_per_cm": r.px_per_cm} for n, _, r in obs],
                         reference_diameter_cm=diameter_cm, up_axis=up_axis)
