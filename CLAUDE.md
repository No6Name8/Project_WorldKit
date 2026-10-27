# CLAUDE.md

Instructions for Claude Code. Read this first in every session. Treat this file as the current product direction and execution priorities.

## Product direction

**WorldKit — Film reality. Build worlds. Run simulations.**

WorldKit transforms real-world video into editable 3D environments that people can inspect, modify, and use to test scenarios. It is not fundamentally a 3D-printing app. Printing may remain a downstream export/use case, but the main vision is **video → reconstructed digital world → AI-assisted editing → simulation**.

Long-term audiences include game developers, engineers, robotics teams, urban planners, and digital-twin/simulation developers. Do not claim the hackathon build supports all these use cases. Demonstrate one small, real, end-to-end workflow and explain how it can expand.

### Hackathon target

Meta Global AI Developer Hackathon 2026. Build period Oct 27–Nov 4, 2026. Submission deadline Nov 4, 9:00 PM Eastern / Nov 5, 5:00 AM Riyadh. Solo project. Judging weights: Impact 30%, Meta AI tooling 25%, Technical execution 20%, Demo 25%.

The target is a credible, working proof of the core idea—not a giant unfinished simulator:
1. Start with **legally reusable real-world footage** found online or the existing phone object video. Do not depend on user-filmed drone footage; drone filming is not an option for the user in Saudi Arabia. Verify the license/terms of any downloaded footage and retain attribution/source.
2. Produce **genuine reconstructed 3D geometry** from the footage. Never substitute a hand-built/test mesh, static illustration, or pre-rendered mockup and present it as reconstruction.
3. Show the real reconstruction in an interactive viewer.
4. Use Meta AI in meaningful, visible roles: SAM 3.1 segmentation/object selection; Muse Spark for scene/request understanding and visual comparison/verification; SAM 3D Objects for reconstruction if access/compute is available. Muse Image is optional only if it adds a real, demonstrated capability. Do not add tools just to increase model count.
5. Implement one reliable edit to actual geometry and validate it with code where possible.
6. If time and genuine scene geometry permit, add **one small real simulation**—for example, collision-aware path planning through obstacles. It must run on actual collision geometry and show a real computed outcome, not just animate an object. If this cannot be completed reliably, show interactive scene editing and label simulation as future direction.
7. Export a usable scene/model, show measurable evidence, document limitations, and make a clear 3-minute demo.

### What success means

Show the source footage, the actual reconstructed output, a real object selection/edit, a verifiable result, and ideally one genuine simulation. Report real processing time and measurements only. Distinguish measured facts from estimates. Never claim an entire city simulator, engineering-grade accuracy, or support for every domain unless actually implemented and validated.

## Priority order (deadline-aware)

1. **Unblock and prove genuine video-to-3D reconstruction.** This is the main technical risk. Resolve model/checkpoint access and compute safely. Test with the existing mug footage first if that is the fastest path to a real mesh; then assess whether a small urban scene is feasible.
2. **Build one complete, visible workflow:** real footage → real mesh → interactive viewer → one actual geometry edit → validation/export.
3. **Integrate Meta models coherently:** SAM 3.1 mask/selection; Muse Spark interpreting intent and comparing before/after; SAM 3D Objects genuine reconstruction if accessible. Record actual model outputs and provenance.
4. **Only then add one simple simulation** if the scene has usable geometry/colliders. Prefer a small collision-aware path-planning scenario with a measurable result over a broad, superficial simulator.
5. **Make the 3-minute demo reliable and persuasive:** source footage, reconstruction, edit, verification, outcome, and larger vision. Prepare a recording backup if live inference is unreliable; recording must show genuine output.
6. UI polish, extra export formats, broad map mode, 3D-print refinements, and additional models are secondary to the above.

Do not spend time on unrelated features before the reconstruction works. Do not repeatedly run tiny tests that do not resolve a real blocker; run meaningful end-to-end validation when feasible.

## Safety, budget, and truthfulness

