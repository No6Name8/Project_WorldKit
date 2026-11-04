"""Pack a real AI Edit rehearsal into a replay the hosted demo viewer can play without an edit server.

    python scripts/make_edit_replay.py [RUN_DIR] [--out viewer/assets/demo/edit_replay]

RUN_DIR is a run whose edits/versions.json was written by real Muse Wonder + Muse Spark edits (default pipeline/runs/rehearse, made by
scripts/rehearse_edits.mjs; run 1 of each instruction is used: v1, v3, v5, v7, v9). Nothing is invented: every instruction, tool call,
Spark sentence, timing and size comes from versions.json; the handle wall is measured on the stored STLs (edit_tools.part_wall_mm);
the models are the stored STLs converted exactly as the edit server converts them (serve.stl_to_glb); the renders are the verifier's own
before/after images (re-encoded as JPEG to keep the page light).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import trimesh
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from worldkit import edit, serve  # noqa: E402
from worldkit import edit_tools as et  # noqa: E402

IDS = {1: "a", 3: "b", 5: "c", 7: "d", 9: "e"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?", type=Path, default=ROOT / "pipeline" / "runs" / "rehearse")
    ap.add_argument("--out", type=Path, default=ROOT / "viewer" / "assets" / "demo" / "edit_replay")
    a = ap.parse_args()
    data = json.loads((a.run / "edits" / "versions.json").read_text(encoding="utf-8"))
    parts = edit.load_parts(a.run)
    handle = parts.get("handle", {}).get("stl_points")
    a.out.mkdir(parents=True, exist_ok=True)

    def mesh(v):
        return trimesh.load(a.run / v["stl"], force="mesh")

    v0 = next(v for v in data["versions"] if v["version"] == 0)
    m0 = mesh(v0)
    (a.out / "v0.glb").write_bytes(serve.stl_to_glb(m0, None))
    wall0 = et.part_wall_mm(m0, handle) if handle is not None else None
    edits = []
    for v in data["versions"]:
        if v["version"] not in IDS:
            continue
        key = IDS[v["version"]]
        m = mesh(v)
        (a.out / f"{key}.glb").write_bytes(serve.stl_to_glb(m, None))
        renders = []
        for i, r in enumerate(v.get("renders") or []):
            src = a.run / r
            if src.is_file():
                dst = f"{key}_render{i}.jpg"
                Image.open(src).convert("RGB").save(a.out / dst, quality=85)
                renders.append(dst)
        rec = {"id": key, "version": v["version"], "instruction": v["instruction"], "seconds": v.get("seconds"),
               "tools": [{"name": c["name"], "arguments": c["arguments"], "sentence": (c.get("result") or {}).get("sentence")} for c in v["tool_calls"]],
               "spark": (v.get("spark") or {}).get("reason"), "verdict": (v.get("verdict") or {}).get("reason"), "retries": v.get("retries", 0),
               "size_before_mm": [round(float(x), 1) for x in m0.extents], "size_after_mm": [round(float(x), 1) for x in m.extents],
               "deterministic_checks": [{"name": c["name"], "ok": c["ok"], "detail": c["detail"]} for c in v.get("deterministic") or []],
               "renders": renders, "model": f"{key}.glb", "created": v.get("created"), "sam_overlay": key == "a"}
        if key in ("a", "c") and handle is not None:
            rec["handle_wall_mm"] = [round(wall0, 2), round(et.part_wall_mm(m, handle), 2)]
        edits.append(rec)
    out = {"title": "Replay of a real run",
           "banner": "Recorded from a real Muse Wonder and Muse Spark run. Live editing needs the local edit server.",
           "source": {"run": a.run.name, "made_by": "scripts/rehearse_edits.mjs (run 1 of 2 for each instruction)", "date": edits[0]["created"][:10] if edits else None,
                      "models": {"tool_calls": "muse-wonder-1.0 (Meta Model API)", "verification": "muse-spark-1.3 (Meta Model API)"},
                      "v0_stl_sha256": hashlib.sha256((a.run / v0["stl"]).read_bytes()).hexdigest(), "notes": "docs/DEMO_EDITS.md"},
           "v0": {"model": "v0.glb", "size_mm": [round(float(x), 1) for x in m0.extents]}, "edits": edits}
    (a.out / "replay.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {a.out} ({len(edits)} edits)")


if __name__ == "__main__":
    main()
