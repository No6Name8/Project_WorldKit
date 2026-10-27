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


# --- reconstruct_via_trellis_space: orchestration, no real network/model ---------------------------


class _FakeTrellisClient:
    """Records every call and answers like the real Space did when the flow was verified live."""

    def __init__(self, space_id, token=None, glb_path=None, fail_on=None, error=None):
        self.space_id, self.token = space_id, token
        self.calls = []
        self._glb_path, self._fail_on, self._error = glb_path, fail_on, error

    def predict(self, **kwargs):
        api = kwargs.get("api_name")
        self.calls.append((api, kwargs))
        if self._fail_on == api:
            raise self._error
        if api == "/start_session":
            return None
        if api == "/preprocess_image":
            return str(self._glb_path.parent / "preprocessed.png")
        if api == "/generate_and_extract_glb":
            return ({"video": "x.mp4"}, str(self._glb_path), str(self._glb_path))
        raise AssertionError(f"unexpected api_name {api}")


def _patch_trellis(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **client_kwargs):
    glb = tmp_path / "sample.glb"
    glb.write_bytes(b"glTF-bytes-from-trellis")
    made = {}

    def _fake_import():
        def _make(space_id, token=None):
            made["client"] = _FakeTrellisClient(space_id, token=token, glb_path=glb, **client_kwargs)
            return made["client"]

        return _make, lambda path: {"path": path}

    monkeypatch.setattr(reconstruct, "_import_gradio_client", _fake_import)
    return made


def test_trellis_runs_the_verified_call_sequence_and_returns_the_glb_bytes(monkeypatch, tmp_path: Path):
    made = _patch_trellis(monkeypatch, tmp_path)
    img = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))

    glb = reconstruct.reconstruct_via_trellis_space(img, hf_token="tok", seed=7, texture_size=512)

    assert glb == b"glTF-bytes-from-trellis"
    client = made["client"]
    assert client.space_id == reconstruct.DEFAULT_TRELLIS_SPACE_ID and client.token == "tok"
    assert [c[0] for c in client.calls] == ["/start_session", "/preprocess_image", "/generate_and_extract_glb"]
    gen = client.calls[2][1]
    assert gen["seed"] == 7 and gen["texture_size"] == 512
    assert gen["multiimages"] == []  # single-image mode: TRELLIS's own multi-image mode was tested and rejected
    assert gen["image"]["path"].endswith("preprocessed.png")  # generation uses the Space's preprocessed image


def test_trellis_wraps_failures_and_explains_an_exhausted_quota(monkeypatch, tmp_path: Path):
    quota = RuntimeError("You have exceeded your free ZeroGPU quota (120s requested vs. 162s left).")
    _patch_trellis(monkeypatch, tmp_path, fail_on="/generate_and_extract_glb", error=quota)
    img = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))

    with pytest.raises(reconstruct.ReconstructionError) as exc_info:
        reconstruct.reconstruct_via_trellis_space(img)

    msg = str(exc_info.value)
    assert "quota" in msg and "resets daily" in msg
    assert "--mesh-backend import" in msg  # points at the working alternative, never at paying


def test_trellis_rejects_an_unexpected_result_shape(monkeypatch, tmp_path: Path):
    made = _patch_trellis(monkeypatch, tmp_path)
    img = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))
    monkeypatch.setattr(_FakeTrellisClient, "predict", lambda self, **kw: None if kw["api_name"] == "/start_session" else "not-a-tuple")

    with pytest.raises(reconstruct.ReconstructionError):
        reconstruct.reconstruct_via_trellis_space(img)


def test_trellis_rejects_a_missing_glb_file(monkeypatch, tmp_path: Path):
    made = _patch_trellis(monkeypatch, tmp_path)
    (tmp_path / "sample.glb").unlink()
    img = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))

    with pytest.raises(reconstruct.ReconstructionError, match="did not produce a GLB"):
        reconstruct.reconstruct_via_trellis_space(img)


def test_hunyuan_path_also_explains_an_exhausted_quota(monkeypatch):
    _patch_gradio_client(monkeypatch, raise_exc=RuntimeError("You have exceeded your free ZeroGPU quota"))
    img = _rgba_bytes((400, 400), opaque_box=(150, 150, 250, 250))

    with pytest.raises(reconstruct.ReconstructionError, match="resets daily"):
        reconstruct.reconstruct_via_hosted_space(img)
