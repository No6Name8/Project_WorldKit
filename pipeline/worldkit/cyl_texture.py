"""Cylindrical multi-view texture fusion for bodies of revolution (mugs, cups, bottles, vases).

Why not project frames straight into the mesh's UV atlas (``texture_project``)? On the SAM 3D mug the atlas is cut into
many small charts, every frame is only approximately registered, and per-texel blending of slightly disagreeing frames
produces ghosting / halftone patterns. A cup's surface has a natural seam-free parameterisation, (theta, h): angle
around the body axis and height. Here every frame is *unwrapped* into that domain, registered by a 2-D shift against the
growing fused map (a one-line problem instead of a 3-D pose problem), fused, and only then resampled into the UV atlas.

Pipeline (all steps record what they did):
 1. ``fit_body``: axis (Y after alignment), centre and radius profile r(h) from the mesh body (handle masked by azimuth).
 2. ``unwrap_frame``: for each (theta, h) the 3-D surface point is projected through the frame's pose; kept only if it
    faces the camera (cos >= min_cos), passes the mesh z-buffer test (handle/rim occlusion) and is inside the object's
    mask, away from its silhouette edge.
 3. ``fuse``: frames are added best-anchored first; each non-anchor frame is shifted (dtheta, dh) to maximise the
    correlation of high-passed luminance with the current fused map, and rejected when the correlation is weak.
 4. ``albedo``: flat-field normalise (removes shading), split into a two-colour pattern with a soft mask, colours taken
    from observed pixels. This is a constrained pattern representation: lighting is not baked into the albedo.
 5. ``continue_pattern``: unobserved angles are filled by periodic repetition of the observed stripes only when their
    spacing is demonstrably regular; the fill is tagged `inferred`, never `observed`.
 6. ``to_uv``: texels on the body (not rim, handle, interior, base) take the fused albedo; everything else keeps the
    generated colour. Geometry and UVs are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import texture_project as tp

N_THETA = 720
N_H = 256


class CylTextureError(RuntimeError):
    pass


# --- body model -----------------------------------------------------------------------------------------------------

@dataclass
class Body:
    cx: float
    cz: float
    y0: float  # base
    y1: float  # rim
    radius: np.ndarray  # r(h) on an N_H grid, h in [0,1]
    handle_az: float

    def r_at(self, h):
        return np.interp(h, np.linspace(0, 1, len(self.radius)), self.radius)

    def point(self, theta, h):
        r = self.r_at(h)
        return np.stack([self.cx + r * np.cos(theta), self.y0 + h * (self.y1 - self.y0), self.cz + r * np.sin(theta)], axis=-1)


def _circle_fit(p: np.ndarray):
    A = np.c_[2 * p, np.ones(len(p))]
    sol = np.linalg.lstsq(A, (p ** 2).sum(1), rcond=None)[0]
    return sol[:2], float(np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 1e-12)))


def fit_body(v: np.ndarray, handle_exclusion_deg: float = 55.0) -> Body:
    from .mesh_qc import handle_azimuth

    v = np.asarray(v, float)
    ang = handle_azimuth(v)
    az = np.arctan2(v[:, 2], v[:, 0] - np.median(v[:, 0]))
    dif = np.abs(np.arctan2(np.sin(az - ang), np.cos(az - ang)))
    body = v[dif > np.radians(handle_exclusion_deg)]
    lo, hi = body[:, 1].min(), body[:, 1].max()
    H = hi - lo
    hs = np.linspace(0.04, 0.96, 24)
    centres, radii = [], []
    for h in hs:
        m = np.abs(body[:, 1] - (lo + h * H)) < 0.03 * H
        if m.sum() < 25:
            centres.append([np.nan, np.nan]); radii.append(np.nan)
            continue
        p = body[m][:, [0, 2]]
        c0 = np.median(p, axis=0)
        r0 = np.linalg.norm(p - c0, axis=1)
        q = p[r0 > np.percentile(r0, 55)]  # start from the outer half (a slice also contains the inner wall)
        c, r = _circle_fit(q)
        for _ in range(4):  # inlier refinement: keep only vertices ON the fitted outer circle, refit
            rr = np.linalg.norm(p - c, axis=1)
            keep = np.abs(rr - r) < 0.04 * r
            if keep.sum() < 20:
                break
            c, r = _circle_fit(p[keep])
        centres.append(c); radii.append(r)
    centres, radii = np.array(centres), np.array(radii)
    ok = np.isfinite(radii)
    if ok.sum() < 6:
        raise CylTextureError("could not fit a body of revolution to the mesh (too few usable slices)")
    cx, cz = np.nanmedian(centres[:, 0]), np.nanmedian(centres[:, 1])
    grid = np.linspace(0, 1, N_H)
    prof = np.interp(grid, hs[ok], radii[ok])
    k = 9
    prof = np.convolve(np.pad(prof, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    return Body(float(cx), float(cz), float(lo), float(hi), prof, float(ang))


# --- unwrapping -----------------------------------------------------------------------------------------------------

@dataclass
class Unwrap:
    name: str
    colour: np.ndarray  # (N_H, N_THETA, 3) float
    weight: np.ndarray  # (N_H, N_THETA)


def _grid():
    th = (np.arange(N_THETA) + 0.5) / N_THETA * 2 * np.pi
    h = (np.arange(N_H) + 0.5) / N_H
    return np.meshgrid(th, h)  # each (N_H, N_THETA)


def unwrap_frame(
    name: str, rgb: np.ndarray, mask: np.ndarray, pose: tp.CameraPose, vertices: np.ndarray, faces: np.ndarray,
    body: Body, *, min_cos: float = 0.30, feather_px: float = 5.0, depth_tolerance: float = 0.04,
) -> Unwrap:
    import cv2

    TH, HH = _grid()
    pts = body.point(TH, HH).reshape(-1, 3)
    nrm = np.stack([np.cos(TH), np.zeros_like(TH), np.sin(TH)], axis=-1).reshape(-1, 3)
    h_img, w_img = mask.shape
    xy, depth = pose.project(vertices)
    zbuf, _, _ = tp.rasterize(xy, depth, faces, (w_img, h_img))
    pxy, pdepth = pose.project(pts)
    ix, iy = np.clip(pxy[:, 0].astype(int), 0, w_img - 1), np.clip(pxy[:, 1].astype(int), 0, h_img - 1)
    in_frame = (pxy[:, 0] >= 0) & (pxy[:, 0] < w_img) & (pxy[:, 1] >= 0) & (pxy[:, 1] < h_img)
    tol = depth_tolerance * (depth.max() - depth.min())
    visible = in_frame & np.isfinite(zbuf[iy, ix]) & (pdepth >= zbuf[iy, ix] - tol)
    ncam = nrm @ pose.rotation().T
    cos = ncam[:, 2]
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    feather = np.clip((dist[iy, ix] - 1.0) / feather_px, 0.0, 1.0)
    w = np.where(visible & (cos >= min_cos) & mask[iy, ix], cos ** 2 * feather, 0.0)
    colour = tp._bilinear(rgb, pxy[:, 0] - 0.5, pxy[:, 1] - 0.5)
    return Unwrap(name, colour.reshape(N_H, N_THETA, 3), w.reshape(N_H, N_THETA))


def _lum(c: np.ndarray) -> np.ndarray:
    return 0.299 * c[..., 0] + 0.587 * c[..., 1] + 0.114 * c[..., 2]


def _highpass(l: np.ndarray, w: np.ndarray) -> np.ndarray:
    import cv2

    num = cv2.GaussianBlur((l * (w > 0.02)).astype(np.float32), (0, 0), 12, borderType=cv2.BORDER_WRAP if False else cv2.BORDER_REFLECT)
    den = cv2.GaussianBlur((w > 0.02).astype(np.float32), (0, 0), 12, borderType=cv2.BORDER_REFLECT)
    return l - num / np.maximum(den, 1e-3)


def _shift(a: np.ndarray, dth: int, dh: int) -> np.ndarray:
    out = np.roll(a, dth, axis=1)
    if dh > 0:
        out = np.concatenate([np.zeros((dh,) + out.shape[1:], out.dtype), out[:-dh]], axis=0)
    elif dh < 0:
        out = np.concatenate([out[-dh:], np.zeros((-dh,) + out.shape[1:], out.dtype)], axis=0)
    return out


def _wncc(a, b, w):
    s = w.sum()
    if s < 50:
        return -1.0
    ma, mb = (a * w).sum() / s, (b * w).sum() / s
    da, db = a - ma, b - mb
    den = np.sqrt(((da * da) * w).sum() * ((db * db) * w).sum())
    return float(((da * db) * w).sum() / den) if den > 1e-9 else -1.0


@dataclass
class Fused:
    colour: np.ndarray
    weight: np.ndarray
    registration: list = field(default_factory=list)


def _grow_cluster(anchor: Unwrap, pool: list[Unwrap], cid: int, *, max_dth: int, max_dh: int, min_ncc: float, min_overlap: float, refine_passes: int = 1, shift_lambda: float = 0.004):
    """Greedy registration of frames that share observed surface with ``anchor`` (and with each other, transitively)."""
    shift_penalty = lambda dth, dh: shift_lambda * (abs(dth) * 360.0 / N_THETA + abs(dh))
    S = anchor.colour * anchor.weight[..., None]
    W = anchor.weight.copy()
    log = [{"frame": anchor.name, "cluster": cid, "role": "anchor" if cid == 0 else "cluster anchor (gauge from silhouette pose only)", "dtheta_deg": 0.0, "dh_px": 0, "ncc": None}]
    used = [anchor.name]
    placed = {anchor.name: (anchor, 0, 0, log[0])}
    pool = list(pool)
    while pool:
        overlaps = [float(np.minimum(u.weight, W).sum()) for u in pool]
        k = int(np.argmax(overlaps))
        if overlaps[k] < min_overlap:
            break
        u = pool.pop(k)
        fused = S / np.maximum(W[..., None], 1e-9)
        fl = _highpass(_lum(fused), W)
        ul = _highpass(_lum(u.colour), u.weight)
        best = (-2.0, 0, 0, 0.0)

        def score(dth, dh):
            ov = np.minimum(_shift(u.weight, dth, dh), W)
            if ov.sum() < min_overlap:
                return None
            return _wncc(_shift(ul, dth, dh), fl, ov) - shift_penalty(dth, dh), float(ov.sum())

        for dth in range(-max_dth, max_dth + 1, 2):
            for dh in range(-max_dh, max_dh + 1, 2):
                r = score(dth, dh)
                if r and r[0] > best[0]:
                    best = (r[0], dth, dh, r[1])
        _, d0, h0, _ = best
        for dth in range(d0 - 1, d0 + 2):
            for dh in range(h0 - 1, h0 + 2):
                r = score(dth, dh)
                if r and r[0] > best[0]:
                    best = (r[0], dth, dh, r[1])
        ncc, dth, dh, ovs = best
        rec = {"frame": u.name, "cluster": cid, "role": "registered", "dtheta_deg": dth * 360.0 / N_THETA, "dh_px": dh, "ncc": round(ncc, 3), "overlap_weight": round(ovs, 1)}
        if ncc < min_ncc:
            rec["role"] = "rejected (registration correlation too weak)"
            log.append(rec)
            continue
        S += _shift(u.colour * u.weight[..., None], dth, dh)
        W += _shift(u.weight, dth, dh)
        log.append(rec)
        used.append(u.name)
        placed[u.name] = (u, dth, dh, rec)
    # leave-one-out refinement: re-register each non-anchor frame against the fused map of ALL the others, which removes
    # the order bias of greedy growth (an early frame was registered against very little evidence)
    for _ in range(refine_passes):
        for name in [n for n in placed if n != anchor.name]:
            u, dth0, dh0, rec = placed[name]
            S_o, W_o = S - _shift(u.colour * u.weight[..., None], dth0, dh0), W - _shift(u.weight, dth0, dh0)
            W_o = np.maximum(W_o, 0)
            fl = _highpass(_lum(S_o / np.maximum(W_o[..., None], 1e-9)), W_o)
            ul = _highpass(_lum(u.colour), u.weight)
            best = (-2.0, dth0, dh0)
            for dth in range(dth0 - 4, dth0 + 5):
                for dh in range(dh0 - 4, dh0 + 5):
                    ov = np.minimum(_shift(u.weight, dth, dh), W_o)
                    if ov.sum() < min_overlap:
                        continue
                    sc = _wncc(_shift(ul, dth, dh), fl, ov) - shift_penalty(dth, dh)
                    if sc > best[0]:
                        best = (sc, dth, dh)
            if best[0] > -1.5 and (best[1], best[2]) != (dth0, dh0):
                S, W = S_o + _shift(u.colour * u.weight[..., None], best[1], best[2]), W_o + _shift(u.weight, best[1], best[2])
                rec["dtheta_deg"], rec["dh_px"], rec["ncc"] = best[1] * 360.0 / N_THETA, best[2], round(best[0], 3)
                rec["refined"] = True
                placed[name] = (u, best[1], best[2], rec)
            else:
                S, W = S_o + _shift(u.colour * u.weight[..., None], dth0, dh0), W_o + _shift(u.weight, dth0, dh0)
    return S, W, log, used


def fuse(unwraps: list[Unwrap], *, max_dtheta_deg: float = 12.0, max_dh: int = 16, min_ncc: float = 0.25, min_overlap: float = 150.0, refine_passes: int = 0, shift_lambda: float = 0.004) -> Fused:
    """Register unwraps by 2-D shift against the growing fused map, then average.

    The first unwrap is the global anchor (never shifted; it fixes the angular gauge, so it should be a frame whose pose
    is well constrained, e.g. handle visible). Frames are added greedily, always the one overlapping the fused map most,
    because a frame can only be registered against evidence it shares. Viewpoints that share NO surface with the cluster
    (the video orbits the mug, so views far apart see different sides) form their own cluster; it is registered
    internally but placed using its silhouette pose only, which is reported. Frames with a weak registration
    correlation are rejected, not blended.
    """
    if not unwraps:
        raise CylTextureError("no unwrapped frames")
    max_dth = int(max_dtheta_deg / 360.0 * N_THETA)
    pool = list(unwraps)
    S_tot = np.zeros_like(unwraps[0].colour)
    W_tot = np.zeros_like(unwraps[0].weight)
    log: list = []
    cid = 0
    while pool:
        anchor = pool.pop(0)
        S, W, lg, used = _grow_cluster(anchor, pool, cid, max_dth=max_dth, max_dh=max_dh, min_ncc=min_ncc, min_overlap=min_overlap, refine_passes=refine_passes, shift_lambda=shift_lambda)
        pool = [u for u in pool if u.name not in used]
        S_tot += S
        W_tot += W
        log += lg
        cid += 1
    return Fused(S_tot / np.maximum(W_tot[..., None], 1e-9), W_tot, log)


# --- albedo ---------------------------------------------------------------------------------------------------------

@dataclass
class Albedo:
    image: np.ndarray  # (N_H, N_THETA, 3) uint8
    pattern: np.ndarray  # soft dark-pattern mask in [0,1]
    observed: np.ndarray  # bool, from real pixels
    inferred: np.ndarray  # bool, periodic continuation
    white: np.ndarray
    dark: np.ndarray
    info: dict = field(default_factory=dict)


def albedo(fused: Fused, base_texture: np.ndarray | None = None, *, min_weight: float = 0.08, inferred_min_cv: float = 0.12) -> Albedo:
    import cv2

    W = fused.weight
    obs = W > min_weight
    lum = _lum(fused.colour).astype(np.float32)
    # flat-field: local white level = smoothed max filter over observed pixels (removes shading and exposure ramps)
    lm = np.where(obs, lum, 0).astype(np.float32)
    white_level = cv2.GaussianBlur(cv2.dilate(lm, np.ones((15, 15), np.uint8)), (0, 0), 10)
    ratio = np.where(obs, lum / np.maximum(white_level, 1.0), 1.0)
    # linear unmixing: pixel = (1-a)*white + a*dark in flat-fielded luminance, with `dark` the median ratio of clearly
    # dark pixels. Keeps the (blurred) edge profile instead of imposing a hard threshold that would thin or fatten stripes.
    dark_ratio = float(np.median(ratio[obs & (ratio < 0.5)])) if (obs & (ratio < 0.5)).sum() > 50 else 0.3
    pat = np.clip((1.0 - ratio) / max(1.0 - dark_ratio, 0.2), 0.0, 1.0)
    pat = np.where(pat < 0.08, 0.0, pat)
    pat = np.where(obs, pat, 0.0)
    strong = obs & (W > np.percentile(W[obs], 40))
    dark_px = fused.colour[strong & (pat > 0.85)]
    white_px = fused.colour[strong & (pat < 0.05)]
    if len(dark_px) < 50 or len(white_px) < 50:
        raise CylTextureError("could not separate a two-colour pattern in the fused observations")
    dark = np.median(dark_px, axis=0)
    white = np.median(white_px, axis=0)
    white_source = "observed pixels (photo exposure)"
    if base_texture is not None:
        b = base_texture[..., :3].reshape(-1, 3).astype(float)
        bl = _lum(b)
        white = np.median(b[bl >= np.percentile(bl, 60)], axis=0)  # the generated body colour: consistent with unobserved areas
        white_source = "generated base texture (a photo's white level depends on exposure)"
    inferred = np.zeros_like(obs)
    info: dict = {"observed_theta_fraction": float((obs.any(axis=0)).mean()), "white_colour_source": white_source, "white_rgb": [float(x) for x in white], "dark_rgb": [float(x) for x in dark]}

    # periodic continuation along theta, only if the observed stripes are demonstrably regular
    band = pat[int(0.10 * N_H): int(0.60 * N_H)]
    col_obs = obs[int(0.10 * N_H): int(0.60 * N_H)].mean(axis=0) > 0.6
    prof = band.mean(axis=0)
    info["stripe_profile_columns_observed"] = int(col_obs.sum())
    period_cols = None
    cv = None
    if col_obs.mean() >= 0.35:
        # stripe centres: local maxima of the (smoothed) profile restricted to observed columns
        k = 7
        sm = np.convolve(np.r_[prof[-k:], prof, prof[:k]], np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
        peaks = [i for i in range(N_THETA) if col_obs[i] and sm[i] > 0.5 * sm[col_obs].max() and sm[i] >= sm[(i - 1) % N_THETA] and sm[i] > sm[(i + 1) % N_THETA]]
        # keep peaks that are real local maxima (>= neighbours within +-12 columns)
        peaks = [i for i in peaks if all(sm[i] >= sm[(i + d) % N_THETA] for d in range(-12, 13))]
        info["stripe_centres_deg"] = [round(i * 360.0 / N_THETA, 1) for i in peaks]
        gaps = []
        for a, b in zip(peaks[:-1], peaks[1:]):
            if col_obs[a:b + 1].all():
                gaps.append(b - a)
        if len(gaps) >= 3:
            gaps = np.array(gaps, float)
            cv = float(gaps.std() / gaps.mean())
            info["stripe_spacing_deg_mean"] = float(gaps.mean() * 360.0 / N_THETA)
            info["stripe_spacing_cv"] = cv
            if cv <= inferred_min_cv:
                period_cols = float(np.median(gaps))
    pat_f = pat.copy()
    obs_cols = obs.any(axis=0)
    if period_cols is not None and not obs_cols.all():
        n_stripes = 360.0 / (period_cols * 360.0 / N_THETA)
        info["implied_stripe_count"] = n_stripes
        # only fill when the stripe count is (close to) a whole number: a regular pattern closes on itself
        if abs(n_stripes - round(n_stripes)) <= 0.25:
            P = 360.0 / round(n_stripes) / 360.0 * N_THETA
            info["period_deg_used"] = P * 360.0 / N_THETA
            for j in np.nonzero(~obs_cols)[0]:
                for k_ in range(1, int(round(n_stripes))):
                    for sgn in (1, -1):
                        src = int(round(j + sgn * k_ * P)) % N_THETA
                        if obs_cols[src] and col_obs[src]:
                            pat_f[:, j] = pat[:, src]
                            inferred[:, j] = True
                            break
                    if inferred[0, j]:
                        break
            info["inferred_theta_fraction"] = float(inferred.any(axis=0).mean())
        else:
            info["continuation"] = f"not applied: implied stripe count {n_stripes:.2f} is not close to a whole number"
    elif period_cols is None:
        info["continuation"] = "not applied: stripe spacing not demonstrably regular" if cv is not None else "not applied: too few stripes observed to measure spacing"
    img = white[None, None, :] * (1 - pat_f[..., None]) + dark[None, None, :] * pat_f[..., None]
    return Albedo(np.clip(img, 0, 255).astype(np.uint8), pat_f, obs, inferred, white, dark, info)


# --- UV resampling --------------------------------------------------------------------------------------------------

def to_uv(
    vertices: np.ndarray, faces: np.ndarray, uvs: np.ndarray, base_texture: np.ndarray, body: Body, alb: Albedo,
    *, min_radial_dot: float = 0.6, h_margin: float = 0.03,
) -> tuple[np.ndarray, dict]:
    import cv2

    th_px, tw_px = base_texture.shape[:2]
    ids, bary = tp._uv_texel_samples(uvs, faces, (tw_px, th_px))
    valid = ids >= 0
    fi = ids[valid]
    pts = (bary[valid][:, :, None] * vertices[faces[fi]]).sum(axis=1)
    e1, e2 = vertices[faces[fi, 1]] - vertices[faces[fi, 0]], vertices[faces[fi, 2]] - vertices[faces[fi, 0]]
    nrm = np.cross(e1, e2)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    dx, dz = pts[:, 0] - body.cx, pts[:, 2] - body.cz
    rho = np.hypot(dx, dz)
    theta = np.arctan2(dz, dx) % (2 * np.pi)
    h = (pts[:, 1] - body.y0) / (body.y1 - body.y0)
    rad = body.r_at(np.clip(h, 0, 1))
    radial_dot = (nrm[:, 0] * dx + nrm[:, 2] * dz) / np.maximum(rho, 1e-9)
    on_body = (np.abs(rho - rad) < 0.12 * rad) & (radial_dot > min_radial_dot) & (h > h_margin) & (h < 1 - h_margin)

    def lookup(a2d, th, hh):
        x = th / (2 * np.pi) * N_THETA - 0.5
        y = hh * N_H - 0.5
        return tp._bilinear(a2d if a2d.ndim == 3 else a2d[..., None], x, y)

    img3 = lookup(alb.image.astype(np.float32), theta, np.clip(h, 0, 1))
    obs = lookup(alb.observed.astype(np.float32), theta, np.clip(h, 0, 1))[:, 0]
    inf = lookup(alb.inferred.astype(np.float32), theta, np.clip(h, 0, 1))[:, 0]
    alpha = np.where(on_body, np.clip(obs + inf, 0, 1), 0.0)
    out = base_texture[..., :3].astype(float).copy()
    flat = out[valid]
    out[valid] = flat * (1 - alpha[:, None]) + img3 * alpha[:, None]
    result = np.clip(out, 0, 255).astype(np.uint8)
    gutter = (~valid).astype(np.uint8)
    if gutter.any():
        res2 = cv2.inpaint(result, gutter * 255, 2, cv2.INPAINT_TELEA)
        res2[valid] = result[valid]
        result = res2
    n = max(len(pts), 1)
    stats = {
        "texels_on_body_fraction": float(on_body.sum() / n),
        "observed_fraction": float(((obs > 0.5) & on_body).sum() / n),
        "inferred_fraction": float(((inf > 0.5) & (obs <= 0.5) & on_body).sum() / n),
        "generated_fraction": float(1.0 - ((alpha > 0.5)).sum() / n),
    }
    return result, stats


def albedo_from_pattern(pat: np.ndarray, fused: Fused, base_texture: np.ndarray | None, info: dict, *, min_weight: float = 0.08) -> Albedo:
    """Two-colour albedo from a (synthesised) pattern map. `observed` = angles with real pixels, `inferred` = the rest."""
    from .pattern_model import pattern_map

    obs_pat, obs = pattern_map(fused.colour, fused.weight, min_weight)
    strong = obs & (fused.weight > np.percentile(fused.weight[obs], 40))
    dark_px = fused.colour[strong & (obs_pat > 0.85)]
    if len(dark_px) < 50:
        raise CylTextureError("no clearly dark observed pixels to take the pattern colour from")
    dark = np.median(dark_px, axis=0)
    if base_texture is not None:
        b = base_texture[..., :3].reshape(-1, 3).astype(float)
        bl = _lum(b)
        white = np.median(b[bl >= np.percentile(bl, 60)], axis=0)
    else:
        white = np.median(fused.colour[strong & (obs_pat < 0.05)], axis=0)
    img = white[None, None, :] * (1 - pat[..., None]) + dark[None, None, :] * pat[..., None]
    obs_cols = obs.any(axis=0)
    observed = np.zeros_like(obs)
    observed[:, obs_cols] = True
    inferred = ~observed
    err = np.abs(obs_pat - pat)[obs]
    info = dict(info)
    info.update({
        "white_rgb": [float(x) for x in white], "dark_rgb": [float(x) for x in dark],
        "model_vs_observation_mae": float(err.mean()), "model_vs_observation_rmse": float(np.sqrt((err ** 2).mean())),
        "observed_theta_fraction": float(obs_cols.mean()),
    })
    return Albedo(np.clip(img, 0, 255).astype(np.uint8), pat.astype(np.float32), observed, inferred, white, dark, info)
