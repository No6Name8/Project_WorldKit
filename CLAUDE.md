# CLAUDE.md

Instructions for Claude Code in this repository. Read first each session.

## Goal

**WorldKit turns real-world video into the most accurate, realistic, useful 3D reconstruction we can build** — objects first, then scenes — that people can inspect, edit, export and simulate. Everything else serves that. Quality of the reconstruction is the priority; features, tooling and docs come second.

Context: Meta Global AI Developer Hackathon 2026 (build Oct 27 – Nov 4; submission Nov 4, 9 PM ET). Judging: impact 30%, Meta AI tooling 25%, technical execution 20%, demo 25%. Meta models in use: Muse Spark (vision/reasoning), SAM 3.1 (segmentation), SAM 3D Objects (reconstruction); VGGT / Muse Wonder / Muse Image are unused so far. Use whatever model or method gives the best result, Meta or not; just record which one produced what.

## How to work

- **Think for yourself.** Find what is actually wrong, form hypotheses, test them, pick the approach most likely to give the best result. Don't wait for step-by-step direction.
- **Everything is changeable.** Existing code, defaults, file layout and earlier decisions can be improved, replaced or deleted when there is a good reason. Try better models, methods and architectures; don't keep a weak approach because it already exists.
- **Keep what works.** Don't break unrelated features, but backward compatibility must not block an important improvement.
- **Judge the real result.** Passing tests is not enough. Look at the 3D model (renders from several angles, side-by-side with the source frames), measure it, and use real footage. Synthetic scenes are for unit tests, not for claims. Check metrics against what you see; if they disagree, the picture wins until explained.
- **Be efficient, not stingy.** Reuse caches and outputs, test locally before GPU. But don't skip a quality improvement just to save time or compute.
- **Be honest.** Say what works, what failed, what was really run (real data, real GPU, real API, browser, or only mocks), and what is still uncertain. Never present generated geometry or texture as measured, and never report a number that wasn't measured.

## Hard safeguards (always)

- Never expose, print, log or commit API keys or secrets. Keys live in gitignored `pipeline/.env`.
- No purchases, paid plans, credit top-ups or new out-of-pocket spending without explicit approval. Spending existing credits on compute is fine; check usage first (`modal billing summary`) and stop if charges beyond credits look possible.
- No `git commit` / `git push` without approval. Don't delete the user's source videos or run outputs they may want; `pipeline/runs/` is local and gitignored.

## Repo and commands (Windows)

- `pipeline/worldkit/` Python pipeline: `object.py` (CLI), `scene.py` (multi-object), `detect`/`segment` (Muse Spark, SAM 3.1), `reconstruct.py` (hosted TRELLIS/Hunyuan), `modal_app/sam3d.py` (SAM 3D on Modal A100), `texture_project`, `cyl_texture`, `pattern_model`, `fidelity`, `understand`, `mesh_qc`, `qc` (reconstruction quality), `scale_cal`, `printprep`, `appearance`, `editcmd`, `webserver`.
- `viewer/` three.js viewer (`index.html`, `physics.js` Rapier, `server.py`), `viewer/tests/*.js` Playwright tests. `viewer/assets/demo/` and `viewer/vendor/` are tracked on purpose (offline demo, no CDN). `docs/`: `TECHNICAL_NOTES.md` (measurements/diagnosis), `DEMO_RUNBOOK.md`, `SUBMISSION.md`, comparison images.
- Python 3.10: `pipeline\.venv\Scripts\python.exe`. Tests: `python -m pytest pipeline/tests` (about 3.5 min). Browser tests: `cd viewer\tests; npm install; npm run test:all` with the viewer server running (`python viewer\server.py`); they use the bundled demo assets, so they run from a fresh clone. `python viewer\server.py --open RUN_DIR` loads a pipeline output.
- AI Edit: `python -m worldkit.edit RUN_DIR "instruction"` / `--undo [N]` / `--list`; `python -m worldkit.serve RUN_DIR` (FastAPI viewer + `/edit`, `/undo`, `/edit/current.stl`). Edit units are mm (the pipeline STL is cm; v0 converts). Muse Wonder tool calling: Chat Completions `tools`, `tool_choice` only `auto`, echo the assistant `tool_calls` message and answer each with a `role=tool` message; it spends many reasoning tokens (use `max_tokens` ~16000).
- CLI: `python -m worldkit.object VIDEO --out pipeline\runs\NAME --object mug [--mesh-backend modal|trellis|hosted|import] [--fidelity auto] [--ref-diameter-cm D | --height-cm H]`. Scene: `python -m worldkit.scene VIDEO --out DIR --objects mug "round side table"`.
- Modal: workspace `no6name8`; SAM 3D needs `modal run "modal_app/sam3d.py::check_env"` (CPU preflight) to pass first; GPU is A100-40GB, `max_containers=1`, 30 min timeout, about $2.10/h, a full object run costs cents. Meta Model API is OpenAI-SDK compatible at `https://api.meta.ai/v1`.
- Real test footage: `pipeline/samples/WhatsApp Video 2026-10-26 at 9.32.38 PM.mp4` (mug + coin on a round table, 480x856, 16 s).

