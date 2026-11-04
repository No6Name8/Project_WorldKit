"""Muse Spark Open on Baseten as an opt-in verification backend (fake client; no network)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image

from worldkit import edit, spark_open
from worldkit.meta_api import MetaAPIUnavailable, ask_muse_spark_with_usage


class FakeCompletions:
    def __init__(self, text):
        self.text, self.calls = text, []

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))], usage=usage)


def fake_client(text):
    comp = FakeCompletions(text)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


def test_ask_spark_open_sends_model_images_and_token_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("WK_BUDGET_FILE", raising=False)
    img = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(img)
    client, comp = fake_client('{"matches_instruction": true, "unintended_changes": false, "confidence": 0.9, "reason": "ok"}')
    text, usage = spark_open.ask_spark_open_with_usage([img], "judge this", client=client)
    kw = comp.calls[0]
    assert kw["model"] == "meta-models/muse-spark-open-1026"
    assert kw["max_tokens"] == spark_open.MAX_TOKENS
    content = kw["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "judge this"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert '"matches_instruction": true' in text and usage["total_tokens"] == 15


def test_spark_open_verdict_feeds_edit_verify_unchanged(tmp_path, monkeypatch):
    import trimesh

    monkeypatch.delenv("WK_BUDGET_FILE", raising=False)
    client, _ = fake_client('thinking... {"matches_instruction": true, "unintended_changes": false, "confidence": 0.8, "reason": "hole visible"}')
    ask = lambda images, prompt: spark_open.ask_spark_open_with_usage(images, prompt, client=client)  # noqa: E731
    box = trimesh.creation.box((10, 10, 10))
    v, usage = edit.verify(box, box, "drill a hole", [{"name": "drill_hole", "arguments": {"diameter_mm": 5}}], [], ask=ask)
    assert v["available"] and v["matches_instruction"] and not v["unintended_changes"] and v["reason"] == "hole visible"


def test_missing_baseten_key_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.setattr(spark_open, "_env_path", lambda: tmp_path / "none.env")
    with pytest.raises(MetaAPIUnavailable):
        spark_open.load_baseten_key()


def test_cli_default_backend_is_meta_and_baseten_is_opt_in(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(edit, "run_edit", lambda d, ins, ask_spark=None: seen.append(ask_spark) or {"applied": False, "attempts": [{"tool_calls": []}], "verdict": {"reason": "x"}})
    edit.main([str(tmp_path), "make it 12 cm tall"])
    edit.main([str(tmp_path), "make it 12 cm tall", "--spark-backend", "baseten"])
    assert seen[0] is ask_muse_spark_with_usage
    assert seen[1] is spark_open.ask_spark_open_with_usage
