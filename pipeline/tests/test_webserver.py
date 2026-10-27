"""Tests for the viewer's Flask backend (worldkit.webserver).

Uses Flask's test client -- no real server process, no real Meta API call
(editcmd.interpret_edit_instruction is monkeypatched). Covers: static file
serving, health check, and /api/edit's success/error-mapping paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worldkit import editcmd, webserver
from worldkit.meta_api import MetaAPIUnavailable


@pytest.fixture
def viewer_dir(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html><body>viewer</body></html>")
    (tmp_path / "assets" / "sample_mug.glb").write_bytes(b"GLTF_FAKE_BYTES")
    return tmp_path


@pytest.fixture
def client(viewer_dir: Path):
    app = webserver.create_app(viewer_dir)
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_serves_viewer_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"viewer" in resp.data


def test_assets_serves_the_sample_mesh(client):
    resp = client.get("/assets/sample_mug.glb")
    assert resp.status_code == 200
    assert resp.data == b"GLTF_FAKE_BYTES"


def test_assets_404s_on_missing_file(client):
    resp = client.get("/assets/does_not_exist.glb")
    assert resp.status_code == 404


def test_health_reports_key_configured_state(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(webserver, "has_api_key", lambda: True)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "muse_spark_key_configured": True}


def test_edit_endpoint_returns_validated_transform_on_success(client, monkeypatch: pytest.MonkeyPatch):
    fake_transform = editcmd.EditTransform(
        translate_cm=(-10.0, 0.0, 0.0), rotate_deg=(0.0, 0.0, 0.0),
        scale_factor=1.0, explanation="moved left", source="muse_spark",
    )
    monkeypatch.setattr(webserver.editcmd, "interpret_edit_instruction", lambda instr: fake_transform)

    resp = client.post("/api/edit", json={"instruction": "move it 10cm left"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["transform"]["translate_cm"] == [-10.0, 0.0, 0.0]
    assert data["transform"]["explanation"] == "moved left"


def test_edit_endpoint_maps_api_unavailable_to_503(client, monkeypatch: pytest.MonkeyPatch):
    def raise_unavailable(instr):
        raise MetaAPIUnavailable("no key configured")

    monkeypatch.setattr(webserver.editcmd, "interpret_edit_instruction", raise_unavailable)

    resp = client.post("/api/edit", json={"instruction": "move it"})

    assert resp.status_code == 503
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error_type"] == "api_unavailable"


def test_edit_endpoint_maps_malformed_response_to_422(client, monkeypatch: pytest.MonkeyPatch):
    def raise_invalid(instr):
        raise editcmd.EditCommandError("no JSON object found in model response")

    monkeypatch.setattr(webserver.editcmd, "interpret_edit_instruction", raise_invalid)

    resp = client.post("/api/edit", json={"instruction": "do something weird"})

    assert resp.status_code == 422
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error_type"] == "invalid_response"


def test_edit_endpoint_handles_missing_instruction_field(client, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_interpret(instr):
        captured["instr"] = instr
        raise editcmd.EditCommandError("instruction is empty")

    monkeypatch.setattr(webserver.editcmd, "interpret_edit_instruction", fake_interpret)

    resp = client.post("/api/edit", json={})

    assert captured["instr"] == ""
    assert resp.status_code == 422
