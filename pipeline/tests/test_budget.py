import json

import pytest

from worldkit import budget, segment
from worldkit.mapmode import cached_mask


def test_budget_caps_calls_and_counts(tmp_path, monkeypatch):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"caps": {"sam": 2}, "used": {}}))
    monkeypatch.setenv("WK_BUDGET_FILE", str(f))
    budget.take("sam", "a")
    budget.take("sam", "b")
    with pytest.raises(budget.BudgetExceeded):
        budget.take("sam", "c")
    d = json.loads(f.read_text())
    assert d["used"]["sam"] == 2 and d["refused"]["sam"] == 1


def test_no_budget_file_means_no_limit(monkeypatch):
    monkeypatch.delenv("WK_BUDGET_FILE", raising=False)
    for _ in range(5):
        budget.take("spark")
    assert budget.used("spark") == 0


def test_budget_refusal_is_not_cached_as_not_found(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"x")

    def refuse(path, concept):
        raise segment.SegmentationBudgetError("cap")

    assert cached_mask(tmp_path / "cache", frame, "mug", segment_fn=refuse) is None
    assert not list((tmp_path / "cache").glob("*.npz"))  # a later run with budget left must still ask SAM
