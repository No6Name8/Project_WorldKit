"""Tests for editcmd.py: JSON extraction/validation and the Muse Spark call wrapper.

No real Meta API calls -- ask_muse_spark_with_usage is monkeypatched for the
wrapper tests, so these exercise the actual parsing/bounds-checking logic
that stands between a model response and the live 3D scene.
"""

from __future__ import annotations

import pytest

from worldkit import editcmd
from worldkit.meta_api import MetaAPIUnavailable


def _ok_response(translate=(0, 0, 0), rotate=(0, 0, 0), scale=1.0, explanation="did the thing"):
    import json

    return json.dumps(
        {
            "translate_cm": {"x": translate[0], "y": translate[1], "z": translate[2]},
            "rotate_deg": {"x": rotate[0], "y": rotate[1], "z": rotate[2]},
            "scale_factor": scale,
            "explanation": explanation,
        }
    )


# --- _extract_json_object / parse_and_validate ---------------------------


def test_parse_and_validate_accepts_clean_json():
    raw = _ok_response(translate=(-10, 0, 0), explanation="moved left 10cm")

    result = editcmd.parse_and_validate(raw)

    assert result.translate_cm == (-10.0, 0.0, 0.0)
    assert result.rotate_deg == (0.0, 0.0, 0.0)
    assert result.scale_factor == 1.0
    assert result.explanation == "moved left 10cm"
    assert result.source == "muse_spark"


def test_parse_and_validate_strips_markdown_code_fences():
    raw = f"Sure, here you go:\n```json\n{_ok_response(scale=1.2)}\n```\nLet me know if that's right."

    result = editcmd.parse_and_validate(raw)

    assert result.scale_factor == 1.2


def test_parse_and_validate_strips_surrounding_prose():
    raw = f"The transform is: {_ok_response(rotate=(0, 30, 0))} -- hope that helps!"

    result = editcmd.parse_and_validate(raw)

    assert result.rotate_deg == (0.0, 30.0, 0.0)


def test_parse_and_validate_rejects_non_json_text():
    with pytest.raises(editcmd.EditCommandError, match="no JSON object found"):
        editcmd.parse_and_validate("I don't understand the instruction.")


def test_parse_and_validate_rejects_missing_required_field():
    import json

    raw = json.dumps({"translate_cm": {"x": 0, "y": 0, "z": 0}, "scale_factor": 1.0})  # missing rotate_deg

    with pytest.raises(editcmd.EditCommandError, match="missing required field"):
        editcmd.parse_and_validate(raw)


def test_parse_and_validate_rejects_non_numeric_field():
    import json

    raw = json.dumps(
        {
            "translate_cm": {"x": "ten", "y": 0, "z": 0},
            "rotate_deg": {"x": 0, "y": 0, "z": 0},
            "scale_factor": 1.0,
        }
    )

    with pytest.raises(editcmd.EditCommandError, match="must be a number"):
        editcmd.parse_and_validate(raw)


def test_parse_and_validate_rejects_out_of_range_translate():
    raw = _ok_response(translate=(99999, 0, 0))

    with pytest.raises(editcmd.EditCommandError, match="outside the allowed range"):
        editcmd.parse_and_validate(raw)


def test_parse_and_validate_rejects_out_of_range_scale():
    raw = _ok_response(scale=-5)

    with pytest.raises(editcmd.EditCommandError, match="outside the allowed range"):
        editcmd.parse_and_validate(raw)


def test_parse_and_validate_rejects_zero_scale():
    raw = _ok_response(scale=0)

    with pytest.raises(editcmd.EditCommandError):
        editcmd.parse_and_validate(raw)


def test_parse_and_validate_defaults_missing_axes_to_zero():
    import json

    raw = json.dumps({"translate_cm": {"x": 5}, "rotate_deg": {}, "scale_factor": 1.0})

    result = editcmd.parse_and_validate(raw)

    assert result.translate_cm == (5.0, 0.0, 0.0)
    assert result.rotate_deg == (0.0, 0.0, 0.0)


def test_parse_and_validate_rejects_boolean_as_number():
    import json

    raw = json.dumps(
        {"translate_cm": {"x": True, "y": 0, "z": 0}, "rotate_deg": {"x": 0, "y": 0, "z": 0}, "scale_factor": 1.0}
    )

    with pytest.raises(editcmd.EditCommandError, match="must be a number"):
        editcmd.parse_and_validate(raw)


# --- interpret_edit_instruction: wraps ask_muse_spark_with_usage ----------


def test_interpret_edit_instruction_rejects_empty_instruction():
    with pytest.raises(editcmd.EditCommandError, match="empty"):
        editcmd.interpret_edit_instruction("   ")


def test_interpret_edit_instruction_returns_validated_transform(monkeypatch: pytest.MonkeyPatch):
    def fake_ask(images, prompt, *, model, client=None):
        assert images == []
        assert "move the mug 10 cm to the left" in prompt.lower()
        return _ok_response(translate=(-10, 0, 0), explanation="moved left 10cm"), {"total_tokens": 42}

    monkeypatch.setattr(editcmd, "ask_muse_spark_with_usage", fake_ask)

    result = editcmd.interpret_edit_instruction("Move the mug 10 cm to the left.")

    assert result.translate_cm == (-10.0, 0.0, 0.0)
    assert result.source == "muse_spark"


def test_interpret_edit_instruction_raises_on_empty_model_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(editcmd, "ask_muse_spark_with_usage", lambda *a, **k: ("", None))

    with pytest.raises(editcmd.EditCommandError, match="empty response"):
        editcmd.interpret_edit_instruction("Rotate it 30 degrees.")


def test_interpret_edit_instruction_propagates_api_unavailable(monkeypatch: pytest.MonkeyPatch):
    def fake_ask(*a, **k):
        raise MetaAPIUnavailable("no key configured")

    monkeypatch.setattr(editcmd, "ask_muse_spark_with_usage", fake_ask)

    with pytest.raises(MetaAPIUnavailable):
        editcmd.interpret_edit_instruction("Make it 20% bigger.")


def test_interpret_edit_instruction_never_applies_malformed_response(monkeypatch: pytest.MonkeyPatch):
    # A real API failure mode: the model chats instead of returning JSON.
    monkeypatch.setattr(
        editcmd, "ask_muse_spark_with_usage",
        lambda *a, **k: ("Sure! I'll move it to the left for you.", None),
    )

    with pytest.raises(editcmd.EditCommandError):
        editcmd.interpret_edit_instruction("Move the mug 10 cm to the left.")
