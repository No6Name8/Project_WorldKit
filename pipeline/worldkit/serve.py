"""Local FastAPI server for the viewer with AI Edit:  python -m worldkit.serve <model_dir> [--port 5060]

Serves the viewer (same static files as viewer/server.py), opens ``model_dir`` (a worldkit.object run directory with
model_print.stl) and exposes the edit session:

    GET  /edit/state          versions, current version, scale caveat
    POST /edit  {instruction} run one AI edit (Wonder tool calling + Spark verification); returns the verdict
    POST /undo  {to?}         step back one version, or to version ``to``
    GET  /edit/model.glb      the CURRENT version as a GLB for the viewer (gray print mesh; Y-up, normalised like the pipeline GLBs)
    GET  /edit/current.stl    the current version as a millimetre STL (download)
    GET  /edit/render/<file>  before/after verification renders of the recorded versions

One edit runs at a time (a second request gets HTTP 409). The Meta API key stays on this machine; the browser never sees it.
"""

from __future__ import annotations

import argparse
import io
import re
import threading
from pathlib import Path

import numpy as np
import trimesh
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from . import edit as edit_mod
from . import editcmd
from .meta_api import MetaAPIError, MetaAPIUnavailable, has_api_key

VIEWER_DIR = Path(__file__).resolve().parents[2] / "viewer"


def stl_to_glb(mesh_mm: trimesh.Trimesh) -> bytes:
    """Viewer GLB from a Z-up mm print mesh: Y-up, centred, longest side 2 units (like appearance.finalize), plain gray dielectric."""
    m = mesh_mm.copy()
    m.vertices = np.stack([m.vertices[:, 0], m.vertices[:, 2], -m.vertices[:, 1]], axis=1)
    m.apply_translation(-(m.bounds[0] + m.bounds[1]) / 2)
    m.apply_scale(2.0 / float(m.extents.max()))
    m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(baseColorFactor=[196, 202, 214, 255], metallicFactor=0.0, roughnessFactor=0.55))
    _ = m.vertex_normals
    return trimesh.Scene(m).export(file_type="glb")


