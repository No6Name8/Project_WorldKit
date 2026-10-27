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


def create_app(viewer_dir: Path) -> Flask:
    viewer_dir = Path(viewer_dir)
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(viewer_dir, "index.html")

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        return send_from_directory(viewer_dir / "assets", filename)

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
