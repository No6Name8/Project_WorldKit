"""Laptop worker: turns uploaded phone videos into results, using the user's own Supabase account (Row Level Security applies; no service key).

    python -m worldkit.worker [--once] [--dry-run] [--coin saudi-25-halalas] [--object mug] [--poll 5]

It signs in as WORKER_EMAIL / WORKER_PASSWORD (the same user that uploaded the videos), polls the ``projects`` table for status 'uploaded',
claims one (sets 'processing'), downloads the video from the private ``videos`` bucket, runs ``worldkit.object`` (mode object) or
``worldkit.scene`` (mode map) exactly like the CLI, uploads the STL / GLB / report files to the private ``results`` bucket under
``<user_id>/<project_id>/`` and sets 'done' with the report (or 'failed' with one plain sentence).

The heavy work (SAM 3D on Modal, Meta APIs) happens HERE, on this machine. The web app never runs the pipeline.

``--dry-run`` skips the pipeline and publishes a bundled run folder instead (viewer/assets/demo/run_mug for object, run_desk for map), so the whole
chain can be tested end to end without any GPU or API cost.

Settings come from the environment or from ``pipeline/.env`` / ``web/.env.local``: VITE_SUPABASE_URL (or SUPABASE_URL), VITE_SUPABASE_PUBLISHABLE_KEY
(or SUPABASE_PUBLISHABLE_KEY), WORKER_EMAIL, WORKER_PASSWORD.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
DEMO = {"object": ROOT / "viewer" / "assets" / "demo" / "run_mug", "map": ROOT / "viewer" / "assets" / "demo" / "run_desk"}
SKIP_DIRS = {"cache", "frames", "_all_frames", "__pycache__", "previews"}
MAX_RESULT_FILE_MB = 45  # Supabase free plan limits one object to 50 MB
CONTENT_TYPES = {".glb": "model/gltf-binary", ".stl": "model/stl", ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg"}


class WorkerError(RuntimeError):
    """A failure whose message is already a plain sentence for the project's ``error`` field."""


def load_env_files() -> None:
    """Fill os.environ from pipeline/.env and web/.env.local (existing variables win; empty values are ignored)."""
    for f in (PIPELINE / ".env", ROOT / "web" / ".env.local"):
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v and not os.environ.get(k.strip()):
                os.environ[k.strip()] = v


def _env(*names: str) -> str | None:
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return None


# --- backend interface (real REST client and a fake for tests) ----------------------------------------------------------------------

class Backend(Protocol):
    user_id: str

    def claim_next(self) -> dict | None: ...
    def update(self, project_id: str, patch: dict) -> None: ...
    def download(self, bucket: str, path: str) -> bytes: ...
    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None: ...