- Never launch paid GPU compute, add a payment method, upgrade a subscription, or raise a spend limit without the user's explicit approval.
- User's Modal dashboard previously showed $0 usage and only $1 included monthly credit; the advertised $100 hackathon credit was not confirmed. Do not assume it arrived.
- Hugging Face gated access for `facebook/sam-3d-objects` is unverified. No local NVIDIA GPU is available. Meta Model API and Baseten connectivity have returned HTTP 200, but Baseten's configured Muse Spark endpoint is not SAM 3D.
- Do not expose API keys or commit secrets. Keys are in gitignored `pipeline/.env`; never print their values.
- Never fabricate reconstruction output, metrics, timing, accuracy, user validation, or capabilities. If blocked, report the exact blocker and choose the best genuine fallback.
- Keep dependencies, runtime, and claims aligned with what was actually tested.

## Current repository / environment

Public repo: https://github.com/No6Name8/Project_WorldKit
Folders: `/app` Next.js/react-three-fiber PWA, `/pipeline` Python pipeline, `/docs` documentation.
Windows; use Python 3.10 via `py -3.10` or `pipeline\.venv\Scripts\python.exe`, not `python3`.
Existing CLI:
```powershell
pipeline\.venv\Scripts\python.exe -m worldkit.object pipeline\samples\<video> --out pipeline\runs\<name> --object "<object>" --height-cm <measured-height>
```
Real phone sample: `pipeline/samples/WhatsApp Video 2026-10-26 at 9.32.38 PM.mp4` (16 sec, 480×856, mug and coin on round table). It is available for a genuine object-level proof.

## Verified status (2026-10-27)

- Pipeline package exists: `frames.py`, `meta_api.py`, `detect.py`, `segment.py`, `modal_app/sam3d.py`, `printprep.py`, `render.py`, and `object.py`.
- Latest reported test run: **31/31 unit tests pass**, primarily synthetic/regression tests. Re-run after meaningful changes.
- Frame extraction on the real video works: 12 sharp frames in about 0.67 sec.
- Genuine Muse Spark API call returned HTTP 200. On an unprompted frame it called the main object “side table,” not “mug”; the explicit `--object mug` override works. Best-frame selection over the full set using Muse Spark remains unverified.
- Genuine SAM 3.1 API segmentation was tested on the real mug frame and isolated the mug including its handle (14,394 mask pixels). Corrected a prior mask-placement bug. A SHA-256 content-keyed cache reuses only genuine non-fallback masks for the exact image+concept; provenance is recorded in `report.json`.
- Local fallback segmentation is imperfect and is dev-only; it can miss parts of the mug. Never represent it as SAM output.
- **Critical: no genuine 3D reconstruction has been completed yet.** `SAM 3D Objects` has not been run against a real checkpoint/image. Any `TEMP_TEST_*` mesh is a hand-built test shape, not a reconstruction. Print-prep/preview were only tested on that synthetic shape.
- SAM 3D's Modal path requires an A100-40GB GPU; previous rate check showed $2.10/hour and a 30-minute function timeout (up to $1.05 GPU runtime, excluding other costs). Checkpoint download currently occurs inside the GPU function. Modal credit balance is unconfirmed; do not launch GPU without approval and confirmed budget.
- Hugging Face gated checkpoint access through the Modal secret is unverified. Any CPU-only access test may incur a small charge and also requires user approval before execution.
- No automatic coin-based scale recovery exists; the mug currently requires user-provided `--height-cm`. A4 scale estimation exists in code but has not been validated for the mug sample.
- Do not claim the end-to-end CLI, reconstruction, scene simulation, or full app workflow works until each has actually been run and inspected.

## Meta tools and intended roles

- **Muse Spark (Meta Model API):** scene/object understanding, interpret user intent into validated edit instructions, and compare before/after renders. Visual verification is not proof of exact geometry.
- **SAM 3.1 (Meta Model API):** segment/track target objects from frames.
- **SAM 3D Objects (Meta open weights):** genuine object reconstruction if gated access and compute are resolved.
- **VGGT (Meta open weights):** potential camera-pose/scene geometry support only if it can be integrated and validated within the deadline.
- **Muse Image:** optional creative visual edits/assets only where it materially improves the workflow.
- Do not claim a model was used in a particular run unless logs/provenance confirm it.

## Demo structure (3 minutes)

