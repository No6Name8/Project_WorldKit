"""Tests for the Modal-independent reconstruction fallback (reconstruct.py).

No real model inference is exercised here (that's neither testable offline
nor the point of this module) -- these tests cover the pure image-cropping
logic, the external-mesh import path, and the hosted-Space orchestration
(request building, result-shape validation, error wrapping) with the
network/model call itself monkeypatched out.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from worldkit import reconstruct


def _rgba_bytes(canvas_size: tuple[int, int], opaque_box: tuple[int, int, int, int] | None) -> bytes:
    """Build a synthetic RGBA PNG: transparent canvas with an opaque box (or none)."""
    w, h = canvas_size
    arr = np.zeros((h, w, 4), dtype="uint8")
    if opaque_box is not None:
        left, top, right, bottom = opaque_box
        arr[top:bottom, left:right] = (200, 120, 60, 255)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


# --- crop_to_alpha_bbox ------------------------------------------------


def test_crop_to_alpha_bbox_crops_tightly_and_pads_to_square_output_size():
    # A small opaque region off-center in a much larger canvas -- mirrors the
    # real defect this function fixes (a 155x140px mug in an 856x480 frame).
    img_bytes = _rgba_bytes((480, 856), opaque_box=(164, 254, 319, 394))

    cropped_bytes = reconstruct.crop_to_alpha_bbox(img_bytes, pad_frac=0.15, out_size=512)

    out = Image.open(io.BytesIO(cropped_bytes))
    assert out.size == (512, 512)
    assert out.mode == "RGBA"
    # The object must now occupy a much larger fraction of the frame than in
    # the original 480x856 canvas (where it was only ~155x140px).
    out_arr = np.array(out)
    opaque_fraction = (out_arr[..., 3] > 0).mean()
    assert opaque_fraction > 0.3  # tightly cropped + padded, not still tiny


def test_crop_to_alpha_bbox_raises_on_fully_transparent_image():
    img_bytes = _rgba_bytes((100, 100), opaque_box=None)

    with pytest.raises(reconstruct.ReconstructionError):
        reconstruct.crop_to_alpha_bbox(img_bytes)


def test_crop_to_alpha_bbox_is_idempotent_on_an_already_tight_square_image():
    img_bytes = _rgba_bytes((200, 200), opaque_box=(10, 10, 190, 190))

    cropped_bytes = reconstruct.crop_to_alpha_bbox(img_bytes, out_size=256)

    out = Image.open(io.BytesIO(cropped_bytes))
    assert out.size == (256, 256)
    out_arr = np.array(out)
    assert (out_arr[..., 3] > 0).mean() > 0.5


# --- load_external_mesh -------------------------------------------------


def test_load_external_mesh_missing_file_raises(tmp_path: Path):
    with pytest.raises(reconstruct.ReconstructionError):
        reconstruct.load_external_mesh(tmp_path / "does_not_exist.glb")


def test_load_external_mesh_empty_file_raises(tmp_path: Path):
    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")

    with pytest.raises(reconstruct.ReconstructionError):
        reconstruct.load_external_mesh(empty)


def test_load_external_mesh_reads_existing_file_bytes(tmp_path: Path):
    mesh_path = tmp_path / "model.obj"
    mesh_path.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    data = reconstruct.load_external_mesh(mesh_path)

    assert data == mesh_path.read_bytes()
    assert len(data) > 0


# --- reconstruct_via_hosted_space: orchestration, no real network/model ---


class _FakeClient:
    """Stand-in for gradio_client.Client -- records calls, returns a canned result."""

    def __init__(self, space_id, token=None, result=None, raise_exc=None):
        self.space_id = space_id
        self.token = token
        self._result = result
        self._raise_exc = raise_exc
        self.predict_kwargs = None

    def predict(self, **kwargs):
        self.predict_kwargs = kwargs
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


def _patch_gradio_client(monkeypatch: pytest.MonkeyPatch, result=None, raise_exc=None):
    created = {}

    def _fake_import():
        def _make_client(space_id, token=None):
            client = _FakeClient(space_id, token=token, result=result, raise_exc=raise_exc)
            created["client"] = client
            return client

        def _handle_file(path):
            return {"path": path}

        return _make_client, _handle_file

    monkeypatch.setattr(reconstruct, "_import_gradio_client", _fake_import)
    return created


def test_reconstruct_via_hosted_space_downloads_the_returned_glb(monkeypatch: pytest.MonkeyPatch):
    fake_result = (
        "<html>...</html>",
        {"value": "/tmp/white_mesh.obj"},
        "/static/abc-123/white_mesh.glb",
        "/static/def-456/white_mesh.obj",
    )
    created = _patch_gradio_client(monkeypatch, result=fake_result)
    requested_urls = []

    def _fake_download(url):
        requested_urls.append(url)
        return b"FAKE_GLB_BYTES"

    monkeypatch.setattr(reconstruct, "_download_glb", _fake_download)

    img_bytes = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))
    glb_bytes = reconstruct.reconstruct_via_hosted_space(
        img_bytes, space_id="someuser/Some-Space", hf_token="tok_123"
    )

    assert glb_bytes == b"FAKE_GLB_BYTES"
    assert requested_urls == ["https://someuser-some-space.hf.space/static/abc-123/white_mesh.glb"]
    assert created["client"].token == "tok_123"
    assert created["client"].predict_kwargs["api_name"] == reconstruct.DEFAULT_API_NAME


def test_reconstruct_via_hosted_space_wraps_space_errors_as_reconstruction_error(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_gradio_client(monkeypatch, raise_exc=RuntimeError("No GPU was available after 60s"))

    img_bytes = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))
    with pytest.raises(reconstruct.ReconstructionError, match="No GPU was available"):
        reconstruct.reconstruct_via_hosted_space(img_bytes, space_id="someuser/Some-Space")


def test_reconstruct_via_hosted_space_rejects_unexpected_result_shape(monkeypatch: pytest.MonkeyPatch):
    _patch_gradio_client(monkeypatch, result="not the expected 4-tuple")

    img_bytes = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))
    with pytest.raises(reconstruct.ReconstructionError, match="unexpected result shape"):
        reconstruct.reconstruct_via_hosted_space(img_bytes, space_id="someuser/Some-Space")


def test_reconstruct_via_hosted_space_reports_missing_gradio_client(monkeypatch: pytest.MonkeyPatch):
    def _raise_import_error():
        raise reconstruct.ReconstructionError("gradio_client is not installed (pip install gradio_client)")

    monkeypatch.setattr(reconstruct, "_import_gradio_client", _raise_import_error)

    img_bytes = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))
    with pytest.raises(reconstruct.ReconstructionError, match="gradio_client is not installed"):
        reconstruct.reconstruct_via_hosted_space(img_bytes)
