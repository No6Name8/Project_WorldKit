"""Re-project real video frames onto a generated mesh's texture (appearance from real pixels).

Generated 3D models (e.g. TRELLIS) paint *invented*, coarse appearance. When the source
video is available we can do better without touching the geometry: estimate each frame's
camera pose from its SAM silhouette, then overwrite the texels that frame actually SEES with
the real pixels. Texels no frame sees keep the generated colours and are reported as such
(``observed_fraction``) -- nothing is invented, and nothing is claimed beyond what was observed.

Camera model: weak-perspective (orthographic) -- the object is small in frame (~150 px of a
480x856 phone video) so perspective distortion is minor. Mesh convention: glTF Y-up.
A pose is (azimuth, elevation, scale, tx, ty): yaw about Y, then tilt the object so a camera
``elevation`` degrees ABOVE the horizon sees its top (+Y) face,
then ``x_img = scale*X + tx``, ``y_img = -scale*Y + ty`` (image y points down).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw


class TextureProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraPose:
    az_deg: float
    el_deg: float
    scale: float  # image pixels per mesh unit
    tx: float
    ty: float

    def rotation(self) -> np.ndarray:
        az, el = np.radians(self.az_deg), np.radians(self.el_deg)
        yaw = np.array([[np.cos(az), 0, np.sin(az)], [0, 1, 0], [-np.sin(az), 0, np.cos(az)]])
        pitch = np.array([[1, 0, 0], [0, np.cos(el), -np.sin(el)], [0, np.sin(el), np.cos(el)]])
        return pitch @ yaw

    def project(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (xy image coords Nx2, depth N). Larger depth = closer to the camera."""
        cam = points @ self.rotation().T
        xy = np.stack([self.scale * cam[:, 0] + self.tx, -self.scale * cam[:, 1] + self.ty], axis=1)
        return xy, cam[:, 2]


def silhouette(vertices: np.ndarray, faces: np.ndarray, pose: CameraPose, size: tuple[int, int]) -> np.ndarray:
    """Fast boolean silhouette (no depth test needed) of the mesh under ``pose``; size = (w, h)."""
    xy, _ = pose.project(vertices)
    img = Image.new("1", size, 0)
    draw = ImageDraw.Draw(img)
    tri = xy[faces]
    for t in tri:
        draw.polygon([tuple(p) for p in t], fill=1)
    return np.array(img, dtype=bool)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _anchored_pose(vertices: np.ndarray, az: float, el: float, mask: np.ndarray) -> CameraPose:
    """Pose at (az, el) whose scale/translation make the mesh's projected bbox match the mask's bbox."""
    probe = CameraPose(az, el, 1.0, 0.0, 0.0)
    xy, _ = probe.project(vertices)
    mx0, my0, mx1, my1 = _bbox(mask)
    w, h = xy[:, 0].max() - xy[:, 0].min(), xy[:, 1].max() - xy[:, 1].min()
    scale = 0.5 * ((mx1 - mx0) / max(w, 1e-9) + (my1 - my0) / max(h, 1e-9))
    cx, cy = (xy[:, 0].max() + xy[:, 0].min()) / 2, (xy[:, 1].max() + xy[:, 1].min()) / 2
    return CameraPose(az, el, scale, (mx0 + mx1) / 2 - scale * cx, (my0 + my1) / 2 - scale * cy)


@dataclass(frozen=True)
class PoseFit:
    pose: CameraPose
    iou: float
    # IoU lost when azimuth is shifted by +/-20 deg: small => azimuth is poorly constrained
    # by the silhouette (e.g. the handle is hidden), so the pose should not be trusted.
    azimuth_sensitivity: float


