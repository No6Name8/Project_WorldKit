"""docs/agent_traces/: one JSON per AI Edit agent run, copied from the stored records (no model call, nothing filled in).

    pipeline\\.venv\\Scripts\\python.exe scripts/make_agent_traces.py

Sources:
- a-e: pipeline/runs/rehearse/edits/versions.json (server record of the 2026-11-03 rehearsal, run 1 of each instruction: v1, v3, v5, v7, v9),
  docs/demo_edits.json (client record: stages the panel showed, client seconds), viewer/assets/demo/edit_replay/replay.json (handle wall
  measured after the fact with edit_tools.part_wall_mm by scripts/make_edit_replay.py).
- refusal_wonder_declined: pipeline/runs/part_live/results.json (real Muse Wonder run, 2026-11-02, an earlier code version in which
  thicken_part could only thicken).
- refusal_wall_floor: edit_tools.apply_tool run here on the real rehearsal mesh and its SAM part (no model call): the 1.2 mm floor.
A field the stored records do not have is left out, never estimated.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
OUT = ROOT / "docs" / "agent_traces"
RUN = ROOT / "pipeline" / "runs" / "rehearse"
EDITS = {"a": 1, "b": 3, "c": 5, "d": 7, "e": 9}


def sam_coverage(v: dict) -> dict | None:
    for c in v["tool_calls"]:
        p = (c.get("result") or {}).get("part")
        if p:
            return {"part": p["part"], "views_found": p["views_found"], "views_total": p["views_total"], "source": "tool result (thicken_part)"}
    for p in (v.get("facts_before") or {}).get("named_parts", []):
        return {"part": p["id"], "views_found": p["sam_views_found"], "views_total": p["sam_views_total"],
                "source": "model facts given to Muse Wonder (the tool used did not need the part)"}
    return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    vers = {v["version"]: v for v in json.loads((RUN / "edits" / "versions.json").read_text(encoding="utf-8"))["versions"]}
    client = {(e["id"], e["run"]): e for e in json.loads((ROOT / "docs" / "demo_edits.json").read_text(encoding="utf-8"))}
    replay = {e["id"]: e for e in json.loads((ROOT / "viewer" / "assets" / "demo" / "edit_replay" / "replay.json").read_text(encoding="utf-8"))["edits"]}
    for eid, n in EDITS.items():
        v = vers[n]
        c = client.get((eid, 1), {})
        cost = v.get("cost") or {}
        t = {"trace": f"rehearsal_{eid}", "kind": "real run (Muse Wonder muse-wonder-1.0 and Muse Spark muse-spark-1.3, Meta Model API)",
             "created": v["created"], "run_dir": "pipeline/runs/rehearse (a copy of runs/mug_grounded; local, gitignored)",
             "instruction": v["instruction"], "version_id": v["version"], "parent_version_id": v["parent"],
             "wonder_tool_calls": [{"name": x["name"], "arguments": x["arguments"], "result_sentence": (x.get("result") or {}).get("sentence"),
                                    "result_ok": (x.get("result") or {}).get("ok")} for x in v["tool_calls"]],
             "wonder_reply": v.get("assistant"), "sam_coverage": sam_coverage(v),
             "deterministic_checks": v["deterministic"],
             "spark_verdict": {k: v["spark"].get(k) for k in ("matches_instruction", "unintended_changes", "confidence", "reason")},
             "decision": "accepted" if v["verdict"]["passed"] else "rejected", "decision_reason": v["verdict"]["reason"],
             "retries": v["retries"], "attempts": v["attempts"], "seconds_server": v["seconds"],
             "tokens": {"muse_wonder": cost.get("wonder_tokens"), "muse_spark": cost.get("spark_tokens")},
             "size_mm_before": (v["tool_calls"][0].get("result") or {}).get("size_mm_before"), "size_mm_after": v.get("size_mm"),
             "renders": [r.replace("\\", "/") for r in v["renders"]],
             "sources": ["pipeline/runs/rehearse/edits/versions.json"]}
        if c:
            t["client"] = {"stages_seen": c.get("stages_seen"), "seconds_client": c.get("seconds_client")}
            t["sources"].append("docs/demo_edits.json")
        r = replay.get(eid, {})
        if r.get("renders"):
            t["renders_in_repo"] = [f"viewer/assets/demo/edit_replay/{x}" for x in r["renders"]]  # JPEG copies of the same renders
        if r.get("handle_wall_mm"):
            t["handle_wall_mm_before_after"] = r["handle_wall_mm"]
            t["handle_wall_source"] = "measured after the run with edit_tools.part_wall_mm (scripts/make_edit_replay.py)"
            t["sources"].append("viewer/assets/demo/edit_replay/replay.json")
        (OUT / f"edit_{eid}.json").write_text(json.dumps(t, indent=1), encoding="utf-8")

    # real refusal: Muse Wonder declined (no tool fitted, in that code version)
    pl = json.loads((ROOT / "pipeline" / "runs" / "part_live" / "results.json").read_text(encoding="utf-8"))
    r3 = next(r for r in pl["results"] if r["n"] == 3)
    (OUT / "refusal_wonder_declined.json").write_text(json.dumps({
        "trace": "refusal_wonder_declined", "kind": "real run (Muse Wonder, Meta Model API), 2026-11-02",
        "note": "Recorded with an earlier code version in which thicken_part could only thicken. thicken_part has accepted negative by_mm since "
                "2026-11-02, and the same instruction was applied in the 2026-11-03 rehearsal (edit_c.json).",
        "instruction": r3["instruction"], "wonder_tool_calls": r3["tools"], "wonder_reply": r3["wonder_text"], "wonder_calls": r3["wonder_calls"],
        "spark_calls": r3["spark_calls"], "decision": "refused (no tool call; the model is unchanged)", "decision_reason": r3["reason"],
        "attempts": r3["attempts"], "seconds_server": r3["seconds"], "handle_wall_mm_before_after": [r3["handle_mm_before"], r3["handle_mm_after"]],
        "sources": ["pipeline/runs/part_live/results.json (scripts/part_edit_live.py)"]}, indent=1), encoding="utf-8")

    # tool-level refusal: the 1.2 mm wall floor, executed here on the real rehearsal mesh (no model call)
    import trimesh

    from worldkit import edit as ed
    from worldkit import edit_tools as et

    mesh = trimesh.load(RUN / vers[0]["stl"], force="mesh")
    et.set_parts(ed.load_parts(RUN))
    wall = et.part_wall_mm(mesh, et.PARTS["handle"]["stl_points"])
    args = {"part": "handle", "by_mm": -4.5}
    try:
        et.apply_tool(mesh, "thicken_part", args)
        res = {"refused": False}
    except et.EditError as exc:
        res = {"refused": True, "tool_error_returned_to_wonder": str(exc)}
    (OUT / "refusal_wall_floor.json").write_text(json.dumps({
        "trace": "refusal_wall_floor", "kind": "tool-level check executed locally on the real rehearsal mesh (v0) and its SAM 3.1 handle selection; no model call",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool_call": {"name": "thicken_part", "arguments": args},
        "handle_wall_mm_before": round(wall, 2) if wall else None, "min_wall_mm": et.MIN_WALL_MM, **res,
        "what_happens_in_the_agent": "worldkit.edit.wonder_tool_step returns the error to Muse Wonder as the tool result; with no successful call the "
                                     "edit is recorded under 'rejected' in versions.json and the model is not changed (edit.run_edit).",
        "sources": ["pipeline/worldkit/edit_tools.py: thicken_part, MIN_WALL_MM", "scripts/make_agent_traces.py"]}, indent=1), encoding="utf-8")
    print("wrote", sorted(p.name for p in OUT.glob("*.json")), "| wall", round(wall, 2) if wall else None, res)


if __name__ == "__main__":
    main()