class SupabaseRest:
    """Minimal Supabase client over REST (auth, PostgREST, Storage) signed in as one user with the publishable key; no extra dependency."""

    def __init__(self, url: str, publishable_key: str, email: str, password: str, session=None):
        import requests

        self.url, self.key, self.email, self.password = url.rstrip("/"), publishable_key, email, password
        self.http = session or requests.Session()
        self.token: str | None = None
        self.user_id = ""
        self.sign_in()

    def sign_in(self) -> None:
        r = self.http.post(f"{self.url}/auth/v1/token?grant_type=password", headers={"apikey": self.key, "Content-Type": "application/json"},
                           data=json.dumps({"email": self.email, "password": self.password}), timeout=30)
        if r.status_code != 200:
            raise WorkerError(f"Worker sign-in failed ({r.status_code}): check WORKER_EMAIL and WORKER_PASSWORD, and that the account exists and its email is confirmed.")
        j = r.json()
        self.token, self.user_id = j["access_token"], j["user"]["id"]

    def _h(self, extra: dict | None = None) -> dict:
        return {"apikey": self.key, "Authorization": f"Bearer {self.token}", **(extra or {})}

    def _req(self, method: str, url: str, **kw):
        r = self.http.request(method, url, headers=self._h(kw.pop("headers", None)), timeout=kw.pop("timeout", 60), **kw)
        if r.status_code == 401:  # token expired: sign in again once
            self.sign_in()
            r = self.http.request(method, url, headers=self._h(kw.get("headers")), timeout=60, **kw)
        return r

    def claim_next(self) -> dict | None:
        r = self._req("GET", f"{self.url}/rest/v1/projects", params={"status": "eq.uploaded", "order": "created_at.asc", "limit": "1", "select": "*"})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        p = rows[0]
        # claim: only succeeds while the row is still 'uploaded' (another worker may have taken it)
        c = self._req("PATCH", f"{self.url}/rest/v1/projects", params={"id": f"eq.{p['id']}", "status": "eq.uploaded"}, headers={"Prefer": "return=representation", "Content-Type": "application/json"},
                      data=json.dumps({"status": "processing", "error": None}))
        c.raise_for_status()
        got = c.json()
        return got[0] if got else None

    def update(self, project_id: str, patch: dict) -> None:
        r = self._req("PATCH", f"{self.url}/rest/v1/projects", params={"id": f"eq.{project_id}"}, headers={"Content-Type": "application/json"}, data=json.dumps(patch))
        r.raise_for_status()

    def download(self, bucket: str, path: str) -> bytes:
        r = self._req("GET", f"{self.url}/storage/v1/object/authenticated/{bucket}/{path}", timeout=600)
        if r.status_code != 200:
            raise WorkerError(f"Could not download {bucket}/{path} ({r.status_code}).")
        return r.content

    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        r = self._req("POST", f"{self.url}/storage/v1/object/{bucket}/{path}", headers={"Content-Type": content_type, "x-upsert": "true"}, data=data, timeout=600)
        if r.status_code not in (200, 201):
            raise WorkerError(f"Could not upload {bucket}/{path} ({r.status_code}): {r.text[:120]}")


# --- pipeline ------------------------------------------------------------------------------------------------------------------------