def fit_pose(
    vertices: np.ndarray,
    faces: np.ndarray,
    mask: np.ndarray,
    *,
    az_step: float = 10.0,
    elevations: tuple[float, ...] = (10.0, 25.0, 40.0, 55.0),
    refine_rounds: int = 3,
) -> PoseFit:
    """Estimate the camera pose of a frame from its silhouette ``mask`` (bool HxW)."""
    if mask.sum() < 100:
        raise TextureProjectionError("mask too small to fit a pose")
    size = (mask.shape[1], mask.shape[0])
    coarse = []
    for el in elevations:
        for az in np.arange(0.0, 360.0, az_step):
            p = _anchored_pose(vertices, float(az), float(el), mask)
            coarse.append((iou(silhouette(vertices, faces, p, size), mask), p))
    coarse.sort(key=lambda t: -t[0])

    best_iou, best = coarse[0]
    steps = np.array([4.0, 4.0, 0.03, 3.0, 3.0])  # az, el, rel-scale, tx, ty
    for _ in range(refine_rounds):
        improved = True
        while improved:
            improved = False
            for axis in range(5):
                for sign in (-1.0, 1.0):
                    vals = [best.az_deg, best.el_deg, best.scale, best.tx, best.ty]
                    vals[axis] += sign * steps[axis] * (best.scale if axis == 2 else 1.0)
                    cand = CameraPose(*vals)
                    score = iou(silhouette(vertices, faces, cand, size), mask)
                    if score > best_iou + 1e-4:
                        best_iou, best, improved = score, cand, True
        steps = steps / 2.0

    sens = []
    for d in (-20.0, 20.0):
        shifted = CameraPose(best.az_deg + d, best.el_deg, best.scale, best.tx, best.ty)
        sens.append(best_iou - iou(silhouette(vertices, faces, shifted, size), mask))
    return PoseFit(pose=best, iou=best_iou, azimuth_sensitivity=float(min(sens)))


# --- rasterisation ------------------------------------------------------------------------------


def _tri_barycentric(p: np.ndarray, gx: np.ndarray, gy: np.ndarray):
    d = (p[1, 1] - p[2, 1]) * (p[0, 0] - p[2, 0]) + (p[2, 0] - p[1, 0]) * (p[0, 1] - p[2, 1])
    if abs(d) < 1e-12:
        return None
    l0 = ((p[1, 1] - p[2, 1]) * (gx - p[2, 0]) + (p[2, 0] - p[1, 0]) * (gy - p[2, 1])) / d
    l1 = ((p[2, 1] - p[0, 1]) * (gx - p[2, 0]) + (p[0, 0] - p[2, 0]) * (gy - p[2, 1])) / d
    return l0, l1, 1.0 - l0 - l1


