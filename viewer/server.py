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

app = create_app(VIEWER_DIR)

if __name__ == "__main__":
    print(f"Muse Spark key configured: {has_api_key()}")
    app.run(host="127.0.0.1", port=5050, debug=False)
