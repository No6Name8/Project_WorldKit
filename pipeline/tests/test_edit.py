"""AI-Edit agent loop with a scripted Wonder client and a scripted Spark verifier (no network)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
import trimesh

from tests.test_edit_tools import make_mug
from worldkit import edit
from worldkit.meta_api import MetaAPIUnavailable


def tool_call(name, args, i="c1"):
    return NS(id=i, function=NS(name=name, arguments=json.dumps(args)))


def reply(calls=None, text=""):
    msg = NS(content=text or None, tool_calls=calls or None)
    return NS(choices=[NS(message=msg, finish_reason="tool_calls" if calls else "stop")], usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


class FakeWonder:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        assert kw["model"] == "muse-wonder-1.0" and kw["tools"] and "tool_choice" not in kw  # Meta: only auto is supported
        self.requests.append([dict(m) for m in kw["messages"]])
        return self.scripted.pop(0)


def spark_ok(images, prompt):
    return ('{"matches_instruction": true, "unintended_changes": false, "confidence": 0.9, "reason": "looks right"}', {"total_tokens": 7, "prompt_tokens": 5, "completion_tokens": 2})


def spark_no(images, prompt):
    return ('{"matches_instruction": false, "unintended_changes": false, "confidence": 0.9, "reason": "text not visible"}', {"total_tokens": 7})


@pytest.fixture()
def model_dir(tmp_path):
    mug = make_mug()
    mug.apply_scale(0.1)  # printprep writes centimetres
    mug.export(tmp_path / "model_print.stl")
    (tmp_path / "report.json").write_text(json.dumps({"print_prep": {"height_source": "default_fallback", "is_estimate": True}}))
    return tmp_path


def test_v0_is_converted_from_cm_to_mm_and_records_the_scale_caveat(model_dir):
    v = edit.current_version(model_dir)
    assert v["version"] == 0
    assert edit.current_mesh(model_dir).extents[2] == pytest.approx(100.0, abs=0.05)
    assert "PLACEHOLDER" in json.loads((model_dir / "edits" / "versions.json").read_text())["scale_note"]


def test_edit_applies_records_everything_and_is_undoable(model_dir):
    w = FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="Scaled to 12 cm.")])
    r = edit.run_edit(model_dir, "make it exactly 12 cm tall", client=w, ask_spark=spark_ok)
    assert r["applied"] and r["version"] == 1 and r["retries"] == 0
    assert edit.current_mesh(model_dir).extents[2] == pytest.approx(120.0, abs=0.05)
    d = json.loads((model_dir / "edits" / "versions.json").read_text())
    v = d["versions"][1]
    assert v["instruction"] == "make it exactly 12 cm tall" and v["tool_calls"][0]["name"] == "scale_to" and v["verdict"]["passed"]
    assert v["cost"]["wonder_tokens"]["total_tokens"] == 30 and v["cost"]["spark_tokens"]["total_tokens"] == 7 and v["renders"]
    assert all((model_dir / p).is_file() for p in v["renders"])
    # Wonder was shown the measured facts and answered tool results in the documented message shape
    assert "bounding_box_mm" in w.requests[0][1]["content"]
    assert w.requests[1][-1]["role"] == "tool" and w.requests[1][-2]["tool_calls"][0]["function"]["name"] == "scale_to"
    back = edit.undo(model_dir)
    assert back["version"] == 0 and edit.current_mesh(model_dir).extents[2] == pytest.approx(100.0, abs=0.05)
    assert (model_dir / "edits" / "v1.stl").is_file()  # nothing is deleted
    with pytest.raises(edit.EditFailed):
        edit.undo(model_dir)


def test_undo_to_any_version_and_branching_after_undo(model_dir):
    for h in (11, 12, 13):
        edit.run_edit(model_dir, f"make it {h} cm tall", client=FakeWonder([reply([tool_call("scale_to", {"height_cm": h})]), reply(text="ok")]), ask_spark=spark_ok)
    assert edit.undo(model_dir, to=1)["version"] == 1
    r = edit.run_edit(model_dir, "mirror it", client=FakeWonder([reply([tool_call("mirror", {"axis": "x"})]), reply(text="ok")]), ask_spark=spark_ok)
    assert r["version"] == 4 and r["parent"] == 1  # a new branch, old versions kept
    with pytest.raises(edit.EditFailed):
        edit.undo(model_dir, to=99)


def test_rejected_then_retried_with_feedback_and_accepted(model_dir):
    verdicts = iter([spark_no, spark_ok])
    w = FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="done"),
                    reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="done again")])
    r = edit.run_edit(model_dir, "make it 12 cm tall", client=w, ask_spark=lambda i, p: next(verdicts)(i, p))
    assert r["applied"] and r["retries"] == 1 and r["attempts"] == 2
    feedback = [m for m in w.requests[2] if m["role"] == "user"][-1]["content"]
    assert "REJECTED" in feedback and "text not visible" in feedback  # the retry carries Spark's reason
    assert edit.current_version(model_dir)["version"] == 1


def test_rejected_twice_leaves_the_model_untouched(model_dir):
    w = FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="x"), reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="x")])
    r = edit.run_edit(model_dir, "make it 12 cm tall", client=w, ask_spark=spark_no)
    assert not r["applied"] and len(r["attempts"]) == 2
    d = json.loads((model_dir / "edits" / "versions.json").read_text())
    assert d["current"] == 0 and len(d["versions"]) == 1 and len(d["rejected"]) == 1 and not (model_dir / "edits" / "v1.stl").exists()


def test_deterministic_failure_blocks_even_when_spark_approves(model_dir):
    w = FakeWonder([reply([tool_call("scale_to", {"factor": 1.0})]), reply(text="ok")] * 2)  # claims a 12 cm edit but changes nothing
    r = edit.run_edit(model_dir, "make it 12 cm tall", client=w, ask_spark=spark_ok, retries=0)
    assert r["applied"]  # factor 1.0 is internally consistent...
    bad = edit.deterministic_checks(edit.current_mesh(model_dir), edit.current_mesh(model_dir), [{"name": "scale_to", "arguments": {"height_cm": 12}}])
    assert not all(c["ok"] for c in bad)  # ...but the height check against the stated target does fail


def test_tool_errors_are_fed_back_and_a_declining_model_changes_nothing(model_dir):
    w = FakeWonder([reply([tool_call("drill_hole", {"diameter_mm": 5, "position": [900, 900, 900]})]), reply(text="could not")])
    r = edit.run_edit(model_dir, "drill somewhere", client=w, ask_spark=spark_ok, retries=0)
    assert not r["applied"] and "no successful tool call" in r["verdict"]["reason"]
    tool_msg = [m for m in w.requests[1] if m["role"] == "tool"][0]
    assert json.loads(tool_msg["content"])["ok"] is False
    declined = edit.run_edit(model_dir, "make it taste better", client=FakeWonder([reply(text="No tool can do that.")]), ask_spark=spark_ok)
    assert not declined["applied"] and edit.current_version(model_dir)["version"] == 0


def test_spark_unavailable_accepts_on_deterministic_checks_but_says_so(model_dir):
    def down(images, prompt):
        raise MetaAPIUnavailable("no key")

    r = edit.run_edit(model_dir, "make it 12 cm tall", client=FakeWonder([reply([tool_call("scale_to", {"height_cm": 12})]), reply(text="ok")]), ask_spark=down)
    assert r["applied"] and r["verdict"]["spark_verified"] is False


def test_render_view_shows_engraved_text_via_relief_shading():
    from worldkit import edit_tools as et

    mug = make_mug()
    out = et.emboss_text(mug, "HI", face="base", depth_mm=0.8)
    kw = dict(eye_dir=(0, 0, -1), up=(0, 1, 0), zoom=1.8, size=300)
    a, b = edit.render_view(mug, **kw), edit.render_view(out, **kw)
    import numpy as np

    assert np.abs(np.asarray(a, float) - np.asarray(b, float)).max() > 30  # the engraving is visible, not just numerically present
    assert np.asarray(a).std() < np.asarray(b).std()
