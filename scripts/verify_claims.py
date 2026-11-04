"""Checks the repo's claims against its own files. No API calls, no GPU, about a minute or less.

    pipeline\\.venv\\Scripts\\python.exe scripts/verify_claims.py        (or: python scripts/verify_claims.py with numpy + trimesh installed)

Prints PASS / FAIL / SKIP per check and exits 1 if any check fails:
1. every accuracy claim in docs/results/*.json: each error % recomputed from the stored model and ruler values, and the mean and worst error
2. docs/accuracy.md regenerated from docs/results (scripts/accuracy_table.py --from-results) is identical to the committed file and to the
   viewer's copy (viewer/assets/demo/accuracy.md)
3. SHA-256 of every input video and STL a claim names, when the file is present (runs are local and gitignored, so a missing file is SKIP)
4. the README headline numbers (mug, two runs) match docs/results and docs/accuracy.md
5. the AI Edit traces match the five-edit tables in docs/AGENTIC.md and docs/DEMO_EDITS.md (tool, seconds)
6. the Muse Spark Open agreement file is complete if the docs claim it
7. every relative link and image in README.md, AGENTS.md, llms.txt and docs/**/*.md points to a file that exists
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "docs" / "results"
sys.path.insert(0, str(ROOT / "scripts"))
out: list[tuple[str, str, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    out.append((status, name, detail))
    print(f"{status} - {name}" + (f" :: {detail}" if detail else ""))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def claims() -> dict:
    d = {}
    for f in sorted(RESULTS.glob("*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(j, dict) and isinstance(j.get("ruler"), dict) and j["ruler"].get("dimensions"):
            d[f.stem] = j
    return d


def check_errors(cs: dict) -> None:
    for cid, c in cs.items():
        bad = []
        meas = c.get("measured_mm") or {}
        for dim, v in c["ruler"]["dimensions"].items():
            m = meas.get(dim, v["model_mm"])  # the unrounded measurement when stored
            err = round(100.0 * (m - v["ruler_mm"]) / v["ruler_mm"], 1)
            if abs(err - v["error_pct"]) > 0.051 or abs(m - v["model_mm"]) > 0.051:
                bad.append(f"{dim}: stored {v['model_mm']} ({v['error_pct']:+}%), recomputed {m:.2f} ({err:+}%)")
        errs = [abs(v["error_pct"]) for v in c["ruler"]["dimensions"].values()]
        mean = round(sum(errs) / len(errs), 1)
        if abs(mean - c["ruler"]["mean_abs_error_pct"]) > 0.051 or max(errs) != c["ruler"]["worst_abs_error_pct"]:
            bad.append(f"mean/worst stored {c['ruler']['mean_abs_error_pct']}/{c['ruler']['worst_abs_error_pct']}, recomputed {mean}/{max(errs)}")
        check(f"accuracy claim {cid}: error % recomputed from model and ruler values", not bad, "; ".join(bad) or
              ", ".join(f"{k.replace('_mm', '')} {v['model_mm']} ({v['error_pct']:+}%)" for k, v in c["ruler"]["dimensions"].items()))


def check_accuracy_md() -> None:
    import accuracy_table as at

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "accuracy.md"
        md = at.from_results(RESULTS, tmp)
    norm = lambda s: s.replace("\r\n", "\n")  # noqa: E731
    committed = norm((ROOT / "docs" / "accuracy.md").read_text(encoding="utf-8"))
    viewer = norm((ROOT / "viewer" / "assets" / "demo" / "accuracy.md").read_text(encoding="utf-8"))
    check("docs/accuracy.md is exactly what docs/results generates", norm(md) == committed)
    check("the viewer's Accuracy page (viewer/assets/demo/accuracy.md) is the same file", viewer == committed)


def check_hashes(cs: dict) -> None:
    files = {}
    for c in list(cs.values()) + [json.loads(f.read_text(encoding="utf-8")) for f in RESULTS.glob("*.json") if f.stem not in cs]:
        if not isinstance(c, dict):
            continue
        for key in ("input", "stl"):
            x = c.get(key)
            if isinstance(x, dict) and x.get("sha256"):
                p = x.get("path") or x.get("video")
                if p:
                    files.setdefault(p, set()).add(x["sha256"])
    for rel, hashes in sorted(files.items()):
        p = ROOT / rel if (ROOT / rel).exists() else ROOT / "pipeline" / rel
        if not p.is_file():
            check(f"SHA-256 of {rel}", None, "file not in this checkout (runs and most videos are local, gitignored)")
            continue
        h = sha(p)
        check(f"SHA-256 of {rel}", h in hashes, h[:16] + ("" if h in hashes else f" vs recorded {sorted(hashes)[0][:16]}"))


def check_readme_numbers(cs: dict) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    acc = (ROOT / "docs" / "accuracy.md").read_text(encoding="utf-8")
    r1, r2 = cs.get("object_mug_coin"), cs.get("object_mug_coin_web_run")
    if not (r1 and r2):
        check("README headline numbers", False, "object_mug_coin / object_mug_coin_web_run missing in docs/results")
        return
    rows = {"height_mm": "Height", "rim_diameter_mm": "Rim diameter", "base_diameter_mm": "Base diameter"}
    bad = []
    for dim, label in rows.items():
        a, b = r1["ruler"]["dimensions"][dim], r2["ruler"]["dimensions"][dim]
        want = f"| {label} | {a['ruler_mm']:g} mm | {a['model_mm']:.1f} mm ({a['error_pct']:+.1f}%) | {b['model_mm']:.1f} mm ({b['error_pct']:+.1f}%) |"
        if want not in readme:
            bad.append(f"README row missing: {want}")
        for v in (a, b):
            if f"{v['model_mm']:.1f} ({v['error_pct']:+.1f}%)" not in acc:
                bad.append(f"accuracy.md lacks {v['model_mm']:.1f} ({v['error_pct']:+.1f}%)")
    for c in (r1, r2):
        if f"{c['ruler']['mean_abs_error_pct']}%" not in readme:
            bad.append(f"README lacks mean error {c['ruler']['mean_abs_error_pct']}%")
    check("README headline table (two mug runs) matches docs/results and docs/accuracy.md", not bad, "; ".join(bad) or
          f"run 1 mean {r1['ruler']['mean_abs_error_pct']}%, run 2 mean {r2['ruler']['mean_abs_error_pct']}%")


def check_traces() -> None:
    tdir = ROOT / "docs" / "agent_traces"
    agentic = (ROOT / "docs" / "AGENTIC.md").read_text(encoding="utf-8")
    demo = (ROOT / "docs" / "DEMO_EDITS.md").read_text(encoding="utf-8")
    bad, n = [], 0
    for f in sorted(tdir.glob("edit_*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        n += 1
        tool = t["wonder_tool_calls"][0]["name"]
        row = lambda text: next((l for l in text.splitlines() if re.match(r"^\| [a-e] \|", l) and t["instruction"] in l), "")  # noqa: E731  the five-edit table rows
        line, dline = row(agentic), row(demo)
        if f"`{tool}`" not in line or f"{t['seconds_server']} /" not in line:
            bad.append(f"{f.name}: AGENTIC.md row does not show {tool} / {t['seconds_server']} s")
        if f"`{tool}`" not in dline or f"{t['seconds_server']} /" not in dline:
            bad.append(f"{f.name}: DEMO_EDITS.md row does not show {tool} / {t['seconds_server']} s")
        if t["spark_verdict"]["reason"].rstrip(".") not in line:
            bad.append(f"{f.name}: Spark sentence not in AGENTIC.md")
    check("AI Edit traces agree with the five-edit tables (tool, seconds, Spark sentence)", n == 5 and not bad, "; ".join(bad) or f"{n} traces")


def check_spark_open() -> None:
    f = RESULTS / "spark_open_agreement.json"
    claimed = any("spark_open_agreement" in p.read_text(encoding="utf-8") for p in [ROOT / "README.md", ROOT / "docs" / "MODELS.md", ROOT / "docs" / "JUDGING.md"] if p.is_file())
    if not f.is_file():
        check("Muse Spark Open agreement file", None if not claimed else False, "not run" if not claimed else "the docs cite it but the file is missing")
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = d["rows"]
    ok = d["completed"] == len(rows) == 5 and d["agree"] == sum(1 for r in rows if r["agree"])
    check("Muse Spark Open agreement: 5 verifications completed and the agreement count matches its rows", ok, f"completed {d['completed']}/{len(rows)}, agree {d['agree']}")


LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def check_links() -> None:
    files = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "llms.txt"] + sorted((ROOT / "docs").rglob("*.md"))
    bad, n = [], 0
    for f in files:
        if not f.is_file():
            continue
        text = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)  # links inside code blocks are examples
        for m in LINK.finditer(text):
            target = m.group(1)
            if re.match(r"^[a-z]+:", target) or target.startswith("#") or target.startswith("<"):
                continue
            path = target.split("#")[0].split("?")[0]
            if not path:
                continue
            n += 1
            if not (f.parent / path).exists():
                bad.append(f"{f.relative_to(ROOT).as_posix()} -> {target}")
    check("every relative link and image in README.md, AGENTS.md, llms.txt and docs/ exists", not bad, f"{n} links checked" + (": missing " + "; ".join(bad[:8]) if bad else ""))


def main() -> int:
    cs = claims()
    check_errors(cs)
    check_accuracy_md()
    check_hashes(cs)
    check_readme_numbers(cs)
    check_traces()
    check_spark_open()
    check_links()
    fails = [o for o in out if o[0] == "FAIL"]
    print(f"\n{sum(o[0] == 'PASS' for o in out)} PASS, {len(fails)} FAIL, {sum(o[0] == 'SKIP' for o in out)} SKIP")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
