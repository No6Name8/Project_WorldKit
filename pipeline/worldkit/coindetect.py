"""Fallback coin finder for coins too small for SAM 3.1 (a 20 mm coin is ~12 px across in a phone video of a whole table).

A gold-coloured coin on a pale surface: threshold a saturated warm hue, keep compact blobs of plausible size that are ringed by low-saturation
pixels (the surface), and return the best one as a full-frame bool mask. This is a heuristic for the known coin colour, not a general detector;
fit_disk then measures its ellipse. At ~12 px a 1 px edge error is ~8% of the diameter, which the report states as the coin-edge uncertainty.
"""
from __future__ import annotations

import cv2
import numpy as np


def find_gold_coin(rgb: np.ndarray, *, min_area: int = 40, max_area: int = 900) -> np.ndarray | None:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    cand = ((h >= 12) & (h <= 34) & (s >= 70) & (v >= 100)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(cand, connectivity=8)
    best, best_score = None, 0.0
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if not (min_area <= area <= max_area) or max(w, hh) > 2.2 * min(w, hh) + 2:
            continue
        blob = lab == i
        cnts, _ = cv2.findContours(blob.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        per = cv2.arcLength(max(cnts, key=cv2.contourArea), True)
        circ = 4 * np.pi * area / max(per * per, 1.0)
        ring = cv2.dilate(blob.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ~cv2.dilate(blob.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        if ring.sum() < 20:
            continue
        ring_sat = float(s[ring].mean())
        ring_val = float(v[ring].mean())
        blob_sat = float(s[blob].mean())
        if ring_sat > 45 or ring_val < 150 or blob_sat < 90 or circ < 0.65:  # a saturated gold disc on a pale, unsaturated, bright surface
            continue
        score = circ * (blob_sat - ring_sat) / 255.0 * min(area / 80.0, 1.0)
        if score > best_score:
            best, best_score = blob, score
    return best