## State of the system (2026-11-01)

Working and verified on the mug video: video -> frames -> Muse Spark -> SAM 3.1 -> SAM 3D (default backend; TRELLIS is the no-GPU fallback) -> finalize/print prep -> viewer (orbit, gizmos, Muse Spark edits, collision probe, export) ; two-object scene (mug on table); Rapier rigid-body physics; coin-based scale estimate; opt-in `--fidelity auto` stripe-pattern model with a held-out check; **AI Edit** (`worldkit.edit` / `worldkit.serve`): Muse Wonder `muse-wonder-1.0` calls geometry tools (`edit_tools.py`: scale_to, emboss_text, drill_hole, flatten_base, split, thicken, mirror) on the print mesh, Muse Spark verifies before/after renders (plus deterministic checks), one retry, versions + undo in `edits/versions.json`, chat panel and Undo/Download STL in the viewer. Python tests and 6 browser suites pass (143 browser checks).

Measured facts worth knowing (details in `docs/TECHNICAL_NOTES.md` and `pipeline/runs/fidelity_round1/`):
- SAM 3D mug geometry is good (watertight, loop handle, level circular rim: planarity 0.9% of radius, 0.26 deg vs base); TRELLIS is lumpier with an uneven rim. All geometry is generated, not measured.
- SAM 3D's own texture draws straight stripes where the real mug has 14 wavy ones. Cross-validated stripe correlation on held-out frames: generated 0.68, UV projection 0.80 (fold-dependent), cylindrical stripe model 0.84. Mug stripe model covers 37% of the surface as observed, 3% inferred, 61% generated.
- Scale from a coin is validated only on synthetic scenes (mean 3.7% error); real accuracy is unverified because the coin and mug were never measured. `print_ready` is true only with a user-measured height plus passing geometry checks.
- Physics uses a convex-hull collider for dynamic objects and exact triangle mesh for static ones; not engineering-accurate.

Pitfalls learned the hard way:
- **Silhouette-only camera poses get elevation wrong by up to 30 deg.** Refine poses photometrically against a texture before using them to project or unwrap.
- **`runs/quality/eval_projection.get_views` caches poses by `sys.argv`, not by mesh.** SAM 3D evaluations once silently used TRELLIS poses and every number from them was wrong. Use `worldkit/qc.py`, which fits poses to the mesh being judged. Don't trust old SAM 3D texture figures.
- Correlating periodic patterns aliases onto the neighbouring stripe unless shifts are bounded; fit body radius from the outer wall only (inner wall biases it).
- Verify visually: bugs found only by looking include invisible simulation clones, a tumbled mug that "penetrated" the ground, and rendered-from-below textures.
- Modal image: needs `CONDA_PREFIX`, libGL, setuptools 80.9.0, nvdiffrast precompiled for sm_80, and the mip-splatting `diff_gaussian_rasterization`; the CPU `check_env` and A100 `gpu_smoke` entry points test these.

- **AI Edit gotchas:** engraved text viewed straight on is invisible in a plainly shaded render (pocket floor and base face the same way): the verifier renderer uses relief shading and a straight-on view, and per-face mean depth fakes geometry on big skinny triangles under oblique views. `printprep` adds a flat plate wider than the object, so measure the body footprint above it (`edit_tools.body_footprint`), not at z=0. Merging vertices of an already-watertight manifold output pinches it non-manifold. trimesh voxelising is far too slow (188 s); use manifold3d Minkowski sums.
- **Dev environment:** stale `viewer/server.py` processes keep serving old code on port 5050 (killing the netstat PID is not enough on Windows; kill by command line). Node's `fetch` refuses ports 5060/5061.
- **Known upstream bug:** `model_print.stl` is written in cm but slicers read STL as mm; AI Edit versions are true mm, the pipeline STL is not.

## Where to push next (ideas, not orders)

Biggest gaps: texture/appearance fidelity beyond stripes (general patterns, gloss), geometry that is measured rather than generated (multi-view or pose-grounded reconstruction, VGGT, depth), scenes with more than two objects and real layout, real-world scale validated against a physical measurement, a full 360-degree capture protocol, and a stronger evaluation harness (held-out views, silhouette and depth agreement, side-by-side renders). Pick what most improves the reconstruction; replace the current approach if something is better.