def rasterize(xy: np.ndarray, depth: np.ndarray, faces: np.ndarray, size: tuple[int, int]):
    """Z-buffer rasterise triangles (pixel centres at +0.5). Returns (zbuf HxW, face_id HxW, bary HxWx3)."""
    w, h = size
    zbuf = np.full((h, w), -np.inf)
    ids = np.full((h, w), -1, dtype=np.int32)
    bary = np.zeros((h, w, 3))
    eps = -1e-6
    for fi in range(len(faces)):
        f = faces[fi]
        p = xy[f]
        x0, x1 = max(int(np.floor(p[:, 0].min())), 0), min(int(np.ceil(p[:, 0].max())), w - 1)
        y0, y1 = max(int(np.floor(p[:, 1].min())), 0), min(int(np.ceil(p[:, 1].max())), h - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        res = _tri_barycentric(p, gx, gy)
        if res is None:
            continue
        l0, l1, l2 = res
        inside = (l0 >= eps) & (l1 >= eps) & (l2 >= eps)
        if not inside.any():
            continue
        z = l0 * depth[f[0]] + l1 * depth[f[1]] + l2 * depth[f[2]]
        sub_z = zbuf[y0 : y1 + 1, x0 : x1 + 1]
        upd = inside & (z > sub_z)
        if not upd.any():
            continue
        sub_z[upd] = z[upd]
        ids[y0 : y1 + 1, x0 : x1 + 1][upd] = fi
        b = bary[y0 : y1 + 1, x0 : x1 + 1]
        b[..., 0][upd], b[..., 1][upd], b[..., 2][upd] = l0[upd], l1[upd], l2[upd]
    return zbuf, ids, bary


def rasterize_fast(xy: np.ndarray, depth: np.ndarray, faces: np.ndarray, size: tuple[int, int]):
    """Painter's-algorithm rasteriser (far->near via OpenCV): ~100x faster than ``rasterize``.

    Faces are drawn in order of mean depth, so it can mis-order interpenetrating or very
    unevenly sized faces; for the small, dense triangles of a generated mesh that only affects a
    negligible number of boundary pixels (see test_rasterize_fast_matches_exact_zbuffer).
    Returns the same (zbuf, face_id, bary) triple as ``rasterize``.
    """
    import cv2

    w, h = size
    order = np.argsort(depth[faces].mean(axis=1))  # far (small depth) first, near last
    canvas = np.zeros((h, w), dtype=np.int32)
    tri = np.round(xy[faces[order]] * 16.0).astype(np.int32)  # 4 fractional bits for sub-pixel accuracy
    for k, t in zip(order, tri):
        cv2.fillConvexPoly(canvas, t, int(k) + 1, lineType=cv2.LINE_8, shift=4)
    ids = canvas - 1
    covered = ids >= 0
    zbuf = np.full((h, w), -np.inf)
    bary = np.zeros((h, w, 3))
    if covered.any():
        ys, xs = np.nonzero(covered)
        f = faces[ids[ys, xs]]
        p = xy[f]  # n x 3 x 2
        gx, gy = xs + 0.5, ys + 0.5
        d = (p[:, 1, 1] - p[:, 2, 1]) * (p[:, 0, 0] - p[:, 2, 0]) + (p[:, 2, 0] - p[:, 1, 0]) * (p[:, 0, 1] - p[:, 2, 1])
        d = np.where(np.abs(d) < 1e-12, 1e-12, d)
        l0 = ((p[:, 1, 1] - p[:, 2, 1]) * (gx - p[:, 2, 0]) + (p[:, 2, 0] - p[:, 1, 0]) * (gy - p[:, 2, 1])) / d
        l1 = ((p[:, 2, 1] - p[:, 0, 1]) * (gx - p[:, 2, 0]) + (p[:, 0, 0] - p[:, 2, 0]) * (gy - p[:, 2, 1])) / d
        l2 = 1.0 - l0 - l1
        # pixels on triangle edges can land slightly outside their face: clamp to a valid barycentric
        l0, l1, l2 = (np.clip(a, -0.05, 1.05) for a in (l0, l1, l2))
        tot = l0 + l1 + l2
        l0, l1, l2 = l0 / tot, l1 / tot, l2 / tot
        zbuf[ys, xs] = l0 * depth[f[:, 0]] + l1 * depth[f[:, 1]] + l2 * depth[f[:, 2]]
        bary[ys, xs] = np.stack([l0, l1, l2], axis=1)
    return zbuf, ids, bary


def uv_to_texel_xy(uvs: np.ndarray, tex_size: tuple[int, int]) -> np.ndarray:
    """trimesh/OpenGL UVs (origin bottom-left) -> texel coordinates (origin top-left)."""
    w, h = tex_size
    return np.stack([uvs[:, 0] * w, (1.0 - uvs[:, 1]) * h], axis=1)


def render_textured(
    vertices: np.ndarray, faces: np.ndarray, uvs: np.ndarray, texture: np.ndarray, pose: CameraPose,
    size: tuple[int, int], *, exact: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Software-render the textured mesh. Returns (rgb HxWx3 uint8, coverage bool HxW).

    ``exact=False`` uses the ~5x faster painter's rasteriser (fine for pose search).
    """
    xy, depth = pose.project(vertices)
    _, ids, bary = (rasterize if exact else rasterize_fast)(xy, depth, faces, size)
    cov = ids >= 0
    out = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    if not cov.any():
        return out, cov
    th, tw = texture.shape[:2]
    tex_xy = uv_to_texel_xy(uvs, (tw, th))
    fi = ids[cov]
    corners = tex_xy[faces[fi]]  # n x 3 x 2
    pt = (bary[cov][:, :, None] * corners).sum(axis=1)
    px = np.clip(pt[:, 0].astype(int), 0, tw - 1)
    py = np.clip(pt[:, 1].astype(int), 0, th - 1)
    out[cov] = texture[py, px, :3]
    return out, cov


def _bilinear(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample img (HxWx3) at float pixel coords, replicating the border."""
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1)
    y = np.clip(y, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    f = img.astype(float)
    return (f[y0, x0] * (1 - fx) * (1 - fy) + f[y0, x1] * fx * (1 - fy)
            + f[y1, x0] * (1 - fx) * fy + f[y1, x1] * fx * fy)


# --- projection of real frames onto the texture ---------------------------------------------------


@dataclass(frozen=True)
class FrameView:
    name: str
    rgb: np.ndarray  # HxWx3 uint8, the real video frame
    mask: np.ndarray  # HxW bool, SAM silhouette of the object in that frame
    pose: CameraPose


@dataclass
class ProjectionResult:
    texture: np.ndarray  # HxWx3 uint8, base texture with observed texels replaced by real pixels
    observed_fraction: float  # fraction of the mesh's texels (UV area) replaced with real pixels
    per_view_fraction: dict[str, float]
    observed_alpha: np.ndarray  # HxW float in [0,1], per texel: how much real-pixel evidence replaced the base


def _uv_texel_samples(uvs: np.ndarray, faces: np.ndarray, tex_size: tuple[int, int]):
    """For every texel covered by a UV triangle: its face id and barycentric weights."""
    tw, th = tex_size
    tex_xy = uv_to_texel_xy(uvs, tex_size)
    ids = np.full((th, tw), -1, dtype=np.int32)
    bary = np.zeros((th, tw, 3))
    zero_depth = np.zeros(len(uvs))
    # rasterise() keeps the highest depth; with constant depth the first-drawn face wins ties, which
    # is all we need for non-overlapping UV islands.
    _, ids, bary = rasterize(tex_xy, zero_depth, faces, (tw, th))
    return ids, bary


def project_frames(
    vertices: np.ndarray,
    faces: np.ndarray,
    uvs: np.ndarray,
    base_texture: np.ndarray,
    views: list[FrameView],
    *,
    feather_px: float = 4.0,
    depth_tolerance: float = 0.03,
    full_weight: float = 0.1,
    sharpness: float = 2.0,
) -> ProjectionResult:
    """Overwrite the texels the given frames can SEE with the frames' real pixels.

    Per texel: visibility by z-buffer depth test from each frame's camera, weight
    ``|cos(view, normal)|^2`` times a feather that fades out near the silhouette edge (edge pixels
    mix in background and grazing-angle colour). Texels with total weight below ``full_weight``
    blend smoothly back toward the generated base colour; unseen texels keep it entirely.
    A larger ``sharpness`` makes each texel dominated by its best-viewed frame (crisper, but
    less averaging of noise); a small one blends frames more evenly (softer, ghosts where frames
    disagree).
    """
    import cv2

    if not views:
        raise TextureProjectionError("no frame views given")
    th, tw = base_texture.shape[:2]
    ids, bary = _uv_texel_samples(uvs, faces, (tw, th))
    valid = ids >= 0
    fi = ids[valid]
    pts = (bary[valid][:, :, None] * vertices[faces[fi]]).sum(axis=1)  # texel -> 3D point
    e1, e2 = vertices[faces[fi, 1]] - vertices[faces[fi, 0]], vertices[faces[fi, 2]] - vertices[faces[fi, 0]]
    nrm = np.cross(e1, e2)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)

    accum = np.zeros((len(pts), 3))
    wsum = np.zeros(len(pts))
    per_view: dict[str, float] = {}
    for v in views:
        h, w = v.mask.shape
        xy, depth = v.pose.project(vertices)
        zbuf, _, _ = rasterize(xy, depth, faces, (w, h))
        pxy, pdepth = v.pose.project(pts)
        ix, iy = np.clip(pxy[:, 0].astype(int), 0, w - 1), np.clip(pxy[:, 1].astype(int), 0, h - 1)
        in_frame = (pxy[:, 0] >= 0) & (pxy[:, 0] < w) & (pxy[:, 1] >= 0) & (pxy[:, 1] < h)
        tol = depth_tolerance * (depth.max() - depth.min())
        visible = in_frame & np.isfinite(zbuf[iy, ix]) & (pdepth >= zbuf[iy, ix] - tol)
        dist = cv2.distanceTransform(v.mask.astype(np.uint8), cv2.DIST_L2, 3)
        feather = np.clip((dist[iy, ix] - 1.0) / feather_px, 0.0, 1.0)
        cos_view = np.abs((nrm @ v.pose.rotation().T)[:, 2])
        weight = np.where(visible, cos_view**sharpness * feather, 0.0)
        colour = _bilinear(v.rgb, pxy[:, 0] - 0.5, pxy[:, 1] - 0.5)
        accum += weight[:, None] * colour
        wsum += weight
        per_view[v.name] = float((weight > 0.05).sum() / max(len(pts), 1))

    alpha = np.clip(wsum / full_weight, 0.0, 1.0)
    observed = np.where(wsum[:, None] > 1e-9, accum / np.maximum(wsum[:, None], 1e-9), 0.0)
    out = base_texture[..., :3].astype(float).copy()
    flat = out[valid]
    out[valid] = flat * (1.0 - alpha[:, None]) + observed * alpha[:, None]

    # Fill the UV gutters (texels outside every triangle) by dilating neighbours, so bilinear
    # filtering at island borders doesn't pull in stale colour.
    result = np.clip(out, 0, 255).astype(np.uint8)
    gutter = (~valid).astype(np.uint8)
    if gutter.any():
        result = cv2.inpaint(result, gutter * 255, 2, cv2.INPAINT_TELEA)
        result[valid] = np.clip(out[valid], 0, 255).astype(np.uint8)
    alpha_tex = np.zeros((th, tw))
    alpha_tex[valid] = alpha
    return ProjectionResult(
        texture=result,
        observed_fraction=float((alpha > 0.5).sum() / max(len(pts), 1)),
        per_view_fraction=per_view,
        observed_alpha=alpha_tex,
    )


# --- photometric pose refinement / multi-view registration ---------------------------------------


def _gray(img: np.ndarray) -> np.ndarray:
    return 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]


