"""Tests for texture_project.py against synthetic ground truth (no network, no GPU).

The scene is a UV-mapped, striped open tube (Y-up, like glTF) with a box "handle" so the silhouette
has an azimuth cue. Frames are rendered from the ground-truth texture at KNOWN poses, so every
claim -- pose recovery, texture recovery, untouched unseen texels, registration fixing a wrong
azimuth -- can be checked exactly rather than just eyeballed.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import trimesh
from PIL import Image

from worldkit import texture_project as tp

W, H = 240, 300
GRAY = 128


def _striped_tube(n_az: int = 48, n_h: int = 6, radius: float = 0.5, height: float = 1.0, handle: bool = True):
    verts, uvs = [], []
    for j in range(n_h + 1):
        y = -height / 2 + height * j / n_h
        for i in range(n_az + 1):  # duplicated seam vertex, like a real UV-unwrapped mesh
            a = 2 * np.pi * i / n_az
            verts.append([radius * np.cos(a), y, radius * np.sin(a)])
            uvs.append([i / n_az, j / n_h])
    faces = []
    for j in range(n_h):
        for i in range(n_az):
            a = j * (n_az + 1) + i
            b, c = a + 1, a + n_az + 1
            faces += [[a, b, c], [b, c + 1, c]]
    verts, uvs, faces = np.array(verts), np.array(uvs), np.array(faces)
    if handle:
        box = trimesh.creation.box(extents=(0.5, 0.12, 0.12))
        box.apply_translation((radius + 0.25, 0.0, 0.0))
        faces = np.vstack([faces, box.faces + len(verts)])
        verts = np.vstack([verts, box.vertices])
        uvs = np.vstack([uvs, np.full((len(box.vertices), 2), 0.0)])
    return verts, faces, uvs


def _ground_truth_texture(w: int = 256, h: int = 128) -> np.ndarray:
    u = (np.arange(w) + 0.5) / w
    v = 1.0 - (np.arange(h) + 0.5) / h  # row 0 is v=1 (top)
    stripes = np.where(np.sin(2 * np.pi * u * 12) > 0, 235, 30)  # 12 stripes around
    tex = np.zeros((h, w, 3), dtype=np.uint8)
    tex[..., 0] = (255 * v)[:, None]  # red encodes v, so a vertical flip is detectable
    tex[..., 1] = stripes[None, :]
    tex[..., 2] = 128
    return tex


def _pose(az: float, el: float = 30.0) -> tp.CameraPose:
    return tp.CameraPose(az, el, 150.0, W / 2, H / 2)


def _frame(name: str, verts, faces, uvs, gt: np.ndarray, pose: tp.CameraPose) -> tp.FrameView:
    rgb, cov = tp.render_textured(verts, faces, uvs, gt, pose, (W, H))
    return tp.FrameView(name, rgb, cov, pose)


@pytest.fixture(scope="module")
def scene():
    verts, faces, uvs = _striped_tube()
    return verts, faces, uvs, _ground_truth_texture(), np.full((128, 256, 3), GRAY, dtype=np.uint8)


# --- camera ---------------------------------------------------------------------------------------


def test_pose_projection_axes_and_depth_convention():
    pose = tp.CameraPose(0.0, 0.0, 100.0, 50.0, 60.0)
    xy, depth = pose.project(np.array([[1.0, 2.0, 3.0]]))
    assert xy[0] == pytest.approx([150.0, -140.0])  # x right, image y points DOWN
    assert depth[0] == pytest.approx(3.0)  # larger depth = closer to the camera


def test_positive_elevation_means_camera_above_looking_at_the_top():
    # Real bug found by rendering the textured result: the first sign convention showed the mug
    # from BELOW, which a silhouette alone cannot reveal (from +44 and -44 deg the outline matches).
    pose = tp.CameraPose(0.0, 45.0, 1.0, 0.0, 0.0)
    _, depth = pose.project(np.array([[0.0, 1.0, 0.0], [0.0, -1.0, 0.0]]))
    assert depth[0] > depth[1]  # the top of the object is nearer the camera than its bottom


# --- rasterisers ----------------------------------------------------------------------------------


def test_rasterize_fast_matches_exact_depth(scene):
    verts, faces, *_ = scene
    xy, depth = _pose(40.0).project(verts)
    z1, i1, _ = tp.rasterize(xy, depth, faces, (W, H))
    z2, i2, _ = tp.rasterize_fast(xy, depth, faces, (W, H))

    cov1, cov2 = i1 >= 0, i2 >= 0
    assert (cov1 != cov2).sum() < 0.03 * cov1.sum()  # coverage differs only along edges
    both = cov1 & cov2
    assert np.median(np.abs(z1[both] - z2[both])) < 0.01 * (depth.max() - depth.min())


def test_rasterize_hides_the_far_side(scene):
    verts, faces, *_ = scene
    xy, depth = _pose(0.0, 0.0).project(verts)
    zbuf, ids, _ = tp.rasterize(xy, depth, faces, (W, H))
    # looking straight on at a tube: every covered pixel shows the NEAR wall, never the far one
    covered = ids >= 0
    assert covered.sum() > 1000
    assert zbuf[covered].min() > -0.01  # near wall only: z >= 0 (far wall would be z < 0)


# --- silhouette pose fit --------------------------------------------------------------------------


def test_fit_pose_recovers_a_known_pose_from_the_silhouette_alone(scene):
    verts, faces, *_ = scene
    truth = tp.CameraPose(40.0, 35.0, 150.0, W / 2, H / 2)
    mask = tp.silhouette(verts, faces, truth, (W, H))

    fit = tp.fit_pose(verts, faces, mask)

    assert fit.iou > 0.95
    diff = (fit.pose.az_deg - truth.az_deg + 180.0) % 360.0 - 180.0
    assert abs(diff) < 12.0  # the handle breaks the symmetry
    assert abs(fit.pose.el_deg - truth.el_deg) < 10.0
    assert fit.azimuth_sensitivity > 0.02  # the silhouette DOES constrain azimuth here


def test_fit_pose_reports_low_azimuth_sensitivity_for_a_symmetric_object():
    verts, faces, _ = _striped_tube(handle=False)
    mask = tp.silhouette(verts, faces, tp.CameraPose(40.0, 35.0, 150.0, W / 2, H / 2), (W, H))

    fit = tp.fit_pose(verts, faces, mask)

    assert fit.azimuth_sensitivity < 0.02  # a plain tube has no azimuth cue: must not be trusted


def test_fit_pose_rejects_an_empty_mask(scene):
    verts, faces, *_ = scene
    with pytest.raises(tp.TextureProjectionError):
        tp.fit_pose(verts, faces, np.zeros((H, W), dtype=bool))


# --- projection -----------------------------------------------------------------------------------


def test_projection_recovers_the_true_texture_where_observed(scene):
    verts, faces, uvs, gt, base = scene
    views = [_frame(f"f{az}", verts, faces, uvs, gt, _pose(az)) for az in (0.0, 120.0, 240.0)]

    res = tp.project_frames(verts, faces, uvs, base, views)

    assert res.observed_fraction > 0.5
    seen = res.observed_alpha > 0.9
    assert seen.sum() > 2000
    err = np.abs(res.texture[seen].astype(float) - gt[seen].astype(float)).mean()
    assert err < 20.0  # blur at stripe edges only; a vertical flip or wrong gauge would give >60


def test_projection_leaves_unobserved_texels_exactly_as_generated(scene):
    verts, faces, uvs, gt, base = scene
    res = tp.project_frames(verts, faces, uvs, base, [_frame("front", verts, faces, uvs, gt, _pose(0.0))])

    never = res.observed_alpha == 0.0
    assert never.sum() > 1000  # a single view cannot see the back of the tube
    assert np.array_equal(res.texture[never & (np.arange(256)[None, :] > 0)], base[never & (np.arange(256)[None, :] > 0)])


def test_projection_does_not_paint_through_the_object(scene):
    # The far wall of the tube must NOT receive the near wall's colours (visibility test). Viewed
    # exactly side-on (elevation 0) the near wall fully hides it; at any positive elevation this
    # OPEN tube legitimately shows the inside of the far wall over the near rim.
    verts, faces, uvs, gt, base = scene
    res = tp.project_frames(verts, faces, uvs, base, [_frame("front", verts, faces, uvs, gt, _pose(0.0, 0.0))])

    # A camera at az=0 sits on +Z, so the far wall is at angle 3*pi/2, i.e. u = 0.75 (u = angle/2pi;
    # the handle is at u=0 = +X = the right-hand silhouette edge, and u=0.5 the left-hand one).
    back_cols = slice(int(0.70 * 256), int(0.80 * 256))
    seen_back = res.observed_alpha[:, back_cols] > 0.5
    assert seen_back.mean() < 0.15


def test_projection_requires_at_least_one_view(scene):
    verts, faces, uvs, gt, base = scene
    with pytest.raises(tp.TextureProjectionError):
        tp.project_frames(verts, faces, uvs, base, [])


# --- photometric registration ---------------------------------------------------------------------


def test_registration_corrects_a_wrong_azimuth_using_the_surface_pattern(scene):
    # Silhouette azimuth is only good to ~10 deg, yet 12 stripes make the pattern shift by half a
    # stripe (15 deg) -- registration must use the stripes to do better.
    verts, faces, uvs, gt, base = scene
    true_az = (0.0, 90.0, 180.0, 270.0)
    views = [_frame(f"f{int(a)}", verts, faces, uvs, gt, _pose(a)) for a in true_az]
    wrong = tp.FrameView(views[1].name, views[1].rgb, views[1].mask, _pose(98.0))  # 8 deg off
    start = [views[0], wrong, views[2], views[3]]

    out = tp.register_views(verts, faces, uvs, base, start, anchor="f0", iterations=1)

    assert out[0].pose.az_deg == 0.0  # the anchor never moves
    assert abs(out[1].pose.az_deg - 90.0) < 3.0
    assert abs(out[1].pose.az_deg - 90.0) < abs(98.0 - 90.0)


# --- selection ------------------------------------------------------------------------------------


def _fit(iou: float, sens: float) -> tp.PoseFit:
    return tp.PoseFit(pose=tp.CameraPose(0, 40, 100, 0, 0), iou=iou, azimuth_sensitivity=sens)


def test_select_views_drops_untrustworthy_frames_and_keeps_the_anchor_first():
    fits = {
        "anchor": _fit(0.90, 0.001),  # anchor is kept even though its azimuth is unconstrained
        "good_big": _fit(0.95, 0.06),
        "good_small": _fit(0.95, 0.06),
        "bad_fit": _fit(0.70, 0.08),  # poor silhouette fit
        "hidden_handle": _fit(0.96, 0.004),  # silhouette does not constrain azimuth
    }
    scales = {"anchor": 100.0, "good_big": 400.0, "good_small": 200.0, "bad_fit": 400.0, "hidden_handle": 400.0}

    chosen = tp.select_views(fits, scales, anchor="anchor", max_views=8)

    assert chosen == ["anchor", "good_big", "good_small"]


def test_select_views_respects_the_view_budget():
    fits = {f"f{i}": _fit(0.95, 0.05) for i in range(6)}
    scales = {k: 100.0 + i for i, k in enumerate(fits)}

    chosen = tp.select_views(fits, scales, anchor="f0", max_views=3)

    assert chosen[0] == "f0" and len(chosen) == 3


def test_select_views_requires_a_known_anchor():
    with pytest.raises(tp.TextureProjectionError):
        tp.select_views({"a": _fit(0.9, 0.05)}, {"a": 1.0}, anchor="missing")


# --- GLB-level API --------------------------------------------------------------------------------


def _glb_with_texture(verts, faces, uvs, texture: np.ndarray) -> bytes:
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uvs, image=Image.fromarray(texture))
    return mesh.export(file_type="glb")


def test_reproject_frames_onto_glb_end_to_end(scene):
    verts, faces, uvs, gt, base = scene
    glb = _glb_with_texture(verts, faces, uvs, base)
    frames = []
    for az in (0.0, 70.0, 140.0, 210.0, 280.0):
        v = _frame(f"f{int(az)}", verts, faces, uvs, gt, _pose(az, 35.0))
        frames.append(tp.FrameInput(v.name, v.rgb, v.mask))

    out, report = tp.reproject_frames_onto_glb(glb, frames, anchor="f0", max_views=5)

    assert report["geometry_modified"] is False
    assert report["anchor_frame"] == "f0"
    assert report["observed_fraction"] > 0.4
    assert report["generated_fraction"] == pytest.approx(1.0 - report["observed_fraction"], abs=1e-3)
    assert len(report["frames_used"]) >= 3
    assert "generated" in report["note"]
    # the geometry in the output GLB is untouched, and the texture really changed
    scene_out = trimesh.load(io.BytesIO(out), file_type="glb", process=False)
    mesh_out = list(scene_out.geometry.values())[0]
    assert np.allclose(np.asarray(mesh_out.vertices), verts, atol=1e-5)
    new_tex = np.array(mesh_out.visual.material.baseColorTexture.convert("RGB")).astype(float)
    assert np.abs(new_tex - GRAY).mean() > 20.0


def test_reproject_frames_onto_glb_rejects_untextured_mesh(scene):
    verts, faces, *_ = scene
    plain = trimesh.Trimesh(vertices=verts, faces=faces, process=False).export(file_type="glb")
    frame = tp.FrameInput("f", np.zeros((H, W, 3), np.uint8), np.ones((H, W), bool))

    with pytest.raises(tp.TextureProjectionError, match="texture"):
        tp.reproject_frames_onto_glb(plain, [frame])


def test_reproject_frames_onto_glb_requires_frames(scene):
    verts, faces, uvs, gt, base = scene
    with pytest.raises(tp.TextureProjectionError):
        tp.reproject_frames_onto_glb(_glb_with_texture(verts, faces, uvs, base), [])


def test_uv_convention_matches_trimesh_so_viewers_agree(scene):
    # Renderer and projector share uv_to_texel_xy, so a vertical flip there would be invisible to
    # every other test. Pin it to trimesh's own definition of where a UV lands in an image.
    from trimesh.visual import uv_to_color

    *_, gt, _ = scene
    uv = np.random.default_rng(0).random((200, 2)) * 0.98 + 0.01
    expected = uv_to_color(uv, Image.fromarray(gt))[:, :3].astype(int)
    xy = tp.uv_to_texel_xy(uv, (gt.shape[1], gt.shape[0]))
    ours = gt[np.clip(xy[:, 1].astype(int), 0, gt.shape[0] - 1), np.clip(xy[:, 0].astype(int), 0, gt.shape[1] - 1)].astype(int)

    assert np.abs(ours - expected).mean() < 8.0  # nearest-texel vs trimesh's sampling: tiny differences only
