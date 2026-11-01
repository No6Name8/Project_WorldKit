"""Test server for map_scene_browser_test.js: the real FastAPI app (worldkit.serve) opened on a SCENE folder (the bundled demo scene plus per-object
STLs, a diorama STL and verify images) with a scripted Wonder/Spark.   python viewer/tests/serve_fake_scene.py <port>"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import trimesh  # noqa: E402
import uvicorn  # noqa: E402

from serve_fake import Wonder, spark  # noqa: E402
from tests.test_edit_tools import make_mug  # noqa: E402
from worldkit import serve  # noqa: E402

if __name__ == "__main__":
    d = Path(tempfile.mkdtemp(prefix="wk_map_ui_"))
    shutil.copytree(ROOT / "viewer" / "assets" / "demo" / "scene", d, dirs_exist_ok=True)
    m = json.loads((d / "scene.json").read_text())
    mug = make_mug()
    mug.apply_scale(0.1)
    for o in m["objects"]:
        if o["id"] == "mug":
            mug.export(d / "objects" / "mug" / "model_print.stl")
            o["stl"], o["printable"], o["size_cm"] = "objects/mug/model_print.stl", True, [8.0, 10.0, 8.0]
        else:
            o["printable"] = False
    (d / "verify").mkdir()
    shutil.copy(d / "layout_check.png", d / "verify" / "view0_a.png")
    trimesh.creation.box(extents=[60, 60, 20]).export(d / "diorama.stl")
    m["diorama"] = {"stl": "diorama.stl", "scale_text": "1:4.0", "size_mm": [60, 60, 20], "warnings": ["scaled to 1:4.0"]}
    m["verify"] = {"mean_iou_before": 0.61, "mean_iou_after": 0.7, "images": ["verify/view0_a.png"]}
    (d / "scene.json").write_text(json.dumps(m))
    uvicorn.run(serve.create_app(d, client=Wonder(), ask_spark=spark), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
