"""Edit server on a COPY of the tracked demo run (the real coin-scaled mug with its real SAM handle part) with a SCRIPTED Wonder and Spark, so
UI screenshots show real geometry edits without spending tokens.   python viewer/tests/serve_fake_real.py <port>"""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from serve_fake import call, spark  # noqa: E402
from worldkit import serve  # noqa: E402


class Wonder:
    def __init__(self):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        msgs = kw["messages"]
        text = msgs[1]["content"].lower().split("measured model facts")[0]
        last_is_tool = msgs[-1]["role"] == "tool"
        calls = None
        if not last_is_tool:
            if "handle" in text and "thinner" not in text:
                calls = [call("thicken_part", {"part": "handle", "by_mm": 2})]
            elif "tall" in text:
                calls = [call("scale_to", {"height_cm": 7.5})]
        msg = NS(content=None if calls else ("Done." if last_is_tool else "I can't make the handle thinner because the available tools can only thicken a named part, not reduce its thickness."), tool_calls=calls)
        return NS(choices=[NS(message=msg, finish_reason="tool_calls" if calls else "stop")], usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


if __name__ == "__main__":
    d = Path(tempfile.mkdtemp(prefix="wk_real_ui_"))
    src = ROOT / "viewer" / "assets" / "demo" / "run_mug"
    for f in ("model.glb", "model_print.stl", "report.json"):
        shutil.copy(src / f, d / f)
    shutil.copytree(src / "parts", d / "parts")
    uvicorn.run(serve.create_app(d, client=Wonder(), ask_spark=spark), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
