"""Stage A of the desk-scene run: what does each sample video contain?

    python scripts/scene_survey.py OUT_DIR [VIDEO ...]      (default: every pipeline/samples/*.mp4, duplicates skipped)

For each video: duration, 24 frames spread evenly across it, then
  - one Muse Spark call on a numbered contact sheet of the 24 frames: which of mug / candle / black case / white marble table top /
    coin / bank card is visible in each frame (cached in OUT_DIR/<video>/spark.json, so a rerun costs nothing);
  - the free detectors on every full-size frame: gold coin by colour (worldkit.coindetect), card by rectangle (worldkit.carddetect).
A reference counts as present only if a detector or Spark sees it; nothing is assumed. Writes OUT_DIR/survey.json and a sheet per video.
SAM 3.1 is not used here (24 frames x 6 concepts x 5 videos would be ~720 calls against a 120 cap); it runs on the chosen video.
Calls count against WK_BUDGET_FILE when set.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from worldkit import carddetect, coindetect  # noqa: E402
from worldkit.frames import _ffmpeg_binary  # noqa: E402
from worldkit.meta_api import ask_muse_spark_with_usage  # noqa: E402

ITEMS = ["striped mug", "candle", "black case", "white marble table top", "coin", "bank card"]
PROMPT = ("The image is a grid of 24 numbered frames from one phone video of a desk. For EACH frame number, list which of these are "
          "clearly visible in that frame: " + ", ".join(ITEMS) + ". A candle may stand in a red holder. Only list what you can actually see. "
          'Answer with JSON only, like {"1": ["striped mug", "coin"], "2": []}. Also add "laps": your estimate of how many times the camera '
          "goes around the table (0 if it does not go around).")


def duration_s(video: Path) -> float:
    err = subprocess.run([_ffmpeg_binary(), "-i", str(video)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def extract(video: Path, out: Path, n: int = 24) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    d = duration_s(video)
    paths = []
    for i in range(n):
        t = d * (i + 0.5) / n
        p = out / f"s{i + 1:02d}.png"
        if not p.is_file():
            subprocess.run([_ffmpeg_binary(), "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(p)], check=True)
        paths.append(p)
    return paths


def sheet(paths: list[Path], dest: Path, tile_w: int = 240) -> Path:
    ims = [Image.open(p).convert("RGB") for p in paths]
    th = int(ims[0].height * tile_w / ims[0].width)
    cols = 6
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (cols * tile_w, rows * th), (0, 0, 0))
    for i, im in enumerate(ims):
        t = im.resize((tile_w, th))
        d = ImageDraw.Draw(t)
        d.rectangle([0, 0, 34, 22], fill=(0, 0, 0))
        d.text((5, 4), str(i + 1), fill=(255, 255, 0))
        s.paste(t, ((i % cols) * tile_w, (i // cols) * th))
    s.save(dest)
    return dest


def main() -> None:
    out = Path(sys.argv[1])
    vids = [Path(v) for v in sys.argv[2:]] or sorted((ROOT / "pipeline" / "samples").glob("*.mp4"))
    seen, report = {}, {}
    for v in vids:
        h = hashlib.sha256(v.read_bytes()).hexdigest()[:16]
        if h in seen:
            report[v.name] = {"duplicate_of": seen[h]}
            continue
        seen[h] = v.name
        vd = out / v.stem
        frames = extract(v, vd / "frames")
        coin = [p.stem for p in frames if coindetect.find_gold_coin(np.array(Image.open(p).convert("RGB"))) is not None]
        card = [p.stem for p in frames if carddetect.find_card(np.array(Image.open(p).convert("RGB"))) is not None]
        sp = vd / "spark.json"
        if sp.is_file():
            spark = json.loads(sp.read_text(encoding="utf-8"))
        else:
            text, _ = ask_muse_spark_with_usage([sheet(frames, vd / "sheet.png")], PROMPT)
            m = re.search(r"\{.*\}", text, re.S)
            spark = json.loads(m.group(0)) if m else {"_raw": text}
            sp.write_text(json.dumps(spark, indent=1), encoding="utf-8")
        counts = {it: sum(1 for k, v2 in spark.items() if k.isdigit() and it in v2) for it in ITEMS}
        report[v.name] = {"duration_s": round(duration_s(v), 1), "frames": len(frames), "spark_frames_with": counts, "spark_laps": spark.get("laps"),
                          "coin_detector_frames": len(coin), "card_detector_frames": len(card)}
        print(v.name, json.dumps(report[v.name]))
    (out / "survey.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