def create_app(model_dir: Path, *, viewer_dir: Path = VIEWER_DIR, client=None, ask_spark=None) -> FastAPI:
    model_dir = Path(model_dir).resolve()
    app = FastAPI(title="WorldKit viewer + AI Edit")
    lock = threading.Lock()
    kw = {}
    if ask_spark is not None:
        kw["ask_spark"] = ask_spark

    def state() -> dict:
        d = edit_mod._load(model_dir)
        return {"enabled": True, "model": model_dir.name, "units": d["units"], "scale_note": d.get("scale_note"), "current": d["current"],
                "versions": [{"version": v["version"], "parent": v["parent"], "instruction": v.get("instruction"), "size_mm": v.get("size_mm"),
                              "reason": (v.get("verdict") or {}).get("reason"), "seconds": v.get("seconds")} for v in d["versions"]],
                "rejected": len(d.get("rejected", [])), "busy": lock.locked()}

    @app.get("/")
    def index():
        return FileResponse(viewer_dir / "index.html")

    @app.get("/assets/{path:path}")
    def assets(path: str):
        return _safe_file(viewer_dir / "assets", path)

    @app.get("/vendor/{path:path}")
    def vendor(path: str):
        return _safe_file(viewer_dir / "vendor", path)

    @app.get("/{name}.js")
    def module(name: str):
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise HTTPException(404)
        return _safe_file(viewer_dir, f"{name}.js")

    @app.get("/open/{path:path}")
    def open_run(path: str):
        return _safe_file(model_dir, path)

    @app.get("/api/health")
    def health():
        return {"ok": True, "muse_spark_key_configured": has_api_key()}

    @app.get("/api/autoload")
    def autoload():
        if (model_dir / "model.glb").is_file():
            return {"kind": "object", "glb": "/open/model.glb", "report": "/open/report.json" if (model_dir / "report.json").is_file() else None, "name": model_dir.name}
        return {"kind": None}

    @app.post("/api/edit")
    def api_edit(body: dict = Body(default={})):
        """The viewer's existing transform-by-chat box (Muse Spark -> move/rotate/scale of the displayed object)."""
        try:
            return {"ok": True, "transform": editcmd.interpret_edit_instruction(body.get("instruction", "")).to_dict()}
        except MetaAPIUnavailable as exc:
            return JSONResponse({"ok": False, "error_type": "api_unavailable", "error": str(exc)}, status_code=503)
        except editcmd.EditCommandError as exc:
            return JSONResponse({"ok": False, "error_type": "invalid_response", "error": str(exc)}, status_code=422)
        except MetaAPIError as exc:
            return JSONResponse({"ok": False, "error_type": "api_error", "error": str(exc)}, status_code=502)

    @app.get("/edit/state")
    def edit_state():
        try:
            return state()
        except edit_mod.EditFailed as exc:
            return {"enabled": False, "error": str(exc)}

    @app.post("/edit")
    def do_edit(body: dict = Body(...)):
        instruction = (body.get("instruction") or "").strip()
        if not instruction or len(instruction) > 400:
            raise HTTPException(422, "instruction must be 1-400 characters")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "another edit is still running")
        try:
            r = edit_mod.run_edit(model_dir, instruction, client=client, **kw)
        except (edit_mod.EditFailed, MetaAPIError) as exc:
            return JSONResponse({"ok": False, "applied": False, "error": str(exc)}, status_code=502 if isinstance(exc, MetaAPIError) else 400)
        finally:
            lock.release()
        attempts = r.get("attempts")
        last = (attempts[-1] if isinstance(attempts, list) and attempts else r)
        calls = r.get("tool_calls") or last.get("tool_calls") or []
        images = r.get("renders") or last.get("images") or []
        spark = r.get("spark") or last.get("spark") or {}
        return {"ok": True, "applied": bool(r["applied"]), "version": r.get("version"), "reason": r["verdict"]["reason"], "spark": spark.get("reason"),
                "spark_verified": r["verdict"].get("spark_verified"), "tools": [{"name": c["name"], "arguments": c["arguments"], "error": c.get("error")} for c in calls],
                "renders": [Path(p).name for p in images], "seconds": r.get("seconds"), "retries": r.get("retries", len(attempts) - 1 if isinstance(attempts, list) else 0),
                "tokens": r.get("cost", {}), "state": state()}

    @app.post("/undo")
    def do_undo(body: dict = Body(default={})):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "an edit is still running")
        try:
            v = edit_mod.undo(model_dir, body.get("to"))
        except edit_mod.EditFailed as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            lock.release()
        return {"ok": True, "version": v["version"], "state": state()}

    @app.get("/edit/model.glb")
    def edit_glb():
        return Response(stl_to_glb(edit_mod.current_mesh(model_dir)), media_type="model/gltf-binary", headers={"Cache-Control": "no-store"})

    @app.get("/edit/current.stl")
    def edit_stl():
        v = edit_mod.current_version(model_dir)
        return FileResponse(model_dir / v["stl"], media_type="model/stl", filename=f"{model_dir.name}_v{v['version']}_mm.stl")

    @app.get("/edit/render/{name}")
    def edit_render(name: str):
        if not re.fullmatch(r"[A-Za-z0-9_\-]+\.png", name):
            raise HTTPException(404)
        return _safe_file(model_dir / "edits", name)

    return app


def _safe_file(root: Path, rel: str) -> FileResponse:
    root = root.resolve()
    p = (root / rel).resolve()
    if root not in p.parents and p != root or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


def main(argv=None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="WorldKit viewer + AI Edit server (FastAPI)")
    ap.add_argument("model_dir", type=Path, help="a worldkit.object run directory (needs model_print.stl for editing; model.glb to display)")
    ap.add_argument("--port", type=int, default=5060)
    a = ap.parse_args(argv)
    if not (a.model_dir / "model_print.stl").is_file():
        raise SystemExit(f"{a.model_dir} has no model_print.stl (run worldkit.object first)")
    print(f"Muse Spark / Wonder key configured: {has_api_key()}\nViewer + AI Edit: http://127.0.0.1:{a.port}/   (model: {a.model_dir})")
    uvicorn.run(create_app(a.model_dir), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
