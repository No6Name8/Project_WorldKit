"""Tests for printprep.py using simple generated meshes (no network, no GPU)."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from worldkit import printprep


@pytest.fixture
def box_mesh() -> trimesh.Trimesh:
    # A 2x2x4 cm box (watertight by construction).
    return trimesh.creation.box(extents=(2.0, 2.0, 4.0))


@pytest.fixture
def thin_plate_mesh() -> trimesh.Trimesh:
    # A 5x5x0.05 cm plate: thinner than any sane min-wall threshold.
    return trimesh.creation.box(extents=(5.0, 5.0, 0.05))


def test_estimate_height_cm_from_a4_scales_linearly():
    # Object is half as tall (in pixels) as the A4 sheet is wide -> ~10.5cm.
    height = printprep.estimate_height_cm_from_a4(object_pixel_height=100, a4_pixel_width=200)
    assert height == pytest.approx(printprep.A4_WIDTH_CM / 2, rel=1e-6)


def test_estimate_height_cm_from_a4_rejects_zero_width():
    with pytest.raises(printprep.PrintPrepError):
        printprep.estimate_height_cm_from_a4(object_pixel_height=100, a4_pixel_width=0)


def test_load_mesh_missing_file_raises(tmp_path: Path):
    with pytest.raises(printprep.PrintPrepError):
        printprep.load_mesh(tmp_path / "does_not_exist.glb")


def test_scale_to_height_sets_exact_z_extent(box_mesh: trimesh.Trimesh):
    scaled, factor = printprep.scale_to_height(box_mesh, target_height_cm=9.5)
    assert scaled.extents[2] == pytest.approx(9.5, rel=1e-6)
    assert factor == pytest.approx(9.5 / 4.0, rel=1e-6)


def test_scale_to_height_rejects_degenerate_mesh():
    flat = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
    flat.vertices[:, 2] = 0.0  # collapse to zero height
    with pytest.raises(printprep.PrintPrepError):
        printprep.scale_to_height(flat, target_height_cm=5.0)


def test_make_watertight_noop_on_already_watertight_box(box_mesh: trimesh.Trimesh):
    assert box_mesh.is_watertight
    repaired, method = printprep.make_watertight(box_mesh)
    assert repaired.is_watertight
    assert method == "none"


def test_add_flat_base_increases_height_and_stays_watertight(box_mesh: trimesh.Trimesh):
    original_height = box_mesh.extents[2]
    based = printprep.add_flat_base(box_mesh, base_thickness_mm=2.0)
    assert based.extents[2] > original_height
    assert based.is_watertight


def test_orient_for_printing_keeps_mesh_above_z_zero(box_mesh: trimesh.Trimesh):
    box_mesh.apply_translation((0, 0, 50))  # move far from the origin
    oriented = printprep.orient_for_printing(box_mesh)
    assert oriented.bounds[0][2] == pytest.approx(0.0, abs=1e-6)


def test_check_min_wall_thickness_flags_thin_plate(thin_plate_mesh: trimesh.Trimesh):
    warnings, n_samples = printprep.check_min_wall_thickness(
        thin_plate_mesh, min_wall_mm=printprep.DEFAULT_MIN_WALL_MM, n_samples=300
    )
    assert n_samples == 300
    assert len(warnings) > 0
    assert all(w.thickness_mm < printprep.DEFAULT_MIN_WALL_MM for w in warnings)


def test_check_min_wall_thickness_clean_on_thick_box(box_mesh: trimesh.Trimesh):
    warnings, _ = printprep.check_min_wall_thickness(
        box_mesh, min_wall_mm=printprep.DEFAULT_MIN_WALL_MM, n_samples=300
    )
    assert len(warnings) == 0


def test_export_stl_writes_readable_file(box_mesh: trimesh.Trimesh, tmp_path: Path):
    out = printprep.export_stl(box_mesh, tmp_path / "out.stl")
    assert out.is_file()
    reloaded = trimesh.load(out, force="mesh")
    assert len(reloaded.faces) > 0


def test_print_ready_true_when_watertight_and_no_thin_wall_warnings():
    report = printprep.PrintPrepReport(
        scale_factor=1.0, target_height_cm=10.0, upright_height_cm=10.0,
        height_source="user", is_estimate=False, dimensions_cm=(10.0, 10.0, 10.0),
        volume_cm3=1000.0, watertight=True, watertight_repair_used="none",
        min_wall_mm_checked=1.2, thin_wall_warnings=[], thin_wall_sample_count=1500,
    )
    assert report.print_ready is True
    assert report.to_dict()["print_ready"] is True


def test_print_ready_false_when_thin_wall_warnings_present():
    report = printprep.PrintPrepReport(
        scale_factor=1.0, target_height_cm=10.0, upright_height_cm=10.0,
        height_source="user", is_estimate=False, dimensions_cm=(10.0, 10.0, 10.0),
        volume_cm3=1000.0, watertight=True, watertight_repair_used="none",
        min_wall_mm_checked=1.2,
        thin_wall_warnings=[printprep.ThinWallWarning(point_cm=(0.0, 0.0, 0.0), thickness_mm=0.5)],
        thin_wall_sample_count=1500,
    )
    assert report.print_ready is False
    assert report.to_dict()["print_ready"] is False


def test_print_ready_false_when_not_watertight():
    report = printprep.PrintPrepReport(
        scale_factor=1.0, target_height_cm=10.0, upright_height_cm=10.0,
        height_source="user", is_estimate=False, dimensions_cm=(10.0, 10.0, 10.0),
        volume_cm3=1000.0, watertight=False, watertight_repair_used="convex_hull_fallback",
        min_wall_mm_checked=1.2, thin_wall_warnings=[], thin_wall_sample_count=1500,
    )
    assert report.print_ready is False


def test_prepare_for_print_end_to_end_with_user_height(box_mesh: trimesh.Trimesh, tmp_path: Path):
    glb_path = tmp_path / "model.glb"
    box_mesh.export(glb_path, file_type="glb")

    report = printprep.prepare_for_print(
        glb_path, tmp_path / "model_print.stl", height_cm=9.5
    )

    assert report.height_source == "user"
    assert report.is_estimate is False
    assert report.watertight is True
    assert report.print_ready is True  # thick-walled box, no thin-wall warnings expected
    assert (tmp_path / "model_print.stl").is_file()
    # upright_height_cm is measured along the ORIGINAL upright axis, before
    # print-orientation can rotate the mesh onto its side -- must equal the
    # requested height exactly (modulo float rounding).
    assert report.upright_height_cm == pytest.approx(9.5, rel=1e-6)
    # dimensions_cm (post print-orientation) may have moved the 9.5cm extent
    # to a different axis, but it must still be present among the extents.
    assert max(report.dimensions_cm) >= 9.5


def test_prepare_for_print_end_to_end_with_a4_estimate(box_mesh: trimesh.Trimesh, tmp_path: Path):
    glb_path = tmp_path / "model.glb"
    box_mesh.export(glb_path, file_type="glb")

    report = printprep.prepare_for_print(
        glb_path, tmp_path / "model_print.stl", estimated_height_cm=12.0
    )

    assert report.height_source == "a4_estimate"
    assert report.is_estimate is True
    assert report.upright_height_cm == pytest.approx(12.0, rel=1e-6)


def test_prepare_for_print_falls_back_without_any_height_hint(box_mesh: trimesh.Trimesh, tmp_path: Path):
    glb_path = tmp_path / "model.glb"
    box_mesh.export(glb_path, file_type="glb")

    report = printprep.prepare_for_print(glb_path, tmp_path / "model_print.stl")

    assert report.height_source == "default_fallback"
    assert report.is_estimate is True
