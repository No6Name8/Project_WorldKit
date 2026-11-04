"""Flask app for the WorldKit 3D viewer's backend.

Serves the static viewer (index.html + assets/) and exposes POST /api/edit,
which turns a natural-language instruction into a validated 3D transform via
Muse Spark (editcmd.py). This is the only place the Meta Model API key is
used -- it never reaches the browser.

Factored out of viewer/server.py (a thin launcher) so it can be imported and
exercised with Flask's test client in tests/test_webserver.py without
needing a real running server or a real Meta API call.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import editcmd
from .meta_api import MetaAPIError, MetaAPIUnavailable, has_api_key


def create_app(viewer_dir: Path, open_dir: Path | None = None) -> Flask:
    """``open_dir``: a pipeline run directory (``scene.json`` or ``model.glb`` + ``report.json``) the viewer loads on start."""
    viewer_dir = Path(viewer_dir)
    open_dir = Path(open_dir).resolve() if open_dir else None
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(viewer_dir, "index.html")

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        return send_from_directory(viewer_dir / "assets", filename)

    @app.get("/<name>.js")
    def viewer_module(name: str):
        # top-level viewer ES modules only (e.g. physics.js); no path traversal, no other file types
        if not name.replace("_", "").isalnum():
            return jsonify({"ok": False, "error": "not found"}), 404
        return send_from_directory(viewer_dir, f"{name}.js")

    @app.get("/vendor/<path:filename>")
    def vendor(filename: str):
        return send_from_directory(viewer_dir / "vendor", filename)

    @app.get("/open/<path:filename>")
    def open_run(filename: str):
        if open_dir is None:
            return jsonify({"ok": False, "error": "no run directory was opened"}), 404
        return send_from_directory(open_dir, filename)

    @app.get("/api/autoload")
    def autoload():
        """What the viewer should load on start: the opened run directory, else nothing (it falls back to the bundled sample)."""
        if open_dir is None or not open_dir.is_dir():
            return jsonify({"kind": None})
        if (open_dir / "scene.json").is_file():
            return jsonify({"kind": "scene", "base": "/open/", "scene": "scene.json", "name": open_dir.name, "source": (open_dir / "source" / "source.json").is_file()})
        if (open_dir / "model.glb").is_file():
            report = (open_dir / "report.json").is_file()
            return jsonify({"kind": "object", "glb": "/open/model.glb", "report": "/open/report.json" if report else None, "name": open_dir.name,
                            "stl": "/open/model_print.stl" if (open_dir / "model_print.stl").is_file() else None, "source": (open_dir / "source" / "source.json").is_file()})
        return jsonify({"kind": None, "error": f"{open_dir} has neither scene.json nor model.glb"})

    @app.get("/edit/state")
    def edit_state():
        """The AI Edit panel asks this; only `python -m worldkit.serve <model_dir>` enables it (answering 200 keeps the browser console clean)."""
        return jsonify({"enabled": False})

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "muse_spark_key_configured": has_api_key()})

    @app.post("/api/edit")
    def api_edit():
        body = request.get_json(silent=True) or {}
        instruction = body.get("instruction", "")

        try:
            transform = editcmd.interpret_edit_instruction(instruction)
        except MetaAPIUnavailable as exc:
            return jsonify({"ok": False, "error_type": "api_unavailable", "error": str(exc)}), 503
        except editcmd.EditCommandError as exc:
            return jsonify({"ok": False, "error_type": "invalid_response", "error": str(exc)}), 422
        except MetaAPIError as exc:
            return jsonify({"ok": False, "error_type": "api_error", "error": str(exc)}), 502

        return jsonify({"ok": True, "transform": transform.to_dict()})

    return app
