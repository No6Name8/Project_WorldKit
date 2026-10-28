"""Constrained pattern representation for periodic vertical stripes on a body of revolution.

When the observed (theta, h) map shows regularly spaced stripes that all share one waveform, the clean description is:
a lattice of N stripe centres (spacing 360/N), one canonical centre curve c(h) (how a stripe wanders sideways with
height), one canonical width curve w(h), and the stripe's vertical extent. Every parameter is *measured* from the real
frames, from many stripes at once, which averages out per-frame blur and misregistration that a straight pixel average
keeps. The model is only used if its checks pass (regular spacing, whole stripe count, shared waveform); otherwise the
caller keeps the plain fused observations. Per-stripe individual variation of the real object is not modelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import cyl_texture as ct

N_THETA = ct.N_THETA
N_H = ct.N_H


class PatternModelError(RuntimeError):
    pass


def pattern_map(colour: np.ndarray, weight: np.ndarray, min_weight: float):
    """Flat-fielded, unmixed dark-pattern map in [0,1] and the observed mask (see ct.albedo for the reasoning)."""
    import cv2

    obs = weight > min_weight
    lum = ct._lum(colour).astype(np.float32)
    lm = np.where(obs, lum, 0).astype(np.float32)
    white_level = cv2.GaussianBlur(cv2.dilate(lm, np.ones((15, 15), np.uint8)), (0, 0), 10)
    ratio = np.where(obs, lum / np.maximum(white_level, 1.0), 1.0)
    dark_mask = obs & (ratio < 0.5)
    dark_ratio = float(np.median(ratio[dark_mask])) if dark_mask.sum() > 50 else 0.3
    pat = np.clip((1.0 - ratio) / max(1.0 - dark_ratio, 0.2), 0.0, 1.0)
    pat = np.where(pat < 0.08, 0.0, pat)
    return np.where(obs, pat, 0.0), obs


def _wrap(d, period):
    return (d + period / 2) % period - period / 2


def column_peaks(pat: np.ndarray, obs: np.ndarray, rows: slice, min_valid: float = 0.8):
    """Stripe centre columns (float) from the row-averaged pattern; only columns observed in most rows count."""
    band, ob = pat[rows], obs[rows]
    valid = ob.mean(axis=0) >= min_valid
    prof = np.where(valid, band.sum(0) / np.maximum(ob.sum(0), 1), 0.0)
    k = 5
    sm = np.convolve(np.r_[prof[-k:], prof, prof[:k]], np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    if not valid.any() or sm.max() <= 0:
        return [], valid
    peaks = [i for i in range(N_THETA) if valid[i] and sm[i] > 0.45 * sm[valid].max()
             and all(sm[i] >= sm[(i + d) % N_THETA] for d in range(-14, 15))]
    # merge plateau duplicates
    merged = []
    for p in peaks:
        if merged and (p - merged[-1]) % N_THETA < 14:
            continue
        merged.append(p)
    return merged, valid


@dataclass
class StripeModel:
    n_stripes: int
    centres_deg: list[float]  # lattice centres in the common gauge
    curve: np.ndarray  # canonical centre offset (columns) per row
    width: np.ndarray  # canonical stripe width (columns) per row
    row_lo: int
    row_hi: int
    edge_cols: float
    checks: dict = field(default_factory=dict)

    def synthesize(self) -> np.ndarray:
        cols = np.arange(N_THETA)[None, :].astype(float)
        out = np.zeros((N_H, N_THETA), np.float32)
        P = N_THETA / self.n_stripes
        for c_deg in self.centres_deg:
            c0 = c_deg / 360.0 * N_THETA
            d = _wrap(cols - c0 - self.curve[:, None], N_THETA)
            half = self.width[:, None] / 2.0
            s = np.clip((half - np.abs(d)) / max(self.edge_cols, 0.5) + 0.5, 0.0, 1.0)
            out = np.maximum(out, s.astype(np.float32))
        rows = np.arange(N_H)
        ends = np.clip((rows - self.row_lo) / 2.0 + 0.5, 0, 1) * np.clip((self.row_hi - rows) / 2.0 + 0.5, 0, 1)
        return out * ends[:, None]


def _stripe_curves(pat, obs, peaks, rows_valid_min: float = 0.9):
    """Per stripe: centre offset (columns) and width (columns) for every row where the stripe's window is fully observed."""
    out = []
    P = N_THETA / max(len(peaks), 1) if len(peaks) > 1 else 50.0
    half = int(0.45 * 51.4)
    cols_rel = np.arange(-half, half + 1)
    for c0 in peaks:
        idx = (c0 + cols_rel) % N_THETA
        win, ow = pat[:, idx], obs[:, idx]
        okrow = ow.mean(axis=1) >= rows_valid_min
        mass = win.sum(axis=1)
        cen = np.where(mass > 0.6, (win * cols_rel[None, :]).sum(axis=1) / np.maximum(mass, 1e-9), np.nan)
        # ends of the window must be (almost) background, otherwise a neighbour stripe leaks in
        edge = (win[:, :3].max(axis=1) < 0.4) & (win[:, -3:].max(axis=1) < 0.4)
        good = okrow & edge & (mass > 0.6)
        c = np.where(good, cen, np.nan)
        w = np.where(good, mass, np.nan)
        if np.isfinite(c).sum() >= 0.12 * N_H:
            out.append((c0, c, w))
    return out


