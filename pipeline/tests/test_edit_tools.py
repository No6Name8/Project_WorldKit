"""Unit tests for the AI-Edit geometry tools on a synthetic mug (mm, Z up, base on z=0)."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh

from worldkit import edit_tools as et


def make_mug(wall=4.0, floor=3.0):
    outer = trimesh.creation.cylinder(radius=40, height=100, sections=96)
    outer.apply_translation([0, 0, 50])
    inner = trimesh.creation.cylinder(radius=40 - wall, height=100, sections=96)
    inner.apply_translation([0, 0, floor + 50])
    body = trimesh.boolean.difference([outer, inner], engine="manifold")
    ring = trimesh.creation.torus(major_radius=22, minor_radius=6, major_sections=48, minor_sections=24)
    ring.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))  # ring plane = x-z
    ring.apply_translation([58, 0, 55])
    mug = trimesh.boolean.union([body, ring], engine="manifold")
    mug.apply_translation([0, 0, -mug.bounds[0][2]])
    return mug


@pytest.fixture(scope="module")
def mug():
    m = make_mug()
    assert m.is_watertight
    return m


def test_scale_to_height_and_factor_keep_base_and_proportions(mug):
    out = et.scale_to(mug, height_cm=12.0)
    assert out.extents[2] == pytest.approx(120.0, abs=0.01) and out.bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert out.extents[0] / mug.extents[0] == pytest.approx(1.2, rel=1e-4) and out.is_watertight
    assert et.scale_to(mug, factor=0.5).volume == pytest.approx(mug.volume * 0.125, rel=1e-3)
    with pytest.raises(et.EditError):
        et.scale_to(mug)
    with pytest.raises(et.EditError):
        et.scale_to(mug, height_cm=1, factor=2)
    with pytest.raises(et.EditError):
        et.scale_to(mug, factor=100)


def test_engraved_base_text_removes_material_keeps_size_and_is_watertight(mug):
    out = et.emboss_text(mug, "WORLDKIT", face="base", depth_mm=0.8)
    assert out.is_watertight and out.volume < mug.volume - 5
    assert np.allclose(out.extents, mug.extents, atol=0.05)  # still sits flat, same outline
    sec = out.section(plane_origin=[0, 0, 0.4], plane_normal=[0, 0, 1])
    assert len(sec.entities) > len(mug.section(plane_origin=[0, 0, 0.4], plane_normal=[0, 0, 1]).entities)  # glyph pockets exist


def test_base_text_depth_is_clamped_to_the_floor(mug):
    thin = make_mug(floor=1.0)
    out = et.emboss_text(thin, "HI", face="base", depth_mm=2.5)
    assert out.is_watertight and floor_ok(out)


def floor_ok(m):
    ft = et.floor_thickness(m)
    return ft is not None and ft > 0.0


def test_front_text_is_raised_on_the_minus_y_side(mug):
    out = et.emboss_text(mug, "MUG", face="front", depth_mm=1.0)
    assert out.is_watertight and out.volume > mug.volume + 5
    assert out.bounds[0][1] < mug.bounds[0][1] - 0.5  # protrudes toward -y
    assert out.bounds[1][1] == pytest.approx(mug.bounds[1][1], abs=0.05)


def test_text_validation(mug):
    for bad in ({"text": ""}, {"text": "x" * 41}, {"text": "A", "face": "side"}, {"text": "A", "depth_mm": 9}, {"text": "A", "mode": "melt"}):
        with pytest.raises(et.EditError):
            et.emboss_text(mug, **bad)


def test_drill_hole_through_the_handle_bar(mug):
    h = et.handle_info(mug)
    assert h["direction_from_body_axis"][0] > 0.9  # handle is on +x
    out = et.drill_hole(mug, 5.0, h["bar_center_point_mm"], direction=h["plane_normal"])
    removed = mug.volume - out.volume
    bar = h["bar_thickness_along_normal_mm"]
    assert out.is_watertight and removed == pytest.approx(np.pi * 2.5**2 * bar, rel=0.25)
    # the hole is real: a ray along its axis now passes straight through the handle
    p = np.array(h["bar_center_point_mm"])
    n = np.array(h["plane_normal"])
    assert len(mug.ray.intersects_location([p - 30 * n], [n])[0]) > 0 and len(out.ray.intersects_location([p - 30 * n], [n])[0]) == 0


def test_drill_hole_that_misses_is_an_error_and_blind_holes_work(mug):
    with pytest.raises(et.EditError):
        et.drill_hole(mug, 5, [500, 500, 50], direction=[0, 0, 1])
    blind = et.drill_hole(mug, 6, [0, 0, 0], direction=[0, 0, 1], through=False, depth_mm=1.5)
    assert blind.volume < mug.volume and blind.is_watertight
    for bad in ({"diameter_mm": 0.1}, {"direction": [0, 0, 0]}, {"through": False}):
        with pytest.raises(et.EditError):
            et.drill_hole(mug, **{"diameter_mm": 5, "position": [0, 0, 1], **bad})


def test_flatten_base_trims_exactly_depth(mug):
    out = et.flatten_base(mug, depth_mm=1.0)
    assert out.extents[2] == pytest.approx(mug.extents[2] - 1.0, abs=0.01) and out.bounds[0][2] == pytest.approx(0.0, abs=1e-6) and out.is_watertight
    with pytest.raises(et.EditError):
        et.flatten_base(mug, depth_mm=60)


def test_split_keeps_one_capped_half(mug):
    up = et.split(mug, "z", 50.0, "upper")
    lo = et.split(mug, "z", 50.0, "lower")
    assert up.is_watertight and lo.is_watertight
    assert up.extents[2] == pytest.approx(50.0, abs=0.05) and lo.extents[2] == pytest.approx(50.0, abs=0.05)
    assert up.volume + lo.volume == pytest.approx(mug.volume, rel=1e-3)
    half = et.split(mug, "x", 0.0, "lower")  # cut through the middle, handle side removed or kept
    assert half.is_watertight and half.volume < mug.volume
    with pytest.raises(et.EditError):
        et.split(mug, "z", 500.0)


def test_thicken_grows_thin_walls_and_leaves_thick_ones_alone():
    thin = make_mug(wall=1.0, floor=1.0)
    assert float(np.percentile(et._sample_thickness(thin), 5)) < 1.3
    out = et.thicken(thin, min_wall_mm=2.5)
    assert out.is_watertight and float(np.percentile(et._sample_thickness(out), 5)) > 2.0
    ok = make_mug(wall=4.0)
    assert et.thicken(ok, min_wall_mm=1.5).volume == pytest.approx(ok.volume, rel=1e-9)


def test_mirror_reflects_about_the_bbox_midplane(mug):
    out = et.mirror(mug, "x")
    assert out.is_watertight and out.volume == pytest.approx(mug.volume, rel=1e-6) and np.allclose(out.extents, mug.extents)
    assert np.allclose(out.bounds, mug.bounds, atol=1e-6)
    # the handle moved from +x to -x
    assert et.handle_info(out)["direction_from_body_axis"][0] < -0.9
    assert et.mirror(out, "x").volume == pytest.approx(mug.volume, rel=1e-6)
    with pytest.raises(et.EditError):
        et.mirror(mug, "w")


def test_apply_tool_summary_and_errors(mug):
    new, summ = et.apply_tool(mug, "scale_to", {"factor": 1.1})
    assert summ["ok"] and summ["watertight"] and summ["size_mm_after"][2] == pytest.approx(mug.extents[2] * 1.1, abs=0.01)
    with pytest.raises(et.EditError):
        et.apply_tool(mug, "explode", {})
    with pytest.raises(et.EditError):
        et.apply_tool(mug, "scale_to", {"nope": 1})


def test_model_facts_are_complete(mug):
    f = et.model_facts(mug)
    assert f["units"] == "mm" and f["size_mm"]["z_height"] == pytest.approx(mug.extents[2], abs=0.01)
    assert f["handle"]["bar_center_point_mm"] and f["base_floor_thickness_mm"] == pytest.approx(3.0, abs=0.3)
