"""Part G: the AI Edit verification step of the five rehearsed edits, run once more through Muse Spark Open (open weights) on Baseten,
on the stored before/after meshes and the stored renders, and compared with the stored Muse Spark (Meta Model API) verdicts.

    pipeline\\.venv\\Scripts\\python.exe scripts/spark_open_agreement.py [--run pipeline/runs/rehearse] [--versions 1 3 5 7 9]

Writes docs/results/spark_open_agreement.json. One Baseten call per edit (5 in total); nothing is edited and no geometry changes.
"Agree" means both models gave the same matches_instruction AND the same unintended_changes, so both would accept (or both reject).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from worldkit import edit  # noqa: E402
from worldkit.spark_open import SPARK_OPEN_MODEL, ask_spark_open_with_usage  # noqa: E402


def accepts(v: dict) -> bool:
    return bool(v.get("matches_instruction")) and not v.get("unintended_changes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=ROOT / "pipeline" / "runs" / "rehearse")
    ap.add_argument("--versions", type=int, nargs="*", default=[1, 3, 5, 7, 9])
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "results" / "spark_open_agreement.json")
    ap.add_argument("--max-tokens", type=int, default=None, help="completion budget per call (default: worldkit.spark_open.MAX_TOKENS)")
    a = ap.parse_args()
    prior = json.loads(a.out.read_text(encoding="utf-8")) if a.out.is_file() else None
    ask = ask_spark_open_with_usage if not a.max_tokens else (lambda im, pr: ask_spark_open_with_usage(im, pr, max_tokens=a.max_tokens))
    data = json.loads((a.run / "edits" / "versions.json").read_text(encoding="utf-8"))
    by = {v["version"]: v for v in data["versions"]}
    rows = []
    for n in a.versions:
        v = by[n]
        before = trimesh.load(a.run / by[v["parent"]]["stl"], force="mesh")
        after = trimesh.load(a.run / v["stl"], force="mesh")
        images = [a.run / r.replace("\\", "/") for r in v["renders"]]
        calls = [{"name": c["name"], "arguments": c["arguments"]} for c in v["tool_calls"]]
        t = time.time()
        verdict, usage = edit.verify(before, after, v["instruction"], calls, images, ask=ask)
        secs = round(time.time() - t, 1)
        meta = v["spark"]
        row = {"version": n, "instruction": v["instruction"], "tools": [c["name"] for c in calls],
               "muse_spark": {k: meta.get(k) for k in ("matches_instruction", "unintended_changes", "confidence", "reason")},
               "spark_open": {k: verdict.get(k) for k in ("matches_instruction", "unintended_changes", "confidence", "reason", "available", "parse_error") if k in verdict},
               "spark_open_seconds": secs, "spark_open_usage": {k: (usage or {}).get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
               "spark_open_max_tokens": a.max_tokens or __import__("worldkit.spark_open", fromlist=["MAX_TOKENS"]).MAX_TOKENS,
               "both_accept": accepts(meta) and accepts(verdict), "agree": accepts(meta) == accepts(verdict) and verdict.get("available", True) and "parse_error" not in verdict}
        rows.append(row)
        print(f"v{n} {v['instruction']!r}: Muse Spark accept={accepts(meta)}  Spark Open accept={accepts(verdict)}  ({secs} s) {verdict.get('reason')!r}")
    if prior:  # a rerun of some edits: keep the other rows, and keep every earlier attempt of a rerun edit (never overwrite a result)
        old = {r["version"]: r for r in prior["rows"]}
        for r in rows:
            if r["version"] in old:
                o = old.pop(r["version"])
                r["earlier_attempts"] = o.pop("earlier_attempts", []) + [o]
        rows = sorted(rows + list(old.values()), key=lambda r: r["version"])
    out = {"what": "AI Edit verification step only (before/after renders + the applied tool calls + measured sizes -> accept or reject)",
           "spark_open_model": SPARK_OPEN_MODEL, "served_by": "Baseten (inference.baseten.co, OpenAI-compatible API)",
           "muse_spark_model": "muse-spark-1.3 (Meta Model API), verdicts stored in the rehearsal run 2026-11-03",
           "inputs": "pipeline/runs/rehearse/edits: the stored before/after STLs and renders; the prompt is worldkit.edit.SPARK_PROMPT",
           "date": time.strftime("%Y-%m-%d"), "calls": len(rows) + sum(len(r.get("earlier_attempts", [])) for r in rows), "completed": sum(1 for r in rows if r["spark_open"].get("available", True) and "parse_error" not in r["spark_open"]),
           "agree": sum(1 for r in rows if r["agree"]), "rows": rows}
    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"agreement {out['agree']}/{len(rows)}; completed {out['completed']}/{len(rows)} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
