"""Write one machine-readable file per accuracy claim to docs/results/ (from the local runs), plus the small reports they rest on.

    python scripts/make_results.py            then   python scripts/accuracy_table.py --from-results   (writes docs/accuracy.md)

Each docs/results/<id>.json holds: the command, the input video (name, SHA-256), the measured STL (path, SHA-256, size), the method used
to measure it (scripts/accuracy_table.measure_mesh: base plate excluded, trimmed circle fits), the measured values, the ruler values
(pipeline/samples/ground_truth.json, evaluation only), the errors, and the text shown with the table in docs/accuracy.md.
pipeline/runs/ is not tracked (frames, caches and meshes are large), so the run's report.json / scene.json is copied to
docs/results/reports/<id>_*.json; the STL itself is tracked only where the viewer bundles it (viewer/assets/demo/...).
Re-measuring needs the local run folder; the stored SHA-256 says whether a later copy is the same file.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import accuracy_table as at  # noqa: E402

RUNS = ROOT / "pipeline" / "runs"
SAMPLES = ROOT / "pipeline" / "samples"
OUT = ROOT / "docs" / "results"
TRUTH = json.loads((SAMPLES / "ground_truth.json").read_text(encoding="utf-8"))
MUG = {k: v for k, v in TRUTH["objects"]["mug"].items() if k.endswith("_mm")}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


def score(measured: dict, ruler: dict, skip=()) -> dict:
    out = {}
    for k, real in ruler.items():
        if k in skip or measured.get(k) is None:
            continue
        out[k] = {"ruler_mm": real, "model_mm": round(measured[k], 1), "error_pct": round(100.0 * (measured[k] - real) / real, 1)}  # error from the unrounded measurement
    errs = [abs(v["error_pct"]) for v in out.values()]
    return {"dimensions": out, "mean_abs_error_pct": round(float(np.mean(errs)), 1) if errs else None, "worst_abs_error_pct": max(errs) if errs else None}


def copy_report(src: Path, dst_name: str) -> str | None:
    if not src.is_file():
        return None
    (OUT / "reports").mkdir(parents=True, exist_ok=True)
    d = json.loads(src.read_text(encoding="utf-8"))
    for big in ("tracks", "per_frame_masks"):  # per-frame mask data is large and not needed to check a claim
        d.pop(big, None)
    (OUT / "reports" / dst_name).write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
    return f"docs/results/reports/{dst_name}"


def object_claim(cid, run, video, command, skip=()):
    rep = json.loads((run / "report.json").read_text(encoding="utf-8"))
    stl = run / "model_print.stl"
    m = at.measure(stl, (rep.get("measurement") or {}).get("height_mm"))
    return {"id": cid, "kind": "object", "run": rel(run), "command": command, "input": {"video": rel(video), "sha256": sha(video)},
            "stl": {"path": rel(stl), "sha256": sha(stl), "bbox_mm": [round(float(x), 1) for x in trimesh.load(stl).extents]},
            "scale_source": (rep.get("measurement") or {}).get("method"), "measure_method": "scripts/accuracy_table.measure (base plate excluded by the report's body height)",
            "measured_mm": {k: round(v, 2) for k, v in m.items() if k.endswith("_mm") and v is not None}, "ruler": score(m, MUG, skip),
            "report_copy": copy_report(run / "report.json", f"{cid}_report.json")}


def scene_stl_claim(cid, run, video, command, obj="mug"):
    stl = run / "objects" / obj / "model_print.stl"
    mesh = trimesh.load(stl)
    m = at.measure_mesh(mesh, float(mesh.extents[2]))  # per-object print STLs from desk.py have no base plate
    return {"id": cid, "kind": "scene", "run": rel(run), "command": command, "input": {"video": rel(video), "sha256": sha(video)},
            "stl": {"path": rel(stl), "sha256": sha(stl), "bbox_mm": [round(float(x), 1) for x in mesh.extents]},
            "measure_method": "scripts/accuracy_table.measure_mesh (object STL without a base plate)",
            "measured_mm": {k: round(v, 2) for k, v in m.items() if k.endswith("_mm") and v is not None}, "ruler": score(m, MUG),
            "report_copy": copy_report(run / "desk_report.json", f"{cid}_desk_report.json"), "scene_copy": copy_report(run / "scene.json", f"{cid}_scene.json")}


def mapmode_claim(cid, run, video, command, model_name, skip=()):
    objs = at.load_model_objects({}, [run])
    mo = next(v for k, v in objs.items() if v["name"] == model_name)
    spec = next(o for o in json.loads((run / "scene.json").read_text(encoding="utf-8"))["objects"] if o["name"] == model_name)
    stl = run / spec["stl"]
    return {"id": cid, "kind": "scene (map mode)", "run": rel(run), "command": command, "input": {"video": rel(video), "sha256": sha(video)},
            "stl": {"object": model_name, "path": rel(stl), "sha256": sha(stl), "bbox_mm": [round(float(x), 1) for x in trimesh.load(stl).extents]}, "scale_source": mo["method"],
            "measure_method": "scripts/accuracy_table.load_model_objects (scene object STL, base plate excluded)",
            "measured_mm": {k: round(v, 2) for k, v in mo["dims"].items() if k.endswith("_mm") and v is not None}, "ruler": score(mo["dims"], MUG, skip),
            "report_copy": copy_report(run / "scene_report.json", f"{cid}_scene_report.json"), "scene_copy": copy_report(run / "scene.json", f"{cid}_scene.json")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mug_v = SAMPLES / "mug_on_table.mp4"
    claims = [
        ("a", object_claim("object_mug_coin", RUNS / "mug_grounded", mug_v,
                           "python -m worldkit.object samples/mug_on_table.mp4 --out runs/mug_grounded --object mug --coin saudi-25-halalas "
                           "--mesh-backend import --mesh-file runs/final_e2e/model.glb   (reconstructed: argv is not logged; the report records the imported "
                           "SAM 3D mesh from runs/final_e2e, sha256 in the copied report)")),
        ("a2", object_claim("object_mug_coin_web_run", RUNS / "web" / "999042e1-e1a2-4b49-861e-ca449f14d8a8" / "run", mug_v,
                            "python -m worldkit.object video.mp4 --out run --object mug --coin saudi-25-halalas   (run by python -m worldkit.worker --once "
                            "on a real upload to the live site, 2026-11-04; no dry run; a fresh SAM 3D Objects mesh. The worker's downloaded video is "
                            "byte-identical to mug_on_table.mp4; see docs/results/e2e_web_real.json)")),
        ("b", mapmode_claim("scene_desk_coin_v1", RUNS / "desk_coin", SAMPLES / "desk_scene_coin.mp4",
                            "python -m worldkit.scene samples/desk_scene_coin.mp4 --out runs/desk_coin --coin saudi-25-halalas --vggt --fresh --keep-frames 20", "striped mug")),
        ("c", mapmode_claim("scene_desk_card_v1", RUNS / "desk_card", SAMPLES / "desk_scene_card.mp4",
                            "python -m worldkit.scene samples/desk_scene_card.mp4 --out runs/desk_card --card --vggt --prefer-vggt ... (the full flag list is in the copied "
                            "scene_report.json; argv is not logged)", "striped cup")),
        ("d", scene_stl_claim("scene_desk_v2", RUNS / "desk_v2", SAMPLES / "desk_scene_coin.mp4",
                              "python -m worldkit.desk samples/desk_scene_coin.mp4 --out runs/desk_v2")),
        ("e", scene_stl_claim("scene_desk_card3", RUNS / "desk_card3", SAMPLES / "desk_scene_card.mp4",
                              'python -m worldkit.desk samples/desk_scene_card.mp4 --out runs/desk_card3 --ref card --fps 1.0 --objects mug case burner bottle --legs '
                              '--concept "burner=silver lantern" --spark-rounds 1')),
        ("f", mapmode_claim("scene_desk_mugref", RUNS / "desk_mugref", SAMPLES / "desk_scene_coin.mp4",
                            'python -m worldkit.scene samples/desk_scene_coin.mp4 --out runs/desk_mugref --ref "striped mug=70mm"', "striped mug", skip=("height_mm",))),
    ]
    for key, c in claims:
        c["section"] = key
        (OUT / f"{c['id']}.json").write_text(json.dumps(c, indent=1), encoding="utf-8")
        print(key, c["id"], {k: v["model_mm"] for k, v in c["ruler"]["dimensions"].items()})


if __name__ == "__main__":
    main()
