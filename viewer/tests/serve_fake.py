"""Test server for ai_edit_browser_test.js: the real FastAPI app (worldkit.serve) with a SCRIPTED Wonder and Spark so the UI can be
tested deterministically without network or tokens.   python viewer/tests/serve_fake.py <port>"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))

import uvicorn  # noqa: E402

from tests.test_edit_tools import make_mug  # noqa: E402
from worldkit import serve  # noqa: E402


def call(name, args):
    return NS(id="c1", function=NS(name=name, arguments=json.dumps(args)))


class Wonder:
    """Reads the instruction from the first user message and answers with the matching tool call (a stand-in for Muse Wonder)."""

    def __init__(self):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        msgs = kw["messages"]
        text = msgs[1]["content"].lower().split("measured model facts")[0]  # the instruction only (the facts JSON also mentions "handle")
        last_is_tool = msgs[-1]["role"] == "tool"
        calls = None
        if not last_is_tool:
            if "tall" in text:
                calls = [call("scale_to", {"height_cm": 12})]
            elif "text" in text:
                calls = [call("emboss_text", {"text": "WORLDKIT", "face": "base"})]
            elif "handle" in text:
                calls = [call("thicken_part", {"part": "handle", "by_mm": 2})]
            elif "reject" in text:
                calls = [call("scale_to", {"factor": 1.1})]
        msg = NS(content=None if calls else ("Done." if last_is_tool else "No tool can do that."), tool_calls=calls)
        return NS(choices=[NS(message=msg, finish_reason="tool_calls" if calls else "stop")], usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


def write_parts(d, mug):
    """A 'handle' part for the synthetic mug (7 of 8 views), with the same quantised GLB positions the viewer matches on."""
    import base64

    import numpy as np
    from worldkit import edit_tools as et

    info = et.handle_info(mug)
    ctr = np.asarray(info["bar_center_point_mm"])
    sel = np.linalg.norm(mug.vertices - ctr, axis=1) < max(info["bar_thickness_along_normal_mm"], 6.0)
    # the viewer GLB is serve.stl_to_glb(make_mug()) (unscaled mug): same linear map, then keys = round(x * 1e4)
    m = make_mug()
    v = np.stack([m.vertices[:, 0], m.vertices[:, 2], -m.vertices[:, 1]], axis=1)
    v -= (v.min(0) + v.max(0)) / 2
    v *= 2.0 / float((m.bounds[1] - m.bounds[0]).max())
    keys = np.round(v[sel].astype(np.float32).astype(np.float64) * 1e4).astype(np.int32)
    b64 = lambda a: base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
    (d / "parts").mkdir(exist_ok=True)
    (d / "parts" / "parts.json").write_text(json.dumps({"object": "mug", "parts": [{"id": "handle", "name": "handle", "views_total": 8, "views_found": 7, "vertex_count": int(sel.sum()),
                                                                                    "glb_keys_b64": b64(keys), "stl_points_b64": b64(mug.vertices[sel].astype(np.float32))}]}))


def spark(images, prompt):
    ok = "reject" not in prompt.lower()
    return json.dumps({"matches_instruction": ok, "unintended_changes": False, "confidence": 0.9, "reason": "looks right" if ok else "does not match"}), {"total_tokens": 3}


if __name__ == "__main__":
    d = Path(tempfile.mkdtemp(prefix="wk_edit_ui_"))
    mug = make_mug()
    mug.apply_scale(0.1)
    mug.export(d / "model_print.stl")
    (d / "report.json").write_text(json.dumps({"print_prep": {"height_source": "default_fallback", "is_estimate": True}}))
    (d / "model.glb").write_bytes(serve.stl_to_glb(make_mug()))
    write_parts(d, mug)
    uvicorn.run(serve.create_app(d, client=Wonder(), ask_spark=spark), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
