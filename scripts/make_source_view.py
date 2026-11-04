"""Builds the viewer's "Source" tab data for the bundled demo runs: a compressed copy of the original video, a poster, 6-10 thumbnails of the
frames the pipeline used (with the object's SAM 3.1 mask outline as a polygon, read from the run's own mask cache) and source.json.

    pipeline\\.venv\\Scripts\\python.exe scripts/make_source_view.py [--only mug desk desk3]

Nothing here calls SAM, Muse Spark or a GPU: masks come from the caches the runs wrote (pipeline/.cache/sam31 for Object mode, runs/<run>/cache/masks
for desk runs). A frame whose mask is not cached gets no outline (and source.json says so). Frame times are exact for the 2 fps / 1 fps
extraction (ffmpeg fps filter: frame N is at (N-1)/fps s). Output: viewer/assets/demo/run_mug/source, run_desk/source, run_desk3/source.
Videos: H.264, CRF 28, no audio, faststart, short side at most 720 px (the demo videos are 480 x 856, so they keep their size)."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import imageio_ffmpeg  # noqa: E402

from worldkit import segment  # noqa: E402  (cache readers only; no API call is made)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RUNS = ROOT / "pipeline" / "runs"
SAMPLES = ROOT / "pipeline" / "samples"
DEMO = ROOT / "viewer" / "assets" / "demo"
THUMB_W = 360
VIDEO_MAX_MB = {"mug": 6.0, "desk": 6.0, "desk3": 6.0}
DESK3_LIMIT_MB = 8.0
TOTAL_LIMIT_MB = 12.0


def ff(*args) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def compress(video: Path, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    ff("-i", video, "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
       "-vf", "scale='if(lt(iw,ih),min(720,iw),-2)':'if(lt(iw,ih),-2,min(720,ih))'", out)
    n, secs = imageio_ffmpeg.count_frames_and_secs(str(out))
    return {"seconds": round(secs, 1), "mb": round(out.stat().st_size / 1e6, 2)}


def poster(video: Path, out: Path, t: float) -> None:
    ff("-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-q:v", "4", out)


def outline(mask: np.ndarray | None, scale: float) -> list[list[list[float]]] | None:
    """The mask's outer contours as polygons in thumbnail pixels (largest first, small specks dropped)."""
    if mask is None or not mask.any():
        return None
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cs = sorted(cs, key=cv2.contourArea, reverse=True)
    keep = [c for c in cs if cv2.contourArea(c) >= 0.05 * cv2.contourArea(cs[0])][:3]
    polys = []
    for c in keep:
        c = cv2.approxPolyDP(c, 1.2, True).reshape(-1, 2) * scale
        polys.append([[round(float(x), 1), round(float(y), 1)] for x, y in c])
    return polys


def thumb(src: Path, out: Path) -> float:
    im = Image.open(src).convert("RGB")
    s = THUMB_W / im.width
    im.resize((THUMB_W, round(im.height * s)), Image.LANCZOS).save(out, quality=80, optimize=True)
    return s


def mug_frames() -> tuple[list[dict], dict]:
    """The 12 sharp frames of the headline mug run. runs/mug_grounded kept only _all_frames; runs/cli_repro (same video, same extraction)
    kept frames/sharp_XX.png. Each sharp frame is a byte copy of one extracted frame, which gives its time."""
    run = RUNS / "mug_grounded"
    rep = json.loads((run / "report.json").read_text(encoding="utf-8"))
    by_hash = {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in sorted((run / "_all_frames").glob("frame_*.png"))}
    sharp = sorted((RUNS / "cli_repro" / "frames").glob("sharp_*.png"))
    coin_frames = {f["frame"] for f in rep["scale_calibration"]["per_frame"]}
    rows = []
    for p in sharp:
        src = by_hash.get(hashlib.sha256(p.read_bytes()).hexdigest())
        n = int(src.stem.split("_")[1]) if src else None
        rows.append({"path": p, "name": p.stem, "time_s": (n - 1) / 2.0 if n else None, "coin": p.stem in coin_frames})
    # 8 of the 12: every frame the coin scale came from, then the sharpest others; shown in time order
    pick = [r for r in rows if r["coin"]] + [r for r in rows if not r["coin"]]
    pick = sorted(pick[:8], key=lambda r: (r["time_s"] is None, r["time_s"] or 0))
    concept = rep.get("object_name") or "mug"
    out = []
    for r in pick:
        cached = segment._load_cached_segmentation(r["path"], concept)
        m = segment.full_frame_mask(r["path"], cached) if cached is not None else None
        out.append({**r, "masks": {concept: m}})
    sc = rep["scale_calibration"]
    meta = {"object": concept, "frames_used": rep.get("frames_extracted"), "scale": f"coin {sc['reference_diameter_cm'] * 10:.1f} mm",
            "built_with": "Muse Spark, SAM 3.1, SAM 3D", "frames_note": f"{len(sharp)} sharp frames kept from {len(by_hash)} extracted at 2 fps; "
            f"{len(coin_frames)} of them set the coin scale"}
    return out, meta


