"""Live check of part edits on a real run: real Muse Wonder + Muse Spark, call counts capped.

    python scripts/part_edit_live.py RUN_DIR [--out runs/part_live] [--wonder-cap 15] [--spark-cap 25]

Each instruction starts from a fresh copy of RUN_DIR (needs model_print.stl, report.json and parts/parts.json from `python -m worldkit.parts`).
Reports the tool Wonder chose, Spark's verdict, the handle's local wall thickness before and after (median ray thickness of surface points
within 2.5 mm of the SAM part), whether the body is unchanged, and the time. Writes results.json and copies the before/after renders.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from worldkit import edit, edit_tools as et  # noqa: E402
from worldkit.meta_api import ask_muse_spark_with_usage, get_client  # noqa: E402

INSTRUCTIONS = ["make the handle thicker", "make the handle 2 mm thicker", "make the handle thinner"]


def handle_thickness(mesh: trimesh.Trimesh, part_pts: np.ndarray, n: int = 6000) -> float:
    from scipy.spatial import cKDTree

    pts, fi = mesh.sample(n, return_index=True, seed=0)  # the same surface samples for before and after
    nrm = mesh.face_normals[fi]
    near = cKDTree(part_pts).query(pts)[0] < 2.5
    pts, nrm = pts[near], nrm[near]
    if len(pts) < 20:
        return float("nan")
    loc, ray, _ = mesh.ray.intersects_location(pts - nrm * 1e-3, -nrm, multiple_hits=False)
    d = np.full(len(pts), np.nan)
    d[ray] = np.linalg.norm(loc - pts[ray], axis=1)
    return float(np.nanmedian(d))


def body_unchanged(before: trimesh.Trimesh, after: trimesh.Trimesh, part_pts: np.ndarray, tol_mm: float = 0.15) -> tuple[bool, float]:
    """Every vertex of ``after`` that is more than 6 mm from the part lies within tol_mm of the original surface."""
    from scipy.spatial import cKDTree

    far = after.vertices[cKDTree(part_pts).query(after.vertices)[0] > 6.0]
    far = far[np.random.default_rng(0).choice(len(far), min(len(far), 4000), replace=False)]
    _, dist, _ = trimesh.proximity.closest_point(before, far)
    return bool(dist.max() <= tol_mm), float(dist.max())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "pipeline" / "runs" / "part_live")
    ap.add_argument("--wonder-cap", type=int, default=15)
    ap.add_argument("--spark-cap", type=int, default=25)
    ap.add_argument("--render-dir", type=Path, default=ROOT / "docs" / "ui")
    ap.add_argument("--only", type=int, default=None, help="run only instruction N (1-based)")
    ap.add_argument("--remeasure", action="store_true", help="no API calls: recompute thickness and body checks from the saved edit folders")
    a = ap.parse_args(argv)
    if a.remeasure:
        rf = a.out / "results.json"
        data = json.loads(rf.read_text())
        pp = edit.load_parts(a.run_dir)["handle"]["stl_points"]
        before = trimesh.load(a.run_dir / "model_print.stl", force="mesh")
        tb = handle_thickness(before, pp)
        for rec in data["results"]:
            rec["handle_mm_before"] = round(tb, 2)
            work = a.out / f"edit{rec['n']}"
            if rec.get("applied"):
                after = trimesh.load(work / edit._load(work)["versions"][-1]["stl"], force="mesh")
                rec["handle_mm_after"] = round(handle_thickness(after, pp), 2)
                ok, dmax = body_unchanged(before, after, pp)
                rec["body_unchanged"], rec["body_max_deviation_mm"] = ok, round(dmax, 3)
            else:
                rec["handle_mm_after"] = rec["handle_mm_before"]
        rf.write_text(json.dumps(data, indent=1))
        for rec in data["results"]:
            print(rec["n"], rec["instruction"], rec["handle_mm_before"], "->", rec["handle_mm_after"], "body unchanged:", rec["body_unchanged"], rec.get("body_max_deviation_mm"))
        return 0
    client = get_client()
    a.out.mkdir(parents=True, exist_ok=True)
    cf = a.out / "calls.json"  # call counts persist across invocations so the caps are cumulative
    calls = json.loads(cf.read_text()) if cf.is_file() else {"wonder": 0, "spark": 0}
    orig = client.chat.completions.create

    def counted(**kw):
        if calls["wonder"] >= a.wonder_cap:
            raise RuntimeError(f"Muse Wonder cap of {a.wonder_cap} calls reached")
        calls["wonder"] += 1
        return orig(**kw)

    client.chat.completions.create = counted

    def spark(images, prompt):
        if calls["spark"] >= a.spark_cap:
            raise RuntimeError(f"Spark cap of {a.spark_cap} calls reached")
        calls["spark"] += 1
        return ask_muse_spark_with_usage(images, prompt)

    parts = edit.load_parts(a.run_dir)
    part_pts = parts["handle"]["stl_points"]
    results = []
    for i, text in enumerate(INSTRUCTIONS, 1):
        if a.only and i != a.only:
            continue
        work = a.out / f"edit{i}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        for f in ("model_print.stl", "report.json", "model.glb"):
            if (a.run_dir / f).is_file():
                shutil.copy(a.run_dir / f, work / f)
        shutil.copytree(a.run_dir / "parts", work / "parts")
        before = trimesh.load(work / "model_print.stl", force="mesh")
        t_before = handle_thickness(before, part_pts)
        w0, s0 = calls["wonder"], calls["spark"]
        t0 = time.time()
        rec = {"n": i, "instruction": text}
        try:
            r = edit.run_edit(work, text, client=client, ask_spark=spark)
        except Exception as exc:  # noqa: BLE001 - a failed live call is a result, not a crash
            rec.update({"error": f"{type(exc).__name__}: {str(exc)[:300]}", "seconds": round(time.time() - t0, 1)})
            results.append(rec)
            continue
        rec["seconds"] = round(time.time() - t0, 1)
        rec["wonder_calls"], rec["spark_calls"] = calls["wonder"] - w0, calls["spark"] - s0
        attempts = r.get("attempts") if isinstance(r.get("attempts"), list) else [r]
        last = attempts[-1]
        tc = r.get("tool_calls") or last.get("tool_calls") or []
        rec["applied"] = bool(r["applied"])
        rec["tools"] = [{"name": c["name"], "arguments": c["arguments"], "error": c.get("error"), "sentence": (c.get("result") or {}).get("sentence")} for c in tc]
        rec["wonder_text"] = (r.get("assistant") or last.get("assistant") or "")[:300]
        rec["reason"] = (r.get("verdict") or {}).get("reason")
        rec["spark"] = (r.get("spark") or last.get("spark") or {}).get("reason")
        rec["attempts"] = len(attempts)
        rec["handle_mm_before"] = round(t_before, 2)
        if r["applied"]:
            after = trimesh.load(work / edit._load(work)["versions"][-1]["stl"], force="mesh")
            rec["handle_mm_after"] = round(handle_thickness(after, part_pts), 2)
            ok, dmax = body_unchanged(before, after, part_pts)
            rec["body_unchanged"], rec["body_max_deviation_mm"] = ok, round(dmax, 3)
        else:
            rec["handle_mm_after"], rec["body_unchanged"] = rec["handle_mm_before"], True  # nothing was applied
        imgs = r.get("renders") or last.get("images") or []
        a.render_dir.mkdir(parents=True, exist_ok=True)
        rec["renders"] = []
        for k, im in enumerate(imgs[:1]):
            dst = a.render_dir / f"part_edit_{i}_{k}.png"
            shutil.copy(work / im, dst)
            rec["renders"].append(dst.name)
        results.append(rec)
        cf.write_text(json.dumps(calls))
        print(json.dumps({k: v for k, v in rec.items() if k not in ("tools",)}, indent=None)[:600])
    rf = a.out / "results.json"
    prev = json.loads(rf.read_text())["results"] if rf.is_file() else []
    merged = {r["n"]: r for r in prev}
    merged.update({r["n"]: r for r in results})
    rf.write_text(json.dumps({"calls": calls, "results": [merged[k] for k in sorted(merged)]}, indent=1))
    print("calls", calls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
