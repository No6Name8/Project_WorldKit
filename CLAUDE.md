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

**Reconstruction is now blocked by a real Modal account requirement, not credit balance:** `modal run` reaches the GPU-attach step and fails with `InvalidError('Please add a payment method to use A100-40GB GPU functions.')`. Per the safety rule above, no payment method was added. `_ensure_checkpoints()`/`reconstruct_mesh` were never reached — no checkpoint download, no inference attempt, no mesh produced. The only mesh in this repo is still the old hand-built `TEMP_TEST_*` placeholder, not a reconstruction. 31/31 tests still pass (unchanged — this session only touched `modal_app/sam3d.py`'s build steps).

**Next action to unblock:** add a payment method to the Modal account via its web dashboard. The same command used throughout (`modal run pipeline/modal_app/sam3d.py --input-image pipeline/runs/mug_local/REAL_SAM31_object_rgba.png --output-glb pipeline/runs/mug_local/model.glb`) should then proceed past this point; whether inference itself succeeds against the real checkpoint is still unverified.
