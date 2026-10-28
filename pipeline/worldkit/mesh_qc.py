"""Geometric quality measurements for a generated cup-like mesh (rim levelness/circularity, axis, base)."""
from __future__ import annotations

import numpy as np


def _plane_fit(p: np.ndarray):
    c = p.mean(0)
    u, s, vt = np.linalg.svd(p - c, full_matrices=False)
    n = vt[2]
    if n[1] < 0:
        n = -n
    return c, n, (p - c) @ n


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(abs(a @ b), -1, 1))))


def handle_azimuth(v: np.ndarray, up: int = 1) -> float:
    """Azimuth (rad, in the x-z plane) of the part of the mesh farthest from the body axis."""
    h = np.delete(np.arange(3), up)
    p = v[:, h]
    c = np.median(p, axis=0)
    r = np.linalg.norm(p - c, axis=1)
    k = r >= np.percentile(r, 99)
    d = p[k] - c
    return float(np.arctan2(d[:, 1].mean(), d[:, 0].mean()))


def rim_ring(v: np.ndarray, H: float, bins: int = 48, depth: float = 0.02) -> np.ndarray:
    """Boolean mask of the rim ring: per azimuth bin, the outer vertices within ``depth``*H of that bin's own highest point.

    Using each bin's own top (instead of one global height slab) keeps the ring intact when the rim is uneven, which is the
    very case the measurement is meant to catch.
    """
    cx, cz = np.median(v[:, 0]), np.median(v[:, 2])
    th = np.arctan2(v[:, 2] - cz, v[:, 0] - cx)
    r = np.hypot(v[:, 0] - cx, v[:, 2] - cz)
    edges = np.linspace(-np.pi, np.pi, bins + 1)
    ring = np.zeros(len(v), bool)
    for i in range(bins):
        m = (th >= edges[i]) & (th < edges[i + 1])
        if m.sum() < 3:
            continue
        ytop = v[m, 1].max()
        if ytop < v[:, 1].max() - 0.10 * H:  # no rim in this sector (e.g. masked handle region): do not take body/base points
            continue
        near = m & (v[:, 1] > ytop - depth * H)
        if near.sum() < 2:
            continue
        ring |= near & (r >= np.percentile(r[near], 50))
    return ring