- 0:00–0:25 — show source footage and the problem.
- 0:25–1:05 — show genuine reconstruction.
- 1:05–1:55 — select an object and make a real edit with visible Meta AI contribution.
- 1:55–2:30 — show before/after, code-based validation, and any real simulation result.
- 2:30–3:00 — show usable export and explain the broader WorldKit vision.
Adjust timing to fit the actual strongest working flow. Never pad the demo with unimplemented features.

## Product positioning

“WorldKit transforms real-world video into editable 3D environments people can use to build, modify, and simulate real-world scenarios.”

The hackathon demo is the first proof of this platform, not the complete platform. Do not claim WorldKit is first or unprecedented. Focus on the integrated workflow and what genuinely works.

## Status update (2026-10-27, later)

**Modal image build for SAM 3D Objects now builds successfully end to end.** HF gated access to `facebook/sam-3d-objects` is confirmed working (two CPU-only checks, no GPU cost: `model_info()` returned `ACCESS_OK siblings=31`; checkpoint size measured at 13.17 GB). The image build itself needed 16 real, logged attempts to fix a chain of genuine dependency-build defects — PEP 517 build isolation hiding torch/pip from several packages' setup.py (`nvidia-pyindex`, `flash-attn`, `pytorch3d`, `gsplat`), a missing `clang++`, pip re-resolving already-installed pinned git-URL dependencies, and `gsplat`'s CUDA arch-detection code needing a live GPU that isn't attached during image build (fixed with `TORCH_CUDA_ARCH_LIST`). Full fix-by-fix detail is in the `modal_app/sam3d.py` commit history/inline comments. **Zero GPU-rate cost across all attempts** — every failure/success happened during the image-build phase, before the A100 is ever attached.

**Reconstruction is now blocked by a real Modal account requirement, not credit balance:** `modal run` reaches the GPU-attach step and fails with `InvalidError('Please add a payment method to use A100-40GB GPU functions.')`. Per the safety rule above, no payment method was added. `_ensure_checkpoints()`/`reconstruct_mesh` were never reached — no checkpoint download, no inference attempt, no mesh produced. 31/31 tests passed as of that check.

## Status update (2026-10-27, later still) — first genuine mesh, via a Modal-independent fallback

**A real, genuine mesh of the mug now exists, generated without Modal.** Since Modal's SAM 3D Objects path stayed blocked on the payment-method requirement above (not worked around), `worldkit/reconstruct.py` was added as a Modal-independent reconstruction fallback that calls a free, publicly hosted Hugging Face Space (`frogleo/Image-to-3D`) running **Hunyuan3D-2 — a Tencent open-weights model, NOT SAM 3D Objects and NOT a Meta model.** Provenance is recorded accurately everywhere (`report.json`'s `mesh_backend`/`mesh_method`; never represented as Meta/SAM 3D output). The official `stabilityai/TripoSR` Space (and its most-liked forks) were checked first and are dead (`RUNTIME_ERROR`/`BUILD_ERROR`/`CONFIG_ERROR` on the HF Spaces API) — not something fixable from here, so the search moved to the next viable free, running Space rather than looping on it, per instruction.

**Real defect found and fixed: naive full-frame input produced garbage.** Sending SAM 3.1's `object_rgba.png` as-is (the real mug occupies only 155x140px of the 480x856 frame) produced an unrecognizable, non-mug shape (visually confirmed, discarded). Cropping tightly to the real alpha bounding box, padding 15%, squaring, and upscaling to 1024x1024 (`reconstruct.crop_to_alpha_bbox`) before sending produced a clearly recognizable mug **with a closed handle loop and a visible interior cavity** (confirmed visually from multiple angles, not just mesh stats) — watertight, 4176 vertices / 10000 faces.

**Mesh validated, not claimed print-ready:** `printprep.prepare_for_print` ran successfully (watertight, flat base added, oriented, STL exported — `runs/mug_local/model.glb` → `runs/mug_local/model_print.stl`, both gitignored/local), but 1151 of 1500 sampled surface points are thinner than the 1.2mm minimum-wall threshold — the reconstruction is a thin single-surface shell, not a solid with real wall thickness, so **`print_ready: false`** is recorded explicitly in `runs/mug_local/report.json`, not glossed over. No real-world height was measured for this mug this session (no `--height-cm`, no A4 estimate), so scale uses the honest `default_fallback` path (`is_estimate: true`, 10cm placeholder) — cm dimensions in the report are explicitly flagged as not measured.