def _ncc(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> float:
    if m.sum() < 200:
        return float("nan")
    x, y = a[m], b[m]
    x, y = x - x.mean(), y - y.mean()
    d = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / d) if d > 1e-6 else float("nan")


def refine_pose_photometric(
    vertices: np.ndarray,
    faces: np.ndarray,
    uvs: np.ndarray,
    texture: np.ndarray,
    frame_rgb: np.ndarray,
    mask: np.ndarray,
    init: CameraPose,
    *,
    evidence: np.ndarray | None = None,
    az_range: float = 15.0,
    rounds: int = 3,
    pad: int = 16,
) -> tuple[CameraPose, float]:
    """Refine a pose so the textured render's stripes line up with the real photo.

    The silhouette barely constrains azimuth on a near-symmetric object, but the surface pattern
    does. Azimuth is kept within ``az_range`` of ``init`` (must be less than half the pattern's
    period, or the search can lock onto the neighbouring stripe). ``evidence`` (optional, texel
    space, 0..1) restricts the photometric term to texels backed by real observations, so the
    search never aligns to generated (invented) pattern. Returns (pose, score) with
    score = NCC + silhouette IoU.
    """
    import cv2

    h, w = mask.shape
    x0, y0, x1, y1 = _bbox(mask)
    x0, y0, x1, y1 = max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, w), min(y1 + pad, h)
    roi_mask = mask[y0:y1, x0:x1]
    roi_gray = cv2.GaussianBlur(_gray(frame_rgb[y0:y1, x0:x1].astype(np.float32)), (0, 0), 1.0)
    size = (x1 - x0, y1 - y0)
    ev_rgb = None if evidence is None else np.repeat(np.clip(evidence * 255.0, 0, 255).astype(np.uint8)[..., None], 3, axis=2)

    def score(pose: CameraPose) -> float:
        p = CameraPose(pose.az_deg, pose.el_deg, pose.scale, pose.tx - x0, pose.ty - y0)
        img, cov = render_textured(vertices, faces, uvs, texture, p, size, exact=False)
        region = cv2.erode((cov & roi_mask).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        if ev_rgb is not None:
            ev_img, _ = render_textured(vertices, faces, uvs, ev_rgb, p, size, exact=False)
            region &= ev_img[..., 0] > 128
        g = cv2.GaussianBlur(_gray(img.astype(np.float32)), (0, 0), 1.0)
        n = _ncc(g, roi_gray, region)
        if np.isnan(n):
            return -1.0
        return n + iou(cov, roi_mask)

    best, best_score = init, score(init)
    steps = np.array([3.0, 3.0, 0.015, 2.0, 2.0])
    for _ in range(rounds):
        improved = True
        while improved:
            improved = False
            for axis in range(5):
                for sign in (-1.0, 1.0):
                    vals = [best.az_deg, best.el_deg, best.scale, best.tx, best.ty]
                    vals[axis] += sign * steps[axis] * (best.scale if axis == 2 else 1.0)
                    if axis == 0 and abs(vals[0] - init.az_deg) > az_range:
                        continue
                    cand = CameraPose(*vals)
                    sc = score(cand)
                    if sc > best_score + 1e-4:
                        best, best_score, improved = cand, sc, True
        steps = steps / 2.0
    return best, float(best_score)


def register_views(
    vertices: np.ndarray,
    faces: np.ndarray,
    uvs: np.ndarray,
    base_texture: np.ndarray,
    views: list[FrameView],
    *,
    anchor: str | None = None,
    iterations: int = 2,
    min_evidence_fraction: float = 0.08,
) -> list[FrameView]:
    """Jointly align several frames' poses by stripe consistency (leave-one-out).

    For each non-anchor frame: build a texture from the OTHER frames, then refine that frame's
    pose against it, scoring only where the others actually observed the surface. The anchor
    (by default the first view -- the frame the mesh was generated from) keeps its pose, which
    fixes the gauge. A frame with too little overlap with the others keeps its silhouette pose.
    """
    cur = list(views)
    anchor = anchor or cur[0].name
    for _ in range(iterations):
        for i, v in enumerate(cur):
            if v.name == anchor:
                continue
            others = [o for j, o in enumerate(cur) if j != i]
            proj = project_frames(vertices, faces, uvs, base_texture, others)
            evidence = (proj.observed_alpha > 0.5).astype(float)
            pose, sc = refine_pose_photometric(
                vertices, faces, uvs, proj.texture, v.rgb, v.mask, v.pose, evidence=evidence
            )
            if sc > 0:
                cur[i] = FrameView(v.name, v.rgb, v.mask, pose)
    return cur


# --- high-level API: real frames -> textured GLB --------------------------------------------------


@dataclass(frozen=True)
class FrameInput:
    name: str
    rgb: np.ndarray  # HxWx3 uint8
    mask: np.ndarray  # HxW bool: genuine (non-fallback) segmentation of the object in this frame


def select_views(
    fits: dict[str, PoseFit],
    scales: dict[str, float],
    *,
    anchor: str,
    max_views: int = 8,
    min_iou: float = 0.85,
    min_azimuth_sensitivity: float = 0.02,
) -> list[str]:
    """Pick the frames whose silhouette pose can be trusted, anchor first.

    A frame is dropped when its silhouette fit is poor, or when the silhouette barely constrains
    azimuth (e.g. the handle is hidden: rotating the mesh 20 deg changes the outline by <2%),
    because such a frame could be projected at the wrong azimuth and smear the pattern. Among the
    rest, larger objects (more real pixels per texel) are preferred.
    """
    if anchor not in fits:
        raise TextureProjectionError(f"anchor frame {anchor!r} has no pose fit")
    ok = [
        n for n, f in fits.items()
        if n != anchor and f.iou >= min_iou and f.azimuth_sensitivity >= min_azimuth_sensitivity
    ]
    ok.sort(key=lambda n: -(fits[n].azimuth_sensitivity * scales[n]))
    return [anchor] + ok[: max(max_views - 1, 0)]


def reproject_frames_onto_glb(
    glb_bytes: bytes,
    frames: list[FrameInput],
    *,
    anchor: str | None = None,
    max_views: int = 8,
    register_iterations: int = 2,
    texture_format: str = "PNG",
) -> tuple[bytes, dict]:
    """Overwrite the texels real frames can see with their real pixels; return (new GLB, report).

    The mesh must be a single textured geometry with UVs (e.g. TRELLIS output). Geometry is never
    modified. ``anchor`` names the frame the mesh was generated from (its pose fixes the
    coordinate gauge); if omitted, the frame with the best silhouette fit is used. The report
    states exactly what was observed vs. left as generated, per-frame pose fits and the knobs.
    """
    import io

    import trimesh

    if not frames:
        raise TextureProjectionError("no frames given")
    scene = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    geoms = list(scene.geometry.values()) if hasattr(scene, "geometry") else [scene]
    if len(geoms) != 1:
        raise TextureProjectionError(f"expected one geometry in the GLB, found {len(geoms)}")
    mesh = geoms[0]
    material = getattr(mesh.visual, "material", None)
    tex_img = getattr(material, "baseColorTexture", None)
    if tex_img is None or getattr(mesh.visual, "uv", None) is None:
        raise TextureProjectionError("mesh has no UV-mapped base colour texture to update")
    vertices, faces, uvs = np.asarray(mesh.vertices, float), np.asarray(mesh.faces), np.asarray(mesh.visual.uv, float)
    base = np.array(tex_img.convert("RGB"))

    fits: dict[str, PoseFit] = {}
    for fr in frames:
        try:
            fits[fr.name] = fit_pose(vertices, faces, fr.mask)
        except TextureProjectionError:
            continue
    if not fits:
        raise TextureProjectionError("no frame had a usable silhouette")
    by_name = {fr.name: fr for fr in frames}
    if anchor is None:
        anchor = max(fits, key=lambda n: fits[n].iou * (0.5 + fits[n].azimuth_sensitivity))
    if anchor not in fits:
        raise TextureProjectionError(f"anchor frame {anchor!r} is not among the usable frames")
    chosen = select_views(fits, {n: fits[n].pose.scale for n in fits}, anchor=anchor, max_views=max_views)
    views = [FrameView(n, by_name[n].rgb, by_name[n].mask, fits[n].pose) for n in chosen]
    registered = register_views(vertices, faces, uvs, base, views, anchor=anchor, iterations=register_iterations)
    result = project_frames(vertices, faces, uvs, base, registered)

    out_img = Image.fromarray(result.texture)
    if texture_format.upper() == "JPEG":
        buf = io.BytesIO()
        out_img.save(buf, format="JPEG", quality=92)
        out_img = Image.open(io.BytesIO(buf.getvalue()))  # keeps .format == "JPEG" so trimesh embeds JPEG
    material.baseColorTexture = out_img
    out = scene.export(file_type="glb")

    report = {
        "method": "silhouette pose fit + photometric (stripe) registration + z-buffer visibility projection",
        "geometry_modified": False,
        "anchor_frame": anchor,
        "frames_considered": len(frames),
        "frames_with_pose_fit": len(fits),
        "frames_used": [
            {
                "name": a.name,
                "silhouette_iou": round(float(fits[a.name].iou), 3),
                "azimuth_sensitivity": round(float(fits[a.name].azimuth_sensitivity), 3),
                "azimuth_deg": [round(float(a.pose.az_deg), 1), round(float(b.pose.az_deg), 1)],  # silhouette -> registered
                "elevation_deg": [round(float(a.pose.el_deg), 1), round(float(b.pose.el_deg), 1)],
                "texel_fraction_seen": round(float(result.per_view_fraction[a.name]), 3),
            }
            for a, b in zip(views, registered)
        ],
        "frames_rejected_for_pose": sorted(set(fits) - set(chosen)),
        "observed_fraction": round(float(result.observed_fraction), 3),
        "generated_fraction": round(float(1.0 - result.observed_fraction), 3),
        "note": (
            "observed_fraction is the share of the texture (UV area) replaced by real video pixels. "
            "The remainder keeps the generator's invented colours, and ALL geometry (including the "
            "unseen side) is still generated, not measured."
        ),
    }
    return out, report