def analyze_cup(v: np.ndarray, *, handle_exclusion_deg: float = 55.0, band: float = 0.03) -> dict:
    """Rim/base/axis analysis for a Y-up cup. All lengths in mesh units; ratios are dimensionless.

    - the handle is masked out by azimuth so it cannot bias circles/planes;
    - body axis = least-squares line through horizontal-slice circle centres (handle-free azimuths only);
    - rim ring = outermost vertices of the top slab; base ring = outermost vertices of the bottom slab;
    - rim_planarity: residual std of the ring around its own best plane / rim radius (waviness);
    - rim_tilt_vs_axis_deg / base_tilt_vs_axis_deg: angle between each ring's plane normal and the body axis
      (0 for a true cup whose rim and base are perpendicular to its axis);
    - rim_tilt_vs_base_deg: angle between the rim plane and the base plane (an orientation-free levelness measure);
    - rim_circularity: radial deviation (std/mean) of the ring from its best-fit circle in its plane.
    """
    v = np.asarray(v, float)
    ang = handle_azimuth(v)
    az = np.arctan2(v[:, 2], v[:, 0] - np.median(v[:, 0]))
    dif = np.abs((np.arctan2(np.sin(az - ang), np.cos(az - ang))))
    body = v[dif > np.radians(handle_exclusion_deg)]
    y = body[:, 1]
    lo, hi = y.min(), y.max()
    H = hi - lo
    centres, hs = [], []
    for a in np.linspace(0.05, 0.95, 10):
        m = np.abs(y - (lo + a * H)) < 0.04 * H
        if m.sum() < 30:
            continue
        p = body[m][:, [0, 2]]
        # algebraic circle fit, ring vertices only (outer shell): keep farthest 50% from the slice centroid
        c0 = p.mean(0)
        r0 = np.linalg.norm(p - c0, axis=1)
        q = p[r0 > np.percentile(r0, 50)]
        A = np.c_[2 * q, np.ones(len(q))]
        b = (q ** 2).sum(1)
        sol = np.linalg.lstsq(A, b, rcond=None)[0]
        centres.append([sol[0], lo + a * H, sol[1]])
        hs.append(np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0)))
    centres = np.array(centres)
    cm = centres.mean(0)
    axis = np.linalg.svd(centres - cm)[2][0]
    if axis[1] < 0:
        axis = -axis
    radii = np.array(hs)

    def ring(sel_hi: bool):
        if sel_hi:
            return body[rim_ring(body, H)]
        m = y < lo + band * H
        p = body[m]
        c = np.array([np.median(p[:, 0]), 0, np.median(p[:, 2])])
        r = np.linalg.norm(p[:, [0, 2]] - c[[0, 2]], axis=1)
        return p[r > np.percentile(r, 70)]

    out: dict = {"height": float(H), "axis": [float(a) for a in axis], "axis_tilt_from_y_deg": _angle(axis, np.array([0, 1, 0])),
                 "radius_rim_band": float(radii[-1]) if len(radii) else None, "radius_base_band": float(radii[0]) if len(radii) else None}
    rim_p, base_p = ring(True), ring(False)
    for name, p in (("rim", rim_p), ("base", base_p)):
        c, n, res = _plane_fit(p)
        ax = axis
        e1 = np.cross(n, [1, 0, 0]); e1 /= np.linalg.norm(e1)
        e2 = np.cross(n, e1)
        q = np.c_[(p - c) @ e1, (p - c) @ e2]
        A = np.c_[2 * q, np.ones(len(q))]
        sol = np.linalg.lstsq(A, (q ** 2).sum(1), rcond=None)[0]
        rad = np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 1e-12))
        rr = np.linalg.norm(q - sol[:2], axis=1)
        out[name] = {
            "n_points": int(len(p)), "normal": [float(a) for a in n], "centre": [float(a) for a in c],
            "tilt_vs_axis_deg": _angle(n, ax), "tilt_from_y_deg": _angle(n, np.array([0, 1, 0])),
            "planarity_resid_over_radius": float(res.std() / rad), "fit_radius": float(rad),
            "circularity_std_over_radius": float(rr.std() / rad),
        }
    out["rim_tilt_vs_base_deg"] = _angle(np.array(out["rim"]["normal"]), np.array(out["base"]["normal"]))
    # A cup's rim and base planes are perpendicular to its axis, and each is fitted from hundreds of vertices; the slice-centre
    # axis is biased by the handle and the taper. When the two planes agree, their mean normal is the better axis estimate.
    out["axis_from_slices"] = out["axis"]
    if out["rim_tilt_vs_base_deg"] <= 3.0:
        na = np.array(out["rim"]["normal"]) + np.array(out["base"]["normal"])
        na /= np.linalg.norm(na)
        out["axis"] = [float(x) for x in na]
        out["axis_method"] = "mean of rim and base plane normals"
        out["axis_tilt_from_y_deg"] = _angle(na, np.array([0, 1, 0]))
        for name in ("rim", "base"):
            out[name]["tilt_vs_axis_deg"] = _angle(np.array(out[name]["normal"]), na)
    else:
        out["axis_method"] = "slice centres (rim and base planes disagree)"
    out["rim_to_base_height"] = float(np.dot(np.array(out["rim"]["centre"]) - np.array(out["base"]["centre"]), axis))
    return out


# --- targeted geometry corrections ----------------------------------------------------------------------------------

def rotation_aligning(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(a @ b)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1.0 + c)


def plan_axis_alignment(report: dict, *, min_tilt_deg: float = 0.5, max_tilt_deg: float = 15.0, max_inconsistency_deg: float = 2.5) -> dict:
    """Decide (from an ``analyze_cup`` report) whether to rotate the mesh so its body axis is +Y.

    A pure rigid rotation: it cannot distort the shape, so it is the safest correction. It is only proposed when the
    body axis, the rim plane normal and the base plane normal AGREE with each other (the object really is a body of
    revolution about that axis) and the tilt is large enough to matter but small enough to be a pose error rather than
    a deliberately slanted object.
    """
    tilt = report["axis_tilt_from_y_deg"]
    rim_ax, base_ax = report["rim"]["tilt_vs_axis_deg"], report["base"]["tilt_vs_axis_deg"]
    consistent = max(rim_ax, base_ax) <= max_inconsistency_deg
    apply = min_tilt_deg <= tilt <= max_tilt_deg and consistent
    reason = (
        f"axis tilt {tilt:.2f} deg from +Y; rim/base planes are {rim_ax:.2f}/{base_ax:.2f} deg off the axis "
        + ("-> rotate so the axis is +Y" if apply else
           "-> no rotation: " + ("tilt below threshold" if tilt < min_tilt_deg else "tilt too large to be a pose error" if tilt > max_tilt_deg else "rim/base disagree with the fitted axis"))
    )
    return {"apply": bool(apply), "tilt_deg": tilt, "reason": reason, "axis": report["axis"]}


