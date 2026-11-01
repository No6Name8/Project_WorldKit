"""AI Edit: an instruction in, a precise, verified, undoable change to the print mesh out.

    python -m worldkit.edit <model_dir> "<instruction>"        # one edit  (model_dir = a run dir with model_print.stl)
    python -m worldkit.edit <model_dir> --undo [N]             # step back one version, or to version N
    python -m worldkit.edit <model_dir> --list

How it works. Muse Wonder (``muse-wonder-1.0``, Meta Model API, Chat Completions function calling) receives the instruction and
the model's measured facts and calls the geometry tools of ``edit_tools`` (it never edits geometry itself). The tools run on the
mesh deterministically. Then (1) deterministic checks (watertight, size/volume expectations per tool) and (2) Muse Spark looking at
before/after renders (``verify``) must both pass. If not, Wonder gets the feedback and tries ONCE more from the unedited model; if
that also fails the edit is recorded as rejected and the model is left untouched. Every applied edit is a new version
(edits/v1.stl, v2.stl, ...) in ``edits/versions.json`` with the instruction, tool calls, verdict, token usage and timing; undo moves the
current pointer to any earlier version and nothing is ever deleted. Units are mm (the pipeline's STL is cm; v0 converts it).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

from . import edit_tools as et
from .meta_api import MUSE_SPARK_MODEL, MetaAPIError, MetaAPIUnavailable, ask_muse_spark_with_usage, get_client

WONDER_MODEL = "muse-wonder-1.0"  # Meta Model API docs (dev.meta.ai/docs): Muse Wonder, Standard mode, preview
MAX_TOOL_TURNS = 6


class EditFailed(RuntimeError):
    pass


# --- tool schemas (OpenAI/Meta Chat Completions function-tool format) -----------------------------------------------------

def _fn(name, description, props, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
                                              "parameters": {"type": "object", "properties": props, "required": list(required)}}}


TOOLS = [
    _fn("scale_to", "Uniformly scale the whole model. Give EITHER height_cm (the new total height in centimetres) OR factor.",
        {"height_cm": {"type": "number", "description": "target total height in cm"}, "factor": {"type": "number", "description": "uniform scale factor"}}),
    _fn("emboss_text", "Put text on the model. face='base' ENGRAVES it into the underside (so it still sits flat; readable when flipped over); "
        "face='front' RAISES it on the -y side. The text is sized automatically to fit.",
        {"text": {"type": "string"}, "face": {"type": "string", "enum": ["base", "front"]}, "depth_mm": {"type": "number", "description": "0.1-3, default 0.6"},
         "mode": {"type": "string", "enum": ["raise", "engrave"], "description": "optional override"}}, ["text"]),
    _fn("drill_hole", "Drill a cylindrical hole. The axis passes through position (mm) along direction. through=true cuts all the way through whatever it meets. "
        "For a keychain hole through the handle use the handle's bar_center_point_mm as position and its plane_normal as direction.",
        {"diameter_mm": {"type": "number"}, "position": {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] mm"},
         "direction": {"type": "array", "items": {"type": "number"}, "description": "[dx,dy,dz], default [0,0,1]"}, "through": {"type": "boolean"},
         "depth_mm": {"type": "number", "description": "for through=false"}}, ["diameter_mm", "position"]),
    _fn("flatten_base", "Shave depth_mm off the underside so the base is one exactly flat plane (the model gets that much shorter).", {"depth_mm": {"type": "number"}}),
    _fn("split", "Cut with a plane axis=offset_mm and keep one half (capped, watertight).",
        {"axis": {"type": "string", "enum": ["x", "y", "z"]}, "offset_mm": {"type": "number"}, "keep": {"type": "string", "enum": ["upper", "lower"]}}),
    _fn("thicken", "Grow walls thinner than min_wall_mm (the outer size grows slightly).", {"min_wall_mm": {"type": "number"}}, ["min_wall_mm"]),
    _fn("mirror", "Mirror the model about the middle of its bounding box along an axis.", {"axis": {"type": "string", "enum": ["x", "y", "z"]}}, ["axis"]),
]

SYSTEM = (
    "You edit a 3D-printable model by calling the provided geometry tools. You cannot edit geometry any other way. "
    "All lengths are millimetres unless a parameter says cm; the model's base sits on z=0 and z is up. Use the measured model facts "
    "(sizes, handle position, base) to choose arguments; never invent coordinates when a fact gives them. Do exactly what the instruction "
    "asks and nothing else. Call the needed tools (several in one turn is fine), and when they have succeeded reply with ONE short sentence "
    "saying what you did. If the instruction cannot be done with the tools, call no tool and say why in one sentence."
)


# --- rendering (small software renderer; fast for 100k faces) ---------------------------------------------------------------

def render_view(mesh: trimesh.Trimesh, eye_dir, up=(0, 0, 1), focus=None, zoom=1.0, frame_radius=None, size=420) -> Image.Image:
    """Orthographic flat-shaded render looking from ``eye_dir`` toward ``focus`` (default bbox centre)."""
    eye = np.asarray(eye_dir, float)
    eye /= np.linalg.norm(eye)
    up = np.asarray(up, float)
    right = np.cross(up, eye)
    if np.linalg.norm(right) < 1e-6:
        right = np.cross([0, 1, 0], eye)
    right /= np.linalg.norm(right)
    upv = np.cross(eye, right)
    c = np.asarray(focus if focus is not None else (mesh.bounds[0] + mesh.bounds[1]) / 2, float)
    radius = frame_radius or float(np.linalg.norm(mesh.extents)) / 2
    s = 0.46 * size / radius * zoom
    rel = mesh.vertices - c
    xy = np.stack([size / 2 + s * (rel @ right), size / 2 - s * (rel @ upv)], axis=1)
    depth = rel @ eye
    f = mesh.faces
    fn = mesh.face_normals
    light = (eye * 0.9 + upv * 0.5 + right * 0.3)
    light /= np.linalg.norm(light)
    shade = 0.28 + 0.72 * np.clip(fn @ light, 0, 1)
    facing = fn @ eye > -0.02
    order = np.argsort(depth[f].mean(axis=1))
    img = Image.new("RGB", (size, size), (233, 236, 241))
    zimg = Image.new("F", (size, size), -1e6)  # per-pixel depth (larger = nearer), for relief shading
    d, dz = ImageDraw.Draw(img), ImageDraw.Draw(zimg)
    for i in order:
        if not facing[i]:
            continue
        poly = [tuple(p) for p in xy[f[i]]]
        v = int(255 * shade[i])
        d.polygon(poly, fill=(int(v * 0.72), int(v * 0.8), v))
        dz.polygon(poly, fill=float(depth[f[i]].mean()))
    # relief shading (a cheap ambient-occlusion look): engraved/recessed areas darken, raised areas lighten. Without it a flat-on view
    # of text cut into a flat base is invisible, because the pocket floor and the base face the same way and are lit identically.
    from scipy import ndimage

    z = np.asarray(zimg, dtype=np.float32)
    valid = z > -1e5
    zf = np.where(valid, z, np.nan)
    fill = np.where(valid, z, np.nanmedian(zf) if valid.any() else 0)
    rel = fill - ndimage.gaussian_filter(fill, 7)
    rel = np.where(valid, rel, 0)
    k = np.clip(rel / max(radius * 0.01, 0.05), -1, 1)  # +-1 at +-1% of the model radius of relief
    arr = np.asarray(img).astype(np.float32)
    arr[valid] *= (1 + 0.45 * k[valid])[:, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _views_for(calls: list[dict], before: trimesh.Trimesh, after: trimesh.Trimesh) -> list[dict]:
    """Which camera(s) show the change best: close-ups for local edits, two general views otherwise."""
    gen = [{"name": "front-right", "eye": (0.7, -1.0, 0.6)}, {"name": "back-left", "eye": (-0.7, 1.0, 0.6)}]
    out = []
    for c in calls:
        a = c["arguments"]
        if c["name"] == "emboss_text" and a.get("face", "base") == "base":
            ctr = (before.bounds[0] + before.bounds[1]) / 2
            out.append({"name": "underside (looking up at the base)", "eye": (0, 0, -1), "up": (0, 1, 0), "focus": (ctr[0], ctr[1], 0.0), "zoom": 1.9})
        elif c["name"] == "emboss_text":
            out.append({"name": "front close-up", "eye": (0, -1, 0.1), "focus": ((before.bounds[0][0] + before.bounds[1][0]) / 2, before.bounds[0][1], 0.5 * before.extents[2]), "zoom": 1.6})
        elif c["name"] == "drill_hole" and a.get("position") is not None:
            n = np.asarray(a.get("direction") or (0, 0, 1), float)
            out.append({"name": "hole close-up (looking along its axis)", "eye": tuple(n), "focus": tuple(a["position"]), "zoom": 2.2})
    return (out[:1] + gen[:1]) if out else gen


def render_comparison(before, after, calls, out_dir: Path, tag: str) -> list[Path]:
    paths = []
    radius = float(np.linalg.norm(np.maximum(before.extents, after.extents))) / 2
    for i, v in enumerate(_views_for(calls, before, after)):
        kw = dict(eye_dir=v["eye"], up=v.get("up", (0, 0, 1)), focus=v.get("focus"), zoom=v.get("zoom", 1.0), frame_radius=radius)
        a, b = render_view(before, **kw), render_view(after, **kw)
        comp = Image.new("RGB", (2 * a.width + 12, a.height + 26), (255, 255, 255))
        comp.paste(a, (0, 26))
        comp.paste(b, (a.width + 12, 26))
        dr = ImageDraw.Draw(comp)
        dr.text((6, 6), f"BEFORE  -  {v['name']}", fill=(0, 0, 0))
        dr.text((a.width + 18, 6), "AFTER", fill=(0, 0, 0))
        p = out_dir / f"{tag}_view{i}.png"
        comp.save(p)
        paths.append(p)
    return paths


# --- versions ----------------------------------------------------------------------------------------------------------------

def _paths(model_dir: Path):
    ed = Path(model_dir) / "edits"
    return ed, ed / "versions.json"


def _load(model_dir: Path) -> dict:
    ed, vj = _paths(model_dir)
    if vj.is_file():
        return json.loads(vj.read_text(encoding="utf-8"))
    src = Path(model_dir) / "model_print.stl"
    if not src.is_file():
        raise EditFailed(f"{model_dir} has no model_print.stl (run worldkit.object first)")
    ed.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.load(src, force="mesh")
    rp0 = Path(model_dir) / "report.json"
    stl_units = (json.loads(rp0.read_text(encoding="utf-8")).get("print_prep", {}).get("stl_units") if rp0.is_file() else None) or "cm"
    if stl_units != "mm":
        mesh.apply_scale(10.0)  # runs made before the units fix wrote centimetres; the edit tools work in millimetres
    mesh.apply_translation([0, 0, -mesh.bounds[0][2]])
    mesh.export(ed / "v0.stl")
    scale_note = None
    rp = Path(model_dir) / "report.json"
    if rp.is_file():
        pp = json.loads(rp.read_text(encoding="utf-8")).get("print_prep", {})
        scale_note = f"height source: {pp.get('height_source')}" + (" (a PLACEHOLDER size, not a measurement)" if pp.get("is_estimate") else "")
    data = {"units": "mm", "source": "model_print.stl" + ("" if stl_units == "mm" else " x10 (legacy cm STL)"), "scale_note": scale_note, "current": 0, "rejected": [],
            "versions": [{"version": 0, "parent": None, "instruction": None, "stl": "edits/v0.stl", "created": time.strftime("%Y-%m-%dT%H:%M:%S")}]}
    vj.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def _save(model_dir: Path, data: dict) -> None:
    _paths(model_dir)[1].write_text(json.dumps(data, indent=1, default=float), encoding="utf-8")


def current_version(model_dir: Path) -> dict:
    d = _load(Path(model_dir))
    return next(v for v in d["versions"] if v["version"] == d["current"])


def current_mesh(model_dir: Path) -> trimesh.Trimesh:
    return trimesh.load(Path(model_dir) / current_version(model_dir)["stl"], force="mesh")


def undo(model_dir: Path, to: int | None = None) -> dict:
    """Move the current pointer back one version (or to version ``to``). Versions are never deleted."""
    model_dir = Path(model_dir)
    d = _load(model_dir)
    cur = next(v for v in d["versions"] if v["version"] == d["current"])
    if to is None:
        if cur["parent"] is None:
            raise EditFailed("already at the original model (v0): nothing to undo")
        to = cur["parent"]
    if not any(v["version"] == to for v in d["versions"]):
        raise EditFailed(f"no version {to}; have {[v['version'] for v in d['versions']]}")
    d["current"] = to
    _save(model_dir, d)
    return next(v for v in d["versions"] if v["version"] == to)


# --- verification ----------------------------------------------------------------------------------------------------------

def deterministic_checks(before: trimesh.Trimesh, after: trimesh.Trimesh, calls: list[dict]) -> list[dict]:
    chk = [{"name": "watertight solid", "ok": bool(after.is_watertight and after.volume > 0), "detail": f"volume {after.volume:.0f} mm3"},
           {"name": "base on z=0", "ok": abs(after.bounds[0][2]) < 0.05, "detail": f"min z {after.bounds[0][2]:.3f}"}]
    for c in calls:
        a, n = c["arguments"], c["name"]
        dv = after.volume - before.volume
        if n == "scale_to":
            want = a.get("height_cm", 0) * 10 if a.get("height_cm") else before.extents[2] * a.get("factor", 1)
            chk.append({"name": "height as requested", "ok": abs(after.extents[2] - want) <= max(0.1, 0.005 * want), "detail": f"{after.extents[2]:.2f} mm vs {want:.2f} mm"})
        elif n == "emboss_text":
            engr = (a.get("mode") or ("engrave" if a.get("face", "base") == "base" else "raise")) == "engrave"
            chk.append({"name": "text changed the solid in the right direction", "ok": (dv < -0.2) if engr else (dv > 0.2), "detail": f"volume change {dv:+.1f} mm3"})
            if a.get("face", "base") == "base":
                chk.append({"name": "base text kept the outline and height", "ok": bool(np.allclose(after.extents, before.extents, atol=0.1)), "detail": str(np.round(after.extents - before.extents, 2).tolist())})
        elif n == "drill_hole":
            chk.append({"name": "hole removed material", "ok": dv < -0.5, "detail": f"volume change {dv:+.1f} mm3"})
            chk.append({"name": "hole did not enlarge the model", "ok": bool(np.all(after.extents <= before.extents + 0.05)), "detail": ""})
        elif n == "flatten_base":
            chk.append({"name": "base shaved by depth", "ok": abs((before.extents[2] - after.extents[2]) - a.get("depth_mm", 0.5)) < 0.05, "detail": f"{before.extents[2] - after.extents[2]:.2f} mm"})
    return chk


SPARK_PROMPT = """You are verifying a 3D-printing model edit by looking at BEFORE | AFTER renders ({n} image(s); each shows the same camera before the edit on the left and after on the right).
Instruction given by the user: "{instruction}"
Tools that were applied (exact): {calls}
Measured change: size before {sb} mm, after {sa} mm.
Judge ONLY from the images and the numbers: (1) does the AFTER model show what the instruction asked for? (2) did anything else change that was not asked for?
Small engraved text or small holes can be hard to see: look carefully at the close-up. If you cannot tell, say so and lower your confidence.
Reply with ONLY one JSON object: {{"matches_instruction": true|false, "unintended_changes": true|false, "confidence": 0.0-1.0, "reason": "one short sentence"}}"""


def verify(before, after, instruction, calls, images: list[Path], ask=ask_muse_spark_with_usage) -> tuple[dict, dict | None]:
    """Muse Spark's visual verdict. Returns (verdict, usage). Unavailable API -> verdict {'available': False}."""
    prompt = SPARK_PROMPT.format(n=len(images), instruction=instruction, calls=json.dumps([{"tool": c["name"], "arguments": c["arguments"]} for c in calls]),
                                 sb=np.round(before.extents, 1).tolist(), sa=np.round(after.extents, 1).tolist())
    try:
        text, usage = ask(images, prompt)
    except MetaAPIUnavailable as exc:
        return {"available": False, "reason": str(exc)}, None
    import re

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"available": True, "parse_error": text[:200], "matches_instruction": False, "unintended_changes": True, "confidence": 0.0, "reason": "unparseable verdict"}, usage
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"available": True, "parse_error": text[:200], "matches_instruction": False, "unintended_changes": True, "confidence": 0.0, "reason": "unparseable verdict"}, usage
    v["available"] = True
    return v, usage


