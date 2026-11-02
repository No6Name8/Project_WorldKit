"""Part selection (SAM votes -> vertices), the GLB->STL transfer and the thicken_part tool on a synthetic mug."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh

from tests.test_edit import model_dir  # noqa: F401  (fixture)
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


def test_low_sam_coverage_refuses_with_a_plain_sentence_through_the_edit_loop(model_dir):
    """3 of 8 views is under half: the edit is refused, nothing changes, and the reason is a sentence the log can show."""
    import json

    from tests.test_edit import FakeWonder, reply, spark_ok, tool_call
    from worldkit import edit

    mug = trimesh.load(model_dir / "model_print.stl", force="mesh")
    info = et.handle_info(mug)
    sel = np.linalg.norm(mug.vertices - np.asarray(info["bar_center_point_mm"]), axis=1) < 8
    (model_dir / "parts").mkdir()
    (model_dir / "parts" / "parts.json").write_text(json.dumps({"parts": [{"id": "handle", "name": "handle", "views_total": 8, "views_found": 3, "vertex_count": int(sel.sum()),
                                                                           "stl_points_b64": pt._b64(mug.vertices[sel].astype(np.float32))}]}))
    wonder = FakeWonder([reply([tool_call("thicken_part", {"part": "handle", "by_mm": 2})]), reply(text="Could not."),
                         reply([tool_call("thicken_part", {"part": "handle", "by_mm": 2})]), reply(text="Could not.")])
    r = edit.run_edit(model_dir, "make the handle thicker", client=wonder, ask_spark=spark_ok)
    try:
        assert not r["applied"]
        calls = r.get("tool_calls") or r["attempts"][-1]["tool_calls"]
        assert calls[0]["error"] == "SAM found the handle in only 3 of 8 views, too few to edit it reliably. Nothing was changed."
        assert edit._load(model_dir)["current"] == 0
    finally:
        et.set_parts({})


def test_growth_covers_the_whole_handle_ring_and_stops_at_the_body():
    body = trimesh.creation.cylinder(radius=40.0, height=160.0, sections=64)
    body.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))  # axis along Y
    ring = trimesh.creation.torus(major_radius=18.0, minor_radius=4.0, major_sections=48, minor_sections=16)
    ring.apply_translation([58.0, 20.0, 0])  # on the +x side in the upper half; its inner edge touches the body
    mesh = trimesh.util.concatenate([body, ring])
    V, F = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    is_ring = np.arange(len(V)) >= len(body.vertices)
    seed = is_ring & (V[:, 1] > 26)  # SAM only saw the upper half of the ring
    grown = pt.grow_protrusion(V, F, seed)
    outside = is_ring & (np.linalg.norm(V[:, [0, 2]], axis=1) > 42.0)  # the part of the ring that is not buried in the body wall
    assert grown[outside].all() and grown[is_ring].mean() > 0.9  # the whole visible ring, including the half SAM never saw
    assert not grown[~is_ring].any()  # and not a single body vertex


DEMO_MUG = __import__("pathlib").Path(__file__).resolve().parents[2] / "viewer" / "assets" / "demo" / "run_mug"


def test_negative_thicken_part_thins_the_handle_but_never_below_the_minimum_wall(tmp_path):
    """Real coin-scaled mug (tracked demo run) with its real SAM handle: -1.5 mm works, -6 mm would leave under 1.2 mm and is refused."""
    import json
    import shutil

    from worldkit import edit

    work = tmp_path / "run"
    work.mkdir()
    for f in ("model_print.stl", "report.json", "model.glb"):
        shutil.copy(DEMO_MUG / f, work / f)
    shutil.copytree(DEMO_MUG / "parts", work / "parts")
    et.set_parts(edit.load_parts(work))
    try:
        mug = trimesh.load(work / "model_print.stl", force="mesh")
        before = et.part_wall_mm(mug, et.PARTS["handle"]["stl_points"])
        new, summ = et.apply_tool(mug, "thicken_part", {"part": "handle", "by_mm": -1.5})
        after = et.part_wall_mm(new, et.PARTS["handle"]["stl_points"])
        assert summ["watertight"] and summ["sentence"] == "Thinned handle by 1.5 mm (SAM found 11 of 12 views)"
        assert after < before - 0.8 and after >= et.MIN_WALL_MM  # thinner, still printable
        assert new.volume < mug.volume - 50
        with pytest.raises(et.EditError, match="below the 1.2 mm minimum. Nothing was changed."):
            et.apply_tool(mug, "thicken_part", {"part": "handle", "by_mm": -6})
        with pytest.raises(et.EditError):
            et.apply_tool(mug, "thicken_part", {"part": "handle", "by_mm": 0})
    finally:
        et.set_parts({})


def test_thinner_instruction_through_the_edit_loop_with_a_fake_wonder(tmp_path):
    import shutil

    from tests.test_edit import FakeWonder, reply, spark_ok, tool_call
    from worldkit import edit

    work = tmp_path / "run"
    work.mkdir()
    for f in ("model_print.stl", "report.json", "model.glb"):
        shutil.copy(DEMO_MUG / f, work / f)
    shutil.copytree(DEMO_MUG / "parts", work / "parts")
    wonder = FakeWonder([reply([tool_call("thicken_part", {"part": "handle", "by_mm": -1.5})]), reply(text="Thinned the handle.")])
    r = edit.run_edit(work, "make the handle thinner", client=wonder, ask_spark=spark_ok)
    try:
        assert r["applied"]
        calls = r.get("tool_calls") or r["attempts"][-1]["tool_calls"]
        assert calls[0]["result"]["sentence"] == "Thinned handle by 1.5 mm (SAM found 11 of 12 views)"
        # the tool the model is offered says it handles BOTH directions
        spec = next(t for t in edit.TOOLS if t["function"]["name"] == "thicken_part")["function"]
        assert "thinner" in spec["description"] and "NEGATIVE" in spec["description"]
    finally:
        et.set_parts({})