def desk_frames(run: Path, concepts: dict) -> tuple[list[dict], dict]:
    rep = json.loads((run / "desk_report.json").read_text(encoding="utf-8"))
    fr = rep["frames"]
    fps = float(fr["fps"])
    names = fr["fit_frames"][:10]
    ref = set(fr.get("reference_detector_frames") or [])
    out = []
    for nm in names:
        p = run / "frames" / f"{nm}.png"
        masks = {}
        for obj, concept in concepts.items():
            if obj not in (rep.get("masks") or {}) or nm not in rep["masks"][obj]:
                continue
            f = run / "cache" / "masks" / (hashlib.sha1(p.read_bytes() + concept.encode()).hexdigest()[:20] + ".npz")
            if f.is_file():
                z = np.load(f)
                masks[obj] = z["m"] if bool(z["ok"]) else None
            else:
                masks[obj] = None
        if concepts.get("candle") and "candle" in masks:  # the candle object is holder + candle
            h = run / "cache" / "masks" / (hashlib.sha1(p.read_bytes() + concepts["holder"].encode()).hexdigest()[:20] + ".npz")
            if h.is_file() and masks["candle"] is not None:
                z = np.load(h)
                if bool(z["ok"]):
                    masks["candle"] = masks["candle"] | z["m"]
        masks.pop("holder", None)
        n = int(nm.split("_")[1])
        out.append({"path": p, "name": nm, "time_s": (n - 1) / fps, "coin": nm in ref, "masks": masks})
    man = json.loads((run / "scene.json").read_text(encoding="utf-8"))
    ss = man["scale_source"]
    objs = [o["name"] for o in man["objects"] if o.get("id") not in ("table", "reference") and o.get("name") not in ("table",)]
    sz = ss.get("size_mm")
    sz = " x ".join(f"{x:g}" for x in sz) if isinstance(sz, list) else (f"{sz:g}" if sz else None)
    meta = {"object": ", ".join(objs), "frames_used": fr["n_frames"], "scale": f"{ss['kind']} {sz} mm" if sz else ss["text"],
            "frames_note": f"{fr['n_frames']} frames at {fps:g} fps for the VGGT cameras; the {len(fr['fit_frames'])} fit frames are shown",
            "verify_iou": (man.get("verify") or {}).get("mean_iou_after"), "verify_images": (man.get("verify") or {}).get("images") or []}
    return out, meta


def verify_pair(run_demo: Path, rel: str, out: Path) -> None:
    """real | reconstruction from a verify image (real | before | after; 'after' is the final placement)."""
    im = Image.open(run_demo / rel).convert("RGB")
    w = im.width // 3
    def content(panel):
        a = np.asarray(panel)
        cols = np.where((a < 250).any(axis=(0, 2)))[0]
        return panel.crop((int(cols.min()), 0, int(cols.max()) + 1, panel.height)) if len(cols) else panel
    real, after = content(im.crop((0, 0, w, im.height))), content(im.crop((2 * w, 0, 3 * w, im.height)))
    gap = 8
    pair = Image.new("RGB", (real.width + after.width + gap, max(real.height, after.height)), (255, 255, 255))
    pair.paste(real, (0, 0)); pair.paste(after, (real.width + gap, 0))
    s = 720 / pair.width
    pair.resize((720, round(pair.height * s)), Image.LANCZOS).save(out, quality=80, optimize=True)


