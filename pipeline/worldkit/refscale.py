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
        if self.kind == "card":
            return f"scale source: card ({self.size_cm * 10:g} x {self.short_cm * 10:g} mm), {method or 'corner fit, weak perspective'}"
        what = {"coin": "diameter", "card": "long side", "object": "height"}[self.kind]
        m = method or {"coin": "disk ellipse major axis (weak perspective)", "card": "corner fit, weak perspective",
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
            return RefSpec("card", "bank card", CARDS[key][0], CARDS[key][1], preset=key)
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


def card_reference_elevation(masks, long_cm: float, short_cm: float) -> float | None:
    """Median elevation of the card frames whose outline is NOT ambiguous (same video, same hand height): used to resolve the others."""
    els = []
    for m in masks:
        q = card_quad(m)
        if q is None:
            continue
        try:
            _, el, amb = card_from_quad(q, long_cm, short_cm)
        except RefError:
            continue
        if not amb:
            els.append(el)
    return float(np.median(els)) if els else None


def card_quad(mask: np.ndarray) -> np.ndarray | None:
    """The card mask's four corners (4x2, image px) or None when the outline is not a clean quadrilateral."""
    import cv2

    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = cv2.convexHull(max(cnts, key=cv2.contourArea))
    peri = cv2.arcLength(c, True)
    for eps in (0.02, 0.03, 0.04, 0.06):
        q = cv2.approxPolyDP(c, eps * peri, True)
        if len(q) == 4:
            return q.reshape(4, 2).astype(float)
    return None


CARD_AMBIGUOUS_RATIO = 3.0  # the second branch fits within this factor of the best: the outline alone cannot tell which side is long
CARD_AXIS_TERM = 0.28


def card_branches(quad: np.ndarray, long_cm: float, short_cm: float) -> list[tuple[float, float, float]]:
    """The physically possible readings (px_per_cm, elevation_deg, residual, |sin a cos a|) of a flat card under weak perspective with no camera roll, best first.

    A card edge of length L at azimuth a projects to (k L cos a, k L sin a sin el); the perpendicular edge of length S to
    (-k S sin a, k S cos a sin el). The horizontal parts are not foreshortened, so k = hypot(ux/L, vx/S) and k sin el = hypot(uy/L, vy/S)
    whatever the card's rotation. Which image edge pair is the long side is tested with uy*vy = -ux*vx*sin^2(el) (sign-safe). When the
    card's edges are close to the image axes both assignments satisfy it: a wide card seen from above looks like a card pointing away seen
    from low down. ``card_observation`` resolves that with the elevation of the other card frames."""
    q = np.asarray(quad, float)
    e0 = ((q[1] - q[0]) + (q[2] - q[3])) / 2.0  # one pair of opposite edges, averaged
    e1 = ((q[3] - q[0]) + (q[2] - q[1])) / 2.0  # the other pair
    out = []
    for u, v in ((e0, e1), (e1, e0)):
        ux, uy, vx, vy = u[0] / long_cm, u[1] / long_cm, v[0] / short_cm, v[1] / short_cm
        k = float(np.hypot(ux, vx))
        if k <= 0:
            continue
        raw = float(np.hypot(uy, vy)) / k
        if raw > 1.05:  # would need the card to be seen from beyond straight above: impossible
            continue
        sin_el = min(raw, 1.0)
        res = abs(uy * vy + ux * vx * sin_el ** 2) / k ** 2
        out.append((k, float(np.degrees(np.arcsin(sin_el))), float(res), abs(ux * vx) / k ** 2))
    if not out:
        raise RefError("degenerate card outline")
    return sorted(out, key=lambda b: b[2])


def card_from_quad(quad: np.ndarray, long_cm: float, short_cm: float, prefer_el: float | None = None) -> tuple[float, float, bool]:
    """(px_per_cm, elevation_deg, ambiguous). Ambiguous outlines take the branch nearest ``prefer_el`` (else the higher elevation)."""
    br = card_branches(quad, long_cm, short_cm)
    # |sin a cos a| below CARD_AXIS_TERM = edges within ~17 deg of the image axes, where perspective noise decides the residual test
    amb = len(br) == 2 and (br[1][2] < CARD_AMBIGUOUS_RATIO * max(br[0][2], 1e-3) or br[0][3] < CARD_AXIS_TERM)
    pick = br[0]
    if amb:
        # nearest to the other card frames' elevation; with none, the higher one (a phone over a desk looks down; it is also the old
        # "long image side = long card side" reading)
        pick = min(br, key=lambda b: abs(b[1] - prefer_el)) if prefer_el is not None else max(br, key=lambda b: b[1])
    return pick[0], pick[1], bool(amb)


def card_observation(mask: np.ndarray, long_cm: float, short_cm: float, prefer_el: float | None = None) -> tuple[float, float]:
    """(px_per_cm, elevation_deg) from a card mask: corner fit (``card_from_quad``; ``prefer_el`` resolves an ambiguous outline) when the
    outline is a clean quadrilateral, otherwise the
    older approximation (long image side = ``long_cm``, short/long ratio = sin(elevation)), which is wrong when the card's long side points
    away from the camera."""
    import cv2

    q = card_quad(mask)
    if q is not None:
        ppc, el, _ = card_from_quad(q, long_cm, short_cm, prefer_el)
        if ppc * long_cm >= 12:
            return ppc, el
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