# --- Wonder tool-calling step -----------------------------------------------------------------------------------------------

def _usage_add(acc: dict, usage) -> None:
    if not usage:
        return
    u = usage if isinstance(usage, dict) else (usage.model_dump() if hasattr(usage, "model_dump") else dict(usage))
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        acc[k] = acc.get(k, 0) + int(u.get(k) or 0)
    details = u.get("completion_tokens_details") or {}
    acc["reasoning_tokens"] = acc.get("reasoning_tokens", 0) + int((details.get("reasoning_tokens") if isinstance(details, dict) else 0) or 0)


def wonder_tool_step(client, messages: list[dict], mesh: trimesh.Trimesh, usage_acc: dict):
    """One bounded tool-calling conversation: Wonder calls tools, we run them on ``mesh`` and answer, until it stops calling tools.
    Returns (final mesh, calls[{name, arguments, result|error}], assistant_text). Wonder is called at most MAX_TOOL_TURNS times."""
    calls: list[dict] = []
    text = ""
    work = mesh
    for _ in range(MAX_TOOL_TURNS):
        try:
            resp = client.chat.completions.create(model=WONDER_MODEL, messages=messages, tools=TOOLS, max_tokens=16000)
        except Exception as exc:  # noqa: BLE001
            raise MetaAPIError(f"Muse Wonder request failed: {exc}") from exc
        _usage_add(usage_acc, getattr(resp, "usage", None))
        msg = resp.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        text = msg.content or ""
        if not tcs and not text and resp.choices[0].finish_reason == "length":
            raise MetaAPIError("Muse Wonder ran out of output tokens while reasoning (finish_reason=length)")
        if not tcs:
            break
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in tcs]})
        for t in tcs:
            try:
                args = json.loads(t.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            rec = {"name": t.function.name, "arguments": args}
            if args is None:
                rec["error"] = "arguments were not valid JSON"
                reply = {"ok": False, "error": rec["error"]}
            else:
                try:
                    work, summ = et.apply_tool(work, t.function.name, args)
                    rec["result"] = summ
                    reply = summ
                except et.EditError as exc:
                    rec["error"] = str(exc)
                    reply = {"ok": False, "error": str(exc)}
            calls.append(rec)
            messages.append({"role": "tool", "tool_call_id": t.id, "content": json.dumps(reply)})
    return work, calls, text


def run_edit(model_dir: Path, instruction: str, *, client=None, ask_spark=ask_muse_spark_with_usage, retries: int = 1) -> dict:
    """Apply one instruction. Returns the version record (applied) or the rejection record. Never leaves the model half-edited."""
    t0 = time.time()
    model_dir = Path(model_dir)
    data = _load(model_dir)
    ed = model_dir / "edits"
    cur = next(v for v in data["versions"] if v["version"] == data["current"])
    before = trimesh.load(model_dir / cur["stl"], force="mesh")
    facts = et.model_facts(before)
    facts["current_version"] = cur["version"]
    if data.get("scale_note"):
        facts["scale_note"] = data["scale_note"]
    client = client or get_client()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Instruction: {instruction}\n\nMeasured model facts (JSON):\n{json.dumps(facts)}"}]
    wonder_usage, spark_usage = {}, {}
    attempts = []
    n_next = max(v["version"] for v in data["versions"]) + 1
    for attempt in range(retries + 1):
        after, calls, text = wonder_tool_step(client, messages, before, wonder_usage)
        ok_calls = [c for c in calls if "error" not in c]
        rec = {"attempt": attempt, "assistant": text, "tool_calls": calls}
        if not ok_calls:
            rec["verdict"] = {"passed": False, "reason": "no successful tool call: " + (text or "; ".join(c.get("error", "") for c in calls))[:300]}
            attempts.append(rec)
            if not calls:  # the model declined; retrying would not change that
                break
            messages.append({"role": "user", "content": "None of your tool calls succeeded. Fix the arguments using the errors above and try again."})
            continue
        det = deterministic_checks(before, after, ok_calls)
        images = render_comparison(before, after, ok_calls, ed, f"v{n_next}_try{attempt}")
        spark, usage = verify(before, after, instruction, ok_calls, images, ask=ask_spark)
        _usage_add(spark_usage, usage)
        det_ok = all(c["ok"] for c in det)
        spark_ok = (not spark.get("available", True)) or (bool(spark.get("matches_instruction")) and not spark.get("unintended_changes"))
        passed = det_ok and spark_ok
        why = [c["name"] + ": " + c["detail"] for c in det if not c["ok"]]
        if spark.get("available", True) and not spark_ok:
            why.append("Muse Spark: " + str(spark.get("reason")))
        rec.update({"deterministic": det, "spark": spark, "images": [str(p.relative_to(model_dir)) for p in images],
                    "verdict": {"passed": passed, "spark_verified": bool(spark.get("available", True)), "reason": "; ".join(why) or "all checks passed"}})
        attempts.append(rec)
        if passed:
            stl = ed / f"v{n_next}.stl"
            after.export(stl)
            version = {"version": n_next, "parent": cur["version"], "instruction": instruction, "stl": f"edits/v{n_next}.stl",
                       "tool_calls": ok_calls, "assistant": text, "verdict": rec["verdict"], "spark": spark, "deterministic": det,
                       "renders": rec["images"], "retries": attempt, "attempts": len(attempts), "facts_before": facts,
                       "cost": {"wonder_tokens": wonder_usage, "spark_tokens": spark_usage, "usd": None,
                                "note": "token counts from the API; no price was looked up"},
                       "seconds": round(time.time() - t0, 1), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "size_mm": [round(float(x), 2) for x in after.extents]}
            data["versions"].append(version)
            data["current"] = n_next
            _save(model_dir, data)
            return {**version, "applied": True}
        if attempt < retries:
            messages.append({"role": "user", "content": "The edit was REJECTED by verification and the model has been reset to the version before it. "
                             f"Problems: {rec['verdict']['reason']}. Try again from the unedited model, correcting the cause."})
    rejected = {"instruction": instruction, "from_version": cur["version"], "attempts": attempts, "seconds": round(time.time() - t0, 1),
                "cost": {"wonder_tokens": wonder_usage, "spark_tokens": spark_usage, "usd": None}, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data["rejected"].append(rejected)
    _save(model_dir, data)
    return {**rejected, "applied": False, "verdict": attempts[-1]["verdict"] if attempts else {"passed": False, "reason": "no attempt ran"}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI Edit: instruction -> verified, undoable change to the print mesh")
    ap.add_argument("model_dir", type=Path)
    ap.add_argument("instruction", nargs="?")
    ap.add_argument("--undo", nargs="?", const=-1, type=int, metavar="N", help="step back one version, or to version N")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.list:
            d = _load(a.model_dir)
            for v in d["versions"]:
                print(("* " if v["version"] == d["current"] else "  ") + f"v{v['version']}  {v.get('instruction') or '(original)'}")
            return 0
        if a.undo is not None:
            v = undo(a.model_dir, None if a.undo == -1 else a.undo)
            print(f"current version is now v{v['version']}  ({v.get('instruction') or 'original'})")
            return 0
        if not a.instruction:
            ap.error("give an instruction, or --undo / --list")
        r = run_edit(a.model_dir, a.instruction)
        calls = ", ".join(f"{c['name']}({json.dumps(c['arguments'])})" for c in r.get("tool_calls", []) or r["attempts"][-1].get("tool_calls", []))
        if r["applied"]:
            print(f"APPLIED as v{r['version']} in {r['seconds']}s: {calls}\n  verdict: {r['verdict']['reason']}  | Spark: {r['spark'].get('reason')}")
            print(f"  renders: {', '.join(r['renders'])}")
            return 0
        print(f"REJECTED (model unchanged): {r['verdict']['reason']}")
        return 1
    except (EditFailed, MetaAPIError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