**`object.py` gained a `--mesh-backend {modal,hosted,import}` flag** (default `modal`, unchanged): `hosted` calls the fallback above; `import` accepts an already-generated `--mesh-file` (GLB/OBJ) and skips reconstruction entirely, running printprep/preview on it directly. SAM 3.1 and Muse Spark stages are untouched. 10 new focused tests added (`tests/test_reconstruct.py`) covering the crop logic and the hosted-Space orchestration/error-wrapping with the network call monkeypatched out — **41/41 tests passing**, no test attempts real model inference.

## Status update (2026-10-27, later still) — browser viewer, real NL editing, and a real collision demo

**New `/viewer` folder: a working, tested 3D viewer/editor.** `viewer/index.html` (three.js via CDN, no build step) + `viewer/server.py` (thin launcher) + `pipeline/worldkit/webserver.py` (the actual Flask app, testable). Loads `viewer/assets/sample_mug.glb` — the real Hunyuan3D-2 mesh from the previous status update, copied in as a small (~170KB) bundled demo asset (one deliberate exception added to `.gitignore`'s blanket `*.glb` rule). **Verified with 19 automated real-browser checks** (`viewer/tests/browser_test.js`, Playwright + Chromium — not mocked: real page, real three.js, real mesh): model load, canvas actually renders varied pixel content, orbit controls leave the model alone, dragging the gizmo genuinely mutates the live object, programmatic transforms apply exact expected deltas, GLB export produces real non-trivial bytes and reloading them recovers the real baked geometry, and a real Muse Spark call moves the object correctly end-to-end. See `docs/DEMO_RUNBOOK.md` for the full list and how to run it.

**Real bug found and fixed via screenshot-driven debugging, not assumption:** first attempt forced a "Z-up" scene convention, reasoning from `printprep.py`'s post-`orient_for_printing` output (which only looks Z-up because that function's `compute_stable_poses` + flat-base step actively re-orients the mesh). Applied to the viewer's *raw*, unprocessed sample mesh, this was visually wrong (screenshot showed the mug on its side). glTF's spec-mandated Y-up — three.js's own default — was correct all along; reverted the Z-up override after confirming via screenshot. Also found and fixed: `updateReadout()` referenced a `const` DOM element before its declaration executed (TDZ `ReferenceError`), triggered because `TransformControls` fires a `change` event synchronously on `gizmo.attach()` — silently broke model loading until the real-browser test caught it.

**Natural-language editing, via a real Muse Spark API call, server-side only.** `pipeline/worldkit/editcmd.py`: a strict prompt asks Muse Spark for one JSON transform delta (`translate_cm`/`rotate_deg`/`scale_factor`), and `parse_and_validate()` rejects anything malformed or out of sane bounds (translate >200cm, rotate >3600°, scale outside [0.01, 100]) before it can ever reach the live scene — tested directly against the real API with the brief's three example instructions ("move 10cm left", "rotate 30 degrees", "make it 20% bigger"), all three returned correct, validated transforms. The API key lives only in `viewer/server.py`'s process (loaded via the existing `pipeline/.env`); the browser only ever calls the local `/api/edit` endpoint. Manual gizmo editing works identically whether or not a key is configured. **Units/coordinate system documented explicitly, including the uncalibrated-scale caveat:** the viewer shows a persistent on-screen banner that distances are NOT physically calibrated (this mesh has no real measured height — same `default_fallback` placeholder convention as `printprep`), so "10cm" is internally consistent but not asserted as a real centimeter. 24 new Python tests (`tests/test_editcmd.py`, `tests/test_webserver.py`) — **65/65 tests passing**, none make a real API call (Muse Spark responses are monkeypatched for determinism; the 3 real-API checks above were done separately, by hand).

**Real limitation found and documented, not hidden:** the bundled sample mesh has **no texture/color data** — the hosted Hunyuan3D-2 Space's `/gen_shape` endpoint returns an untextured "white mesh" preview (confirmed via `trimesh`: `visual.kind` is `None`), not a textured result. The viewer renders it as plain gray geometry. Noted in `docs/DEMO_RUNBOOK.md`, not glossed over.

