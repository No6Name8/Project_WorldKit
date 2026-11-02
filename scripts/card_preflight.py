"""Card preflight for a scene run: does the bank card show up in the frames the scene run will use?

    python scripts/card_preflight.py OUT_DIR VIDEO [VIDEO ...] [--keep 12] [--sheet docs/ui/card_preflight.png]

For each video, extracts the same sharp frames the scene run extracts (fps 2, keep N, into OUT_DIR/<video stem>), asks SAM 3.1 for
"bank card" in each, falls back to the rectangle detector (worldkit.carddetect) where SAM finds nothing, and counts frames with a card.
Writes OUT_DIR/card_preflight.json and a contact sheet (frame with the mask outlined, labelled SAM or rectangle) to check by eye.
SAM calls count against WK_BUDGET_FILE when it is set; cached results are free.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from worldkit import carddetect, segment  # noqa: E402
from worldkit import frames as frames_mod  # noqa: E402
from worldkit.mapmode import cached_mask  # noqa: E402

CONCEPT = "bank card"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("videos", nargs="+", type=Path)
    ap.add_argument("--keep", type=int, default=12)
    ap.add_argument("--sheet", type=Path, default=ROOT / "docs" / "ui" / "card_preflight.png")
    a = ap.parse_args()
    report, tiles = {}, []
    for v in a.videos:
        if not v.is_file():
            report[v.name] = {"error": "file not found"}
            continue
        vdir = a.out / v.stem
        fr = frames_mod.extract_sharp_frames(v, vdir, fps=2.0, keep_n=a.keep)
        rows = []
        for f in fr:
            rgb = np.array(Image.open(f.path).convert("RGB"))
            m = cached_mask(vdir / "cache" / "masks", f.path, CONCEPT)
            how = "sam" if m is not None and m.sum() >= 120 else None
            if how is None:
                m = carddetect.find_card(rgb)
                how = "rectangle" if m is not None else None
            row = {"frame": Path(f.path).stem, "found": how}
            if how:
                cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                (_, _), (w, h), _ = cv2.minAreaRect(max(cnts, key=cv2.contourArea))
                row.update(area_px=int(m.sum()), long_px=round(max(w, h), 1), short_px=round(min(w, h), 1))
                np.save(vdir / f"card_{row['frame']}.npy", m)
                tiles.append((v.stem, row, rgb, m))
            rows.append(row)
        report[v.name] = {"frames": rows, "with_card": sum(1 for r in rows if r["found"]), "by_sam": sum(1 for r in rows if r["found"] == "sam"),
                          "by_rectangle": sum(1 for r in rows if r["found"] == "rectangle")}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "card_preflight.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if tiles:
        tw = 240
        ims = []
        for stem, row, rgb, m in tiles:
            im = Image.fromarray(rgb)
            ov = np.array(im).copy()
            ov[m] = (0.45 * ov[m] + 0.55 * np.array([47, 110, 210])).astype(np.uint8)
            cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(ov, cnts, -1, (255, 230, 0), 2)
            im = Image.fromarray(ov)
            im = im.resize((tw, int(im.height * tw / im.width)))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, tw, 18], fill=(0, 0, 0))
            d.text((4, 3), f"{stem[:18]} {row['frame']} {row['found']} {row['long_px']:.0f}x{row['short_px']:.0f}px", fill=(255, 255, 255))
            ims.append(im)
        cols = min(6, len(ims))
        rows_n = (len(ims) + cols - 1) // cols
        th = max(i.height for i in ims)
        sheet = Image.new("RGB", (cols * tw, rows_n * th), (17, 18, 20))
        for i, im in enumerate(ims):
            sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
        a.sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(a.sheet)
    print(json.dumps({k: {kk: vv for kk, vv in r.items() if kk != "frames"} for k, r in report.items()}, indent=1))


if __name__ == "__main__":
    main()
