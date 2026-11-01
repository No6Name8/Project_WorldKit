"""FastAPI server for the viewer + AI Edit (scripted Wonder/Spark, no network)."""
from __future__ import annotations

import json
import threading

import pytest
import trimesh
from fastapi.testclient import TestClient

from tests.test_edit import FakeWonder, model_dir, reply, spark_no, spark_ok, tool_call  # noqa: F401  (model_dir is a fixture)
from worldkit import serve


def client(model_dir, wonder, spark=spark_ok):
    (model_dir / "model.glb").write_bytes(b"glTF")  # presence only: autoload points at it
    return TestClient(serve.create_app(model_dir, client=wonder, ask_spark=spark))


def test_state_edit_undo_download_roundtrip(model_dir):
    c = client(model_dir, FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="Scaled.")]))
    st = c.get("/edit/state").json()
    assert st["enabled"] and st["current"] == 0 and st["units"] == "mm" and "PLACEHOLDER" in st["scale_note"]
    r = c.post("/edit", json={"instruction": "make it 12 cm tall"}).json()
    assert r["ok"] and r["applied"] and r["version"] == 1 and r["tools"][0]["name"] == "scale_to" and r["state"]["current"] == 1
    assert r["spark"] == "looks right" and r["renders"] and c.get(f"/edit/render/{r['renders'][0]}").status_code == 200
    glb = c.get("/edit/model.glb")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    scene = trimesh.load(__import__("io").BytesIO(glb.content), file_type="glb")
    ext = list(scene.geometry.values())[0].extents
    assert ext.max() == pytest.approx(2.0, abs=1e-3) and ext[1] < ext.max()  # longest side normalised to 2 units, Y-up (height is not the longest side: plate)
    stl = c.get("/edit/current.stl")
    assert stl.status_code == 200 and "v1_mm.stl" in stl.headers["content-disposition"]
    assert trimesh.load(__import__("io").BytesIO(stl.content), file_type="stl").extents[2] == pytest.approx(120.0, abs=0.05)
    u = c.post("/undo", json={}).json()
    assert u["ok"] and u["version"] == 0 and u["state"]["current"] == 0
    assert c.post("/undo", json={}).status_code == 400  # nothing left to undo
    assert trimesh.load(__import__("io").BytesIO(c.get("/edit/current.stl").content), file_type="stl").extents[2] == pytest.approx(100.0, abs=0.05)


def test_rejected_edit_reports_why_and_leaves_state(model_dir):
    w = FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="x")] * 2)
    c = client(model_dir, w, spark_no)
    r = c.post("/edit", json={"instruction": "make it 12 cm tall"}).json()
    assert r["ok"] and not r["applied"] and "text not visible" in r["reason"] and r["state"]["current"] == 0 and r["state"]["rejected"] == 1


def test_validation_traversal_and_concurrency(model_dir):
    c = client(model_dir, FakeWonder([]))
    assert c.post("/edit", json={"instruction": ""}).status_code == 422
    assert c.post("/edit", json={"instruction": "x" * 500}).status_code == 422
    assert c.get("/edit/render/..%2F..%2Freport.json").status_code in (404, 422)
    assert c.get("/open/../report.json").status_code in (404, 400)
    assert c.get("/open/%2e%2e/report.json").status_code == 404
    assert c.get("/missing.js").status_code == 404
    assert c.get("/api/autoload").json()["kind"] == "object"
    # a second edit while one is running is refused, not queued
    gate, entered = threading.Event(), threading.Event()

    def slow(images, prompt):
        entered.set()
        gate.wait(5)
        return spark_ok(images, prompt)

    app = serve.create_app(model_dir, client=FakeWonder([reply([tool_call("scale_to", {"height_cm": 11})]), reply(text="ok")]), ask_spark=slow)
    c2 = TestClient(app)
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c2.post("/edit", json={"instruction": "make it 11 cm tall"})))
    t.start()
    assert entered.wait(10)
    assert c2.post("/edit", json={"instruction": "again"}).status_code == 409
    assert c2.get("/edit/state").json()["busy"] is True
    gate.set()
    t.join(10)
    assert out["r"].status_code == 200 and out["r"].json()["applied"]


def test_server_without_stl_is_a_clear_error(tmp_path):
    c = TestClient(serve.create_app(tmp_path))
    assert c.get("/edit/state").json()["enabled"] is False


def test_scene_mode_edits_one_object_in_its_own_folder(model_dir, tmp_path):
    """A scene folder: /edit/state without obj lists editable objects; edits, undo, glb and stl are per object and cannot escape the scene."""
    scene = tmp_path / "scene"
    odir = scene / "objects" / "mug"
    odir.mkdir(parents=True)
    for f in model_dir.iterdir():
        if f.is_file():
            (odir / f.name).write_bytes(f.read_bytes())
    (odir / "model.glb").write_bytes(serve.stl_to_glb(trimesh.load(odir / "model_print.stl")))
    (scene / "scene.json").write_text(json.dumps({"worldkit_scene": 1, "objects": [{"id": "desk", "mesh": "x"}, {"id": "mug", "stl": "objects/mug/model_print.stl"}]}))
    c = TestClient(serve.create_app(scene, client=FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="Scaled.")]), ask_spark=spark_ok))
    assert c.get("/api/autoload").json() == {"kind": "scene", "base": "/open/", "name": "scene"}
    assert c.get("/edit/state").json() == {"enabled": True, "scene_mode": True, "objects": ["mug"]}
    assert c.get("/edit/state?obj=desk").status_code == 404 and c.post("/edit", json={"instruction": "x", "obj": "../x"}).status_code == 404
    r = c.post("/edit", json={"instruction": "make it 12 cm tall", "obj": "mug"}).json()
    assert r["ok"] and r["applied"] and r["state"]["current"] == 1 and (odir / "edits" / "versions.json").is_file()
    stl = c.get("/edit/current.stl?obj=mug")
    assert "mug_v1_mm.stl" in stl.headers["content-disposition"] and trimesh.load(__import__("io").BytesIO(stl.content), file_type="stl").extents[2] == pytest.approx(120.0, abs=0.05)
    g = trimesh.load(__import__("io").BytesIO(c.get("/edit/model.glb?obj=mug").content), file_type="glb")
    assert list(g.geometry.values())[0].extents.max() > 0  # keeps the object's original GLB scale (not renormalised to 2)
    assert c.post("/undo", json={"obj": "mug"}).json()["state"]["current"] == 0
    assert c.get("/open/objects/mug/model_print.stl").status_code == 200
