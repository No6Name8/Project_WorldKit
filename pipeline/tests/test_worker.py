"""Laptop worker with a fake Supabase backend (no network, no pipeline run): status transitions, failure path, storage paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from worldkit import worker as w


class FakeBackend:
    """In-memory projects table and storage with the same interface as SupabaseRest."""

    def __init__(self, projects, files=None, user_id="user-1"):
        self.user_id = user_id
        self.projects = {p["id"]: dict(p) for p in projects}
        self.storage = dict(files or {})  # (bucket, path) -> bytes
        self.history = []                  # (project id, status) every time a status is written
        self.fail_download = False

    def claim_next(self):
        for p in sorted(self.projects.values(), key=lambda r: r.get("created_at", "")):
            if p["status"] == "uploaded":
                p["status"] = "processing"
                self.history.append((p["id"], "processing"))
                return dict(p)
        return None

    def update(self, pid, patch):
        self.projects[pid].update(patch)
        if "status" in patch:
            self.history.append((pid, patch["status"]))

    def download(self, bucket, path):
        if self.fail_download:
            raise w.WorkerError(f"Could not download {bucket}/{path} (404).")
        return self.storage[(bucket, path)]

    def upload(self, bucket, path, data, content_type):
        self.storage[(bucket, path)] = data
        self.last_type = content_type


def opts(**kw):
    return argparse.Namespace(**{"dry_run": False, "coin": "saudi-25-halalas", "object": "mug", "poll": 0.0, **kw})


def project(pid="p1", mode="object", **kw):
    return {"id": pid, "user_id": "user-1", "name": "my mug", "mode": mode, "status": "uploaded", "video_path": f"user-1/{pid}/video.mp4", "created_at": f"2026-11-02T10:0{pid[-1]}", **kw}


def fake_run(tmp_files):
    def run(proj, video, out_dir, o):
        out_dir.mkdir(parents=True, exist_ok=True)
        for rel, data in tmp_files.items():
            (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (out_dir / rel).write_bytes(data)
        return out_dir
    return run


def test_uploaded_to_processing_to_done_with_result_paths_and_report(tmp_path):
    b = FakeBackend([project()], {("videos", "user-1/p1/video.mp4"): b"video"})
    calls = {}

    def pipeline(proj, video, out_dir, o):
        calls.update(video=video.read_bytes(), mode=proj["mode"], coin=o.coin, object=o.object)
        return fake_run({"model.glb": b"glb", "model_print.stl": b"stl", "report.json": json.dumps({"object_name": "mug", "measurement": {"height_mm": 71.3, "scale_source": "scale source: coin"}, "junk": 1}).encode(),
                         "frames/sharp_00.png": b"png", "cache/x.json": b"{}"})(proj, video, out_dir, o)

    assert w.Worker(b, opts(), tmp_path, pipeline, log=lambda s: None).run_once() == "done"
    assert b.history == [("p1", "processing"), ("p1", "done")]
    assert calls == {"video": b"video", "mode": "object", "coin": "saudi-25-halalas", "object": "mug"}
    keys = {k for k in b.storage if k[0] == "results"}
    assert keys == {("results", "user-1/p1/model.glb"), ("results", "user-1/p1/model_print.stl"), ("results", "user-1/p1/report.json")}  # user_id/project_id/..., no frames or cache
    p = b.projects["p1"]
    assert p["status"] == "done" and p["result_path"] == "user-1/p1" and p["error"] is None
    assert p["report"]["measurement"]["height_mm"] == 71.3 and p["report"]["kind"] == "object" and "junk" not in p["report"]
    assert sorted(p["report"]["files"]) == ["model.glb", "model_print.stl", "report.json"]


def test_map_mode_runs_the_scene_pipeline_name_and_uploads_nested_files(tmp_path):
    b = FakeBackend([project(mode="map")], {("videos", "user-1/p1/video.mp4"): b"v"})
    seen = {}

    def pipeline(proj, video, out_dir, o):
        seen["mode"] = proj["mode"]
        return fake_run({"scene.json": b"{}", "scene_report.json": b'{"verify": {"mean_iou_before": 0.2, "mean_iou_after": 0.5, "extra": 1}}', "diorama.stl": b"d", "objects/mug/model.glb": b"g",
                         "objects/mug/model_print.stl": b"s", "verify/weak_view0.png": b"p"})(proj, video, out_dir, o)

    assert w.Worker(b, opts(), tmp_path, pipeline, log=lambda s: None).run_once() == "done"
    assert seen["mode"] == "map"
    assert ("results", "user-1/p1/objects/mug/model.glb") in b.storage and ("results", "user-1/p1/verify/weak_view0.png") in b.storage
    assert b.projects["p1"]["report"]["verify"] == {"mean_iou_before": 0.2, "mean_iou_after": 0.5, "camera_method_kept": None}


def test_pipeline_failure_sets_failed_with_a_plain_sentence_and_uploads_nothing(tmp_path):
    b = FakeBackend([project()], {("videos", "user-1/p1/video.mp4"): b"v"})

    def boom(*a):
        raise w.WorkerError("The pipeline stopped: SAM 3.1 could not find the mug in any frame.")

    assert w.Worker(b, opts(), tmp_path, boom, log=lambda s: None).run_once() == "failed"
    p = b.projects["p1"]
    assert p["status"] == "failed" and p["error"] == "The pipeline stopped: SAM 3.1 could not find the mug in any frame."
    assert not [k for k in b.storage if k[0] == "results"] and b.history == [("p1", "processing"), ("p1", "failed")]


def test_unexpected_errors_and_missing_downloads_do_not_kill_the_worker(tmp_path):
    b = FakeBackend([project("p1"), project("p2")], {("videos", "user-1/p1/video.mp4"): b"v", ("videos", "user-1/p2/video.mp4"): b"v"})
    b.fail_download = True
    wk = w.Worker(b, opts(), tmp_path, lambda *a: (_ for _ in ()).throw(KeyError("x")), log=lambda s: None)
    assert wk.run_once() == "failed" and "404" in b.projects["p1"]["error"]
    b.fail_download = False
    assert wk.run_once() == "failed" and b.projects["p2"]["error"].startswith("The worker hit an unexpected error: KeyError")
    assert wk.run_once() is None  # nothing left to claim


def test_a_model_less_run_fails_instead_of_reporting_done(tmp_path):
    b = FakeBackend([project()], {("videos", "user-1/p1/video.mp4"): b"v"})
    assert w.Worker(b, opts(), tmp_path, fake_run({"report.json": b"{}"}), log=lambda s: None).run_once() == "failed"
    assert "no 3D model" in b.projects["p1"]["error"]


def test_dry_run_publishes_the_bundled_demo_without_running_or_downloading_the_pipeline(tmp_path):
    b = FakeBackend([project(), project("p2", mode="map")])  # no video in storage: a dry run must not need it
    ran = []
    wk = w.Worker(b, opts(dry_run=True), tmp_path, lambda *a: ran.append(1), log=lambda s: None)
    assert wk.run_once() == "done" and wk.run_once() == "done" and not ran
    assert ("results", "user-1/p1/model.glb") in b.storage and ("results", "user-1/p1/parts/parts.json") in b.storage
    assert ("results", "user-1/p2/scene.json") in b.storage and ("results", "user-1/p2/diorama.stl") in b.storage
    assert b.projects["p1"]["report"]["dry_run"] and b.projects["p1"]["report"]["files"]


def test_projects_without_a_video_fail_and_other_statuses_are_ignored(tmp_path):
    b = FakeBackend([project(video_path=None), project("p2", status="done")])
    wk = w.Worker(b, opts(), tmp_path, lambda *a: None, log=lambda s: None)
    assert wk.run_once() == "failed" and b.projects["p1"]["error"] == "This project has no video to process."
    assert wk.run_once() is None and b.projects["p2"]["status"] == "done"


def test_content_types_and_size_filter(tmp_path):
    run = tmp_path / "run"
    (run / "objects" / "a").mkdir(parents=True)
    (run / "model.glb").write_bytes(b"g")
    (run / "notes.txt").write_bytes(b"x")  # not a viewer/print file
    (run / "objects" / "a" / "model_print.stl").write_bytes(b"s")
    assert [f.name for f in w.result_files(run)] == ["model.glb", "model_print.stl"]
    assert w.CONTENT_TYPES[".glb"] == "model/gltf-binary" and w.CONTENT_TYPES[".stl"] == "model/stl"


def test_rest_client_paths_use_the_user_token_and_never_a_service_key():
    calls = []

    class R:
        def __init__(self, code=200, body=None):
            self.status_code, self._b, self.content, self.text = code, body, b"bytes", ""

        def json(self):
            return self._b

        def raise_for_status(self):
            assert self.status_code < 400

    class S:
        def post(self, url, **kw):
            calls.append(("POST", url, kw.get("headers", {})))
            return R(200, {"access_token": "tok", "user": {"id": "u1"}})

        def request(self, method, url, **kw):
            calls.append((method, url, kw.get("headers", {}), kw.get("params")))
            if method == "GET" and "rest/v1" in url:
                return R(200, [{"id": "p9", "status": "uploaded"}])
            if method == "PATCH":
                return R(200, [{"id": "p9", "status": "processing"}])
            return R(200, None)

    c = w.SupabaseRest("https://x.supabase.co/", "sb_publishable_abc", "a@b.c", "pw", session=S())
    assert c.user_id == "u1"
    assert c.claim_next()["status"] == "processing"
    c.upload("results", "u1/p9/model.glb", b"g", "model/gltf-binary")
    c.download("videos", "u1/p9/video.mp4")
    urls = [x[1] for x in calls]
    assert "https://x.supabase.co/auth/v1/token?grant_type=password" in urls
    assert any(u.endswith("/storage/v1/object/results/u1/p9/model.glb") for u in urls) and any(u.endswith("/storage/v1/object/authenticated/videos/u1/p9/video.mp4") for u in urls)
    assert all(h.get("apikey") == "sb_publishable_abc" for _, _, h, *_ in calls) and all(h.get("Authorization") in (None, "Bearer tok") for _, _, h, *_ in calls)
    claim = [x for x in calls if x[0] == "PATCH"][0]
    assert claim[3] == {"id": "eq.p9", "status": "eq.uploaded"}  # the claim only wins while the row is still 'uploaded'


def test_main_reports_missing_settings_without_crashing(monkeypatch, capsys):
    for k in ("VITE_SUPABASE_URL", "SUPABASE_URL", "VITE_SUPABASE_PUBLISHABLE_KEY", "SUPABASE_PUBLISHABLE_KEY", "WORKER_EMAIL", "WORKER_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(w, "load_env_files", lambda: None)
    assert w.main(["--once"]) == 2
    assert "WORKER_EMAIL" in capsys.readouterr().err