def _align_rows(curves: list[np.ndarray], max_shift: int = 24):
    """Vertical shift per curve to a median template (stripes seen in different frames sit at slightly different rows)."""
    shifts = [0] * len(curves)
    for _ in range(3):
        aligned = [_shift_nan(c, s) for c, s in zip(curves, shifts)]
        tmpl = np.nanmedian(np.stack(aligned), axis=0)
        new = []
        for c in curves:
            best, bs = np.inf, 0
            for s in range(-max_shift, max_shift + 1):
                a = _shift_nan(c, s)
                m = np.isfinite(a) & np.isfinite(tmpl)
                if m.sum() < 20:
                    continue
                e = np.mean((a[m] - tmpl[m]) ** 2)
                if e < best:
                    best, bs = e, s
            new.append(bs)
        med = int(np.median(new))
        shifts = [s - med for s in new]  # gauge: median shift 0
    return shifts


def _shift_nan(a: np.ndarray, s: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    if s >= 0:
        out[s:] = a[: len(a) - s] if s else a
    else:
        out[:s] = a[-s:]
    return out


def fit_stripe_model(
    frame_unwraps: list[ct.Unwrap], fused: ct.Fused, *, min_weight: float = 0.08,
    max_spacing_cv: float = 0.10, position_mode: str = "observed", rows: slice = slice(int(0.12 * N_H), int(0.50 * N_H)),
) -> StripeModel:
    """Fit lattice + canonical waveform. Raises PatternModelError (with the reason) if the stripes are not periodic enough."""
    import cv2

    # 1. lattice from the fused map (cluster registration already applied)
    fpat, fobs = pattern_map(fused.colour, fused.weight, min_weight)
    peaks, valid = column_peaks(fpat, fobs, rows)
    if len(peaks) < 5:
        raise PatternModelError(f"only {len(peaks)} stripes found in the observed region (need >= 5 to measure a lattice)")
    pk = np.array(peaks, float)
    gaps = np.diff(pk)
    # spacing statistics from neighbours that were both observed (a gap spanning unobserved columns is not a spacing)
    ok = [g for a, g in zip(peaks[:-1], gaps) if valid[int(a): int(a + g) + 1].all()]
    if len(ok) < 3:
        raise PatternModelError("too few adjacent stripe pairs are observed to measure the spacing")
    ok = np.array(ok, float)
    cv = float(ok.std() / ok.mean())
    if cv > max_spacing_cv:
        raise PatternModelError(f"stripe spacing is not regular enough (cv={cv:.3f} > {max_spacing_cv})")
    n_float = N_THETA / float(np.median(ok))
    # the stripe count must be a whole number: choose the integer whose lattice best explains the observed centres
    best = None
    for n_try in range(max(int(np.floor(n_float)) - 1, 3), int(np.ceil(n_float)) + 2):
        P_try = N_THETA / n_try
        ang_try = (pk % P_try) / P_try * 2 * np.pi
        ph = (np.arctan2(np.sin(ang_try).mean(), np.cos(ang_try).mean()) % (2 * np.pi)) / (2 * np.pi) * P_try
        rms = float(np.sqrt(np.mean(_wrap(pk - ph, P_try) ** 2)))
        if best is None or rms / P_try < best[0]:
            best = (rms / P_try, n_try, P_try, ph, rms)
    rel_rms, n, P, phase, rms_cols = best
    if rel_rms > 0.30:
        raise PatternModelError(f"no whole stripe count fits the observed stripe centres (best n={n}, residual {rel_rms:.2f} of a period)")
    lattice_rms_deg = float(rms_cols * 360.0 / N_THETA)
    lattice_deg = [float(((phase + i * P) % N_THETA) * 360.0 / N_THETA) for i in range(n)]
    # observed stripes keep their MEASURED positions (the real object's spacing is not exactly regular, and the angular
    # gauge between viewpoint clusters is only known to a few degrees); only slots with no observation use the lattice
    centres_deg = []
    obs_cols_deg = [float(c) * 360.0 / N_THETA for c in peaks]
    for ld in lattice_deg:
        near = [o for o in obs_cols_deg if abs(_wrap(o - ld, 360.0)) < 0.3 * 360.0 / n]
        centres_deg.append(float(near[0]) if (position_mode == "observed" and near) else ld)

    # 2. canonical waveform from single frames (no cross-frame blur)
    curves_c, curves_w = [], []
    for u in frame_unwraps:
        pat_u, obs_u = pattern_map(u.colour, u.weight, 0.15)
        pk_u, _ = column_peaks(pat_u, obs_u, rows, min_valid=0.7)
        for c0, c, w in _stripe_curves(pat_u, obs_u, pk_u):
            curves_c.append(c - np.nanmean(c)); curves_w.append(w)
    if len(curves_c) < 4:
        raise PatternModelError(f"only {len(curves_c)} complete single-frame stripe observations (need >= 4)")
    shifts = _align_rows(curves_c)
    ac = np.stack([_shift_nan(c, s) for c, s in zip(curves_c, shifts)])
    aw = np.stack([_shift_nan(w, s) for w, s in zip(curves_w, shifts)])
    support = np.isfinite(ac).sum(axis=0)
    ok_rows = support >= max(3, int(0.3 * len(curves_c)))
    if ok_rows.sum() < 0.2 * N_H:
        raise PatternModelError("stripe observations do not overlap vertically enough to build a canonical waveform")
    curve = np.where(ok_rows, np.nanmedian(np.where(np.isfinite(ac), ac, np.nan), axis=0), np.nan)
    width = np.where(ok_rows, np.nanmedian(aw, axis=0), np.nan)
    # agreement between stripes = how well "one shared waveform" describes them
    dev = np.nanstd(ac, axis=0)
    shared = float(np.nanmedian(dev[ok_rows]))
    rows_idx = np.nonzero(ok_rows)[0]
    lo, hi = int(rows_idx.min()), int(rows_idx.max())
    # fill interior gaps linearly, extend ends flat
    xs = np.arange(N_H)
    curve = np.interp(xs, rows_idx, curve[rows_idx])
    width = np.interp(xs, rows_idx, width[rows_idx])
    k = 4  # light smoothing: the per-row median over a few dozen stripes is still a little jagged
    sm = lambda a: np.convolve(np.pad(a, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    curve, width = sm(curve), sm(width)
    # vertical extent of the stripe: where its width is a sizeable fraction of the maximum, probed on the full pattern map
    colmass = fpat.copy()
    ext_rows = np.nonzero(colmass[:, [int(c / 360 * N_THETA) % N_THETA for c in centres_deg if valid[int(c / 360 * N_THETA) % N_THETA]]].mean(axis=1) > 0.35)[0] if valid.any() else rows_idx
    if len(ext_rows) < 10:
        ext_rows = rows_idx
    row_lo, row_hi = int(ext_rows.min()), int(ext_rows.max())
    model = StripeModel(
        n_stripes=n, centres_deg=centres_deg, curve=curve, width=width, row_lo=row_lo, row_hi=row_hi, edge_cols=1.2,
        checks={
            "stripes_observed_for_lattice": len(peaks), "spacing_cv": cv, "spacing_deg_mean": float(np.median(ok) * 360.0 / N_THETA),
            "implied_count": n_float, "stripe_count_chosen": n, "lattice_rel_residual": rel_rms, "lattice_rms_residual_deg": lattice_rms_deg, "position_mode": position_mode, "slots_with_observed_position": sum(1 for a, b in zip(centres_deg, lattice_deg) if a != b), "slots_from_lattice": sum(1 for a, b in zip(centres_deg, lattice_deg) if a == b),
            "single_frame_stripe_observations": len(curves_c), "waveform_shared_std_cols": shared,
            "waveform_amplitude_cols": float(np.nanmax(curve[rows_idx]) - np.nanmin(curve[rows_idx])),
            "stripe_width_deg": float(np.nanmedian(width[rows_idx]) * 360.0 / N_THETA),
            "vertical_extent_h": [row_lo / N_H, row_hi / N_H],
        },
    )
    return model
