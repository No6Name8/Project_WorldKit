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
