"""Tests for appearance.py: making a generated, textured GLB display correctly."""

from __future__ import annotations

import io
import json
import struct

import numpy as np
import pytest
import trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial

from worldkit import appearance


def _glb_json(glb: bytes) -> dict:
    length = struct.unpack("<I", glb[12:16])[0]
    return json.loads(glb[20 : 20 + length])


def _textured_glb(metallic=None, extents=(0.4, 0.3, 0.3), offset=(5.0, -2.0, 1.0)) -> bytes:
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(offset)
    uv = np.random.default_rng(0).random((len(mesh.vertices), 2))
    tex = Image.fromarray((np.random.default_rng(1).random((16, 16, 3)) * 255).astype("uint8"))
    mat = PBRMaterial(baseColorTexture=tex, metallicFactor=metallic, roughnessFactor=1.0)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=mat)
    return mesh.export(file_type="glb")


def test_implicit_default_metallic_is_replaced_by_zero():
    # glTF's default metallicFactor is 1.0, so a material that omits it renders near-black without
    # an environment map -- observed on real TRELLIS output (unlit sides of the mug almost black).
    glb = _textured_glb(metallic=None)
    assert "metallicFactor" not in _glb_json(glb)["materials"][0]["pbrMetallicRoughness"]

    out, report = appearance.finalize_textured_glb(glb)

    assert _glb_json(out)["materials"][0]["pbrMetallicRoughness"]["metallicFactor"] == 0.0
    assert report["metallic_factor_set_to_zero"] == [0]


def test_an_explicit_metallic_value_is_never_overridden():
    out, report = appearance.finalize_textured_glb(_textured_glb(metallic=0.4))

    assert _glb_json(out)["materials"][0]["pbrMetallicRoughness"]["metallicFactor"] == pytest.approx(0.4)
    assert report["metallic_factor_set_to_zero"] == []


def test_vertex_normals_are_written_for_smooth_shading():
    out, report = appearance.finalize_textured_glb(_textured_glb())

    assert "NORMAL" in _glb_json(out)["meshes"][0]["primitives"][0]["attributes"]
    assert report["vertex_normals_added"] is True


def test_scale_is_centred_and_normalised_to_the_viewer_size_without_claiming_calibration():
    out, report = appearance.finalize_textured_glb(_textured_glb(extents=(0.4, 0.3, 0.3)), target_extent=2.0)

    mesh = list(trimesh.load(io.BytesIO(out), file_type="glb", process=False).geometry.values())[0]
    assert mesh.extents.max() == pytest.approx(2.0, rel=1e-4)
    assert np.allclose(mesh.bounds.mean(axis=0), 0.0, atol=1e-4)
    assert report["scale_normalized"]["from_max_extent"] == pytest.approx(0.4, rel=1e-3)
    assert "NOT a real-world calibration" in report["scale_normalized"]["note"]


def test_scale_normalisation_can_be_disabled():
    out, report = appearance.finalize_textured_glb(_textured_glb(extents=(0.4, 0.3, 0.3)), normalize_scale=False)

    mesh = list(trimesh.load(io.BytesIO(out), file_type="glb", process=False).geometry.values())[0]
    assert mesh.extents.max() == pytest.approx(0.4, rel=1e-4)
    assert report["scale_normalized"] is None


def test_texture_and_uvs_survive_finalisation():
    glb = _textured_glb()
    out, _ = appearance.finalize_textured_glb(glb)

    before = list(trimesh.load(io.BytesIO(glb), file_type="glb", process=False).geometry.values())[0]
    after = list(trimesh.load(io.BytesIO(out), file_type="glb", process=False).geometry.values())[0]
    assert np.allclose(np.asarray(before.visual.uv), np.asarray(after.visual.uv), atol=1e-5)
    assert np.array_equal(np.array(before.visual.material.baseColorTexture), np.array(after.visual.material.baseColorTexture))


def test_glb_is_textured_distinguishes_textured_untextured_and_garbage():
    plain = trimesh.creation.box().export(file_type="glb")

    assert appearance.glb_is_textured(_textured_glb()) is True
    assert appearance.glb_is_textured(plain) is False
    assert appearance.glb_is_textured(b"definitely not a glb") is False