def apply_axis_alignment(v: np.ndarray, axis: list[float], pivot: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    R = rotation_aligning(np.asarray(axis, float), np.array([0.0, 1.0, 0.0]))
    p = np.asarray(v, float).mean(axis=0) if pivot is None else pivot
    return (np.asarray(v, float) - p) @ R.T + p, R


def plan_rim_regularization(report: dict, understanding_rim_ok: bool, *, min_planarity: float = 0.02, min_circularity: float = 0.04) -> dict:
    """Rim correction is allowed only when the vision analysis says the rim is circular/level AND the measurement shows a defect."""
    rim = report["rim"]
    defect = rim["planarity_resid_over_radius"] > min_planarity or rim["circularity_std_over_radius"] > min_circularity
    apply = bool(understanding_rim_ok and defect)
    return {
        "apply": apply, "planarity": rim["planarity_resid_over_radius"], "circularity": rim["circularity_std_over_radius"],
        "reason": ("rim defect measured and the vision analysis says the rim is circular and level -> regularise"
                   if apply else
                   "no rim correction: " + ("measured rim is within tolerance" if not defect else "vision analysis does not support a circular level rim")),
    }


def regularize_rim(v: np.ndarray, report: dict, *, blend_fraction: float = 0.06, bins: int = 48) -> np.ndarray:
    """Level the rim: move the ring to its best-fit plane, with a smooth falloff over ``blend_fraction`` of the body height.

    For every azimuth the rim ring's height error against the fitted plane is measured (median over a bin of ring vertices,
    smoothed around the circle); vertices in the blend zone below the rim are lifted/lowered by that error times a
    smoothstep weight. Only heights move; radial positions, UVs and topology are unchanged.
    """
    v = np.asarray(v, float).copy()
    n = np.array(report["rim"]["normal"], float)
    c = np.array(report["rim"]["centre"], float)
    H = report["height"]
    top = v[:, 1].max()
    cx, cz = np.median(v[:, 0]), np.median(v[:, 2])
    th = np.arctan2(v[:, 2] - cz, v[:, 0] - cx)
    r = np.hypot(v[:, 0] - cx, v[:, 2] - cz)
    ring = rim_ring(v, H, bins)

    def plane_y(x, z):
        return c[1] - (n[0] * (x - c[0]) + n[2] * (z - c[2])) / n[1]

    edges = np.linspace(-np.pi, np.pi, bins + 1)
    err = np.full(bins, np.nan)
    ring_y = np.full(bins, np.nan)
    for i in range(bins):
        m = ring & (th >= edges[i]) & (th < edges[i + 1])
        if m.sum() >= 2:
            err[i] = np.median(plane_y(v[m, 0], v[m, 2]) - v[m, 1])
            ring_y[i] = np.median(v[m, 1])
    ok = np.isfinite(err)
    if ok.sum() < bins // 3:
        return v
    centres = (edges[:-1] + edges[1:]) / 2
    wrap = lambda a: np.interp(centres, np.r_[centres[ok] - 2 * np.pi, centres[ok], centres[ok] + 2 * np.pi], np.r_[a[ok], a[ok], a[ok]])
    err, ring_y = wrap(err), wrap(ring_y)
    k = 2
    sm = lambda a: np.convolve(np.r_[a[-k:], a, a[:k]], np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    err = sm(err)
    ext = lambda a: np.interp(th, np.r_[centres, centres[-1] + (centres[1] - centres[0])], np.r_[a, a[0]])
    delta, local_top = ext(err), ext(ring_y)
    depth = np.clip((local_top - v[:, 1]) / (blend_fraction * H), 0.0, 1.0)  # distance below THIS azimuth's rim, 0 at/above it
    w = 1.0 - depth * depth * (3 - 2 * depth)
    zone = (v[:, 1] > local_top - blend_fraction * H) & (v[:, 1] < local_top + 0.02 * H)
    v[zone, 1] += (w * delta)[zone]
    return v
    centres = (edges[:-1] + edges[1:]) / 2
    err = np.interp(centres, np.r_[centres[ok] - 2 * np.pi, centres[ok], centres[ok] + 2 * np.pi], np.r_[err[ok], err[ok], err[ok]])
    k = 2
    err = np.convolve(np.r_[err[-k:], err, err[:k]], np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    delta = np.interp(th, np.r_[centres - 0, centres[-1] + (centres[1] - centres[0])], np.r_[err, err[0]])
    depth = np.clip((top - v[:, 1]) / (blend_fraction * H), 0.0, 1.0)
    w = 1.0 - depth * depth * (3 - 2 * depth)
    zone = v[:, 1] > top - blend_fraction * H
    v[zone, 1] += (w * delta)[zone]
    return v
