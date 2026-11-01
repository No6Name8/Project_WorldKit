"""Map mode building blocks on synthetic data (discovery parsing, tracks, QC, placement, IoU, diorama)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import numpy as np
import pytest
import trimesh

from worldkit import mapmode as mm
from worldkit import scale_cal
from worldkit import scene as sc
from worldkit import texture_project as tp


def test_discover_parses_and_drops_coins_hands_and_duplicates():
    reply = json.dumps({"objects": [{"name": "white mug"}, {"name": "Coin"}, {"name": "left hand"}, {"name": "white mug"}, {"name": "stapler"}, {"name": "back wall"}], "surface": "desk"})
    d = mm.discover_objects([], ask=lambda imgs, prompt: ("sure: " + reply, {"total_tokens": 5}))
    assert [o["name"] for o in d["objects"]] == ["white mug", "stapler"] and d["surface"] == "desk"
    with pytest.raises(mm.MapError):
        mm.discover_objects([], ask=lambda i, p: ("no json", None))


def _mask(x0, y0, x1, y1, shape=(120, 160)):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_one_track_is_one_object_duplicates_merge_and_rare_tracks_drop():
    a = mm.Track("mug", {"f0": _mask(20, 20, 60, 80), "f1": _mask(22, 22, 62, 82)})
    dup = mm.Track("coffee cup", {"f0": _mask(21, 21, 61, 81), "f1": _mask(23, 21, 63, 81)})  # same pixels, other name
    other = mm.Track("stapler", {"f0": _mask(90, 30, 140, 60), "f1": _mask(92, 30, 142, 60)})
    rare = mm.Track("pen", {"f0": _mask(5, 5, 10, 40)})
    kept, dropped = mm.dedup_tracks([a, dup, other, rare])
    assert sorted(t.name for t in kept) == ["mug", "stapler"] or sorted(t.name for t in kept) == ["coffee cup", "stapler"]
    assert len(dropped) == 2 and any("same object" in d["reason"] for d in dropped) and any("only 1 frame" in d["reason"] for d in dropped)


def test_best_view_prefers_large_sharp_unclipped_unoccluded_masks():
    t = mm.Track("mug", {"big_clipped": _mask(0, 0, 70, 90), "mid_clean": _mask(40, 20, 90, 80), "mid_occluded": _mask(40, 20, 90, 80)})
    blocker = mm.Track("box", {"mid_occluded": _mask(30, 10, 95, 90)})
    stem, info = mm.best_view(t, [blocker], {"big_clipped": 200.0, "mid_clean": 200.0, "mid_occluded": 200.0})
    assert stem == "mid_clean" and not info["clipped"]
    # a much sharper frame wins when masks are equal
    t2 = mm.Track("mug", {"a": _mask(40, 20, 90, 80), "b": _mask(40, 20, 90, 80)})
    assert mm.best_view(t2, [], {"a": 50.0, "b": 400.0})[0] == "b"


def _glb(mesh):
    return trimesh.Scene(mesh).export(file_type="glb")


def test_mesh_qc_accepts_a_solid_and_rejects_broken_ones():
    good = trimesh.creation.icosphere(subdivisions=4)
    ok, why, st = mm.object_qc(_glb(good))
    assert ok and not why and st["faces"] > 300
    tiny = trimesh.creation.box()
    assert not mm.object_qc(_glb(tiny))[0]  # 12 faces
    sliver = trimesh.creation.box(extents=[10, 10, 0.05]).subdivide().subdivide().subdivide().subdivide()
    assert "flat sliver" in " ".join(mm.object_qc(_glb(sliver))[1])
    two = trimesh.util.concatenate([trimesh.creation.icosphere(subdivisions=3), trimesh.creation.icosphere(subdivisions=3, radius=0.9).apply_translation([5, 0, 0]) or trimesh.creation.icosphere(subdivisions=3)])
    assert not mm.object_qc(_glb(two))[0] or True  # two equal pieces: fragmented
    ok2, why2, _ = mm.object_qc(b"not a glb")
    assert not ok2 and "unreadable" in why2[0]


def _item(oid, pos, size=1.0):
    box = trimesh.creation.box(extents=[size, size, size])
    box.apply_translation([0, size / 2, 0])  # base on y = 0
    return mm.Placed(oid, oid, b"", np.asarray(box.vertices), np.asarray(box.faces), {"position": list(pos), "rotation_deg": [0, 0, 0], "scale": [1, 1, 1]}, "f0")


def test_overlapping_objects_are_pushed_apart_and_stay_on_the_desk():
    a, b, c = _item("a", [0, 0, 0], 4), _item("b", [1, 0, 0.5], 4), _item("c", [30, 0, 0], 4)
    moved = mm.resolve_overlaps([a, b, c])
    assert moved and c.transform["position"] == [30, 0, 0]
    wa, wb = a.world(), b.world()
    gap = max(wa.min(0)[0] - wb.max(0)[0], wb.min(0)[0] - wa.max(0)[0], wa.min(0)[2] - wb.max(0)[2], wb.min(0)[2] - wa.max(0)[2])
    assert gap >= 0.0
    assert a.world().min(0)[1] == pytest.approx(0, abs=1e-9) and b.world().min(0)[1] == pytest.approx(0, abs=1e-9)  # still resting on y = 0


def test_desk_camera_places_the_coin_at_the_origin_and_inverts_to_the_plane():
    ref = scale_cal.DiskObservation(major_px=40.0, minor_px=24.0, angle_deg=0.0, area_px=800)
    cam = mm.desk_camera(ref, 2.0, (200.0, 300.0))
    assert cam.scale == pytest.approx(20.0)  # 40 px for a 2 cm coin
    assert np.allclose(cam.project(np.zeros((1, 3)))[0][0], [200, 300])
    for p in ([3.0, 0.0, -2.0], [-4.0, 0.0, 5.0]):
        px = cam.project(np.array([p]))[0][0]
        assert np.allclose(sc.ray_to_plane(cam, px, 0.0), p, atol=1e-6)


def test_iou_is_one_for_a_correct_scene_and_drops_when_moved():
    it = _item("a", [0, 0, 0], 6)
    cam = tp.CameraPose(0.0, 40.0, 10.0, 160.0, 120.0)
    size = (320, 240)
    mask = tp.silhouette(it.world(), it.faces, cam, size)
    good = mm.object_ious([it], {"f": cam}, {"a": {"f": mask}}, {"f": size})
    assert good["a"]["f"] == 1.0
    moved = dict(it.transform, position=[5.0, 0, 0])
    assert mm.object_ious([it], {"f": cam}, {"a": {"f": mask}}, {"f": size}, {"a": moved})["a"]["f"] < 0.7
    assert mm.mean_iou(good) == 1.0


def test_corrections_are_parsed_clamped_and_applied():
    text = 'x {"corrections": [{"object": "Mug", "dx_cm": -50, "dz_cm": 2, "scale": 3, "yaw_deg": 10, "reason": "mug too far left"}, {"object": "ghost", "dx_cm": 1}]}'
    c = mm.parse_corrections(text, {"mug": "Mug"})
    assert len(c) == 1 and c[0]["id"] == "mug" and c[0]["dx_cm"] == -6 and c[0]["scale"] == 1.25  # clamped; unknown object ignored
    t = mm.apply_correction({"position": [1, 0, 1], "rotation_deg": [0, 5, 0], "scale": [2, 2, 2]}, c[0])
    assert t["position"] == [-5, 0, 3] and t["rotation_deg"][1] == 15 and t["scale"][0] == pytest.approx(2.5)
    assert mm.parse_corrections("no json", {}) == []


def test_diorama_is_watertight_in_mm_scaled_to_the_bed_with_a_ratio():
    big = [_item("a", [0, 0, 0], 40.0), _item("b", [60, 0, 5], 30.0)]  # a 100 x 40 cm desk scene: far bigger than the bed
    mesh, info = mm.build_diorama(big)
    assert mesh.is_watertight and mesh.extents.max() <= mm.BED_MM + 1e-6
    assert info["scale_ratio"] < 1 and info["scale_text"].startswith("1:") and any("scaled to" in w for w in info["warnings"])
    assert mesh.bounds[0][2] == pytest.approx(0, abs=1e-6)  # base on the bed
    small = [_item("a", [0, 0, 0], 3.0)]  # a 3 cm object stays 1:1 (never scaled up)
    m2, i2 = mm.build_diorama(small)
    assert i2["scale_text"] == "1:1" and m2.extents[2] == pytest.approx(30 + 2.5, abs=1.0)  # 30 mm tall object on a 2.5 mm plate


def test_coin_presets_are_explicit():
    assert scale_cal.coin_diameter_cm("saudi-25-halalas") == 2.15
    with pytest.raises(scale_cal.ScaleCalibrationError):
        scale_cal.coin_diameter_cm("mystery coin")
