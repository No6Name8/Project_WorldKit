"""Scene composition geometry (pure functions) and the manifest produced by the import backend end to end."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from worldkit import scene
from worldkit import texture_project as tp


def _pose(az=30.0, el=40.0, scale=100.0, tx=240.0, ty=400.0):
    return tp.CameraPose(az, el, scale, tx, ty)


def test_ray_to_plane_is_the_exact_inverse_of_projection():
    pose = _pose()
    for plane_y in (0.0, 0.7):
        world = np.array([0.31, plane_y, -0.22])
        pixel = pose.project(world[None, :])[0][0]
        np.testing.assert_allclose(scene.ray_to_plane(pose, pixel, plane_y), world, atol=1e-9)


def test_place_child_lands_on_the_parent_top_with_the_right_scale_and_yaw():
    parent = trimesh.creation.box(extents=(2.0, 1.0, 2.0))  # top at y=+0.5
    child = trimesh.creation.cylinder(radius=0.3, height=0.6)
    child.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))  # axis -> y
    pv, cv = np.asarray(parent.vertices), np.asarray(child.vertices)
    ppose = _pose(az=20.0, el=45.0, scale=120.0)
    # ground truth: child at scale 0.5, yaw 35, standing at (0.4, top, -0.3)
    true = {"position": None, "rotation_deg": [0, 35.0, 0], "scale": [0.5] * 3}
    base_world = np.array([0.4, scene.top_plane_y(pv), -0.3])
    bc = scene.base_center(cv)
    true["position"] = list(base_world - 0.5 * (scene.yaw_matrix(35.0) @ bc))
    world_child = scene.transform_vertices(cv, true)
    # what each object's own pose fit would report under the same camera
    child_pose = tp.CameraPose(20.0 + 35.0, 45.0, 120.0 * 0.5, 0.0, 0.0)
    xy_c = ppose.project(world_child)[0]
    own = child_pose.project(cv)[0]
    child_pose = tp.CameraPose(child_pose.az_deg, child_pose.el_deg, child_pose.scale, *(xy_c.mean(axis=0) - own.mean(axis=0)))
    placed = scene.place_child(ppose, pv, child_pose, cv)
    assert placed["scale"][0] == pytest.approx(0.5, rel=1e-6)
    assert placed["rotation_deg"][1] == pytest.approx(35.0)
    wp = scene.transform_vertices(cv, placed)
    assert wp[:, 1].min() == pytest.approx(scene.top_plane_y(pv), abs=0.02)  # rests on the top plane


def test_aabb_overlap_separates_resting_contact_from_penetration():
    a = np.array([[0, 0, 0], [1, 1, 1]], float)
    touching = a + [0, 1.0, 0]
    inside = a + [0, 0.5, 0]
    far = a + [3, 0, 0]
    assert scene.aabb_overlap(a, touching)["penetration"][1] == pytest.approx(0.0)
    assert scene.aabb_overlap(a, inside)["intersects"] is True
    assert scene.aabb_overlap(a, far)["intersects"] is False


def test_layout_iou_is_one_for_a_perfectly_reprojected_scene():
    parent = trimesh.creation.box(extents=(2.0, 1.0, 2.0))
    child = trimesh.creation.box(extents=(0.4, 0.4, 0.4))
    pv, pf, cv, cf = np.asarray(parent.vertices), np.asarray(parent.faces), np.asarray(child.vertices), np.asarray(child.faces)
    pose = _pose(scale=120.0)
    t = {"position": [0.2, 0.7, 0.1], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}
    size = (480, 856)
    masks = {"parent": tp.silhouette(pv, pf, pose, size), "kid": tp.silhouette(scene.transform_vertices(cv, t), cf, pose, size)}
    out = scene.layout_iou(pose, pv, pf, {"kid": (cv, cf, t)}, size, masks)
    assert out["parent"] == 1.0 and out["kid"] == 1.0 and out["all"] == 1.0
    wrong = {"position": [0.9, 0.7, 0.1], "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}
    assert scene.layout_iou(pose, pv, pf, {"kid": (cv, cf, wrong)}, size, masks)["kid"] < 0.5


def test_a_scene_needs_two_objects(tmp_path: Path):
    with pytest.raises(scene.SceneError):
        scene.run(scene.parse_args(["video.mp4", "--out", str(tmp_path), "--objects", "mug"]))


def test_slug_ids_are_filesystem_safe():
    assert scene._slug("round side table") == "round_side_table"
    assert scene._slug("Mug!") == "mug"


def test_committed_demo_manifest_is_consistent_if_present():
    """If the real demo scene exists locally, its manifest must reference existing meshes/reports and a valid layout."""
    root = Path(__file__).resolve().parents[1] / "runs" / "scene_demo_mug_table"
    if not (root / "scene.json").is_file():
        pytest.skip("demo scene not generated on this machine")
    m = json.loads((root / "scene.json").read_text())
    assert m["worldkit_scene"] == scene.SCENE_VERSION and len(m["objects"]) >= 2
    assert sum(o["role"] == "parent" for o in m["objects"]) == 1
    for o in m["objects"]:
        assert (root / o["mesh"]).is_file() and (root / o["report"]).is_file()
        assert o["reconstruction_model"]["is_meta_model"] is True
    assert m["layout_iou"]["all"] > 0.6