def build(key: str, video: Path, demo_run: Path, frames: list[dict], meta: dict) -> dict:
    out = demo_run / "source"
    (out / "frames").mkdir(parents=True, exist_ok=True)
    for old in (out / "frames").glob("*.jpg"):
        old.unlink()
    vname = video.name
    v = compress(video, out / vname)
    poster(out / vname, out / "poster.jpg", 1.0)
    items = []
    for r in frames:
        fn = f"frames/{r['name']}.jpg"
        s = thumb(r["path"], out / fn)
        outlines = {o: outline(m, s) for o, m in r["masks"].items()}
        items.append({"img": fn, "name": r["name"], "time_s": r["time_s"], "coin": r["coin"],
                      "outlines": {o: p for o, p in outlines.items() if p}, "missing_masks": [o for o, p in outlines.items() if not p]})
    with Image.open(out / items[0]["img"]) as t0:
        tw, th = t0.size
    doc = {"worldkit_source": 1, "video": vname, "poster": "poster.jpg", "file_name": video.name, "duration_s": v["seconds"],
           "frames_used": meta["frames_used"], "frames_note": meta["frames_note"], "object": meta["object"], "scale_source": meta["scale"],
           "built_with": meta.get("built_with"), "thumb_size": [tw, th], "frames": items,
           "provenance": "scripts/make_source_view.py: video re-encoded (H.264 CRF 28, no audio); outlines from the run's cached SAM 3.1 masks, "
                         "no new model calls; frame times from the fixed-rate extraction"}
    if meta.get("verify_images"):
        verify_pair(demo_run, meta["verify_images"][0], out / "verify_pair.jpg")
        doc["verify"] = {"img": "verify_pair.jpg", "mean_iou": meta["verify_iou"],
                         "label": f"Held-out frame: real vs reconstruction, mean IoU {meta['verify_iou']:.3f}", "frame": meta["verify_images"][0].split("_frame_")[-1].split(".")[0]}
    (out / "source.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"{key}: video {v['mb']} MB, {v['seconds']} s; {len(items)} thumbnails "
          f"({sum(1 for i in items if i['outlines'])} with outlines); source/ total {size:.2f} MB")
    return {"video_mb": v["mb"], "total_mb": size}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=["mug", "desk", "desk3"])
    a = ap.parse_args()
    sizes = {}
    if "mug" in a.only:
        fr, meta = mug_frames()
        sizes["mug"] = build("mug", SAMPLES / "mug_on_table.mp4", DEMO / "run_mug", fr, meta)
    desk_concepts = {"mug": "striped mug", "case": "black case", "candle": "white candle", "holder": "red candle holder"}
    if "desk" in a.only:
        fr, meta = desk_frames(RUNS / "desk_v2", desk_concepts)
        meta["built_with"] = "SAM 3.1, SAM 3D, VGGT"  # desk v2: objects named in the command, no Spark round ran (spark_calls 0)
        sizes["desk"] = build("desk", SAMPLES / "desk_scene_coin.mp4", DEMO / "run_desk", fr, meta)
    if "desk3" in a.only:
        rep3 = json.loads((RUNS / "desk_card3" / "desk_report.json").read_text(encoding="utf-8"))
        fr, meta = desk_frames(RUNS / "desk_card3", {"mug": "striped mug", "case": "black case", "burner": "silver lantern", "bottle": "water bottle"})
        meta["built_with"] = "SAM 3.1, SAM 3D, VGGT" + (", Muse Spark" if (rep3.get("verify") or {}).get("spark_calls") else "")
        r = build("desk3", SAMPLES / "desk_scene_card.mp4", DEMO / "run_desk3", fr, meta)
        if r["video_mb"] >= DESK3_LIMIT_MB or sum(s["total_mb"] for s in sizes.values()) + r["total_mb"] > TOTAL_LIMIT_MB:
            import shutil
            shutil.rmtree(DEMO / "run_desk3" / "source")
            print(f"desk3: left out (video {r['video_mb']} MB; limit {DESK3_LIMIT_MB} MB, total limit {TOTAL_LIMIT_MB} MB)")
        else:
            sizes["desk3"] = r
    for k, s in sizes.items():
        if s["video_mb"] > VIDEO_MAX_MB[k]:
            print(f"WARNING: {k} video {s['video_mb']} MB is over {VIDEO_MAX_MB[k]} MB")
    print(f"total added: {sum(s['total_mb'] for s in sizes.values()):.2f} MB")
    runs = sorted(f"assets/demo/{d.name}/" for d in DEMO.iterdir() if (d / "source" / "source.json").is_file())
    (DEMO / "source_index.json").write_text(json.dumps({"runs": runs, "note": "bundled runs with Source-tab data (scripts/make_source_view.py)"}, indent=1), encoding="utf-8")
    print("source_index.json:", runs)


if __name__ == "__main__":
    main()
