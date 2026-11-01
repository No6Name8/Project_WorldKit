"""Scale from any reference of known size seen in the video: a coin or a bank card (built-in presets) or any named object whose size
the user supplies (``--ref "striped mug=70mm"``). The user-supplied size is an explicit INPUT, exactly like a coin diameter; it is never
guessed and never taken from evaluation data.

Reliability order (the most reliable reference visible wins, and the choice is recorded): coin (3) > card (2) > user-given object (1).
- coin: a disk, so the ellipse major axis is the diameter whatever the viewing angle (``scale_cal.fit_disk``), and minor/major gives the
  camera elevation.
- card: a flat rectangle of known long side. APPROXIMATE: the long image side is assumed to run roughly across the view (not foreshortened);
  the short/long ratio then gives the elevation. Built in but not validated on real footage.
- object: the reconstructed mesh of that object is scaled so its upright height equals the given height. Because generated meshes can have
  wrong proportions, this is the least reliable source (the mug's generated height was ~10% too tall in the coin experiments).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

COINS = {  # diameter in cm, see scale_cal.COINS for sources
    "saudi-25-halalas": 2.15,
}
CARDS = {"credit-card": (8.56, 5.40)}  # ISO/IEC 7810 ID-1, cm (long, short)
RELIABILITY = {"coin": 3, "card": 2, "object": 1}


class RefError(ValueError):
    pass


@dataclass(frozen=True)
class RefSpec:
    kind: str            # coin | card | object
    name: str            # SAM concept used to find it ("coin", "credit card", "striped mug")
    size_cm: float       # coin diameter / card long side / object upright height
    short_cm: float | None = None
    preset: str | None = None

    @property
    def reliability(self) -> int:
        return RELIABILITY[self.kind]

    def describe(self, method: str | None = None) -> str:
        what = {"coin": "diameter", "card": "long side", "object": "height"}[self.kind]
        m = method or {"coin": "disk ellipse major axis (weak perspective)", "card": "rectangle long side (approximate)",
                       "object": "reconstructed mesh height set to the supplied value"}[self.kind]
        return f"scale source: {self.name}, {self.size_cm * 10:g} mm {what}, {m}"


_SIZE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(mm|cm|m)?\s*$", re.I)


def parse_size_cm(text: str) -> float:
    m = _SIZE.match(text)
    if not m:
        raise RefError(f"cannot read the size {text!r} (use e.g. 70mm, 7cm)")
    v = float(m.group(1))
    return v * {"mm": 0.1, "cm": 1.0, "m": 100.0, None: 0.1}[(m.group(2) or "").lower() or None]  # bare numbers are millimetres


def parse_ref(text: str) -> RefSpec:
    """'striped mug=70mm' -> object reference (height). Preset names: a coin or card preset, e.g. 'saudi-25-halalas' or 'credit-card'."""
    if "=" not in text:
        key = text.strip().lower()
        if key in COINS:
            return RefSpec("coin", "coin", COINS[key], preset=key)
        if key in CARDS:
            return RefSpec("card", "credit card", CARDS[key][0], CARDS[key][1], preset=key)
        raise RefError(f"--ref needs name=size (e.g. \"striped mug=70mm\") or a preset ({sorted(COINS)}, {sorted(CARDS)}), got {text!r}")
    name, size = text.rsplit("=", 1)
    name = name.strip()
    if not name:
        raise RefError("--ref name is empty")
    cm = parse_size_cm(size)
    if not 0.2 <= cm <= 300:
        raise RefError(f"--ref size {cm:g} cm is outside 2 mm .. 3 m")
    return RefSpec("object", name, cm)


def specs_from_args(coin: str | None, ref_diameter_cm: float | None, card: bool, refs: list[str] | None, ref_concept: str = "coin") -> list[RefSpec]:
    out: list[RefSpec] = []
    if coin:
        out.append(parse_ref(coin))
    elif ref_diameter_cm:
        out.append(RefSpec("coin", ref_concept, float(ref_diameter_cm)))
    if card:
        out.append(parse_ref("credit-card"))
    for r in refs or []:
        out.append(parse_ref(r))
    return sorted(out, key=lambda s: -s.reliability)


def name_matches(a: str, b: str) -> bool:
    ta = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if w not in {"the", "a", "an"}}
    tb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if w not in {"the", "a", "an"}}
    return bool(ta and tb and (ta <= tb or tb <= ta or len(ta & tb) / min(len(ta), len(tb)) >= 0.6))


@dataclass(frozen=True)
class FrameRef:
    """What one frame says about the desk plane: pixels per cm on it, camera elevation, and a pixel on the plane used as the world origin."""
    px_per_cm: float
    elevation_deg: float
    anchor_px: tuple


def card_observation(mask: np.ndarray, long_cm: float, short_cm: float) -> tuple[float, float]:
    """(px_per_cm, elevation_deg) from a card mask: long side = ``long_cm``; short/long image ratio gives sin(elevation) (see module note)."""
    import cv2

    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        raise RefError("empty card mask")
    (_, _), (w, h), _ = cv2.minAreaRect(max(cnts, key=cv2.contourArea))
    long_px, short_px = max(w, h), min(w, h)
    if long_px < 12:
        raise RefError("card mask too small")
    ppc = long_px / long_cm
    sin_el = np.clip((short_px / ppc) / short_cm, 0.0, 1.0)
    return float(ppc), float(np.degrees(np.arcsin(sin_el)))