def run_pipeline(project: dict, video: Path, out_dir: Path, opts: argparse.Namespace) -> Path:
    """Run the same CLI a person would: worldkit.object (object) or worldkit.scene (map). Raises WorkerError with a plain sentence."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if project["mode"] == "map":
        cmd = [sys.executable, "-m", "worldkit.scene", str(video), "--out", str(out_dir), "--coin", opts.coin]
        report_name = "scene_report.json"
    else:
        cmd = [sys.executable, "-m", "worldkit.object", str(video), "--out", str(out_dir), "--object", opts.object, "--coin", opts.coin]
        report_name = "report.json"
    log = out_dir / "log.txt"
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, cwd=PIPELINE, stdout=fh, stderr=subprocess.STDOUT, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if proc.returncode != 0:
        tail = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-1:] or ["no output"]
        raise WorkerError(f"The pipeline stopped: {tail[0][:200]}")
    if not (out_dir / report_name).is_file():
        raise WorkerError(f"The pipeline finished without writing {report_name}.")
    return out_dir


def result_files(run_dir: Path) -> list[Path]:
    """Every file of a finished run that the viewer or a print needs (no frames, caches or previews)."""
    files = []
    for f in sorted(run_dir.rglob("*")):
        if not f.is_file() or any(part in SKIP_DIRS for part in f.relative_to(run_dir).parts):
            continue
        if f.suffix.lower() not in CONTENT_TYPES:
            continue
        if f.stat().st_size > MAX_RESULT_FILE_MB * 1_000_000:
            continue
        files.append(f)
    return files


def read_report(run_dir: Path, mode: str) -> dict:
    f = run_dir / ("scene_report.json" if mode == "map" else "report.json")
    if not f.is_file():
        f = run_dir / "report.json"
    rep = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    keep = {k: rep[k] for k in ("object_name", "measurement", "scale_calibration", "print_ready", "scale_source", "built", "skipped", "verify", "diorama", "timings_sec", "warnings") if k in rep}
    if "verify" in keep and isinstance(keep["verify"], dict):
        keep["verify"] = {k: keep["verify"].get(k) for k in ("mean_iou_before", "mean_iou_after", "camera_method_kept")}
    return keep


# --- the worker ------------------------------------------------------------------------------------------------------------------------

class Worker:
    def __init__(self, backend: Backend, opts: argparse.Namespace, workdir: Path, pipeline: Callable = run_pipeline, log: Callable[[str], None] = print):
        self.b, self.opts, self.workdir, self.pipeline, self.log = backend, opts, Path(workdir), pipeline, log

    def process(self, project: dict) -> str:
        """Process one CLAIMED project (status already 'processing'). Returns the final status."""
        pid, uid = project["id"], self.b.user_id
        base = f"{uid}/{pid}"
        try:
            if not project.get("video_path"):
                raise WorkerError("This project has no video to process.")
            work = self.workdir / pid
            work.mkdir(parents=True, exist_ok=True)
            video = work / ("video" + (Path(project["video_path"]).suffix or ".mp4"))
            if self.opts.dry_run:
                run_dir = DEMO[project["mode"]]
            else:
                video.write_bytes(self.b.download("videos", project["video_path"]))
                run_dir = self.pipeline(project, video, work / "run", self.opts)
            names = []
            for f in result_files(run_dir):
                rel = f.relative_to(run_dir).as_posix()
                self.b.upload("results", f"{base}/{rel}", f.read_bytes(), CONTENT_TYPES.get(f.suffix.lower()) or mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                names.append(rel)
            if not any(n.endswith(".glb") for n in names):
                raise WorkerError("The run produced no 3D model to show.")
            report = read_report(run_dir, project["mode"])
            report["files"] = names
            report["kind"] = project["mode"]
            if self.opts.dry_run:
                report["dry_run"] = "published a bundled demo run; the pipeline did not run"
            self.b.update(pid, {"status": "done", "result_path": base, "report": report, "error": None})
            self.log(f"{pid}: done ({len(names)} files)")
            return "done"
        except WorkerError as exc:
            msg = str(exc)
        except Exception as exc:  # noqa: BLE001 - whatever went wrong becomes one plain sentence on the project, the worker keeps running
            msg = f"The worker hit an unexpected error: {type(exc).__name__}: {str(exc)[:160]}"
        self.log(f"{pid}: failed: {msg}")
        try:
            self.b.update(pid, {"status": "failed", "error": msg})
        except Exception as exc:  # noqa: BLE001
            self.log(f"{pid}: could not record the failure: {exc}")
        return "failed"

    def run_once(self) -> str | None:
        p = self.b.claim_next()
        return self.process(p) if p else None

    def loop(self, poll: float, once: bool = False) -> None:
        while True:
            try:
                r = self.run_once()
            except Exception as exc:  # noqa: BLE001 - network blips must not stop the worker
                self.log(f"poll failed: {exc}")
                r = None
            if once:
                return
            if r is None:
                time.sleep(poll)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WorldKit laptop worker: process uploaded videos from Supabase")
    p.add_argument("--once", action="store_true", help="process at most one waiting project, then exit")
    p.add_argument("--dry-run", action="store_true", help="publish a bundled demo run instead of running the pipeline (no GPU, no API calls)")
    p.add_argument("--coin", default="saudi-25-halalas", help="scale reference coin (scale_cal.COINS)")
    p.add_argument("--object", default="mug", help="object-mode SAM concept (what to reconstruct)")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--workdir", type=Path, default=PIPELINE / "runs" / "web")
    return p.parse_args(argv)


def main(argv=None) -> int:
    load_env_files()
    a = parse_args(argv)
    url, key = _env("VITE_SUPABASE_URL", "SUPABASE_URL"), _env("VITE_SUPABASE_PUBLISHABLE_KEY", "SUPABASE_PUBLISHABLE_KEY")
    email, pw = _env("WORKER_EMAIL"), _env("WORKER_PASSWORD")
    missing = [n for n, v in (("VITE_SUPABASE_URL", url), ("VITE_SUPABASE_PUBLISHABLE_KEY", key), ("WORKER_EMAIL", email), ("WORKER_PASSWORD", pw)) if not v]
    if missing:
        print(f"Missing settings: {', '.join(missing)}. Put them in pipeline/.env (worker login) or web/.env.local (project URL and key).", file=sys.stderr)
        return 2
    try:
        backend = SupabaseRest(url, key, email, pw)
    except WorkerError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Signed in as the worker; waiting for uploads{' (dry run: no pipeline)' if a.dry_run else ''}. Ctrl+C to stop.")
    try:
        Worker(backend, a, a.workdir).loop(a.poll, once=a.once)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
