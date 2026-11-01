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


def test_serves_top_level_viewer_module_and_rejects_traversal(client, viewer_dir: Path):
    (viewer_dir / "physics.js").write_text("export const x = 1;")
    r = client.get("/physics.js")
    assert r.status_code == 200 and b"export const x" in r.data
    assert client.get("/missing.js").status_code == 404
    assert client.get("/..%2Findex.js").status_code in (404, 400)


def test_open_dir_autoload_vendor_and_traversal(viewer_dir: Path, tmp_path: Path):
    (viewer_dir / "vendor").mkdir()
    (viewer_dir / "vendor" / "lib.js").write_text("export const z = 1;")
    run = tmp_path / "run"
    run.mkdir()
    (run / "model.glb").write_bytes(b"GLB")
    (run / "report.json").write_text("{}")
    c = webserver.create_app(viewer_dir, open_dir=run).test_client()
    assert c.get("/vendor/lib.js").status_code == 200
    cfg = c.get("/api/autoload").get_json()
    assert cfg["kind"] == "object" and cfg["glb"] == "/open/model.glb" and cfg["report"] == "/open/report.json"
    assert c.get("/open/model.glb").data == b"GLB"
    assert c.get("/open/../index.html").status_code in (400, 404)
    assert c.get("/open/%2e%2e/index.html").status_code in (400, 404)
    (run / "scene.json").write_text("{}")
    assert c.get("/api/autoload").get_json()["kind"] == "scene"  # a scene takes precedence
    # no run opened: nothing to autoload and /open is closed
    c2 = webserver.create_app(viewer_dir).test_client()
    assert c2.get("/api/autoload").get_json()["kind"] is None and c2.get("/open/model.glb").status_code == 404
    # empty directory is reported, not crashed on
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "neither" in webserver.create_app(viewer_dir, open_dir=empty).test_client().get("/api/autoload").get_json()["error"]


def test_plain_viewer_server_reports_ai_edit_disabled(client):
    assert client.get("/edit/state").get_json() == {"enabled": False}
