"""Real-vs-model accuracy table.   python scripts/accuracy_table.py [--runs mug=pipeline/runs/mug_grounded ...] [--scenes pipeline/runs/desk]

Loads pipeline/samples/ground_truth.json (the user's ruler measurements; EVALUATION ONLY, never used to scale or tune anything) and the print
STLs (millimetres, Z up) of the given runs/scenes, matches objects by name, and writes docs/accuracy.md.

Model dimensions are measured on the exported STL with the base plate excluded (the body's bottom is zmax - report height). Circular
dimensions come from a trimmed least-squares circle fit of a horizontal slice, so a handle does not inflate them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
GT_PATH = ROOT / "pipeline" / "samples" / "ground_truth.json"
OUT_MD = ROOT / "docs" / "accuracy.md"
DEFAULT_RUNS = {"mug": ROOT / "pipeline" / "runs" / "mug_grounded"}


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def fit_circle(xy: np.ndarray) -> float | None:
    """Diameter of a trimmed least-squares circle (drops handle/outlier points); None if too few points."""
    pts = np.asarray(xy, float)
    for _ in range(4):
        if len(pts) < 8:
            return None
        A = np.c_[2 * pts, np.ones(len(pts))]
        b = (pts ** 2).sum(1)
        c, *_ = np.linalg.lstsq(A, b, rcond=None)
        r = float(np.sqrt(max(c[2] + c[0] ** 2 + c[1] ** 2, 0)))
        res = np.abs(np.linalg.norm(pts - c[:2], axis=1) - r)
        keep = res < max(0.05 * r, 0.4)
        if keep.all():
            break
        pts = pts[keep]
    return 2 * r


def slice_xy(mesh: trimesh.Trimesh, z: float) -> np.ndarray | None:
    s = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return None
    pts = []
    for e in s.discrete:
        pts.append(e[:, :2])
    return np.vstack(pts) if pts else None


def footprint_rect(mesh: trimesh.Trimesh, z0: float, z1: float) -> tuple[float, float]:
    """Smallest-area rectangle (long, short side) around the horizontal footprint between z0 and z1."""
    v = mesh.vertices[(mesh.vertices[:, 2] >= z0) & (mesh.vertices[:, 2] <= z1)][:, :2]
    best = None
    for a in np.deg2rad(np.arange(0, 90, 1.0)):
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        w = v @ R
        ext = w.max(0) - w.min(0)
        if best is None or ext[0] * ext[1] < best[0]:
            best = (ext[0] * ext[1], sorted(ext, reverse=True))
    return float(best[1][0]), float(best[1][1])


def measure(stl: Path, body_height_mm: float | None) -> dict:
    return measure_mesh(trimesh.load(stl), body_height_mm)


def measure_mesh(m: trimesh.Trimesh, body_height_mm: float | None) -> dict:
    zmax = float(m.bounds[1][2])
    zmin_all = float(m.bounds[0][2])
    if body_height_mm is None:
        raise SystemExit("no recorded body height (report missing); cannot exclude the base plate")
    zb = zmax - body_height_mm  # top of the added base plate = bottom of the object
    out = {"height_mm": zmax - zb, "_plate_mm": zb - zmin_all}
    z_rim = zmax - 0.04 * body_height_mm
    z_base = zb + 0.03 * body_height_mm
    for key, z in (("rim_diameter_mm", z_rim), ("base_diameter_mm", z_base)):
        xy = slice_xy(m, z)
        out[key] = fit_circle(xy) if xy is not None else None
    mid = [fit_circle(slice_xy(m, zb + f * body_height_mm)) for f in np.linspace(0.1, 0.9, 9) if slice_xy(m, zb + f * body_height_mm) is not None]
    mid = [d for d in mid if d]
    out["diameter_mm"] = float(max(mid)) if mid else None  # widest body circle
    long_, short = footprint_rect(m, zb + 0.05 * body_height_mm, zmax)
    out["width_mm"], out["depth_mm"] = long_, short
    return out


def load_model_objects(runs: dict[str, Path], scenes: list[Path]) -> dict[str, dict]:
    found = {}
    for name, d in runs.items():
        rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
        h = (rep.get("measurement") or {}).get("height_mm")
        found[norm(name)] = {"name": name, "source": str(d), "dims": measure(d / "model_print.stl", h), "method": (rep.get("measurement") or {}).get("method")}
    for sd in scenes:
        man = json.loads((sd / "scene.json").read_text(encoding="utf-8"))
        for o in man["objects"]:
            if not o.get("stl"):
                continue
            rep = json.loads((sd / o["report"]).read_text(encoding="utf-8")) if o.get("report") and (sd / o["report"]).is_file() else {}
            h = (rep.get("measurement") or {}).get("height_mm") or (o.get("size_cm") or [0, 0, 0])[1] * 10 or None
            found[norm(o["name"])] = {"name": o["name"], "source": str(sd), "dims": measure(sd / o["stl"], h), "is_ref": bool(o.get("is_scale_reference")),
                                      "method": (man.get("scale_source") or {}).get("text", "scene").replace("scale source: ", "")}
    return found


def from_results(results_dir: Path, out: Path) -> str:
    """docs/accuracy.md generated from docs/results/*.json (one file per claim, written by scripts/make_results.py) and the page's prose
    (docs/results/accuracy_text.json). Every number in a table comes from a claim file."""
    import re

    text = json.loads((results_dir / "accuracy_text.json").read_text(encoding="utf-8"))
    claims = {}
    for f in sorted(results_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("section"):
            claims[d["section"]] = d
    lines = [text["intro"], ""]
    names = {"height_mm": "height", "rim_diameter_mm": "rim_diameter", "base_diameter_mm": "base_diameter"}
    for key, sec in text["sections"].items():
        lines += [f"## {sec['title']}", ""]
        if sec.get("before"):
            lines += [sec["before"], ""]
        c = claims.get(key)
        if c and sec.get("scale_label"):
            lines += ["| object | dimension | real mm | model mm | error % | model scale from |", "|---|---|---:|---:|---:|---|"]
            for dim, v in c["ruler"]["dimensions"].items():
                lines.append(f"| {sec['object_label']} | {names[dim]} | {v['ruler_mm']:g} | {v['model_mm']:.1f} | {v['error_pct']:+.1f} | {sec['scale_label']} |")
            lines.append("")
            after = re.sub(r"Mean absolute error [\d.]+%, worst [\d.]+%", f"Mean absolute error {c['ruler']['mean_abs_error_pct']}%, worst {c['ruler']['worst_abs_error_pct']}%",
                           sec.get("after", ""), count=1)
            if after:
                lines += [after, ""]
            lines += [f"Source: `docs/results/{c['id']}.json` (command, input SHA-256, measured STL SHA-256).", ""]
        elif key.startswith("Side by side"):
            cols = [k for k in "abcde" if k in claims]
            hdr = {"a": "a) coin, Object mode", "b": "b) coin, scene mode", "c": "c) card, scene mode", "d": "d) coin, scene v2", "e": "e) card, three laps"}
            lines += ["| dimension | real mm | " + " | ".join(hdr[k] for k in cols) + " |", "|---|---:|" + "---:|" * len(cols)]
            for dim, label in (("height_mm", "height"), ("rim_diameter_mm", "rim diameter"), ("base_diameter_mm", "base diameter")):
                vals = [claims[k]["ruler"]["dimensions"].get(dim) for k in cols]
                real = next(v["ruler_mm"] for v in vals if v)
                lines.append(f"| {label} | {real:g} | " + " | ".join(f"{v['model_mm']:.1f} ({v['error_pct']:+.1f}%)" if v else "n/a" for v in vals) + " |")
            lines.append("")
        elif sec.get("after"):
            lines += [sec["after"], ""]
    md = "\n".join(lines).rstrip() + "\n"
    out.write_text(md, encoding="utf-8")
    return md


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=None, help="name=run_dir (an object-mode run with model_print.stl + report.json)")
    ap.add_argument("--scenes", nargs="*", default=[], help="scene folders (scene.json from worldkit.scene)")
    ap.add_argument("--truth", type=Path, default=GT_PATH)
    ap.add_argument("--out", type=Path, default=OUT_MD)
    ap.add_argument("--alias", action="append", default=[], help='model name = ground-truth name, e.g. "striped cup=mug" (the discovery step names objects freely)')
    ap.add_argument("--from-results", nargs="?", const=str(ROOT / "docs" / "results"), default=None,
                    help="generate docs/accuracy.md from docs/results/*.json (written by scripts/make_results.py) instead of measuring runs")
    a = ap.parse_args(argv)
    if a.from_results:
        md = from_results(Path(a.from_results), a.out)
        print(md)
        return 0
    runs = DEFAULT_RUNS if a.runs is None else {kv.split("=", 1)[0]: Path(kv.split("=", 1)[1]) for kv in a.runs}
    truth = json.loads(a.truth.read_text(encoding="utf-8"))
    model = load_model_objects({k: v for k, v in runs.items() if (v / "model_print.stl").is_file()}, [Path(s) for s in a.scenes])

    for al in a.alias:
        src, dst = (norm(x) for x in al.split("=", 1))
        if src in model and dst not in model:
            model[dst] = {**model[src], "alias_of": model[src]["name"]}
            del model[src]
    rows, unmatched_model, unmatched_truth, references, unscored = [], set(model), [], [], []
    for name, gt in truth["objects"].items():
        mo = model.get(norm(name)) or next((v for k, v in model.items() if set(norm(name).split()) <= set(k.split())), None)  # 'mug' matches 'striped mug'
        dims = {k: v for k, v in gt.items() if k.endswith("_mm") and v is not None}
        if not mo:
            unmatched_truth.append(name)
            continue
        unmatched_model.discard(norm(mo['name']))
        if mo.get("is_ref"):
            references.append(name)  # a scale reference never scores the dimension it defines (its height); its other dimensions are still real checks
            dims = {k: v for k, v in dims.items() if k != "height_mm"}
        if not dims:
            unscored.append(name)
            continue
        for k, real in dims.items():
            got = mo["dims"].get(k)
            rows.append((name + (f" (named '{mo['alias_of']}' by discovery)" if mo.get("alias_of") else ""), k, real, got, None if got is None else 100.0 * (got - real) / real, mo["method"]))
    coin = truth.get("coin", {})
    lines = ["# Real vs model accuracy", "",
             f"Ground truth: `{a.truth.relative_to(ROOT)}` (ruler measurements; evaluation only, never used to scale or tune). Model sizes: print STLs in mm, base plate excluded.", "",
             "| object | dimension | real mm | model mm | error % | model scale from |", "|---|---|---:|---:|---:|---|"]
    for n, k, real, got, err, meth in rows:
        lines.append(f"| {n} | {k[:-3]} | {real:g} | {'n/a' if got is None else f'{got:.1f}'} | {'n/a' if err is None else f'{err:+.1f}'} | {meth} |")
    if rows:
        errs = [abs(r[4]) for r in rows if r[4] is not None]
        lines += ["", f"Mean absolute error {np.mean(errs):.1f}% over {len(errs)} dimensions; worst {max(errs):.1f}%."]
    if references:
        lines += ["", "**Scale references (not scored, they define the scale):** " + ", ".join(references)]
    if unscored:
        lines += ["", "**Unscored (ground truth is null, not measured yet):** " + ", ".join(unscored)]
    if unmatched_truth:
        lines += ["", "**Ground-truth objects with no model output (unmatched):** " + ", ".join(unmatched_truth)]
    if unmatched_model:
        lines += ["", "**Model objects with no ground-truth entry (unscored):** " + ", ".join(f"{model[k]['name']} ({model[k]['dims']['height_mm']:.0f} mm tall{', scale reference' if model[k].get('is_ref') else ''})" for k in sorted(unmatched_model))]
    if coin.get("diameter_mm") is None:
        lines += ["", "Coin: no ruler measurement recorded in ground_truth.json; the preset diameter is a looked-up value."]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
