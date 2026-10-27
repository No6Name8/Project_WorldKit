"""Interpret a natural-language edit instruction into a structured 3D transform.

Used by the viewer's "type an instruction" box (viewer/server.py calls this).
Muse Spark (the Meta Model API's vision/reasoning model -- same one already
used for object detection in detect.py) is asked to return a strict JSON
delta describing ONE edit: a translation, a rotation, and/or a uniform scale
factor, relative to the object's current transform. The response is never
applied blindly -- ``parse_and_validate`` rejects anything that isn't
well-formed JSON within sane bounds before it ever reaches the 3D scene.

Units/coordinate system (must match the viewer's documented convention):
  - translate_cm: object-space X/Y/Z translation in centimeters.
  - rotate_deg: additional rotation in degrees about X/Y/Z (applied as
    Euler angles, order XYZ, on top of the current orientation).
  - scale_factor: a multiplier on the CURRENT scale (1.0 = no change,
    1.2 = 20% bigger, 0.5 = half size). Never an absolute scale.

Whether "cm" is a real-world centimeter depends entirely on whether the
loaded model's scale was calibrated (see viewer/index.html's SCALE banner
and CLAUDE.md) -- this module only guarantees the NUMBER is what the user
asked for, not that it corresponds to a physically measured centimeter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .meta_api import MetaAPIError, MetaAPIUnavailable, ask_muse_spark_with_usage

EDIT_MODEL = "muse-spark-1.3"  # same verified-working model as detect.py; there is no separate "Muse Wonder" access on this account

MAX_TRANSLATE_CM = 200.0  # sanity bound: reject a response asking to move the object >2m
MAX_ROTATE_DEG = 3600.0  # 10 full turns; anything past this is almost certainly a malformed response
MIN_SCALE_FACTOR = 0.01
MAX_SCALE_FACTOR = 100.0

_SYSTEM_PROMPT = """You control a 3D object transform editor. Given one natural-language edit \
instruction, respond with ONLY a single JSON object (no prose, no markdown fences) with this exact shape:

{"translate_cm": {"x": <number>, "y": <number>, "z": <number>}, \
"rotate_deg": {"x": <number>, "y": <number>, "z": <number>}, \
"scale_factor": <number>, "explanation": "<short plain-English summary of what you did>"}

Rules:
- translate_cm is a RELATIVE move in centimeters in the object's local X/Y/Z axes (X=right, Y=forward, Z=up). Use 0 for axes not mentioned.
- rotate_deg is a RELATIVE rotation in degrees about X/Y/Z, applied on top of the current orientation. Use 0 for axes not mentioned. A top-down "rotate it" instruction with no axis means rotate about Z.
- scale_factor is a MULTIPLIER on the current size: 1.0 means unchanged. "20% bigger" -> 1.2. "half size" -> 0.5. Use 1.0 if the instruction does not mention size.
- "left"/"right" means -X/+X, "forward"/"back" means +Y/-Y, "up"/"down" means +Z/-Z, unless the instruction says otherwise.
- If the instruction is ambiguous, nonsensical, or not an edit at all, still return valid JSON with all deltas at their no-op defaults (0, 0, 1.0) and explain why in "explanation".
- Never include any text outside the single JSON object."""


class EditCommandError(RuntimeError):
    pass


@dataclass
class EditTransform:
    translate_cm: tuple[float, float, float]
    rotate_deg: tuple[float, float, float]
    scale_factor: float
    explanation: str
    source: str  # "muse_spark" | "manual"

    def to_dict(self) -> dict:
        return {
            "translate_cm": list(self.translate_cm),
            "rotate_deg": list(self.rotate_deg),
            "scale_factor": self.scale_factor,
            "explanation": self.explanation,
            "source": self.source,
        }


def _extract_json_object(text: str) -> dict:
    """Pull the first {...} JSON object out of a model response, tolerating stray prose/fences."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence_match.group(1) if fence_match else text
    brace_match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not brace_match:
        raise EditCommandError(f"no JSON object found in model response: {text[:200]!r}")
    try:
        return json.loads(brace_match.group(0))
    except json.JSONDecodeError as exc:
        raise EditCommandError(f"model response was not valid JSON: {exc}; raw: {text[:200]!r}") from exc


def _validated_number(value, *, lo: float, hi: float, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EditCommandError(f"{field} must be a number, got {value!r}")
    value = float(value)
    if not (lo <= value <= hi):
        raise EditCommandError(f"{field}={value} is outside the allowed range [{lo}, {hi}]")
    return value


def parse_and_validate(raw_text: str, *, source: str = "muse_spark") -> EditTransform:
    """Parse a model response (or hand-built dict-as-JSON string) into a bounds-checked EditTransform.

    Raises EditCommandError on anything malformed or out of sane bounds --
    callers must never apply an un-validated transform to the live scene.
    """
    obj = _extract_json_object(raw_text)

    for required in ("translate_cm", "rotate_deg", "scale_factor"):
        if required not in obj:
            raise EditCommandError(f"response missing required field {required!r}: {obj!r}")

    t = obj["translate_cm"]
    r = obj["rotate_deg"]
    if not isinstance(t, dict) or not isinstance(r, dict):
        raise EditCommandError("translate_cm and rotate_deg must both be {x,y,z} objects")

    translate = tuple(
        _validated_number(t.get(axis, 0), lo=-MAX_TRANSLATE_CM, hi=MAX_TRANSLATE_CM, field=f"translate_cm.{axis}")
        for axis in ("x", "y", "z")
    )
    rotate = tuple(
        _validated_number(r.get(axis, 0), lo=-MAX_ROTATE_DEG, hi=MAX_ROTATE_DEG, field=f"rotate_deg.{axis}")
        for axis in ("x", "y", "z")
    )
    scale_factor = _validated_number(
        obj["scale_factor"], lo=MIN_SCALE_FACTOR, hi=MAX_SCALE_FACTOR, field="scale_factor"
    )
    explanation = obj.get("explanation")
    if not isinstance(explanation, str):
        explanation = ""

    return EditTransform(
        translate_cm=translate, rotate_deg=rotate, scale_factor=scale_factor,
        explanation=explanation.strip() or "(no explanation given)", source=source,
    )


def interpret_edit_instruction(instruction: str, *, client=None) -> EditTransform:
    """Ask Muse Spark to turn a natural-language instruction into a validated EditTransform.

    Raises MetaAPIUnavailable (no key / billing-or-auth rejection -- callers
    may fall back to manual editing) or EditCommandError (reachable API, but
    a malformed/out-of-range response -- never silently applied) or
    MetaAPIError (any other API failure). Never returns a fabricated
    transform: every path either returns a real, validated model response
    or raises.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise EditCommandError("instruction is empty")

    prompt = f"{_SYSTEM_PROMPT}\n\nInstruction: {instruction}"
    text, _usage = ask_muse_spark_with_usage([], prompt, model=EDIT_MODEL, client=client)
    if not text.strip():
        raise EditCommandError("Muse Spark returned an empty response")

    return parse_and_validate(text, source="muse_spark")
