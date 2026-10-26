"""Use Muse Spark to name the main object and pick the best frame to rebuild it from.

Falls back to a local heuristic (sharpest frame, --object name or a generic
placeholder) when the Meta API is unavailable (no key, or billing/auth
rejection) -- never when the API is simply erroring for another reason.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from .frames import Frame
from .meta_api import MetaAPIUnavailable, ask_muse_spark_with_usage

_PROMPT = """You are looking at {n} candidate photos (index 0 to {n_minus_1}) \
of the same physical scene, taken moments apart from a phone video.

Identify the single main object a person filmed this video to capture (ignore \
background clutter, hands, and the floor/table unless the object IS the \
floor/table). Then pick the single best photo index to 3D-reconstruct that \
object from: it should show the object clearly, in focus, not cut off by the \
frame edge, and not blocked by a hand or another object.

Reply with ONLY a single JSON object, no other text, in exactly this shape:
{{"object_name": "<short name, e.g. 'coffee mug'>", "best_frame_index": <int>, \
"reason": "<one short sentence>"}}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class DetectionError(RuntimeError):
    pass


@dataclass
class Detection:
    object_name: str
    best_frame: Frame
    reason: str
    raw_response: str | None
    usage: dict | None = None
    used_fallback: bool = False


def _parse_json_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_RE.search(text)
    if not match:
        raise DetectionError(
            f"Muse Spark did not return parseable JSON. Raw response:\n{text}"
        )
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise DetectionError(
            f"Muse Spark returned malformed JSON: {exc}. Raw response:\n{text}"
        ) from exc


def _fallback_detect(frames: list[Frame], object_name_override: str | None) -> Detection:
    # frames is already sorted sharpest-first by frames.extract_sharp_frames.
    best = frames[0]
    name = object_name_override or "object (unnamed - Meta API unavailable, pass --object)"
    return Detection(
        object_name=name,
        best_frame=best,
        reason="FALLBACK: Meta API unavailable; picked the sharpest extracted frame instead of asking Muse Spark.",
        raw_response=None,
        usage=None,
        used_fallback=True,
    )


def detect_object(
    frames: list[Frame],
    *,
    object_name_override: str | None = None,
    client: OpenAI | None = None,
) -> Detection:
    """Ask Muse Spark to name the object and choose the best reconstruction frame.

    ``object_name_override`` replaces the detected name (e.g. the user passed
    ``--object "mug"``) but Muse Spark is still asked to pick the best frame,
    since that judgment doesn't depend on what we call the object -- unless
    the API is unavailable, in which case we fall back entirely (see
    _fallback_detect).
    """
    if not frames:
        raise DetectionError("no frames to detect from")

    prompt = _PROMPT.format(n=len(frames), n_minus_1=len(frames) - 1)
    try:
        raw, usage = ask_muse_spark_with_usage([f.path for f in frames], prompt, client=client)
    except MetaAPIUnavailable:
        return _fallback_detect(frames, object_name_override)

    parsed = _parse_json_response(raw)

    try:
        best_idx = int(parsed["best_frame_index"])
    except (KeyError, ValueError, TypeError) as exc:
        raise DetectionError(f"Muse Spark response missing valid best_frame_index: {parsed}") from exc
    if not 0 <= best_idx < len(frames):
        raise DetectionError(
            f"Muse Spark picked best_frame_index={best_idx}, out of range for {len(frames)} frames"
        )

    object_name = object_name_override or parsed.get("object_name") or "object"
    reason = parsed.get("reason", "")

    return Detection(
        object_name=object_name,
        best_frame=frames[best_idx],
        reason=reason,
        raw_response=raw,
        usage=usage,
        used_fallback=False,
    )
