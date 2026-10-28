"""Launcher for the WorldKit 3D viewer's local dev server.

The actual Flask app lives in worldkit.webserver (testable with Flask's
test client); this script just wires it to this directory and runs it.

Run:
    pipeline\\.venv\\Scripts\\python.exe viewer\\server.py
Then open http://127.0.0.1:5050/
"""

from __future__ import annotations

import sys
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(VIEWER_DIR.parent / "pipeline"))

from worldkit.meta_api import has_api_key  # noqa: E402
from worldkit.webserver import create_app  # noqa: E402

import argparse  # noqa: E402


def build_app(open_dir=None):
    return create_app(VIEWER_DIR, open_dir=open_dir)


app = build_app()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="WorldKit viewer")
    ap.add_argument("--open", dest="open_dir", default=None, help="a pipeline run directory (scene.json or model.glb + report.json) to load on start")
    ap.add_argument("--port", type=int, default=5050)
    args = ap.parse_args()
    if args.open_dir and not Path(args.open_dir).is_dir():
        raise SystemExit(f"--open: not a directory: {args.open_dir}")
    print(f"Muse Spark key configured: {has_api_key()}  (natural-language edits need it; everything else works without)")
    print(f"Viewer: http://127.0.0.1:{args.port}/" + (f"   loading {args.open_dir}" if args.open_dir else ""))
    build_app(args.open_dir).run(host="127.0.0.1", port=args.port, debug=False)
