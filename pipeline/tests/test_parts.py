"""Part selection (SAM votes -> vertices), the GLB->STL transfer and the thicken_part tool on a synthetic mug."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh

from tests.test_edit_tools import make_mug
from worldkit import edit_tools as et
from worldkit import parts as pt
from worldkit import texture_project as tp


def test_votes_pick_the_vertices_inside_the_part_mask_and_count_views():
    sph = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    V, F = np.asarray(sph.vertices), np.asarray(sph.faces)
    pose = tp.CameraPose(0.0, 0.0, 40.0, 100.0, 100.0)
    size = (200, 200)
    obj = tp.silhouette(V, F, pose, size)
    part = np.zeros(size, bool)
    part[:, 100:] = True  # the right half of the image is the "part"
    frames = [{"obj_mask": obj, "part_mask": part, "pose": pose}] * 3 + [{"obj_mask": obj, "part_mask": None, "pose": pose}]
    r = pt.vote_part(V, F, np.asarray(sph.vertex_normals), frames)
    assert r["views_total"] == 4 and r["views_found"] == 3
    sel = r["selected"]
    xy, _ = pose.project(V)
    facing = np.asarray(sph.vertex_normals) @ pose.rotation()[2] > 0.1
    assert sel.sum() > 20 and (xy[sel, 0] >= 99).all() and facing[sel].all()  # only camera-facing vertices on the part side


def test_glb_to_stl_transform_recovers_a_known_placement():
    mug = make_mug()  # print mesh: mm, z up, base on z=0 (100 mm tall)
    body_h = float(mug.bounds[1][2])
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)  # print (x,y,z) -> glb (x, z, -y): the inverse of the pipeline's Y-up -> Z-up
    glb_v = (mug.vertices @ base.T) / 10.0  # 10 mm per unit
    glb_v = glb_v - glb_v.min(0) + 3.0  # arbitrary offset
    fn, rms = pt.glb_to_stl_transform(glb_v, mug, 10.0, body_h)
    assert rms < 0.8
    assert np.allclose(fn(glb_v[:50]), mug.vertices[:50], atol=1.5)


def test_thicken_part_grows_only_the_part_and_reads_as_a_sentence():
    mug = make_mug()
    mug.apply_scale(0.1)
    info = et.handle_info(mug)
    ctr = np.asarray(info["bar_center_point_mm"])
    sel = np.linalg.norm(mug.vertices - ctr, axis=1) < max(info["bar_thickness_along_normal_mm"], 6.0)
    et.set_parts({"handle": {"stl_points": mug.vertices[sel].copy(), "views_found": 7, "views_total": 8}})
    try:
        new, summ = et.apply_tool(mug, "thicken_part", {"part": "handle", "by_mm": 2})
        assert summ["watertight"] and summ["sentence"] == "Thickened handle by 2 mm (SAM found 7 of 8 views)"
        assert new.volume > mug.volume + 100  # the handle got heavier
        # the body far from the handle keeps its footprint (compare bounding extents on the side opposite the handle)
        assert abs(new.bounds[0][0] - mug.bounds[0][0]) < 0.2 or abs(new.bounds[1][0] - mug.bounds[1][0]) < 0.2
        with pytest.raises(et.EditError):
            et.apply_tool(mug, "thicken_part", {"part": "lid", "by_mm": 2})
        with pytest.raises(et.EditError):
            et.apply_tool(mug, "thicken_part", {"part": "handle", "by_mm": 50})
        assert et.model_facts(mug)["named_parts"][0]["usable_for_thicken_part"] is True
    finally:
        et.set_parts({})


def test_b64_roundtrip():
    a = np.random.default_rng(0).normal(size=(5, 3)).astype(np.float32)
    assert np.array_equal(pt.unb64(pt._b64(a), np.float32), a)