**A small, genuinely real collision-detection feature, not physics and not decorative animation.** The viewer's "Enable collision probe" button adds a WASD/QE-steerable sphere; each move casts a real `THREE.Raycaster` ray against the actual loaded mesh's `BufferGeometry` triangles (`intersectObject(modelGroup, true)`) and stops the probe just short of any real surface hit, confirmed by 4 of the 19 browser checks (blocked near the mesh with a real hit-distance reading; free in open space). No gravity/mass/forces are simulated — described everywhere as "collision-aware movement," never "physics."

**Not built, deliberately out of scope this round:** a multi-object/scene reconstruction (still single-object only), per-edit undo history (only a single "reset to identity" exists), and the `/app` Next.js PWA wrapper mentioned in the original plan (the viewer runs standalone via `viewer/server.py` for now).

**Next action to unblock:** add a payment method to the Modal account via its web dashboard. The same command used throughout (`modal run pipeline/modal_app/sam3d.py --input-image pipeline/runs/mug_local/REAL_SAM31_object_rgba.png --output-glb pipeline/runs/mug_local/model.glb`) should then proceed past this point; whether inference itself succeeds against the real checkpoint is still unverified.


## Status update (2026-10-27, after first hosted mesh) — judging-focused workbench

A lightweight interactive GLB workbench was added at `viewer/index.html` (commit `aa4f81a`). It is intended to load the real local GLB, orbit/zoom, apply move/rotate/scale transforms to the loaded model, show mesh counts/dimensions, optionally load the source video and report JSON, and export an edited GLB. **This is source code committed to GitHub, not yet verified in a real browser session.** It loads Three.js modules from jsDelivr and therefore requires internet access. Do not claim browser testing or a successful export until the user/agent actually runs it with the real `runs/mug_local/model.glb`.

The README was updated in commit `77b8eda` to distinguish implemented work from roadmap items. It now honestly records that the current reconstruction is Hunyuan3D-2 via a third-party community Space, not a Meta model; that SAM 3D Objects inference has not run because Modal requires a payment method; that print-prep reports the mesh is not print-ready; and that no genuine simulation or full scene reconstruction is implemented.

**Do not add a payment method or launch paid GPU work without explicit user approval.** The latest Modal dashboard showed $0 charges, $0.51 usage offset by credits, and $1 included monthly compute; only about $0.49 of that monthly included allowance appeared unused. The advertised $100 Modal hackathon credit remains unconfirmed.

### Next actions, in priority order

1. **Validate the workbench with the actual local files.** Open `viewer/index.html` in a modern browser with internet access, load `pipeline/runs/mug_local/model.glb` and `report.json`, check that the mug renders correctly, move/rotate/scale it, export the edited GLB, and reload the export to verify it is real. Fix actual bugs found; don't assume the viewer works from code inspection alone.
2. **Record the evidence for the judging demo.** Keep source video, original reconstruction, actual edit, edited export, and report/provenance visible. Do not commit local generated mesh artifacts unless deliberately approved and repo size/licensing reviewed.
3. **Strengthen the Meta contribution.** Use the existing real SAM 3.1 mask and Muse Spark response accurately. Next valuable increment is Muse Spark translating a constrained natural-language edit into a validated transform, followed by a code-based check and a genuine before/after comparison. Keep API keys server-side/local; never expose them in browser code or commit them.
4. **Only then attempt one genuine simulation**, if usable collision geometry can be built reliably. A collision-aware path through obstacles is preferable to a decorative animation. The mug alone is not an environment; if no suitable real scene is available, show editing/export and label simulation as future work.
5. **Prepare and rehearse the 3-minute demo** against the judging rubric: impact (30%), Meta AI tooling (25%), technical execution (20%), demo quality (25%). Use a specific audience/problem, genuine output, one real edit, verifiable measurements, clear model provenance, and honest limitations. No unsupported claims about engineering accuracy, printing readiness, full city reconstruction, or simulation.

Current highest-risk gap: there is now a reported genuine single-object mesh, but the interactive viewer has not yet been run against it and there is still no complete video-to-edit-to-simulation workflow. Focus on closing one end-to-end proof rather than adding unrelated features.
