"""Structured object understanding from selected frames, by Muse Spark (Meta Model API; no new service).

The model's answer is machine-readable observations with confidences and frame references, not prose. It is used to
*decide* things (see ``fidelity.decide_plan``): whether a body-of-revolution texture model applies, whether a rim
constraint is permitted, which stripe count/region to cross-check the measured pattern against. It is never accepted as
proof: every use is followed by a deterministic measurement, and disagreements are reported with the measurement winning.
Responses are cached by content hash so a rerun costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .meta_api import MetaAPIError, MetaAPIUnavailable, ask_muse_spark_with_usage

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "understand"

PROMPT = """You are analysing {n} video frames of ONE object (frame indices 0..{last}) for a 3D reconstruction system.
Report only what the frames show. If you cannot tell, say "unclear" or null and give a low confidence. Do not guess hidden sides.
Return ONLY one JSON object with exactly these keys:
{{
 "object_category": "short noun, e.g. mug",
 "category_confidence": 0.0-1.0,
 "body_of_revolution": true|false|null,   // is the main body essentially rotationally symmetric (cup, mug, bottle, vase)?
 "body_of_revolution_confidence": 0.0-1.0,
 "has_handle": true|false|null,
 "handle_shape": "loop|ring|bar|none|unclear",
 "rim": {{"visible": true|false, "shape": "circular|oval|irregular|unclear", "level": "level|tilted|unclear", "confidence": 0.0-1.0}},
 "pattern": {{
   "present": true|false|null,
   "type": "vertical stripes|horizontal stripes|dots|checks|none|other|unclear",
   "stripe_shape": "wavy|straight|zigzag|unclear",
   "colors": ["main body colour", "pattern colour"],
   "visible_stripe_count_per_frame": [integer or null for each frame, in order],
   "estimated_total_stripes_around": integer or null,
   "region_top_fraction_of_body_height": 0.0-1.0 or null,   // 0 = base, 1 = rim: where the pattern ends at the top
   "region_bottom_fraction_of_body_height": 0.0-1.0 or null,
   "confidence": 0.0-1.0
 }},
 "material": "short phrase",
 "unobserved_regions": ["regions no frame shows"],
 "frame_notes": [{{"frame": index, "handle_visible": true|false|null, "interior_visible": true|false|null}}]
}}"""


class UnderstandingError(RuntimeError):
    pass


@dataclass
class Understanding:
    data: dict
    frames: list[str]
    from_cache: bool = False
    usage: dict | None = None
    used_fallback: bool = False
    notes: list[str] = field(default_factory=list)

    # --- typed accessors used by the planner (all tolerate missing/low-confidence fields) ---
    def conf(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.data.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def category(self) -> str:
        return str(self.data.get("object_category") or "").lower()

    @property
    def pattern(self) -> dict:
        return self.data.get("pattern") or {}

    def to_dict(self) -> dict:
        return {"observations": self.data, "frames": self.frames, "from_cache": self.from_cache, "usage": self.usage,
                "used_fallback": self.used_fallback, "notes": self.notes}


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise UnderstandingError(f"Muse Spark returned no JSON object: {text[:200]}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise UnderstandingError(f"Muse Spark returned malformed JSON: {exc}") from exc


_REQUIRED = ("object_category", "body_of_revolution", "pattern")


def validate(data: dict) -> list[str]:
    """Problems that make the answer unusable for decisions (empty list = usable)."""
    problems = [f"missing key {k}" for k in _REQUIRED if k not in data]
    pat = data.get("pattern")
    if pat is not None and not isinstance(pat, dict):
        problems.append("pattern is not an object")
    return problems


def _cache_key(images: list[Path]) -> str:
    h = hashlib.sha256(PROMPT.encode())
    for p in images:
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def analyse_frames(images: list[Path], *, use_cache: bool = True, ask=ask_muse_spark_with_usage) -> Understanding:
    """One Muse Spark call over ``images`` (object crops are best). Falls back to an EMPTY understanding when the API is
    unavailable, which makes the planner choose conservative defaults instead of acting on invented observations."""
    names = [Path(p).name for p in images]
    key = _cache_key(images)
    cache_file = CACHE_DIR / f"{key}.json"
    if use_cache and cache_file.is_file():
        d = json.loads(cache_file.read_text(encoding="utf-8"))
        return Understanding(d["data"], names, from_cache=True, usage=d.get("usage"))
    try:
        raw, usage = ask(list(images), PROMPT.format(n=len(images), last=len(images) - 1))
    except MetaAPIUnavailable as exc:
        return Understanding({}, names, used_fallback=True, notes=[f"Muse Spark unavailable: {exc}"])
    except MetaAPIError as exc:
        raise UnderstandingError(str(exc)) from exc
    data = _parse(raw)
    problems = validate(data)
    if problems:
        raise UnderstandingError("Muse Spark answer unusable: " + "; ".join(problems))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"data": data, "usage": usage, "frames": names}, indent=1), encoding="utf-8")
    return Understanding(data, names, usage=usage)
