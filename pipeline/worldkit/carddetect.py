"""Fallback finder for a bank card (ID-1, 85.6 x 54 mm) lying flat on a desk, used only when SAM 3.1 finds no "bank card" in a frame.

Looks for convex four-sided contours whose filled area is close to their bounding rotated rectangle (a flat card seen in perspective is a
near-rectangular quadrilateral), of plausible size, with an image long/short ratio that a 1.586 card can produce: about 1.2 (long side
foreshortened) to 6 (short side foreshortened at a low camera). That ratio window is wide, so this can also accept other rectangles
(a phone, a notebook, a box lid): every detection must be checked by eye on the contact sheet before it is trusted.
"""
from __future__ import annotations

import cv2
import numpy as np

CARD_ASPECT = 85.6 / 54.0


def find_card(rgb: np.ndarray, *, min_frac: float = 0.002, max_frac: float = 0.12) -> np.ndarray | None:
    h, w = rgb.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 40, 120), np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best, best_score = None, 0.0
    for c in cnts:
        area = cv2.contourArea(c)
        if not (min_frac * w * h <= area <= max_frac * w * h):
            continue
        quad = cv2.approxPolyDP(c, 0.03 * cv2.arcLength(c, True), True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        (_, _), (rw, rh), _ = cv2.minAreaRect(quad)
        if min(rw, rh) < 8:
            continue
        ratio = max(rw, rh) / min(rw, rh)
        fill = cv2.contourArea(quad) / (rw * rh)
        if not (1.2 <= ratio <= 6.0) or fill < 0.8:
            continue
        score = fill * area * (1.0 / (1.0 + abs(np.log(ratio / CARD_ASPECT))))
        if score > best_score:
            best, best_score = quad, score
    if best is None:
        return None
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [best.reshape(-1, 2)], 1)
    return m.astype(bool)
