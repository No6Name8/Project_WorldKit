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
        text = msgs[1]["content"].lower()
        last_is_tool = msgs[-1]["role"] == "tool"
        calls = None
        if not last_is_tool:
            if "tall" in text:
                calls = [call("scale_to", {"height_cm": 12})]
            elif "text" in text:
                calls = [call("emboss_text", {"text": "WORLDKIT", "face": "base"})]
            elif "reject" in text:
                calls = [call("scale_to", {"factor": 1.1})]
        msg = NS(content=None if calls else ("Done." if last_is_tool else "No tool can do that."), tool_calls=calls)
        return NS(choices=[NS(message=msg, finish_reason="tool_calls" if calls else "stop")], usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


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
    uvicorn.run(serve.create_app(d, client=Wonder(), ask_spark=spark), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
