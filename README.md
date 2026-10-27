# WorldKit — Film reality. Build worlds. Run simulations.

**WorldKit's goal:** transform real-world video into editable 3D environments that people can inspect, modify, and eventually use to test scenarios.

This repository is a hackathon prototype, not a finished city simulator or engineering tool. The current verified milestone is a single-object pipeline demonstrated on a mug: Meta Muse Spark scene/object understanding and SAM 3.1 segmentation feed a reconstruction fallback that uses **Hunyuan3D-2 through a community-hosted Hugging Face Space**. That reconstruction backend is from Tencent's open-weights model family; it is **not a Meta model and not SAM 3D Objects**.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track, Push the Frontier theme). Build period: Oct 27–Nov 4, 2026. Submission deadline: Nov 4, 9:00 PM Eastern / Nov 5, 5:00 AM Riyadh.

## What exists now

- **Python object pipeline:** video frame extraction, Muse Spark object understanding with explicit-object override, SAM 3.1 segmentation, reconstruction backend selection, mesh print-prep, and preview rendering.
- **Real reconstruction proof:** a genuine mug GLB generated with Hunyuan3D-2 from a tightly cropped SAM 3.1 object image. The generated local artifact is kept under the git-ignored `pipeline/runs/mug_local/` directory; large run artifacts are not committed.
- **Print-prep validation:** the mug mesh was exported to STL and sampled for wall thickness. It is **not print-ready**: many sampled points failed the 1.2 mm wall-thickness threshold, and its 10 cm scale is an explicitly flagged placeholder estimate.
- **Interactive, browser-verified viewer:** `viewer/index.html` (+ `viewer/server.py`, a small Flask backend) loads a GLB, orbits around it, applies a real object transform via a move/rotate/scale gizmo, exports/reloads the edited GLB, and optionally loads a run's `report.json`/source video for provenance. **Verified with 20 automated real-browser checks** (Playwright + Chromium, `viewer/tests/browser_test.js`) — not just committed, unverified source.
- **Real natural-language editing:** type an instruction like "move it 10 cm to the left" and it's interpreted by a real Muse Spark API call (`pipeline/worldkit/editcmd.py`), validated server-side (malformed/out-of-range responses are rejected before they can touch the model), and applied to the live object. The API key never reaches the browser. Manual gizmo editing works identically with or without a configured key.
- **A real, scoped collision-detection demo:** the viewer's collision probe uses genuine ray/triangle intersection tests (`THREE.Raycaster`) against the actual loaded mesh — it stops at real geometry rather than passing through. Not physics (no gravity/mass/forces), and scoped to the one reconstructed object, not a multi-object scene.
- **Verified as one product, not just as separate parts:** a real, fresh `python -m worldkit.object ... --mesh-backend hosted` run (video -> frames -> Muse Spark -> SAM 3.1 -> Hunyuan3D-2 -> print-prep) produced a genuine mesh and `report.json`, which was then loaded into the running viewer and driven through a full real edit sequence (AI edit -> manual gizmo edit on top of it -> collision probe against the edited model -> export -> reload) and the provenance panel, in one single automated integration test.
- **Automated tests:** 68/68 Python tests passing (`pytest`), 23/23 real-browser checks (`viewer/tests/browser_test.js`, includes a simulated-API-failure path confirming manual editing still works), and 11/11 end-to-end integration checks (`viewer/tests/integration_test.js`) against the real pipeline artifacts above — 102 automated checks total. The hosted-model integration tests mock the network call for determinism; a handful of real API calls (Muse Spark detection, segmentation, and the NL-edit examples) were made and verified by hand or by the integration test, separately from the mocked unit suite.

## What is not yet implemented or verified

- Full multi-object or room/city reconstruction from video — the collision demo and viewer both operate on one reconstructed object.
- SAM 3D Objects inference: the Modal image builds, but Modal blocks A100 functions until a payment method is added. No payment method was added.
- Calibrated real-world scale — the current mesh's dimensions use an explicitly-flagged, unmeasured placeholder.
- Engineering-grade scale/geometry accuracy, printer-ready wall thickness (this mesh fails print validation), or reliable export to Unity/Unreal/CAD.
- Muse Wonder, VGGT, or Muse Image integration in the verified mug run.
- A deployed, production-ready web application, the planned `/app` Next.js PWA wrapper, or account/device sync — the viewer runs standalone via `viewer/server.py` today.

## Run the Python pipeline

From the repository root on Windows, using Python 3.10 and the environment in `pipeline/.venv`:

```powershell
pipeline\.venv\Scripts\python.exe -m worldkit.object "pipeline\samples\<video>" --out "pipeline\runs\<run-name>" --object "mug" --mesh-backend hosted
```

Use `--mesh-backend modal` only if Modal GPU execution is available and its cost has been explicitly approved. The hosted Hunyuan3D-2 Space is third-party infrastructure and may be unavailable, queued, or change its API.

To process a previously generated mesh without making a reconstruction request:

```powershell
pipeline\.venv\Scripts\python.exe -m worldkit.object "pipeline\samples\<video>" --out "pipeline\runs\<run-name>" --object "mug" --mesh-backend import --mesh-file "pipeline\runs\<run-name>\model.glb"
```

Inspect `report.json` for actual backend provenance, stage timings, warnings, and print-prep validation. Do not infer print readiness from watertightness alone.

## Open the interactive viewer

```powershell
pipeline\.venv\Scripts\python.exe viewer\server.py
```

Then open `http://127.0.0.1:5050/` (needs internet access — it loads three.js from a CDN). It loads the bundled `viewer/assets/sample_mug.glb` automatically; use "Load GLB" to pick a different local file, e.g. your own `pipeline/runs/.../model.glb`. Optionally load `report.json` and the source video in the provenance panel. Use the gizmo or the natural-language box to move/rotate/scale the model, enable the collision probe, and export an edited GLB.

The viewer edits the loaded object's transform, not its internal topology. The collision probe is real ray/triangle collision detection, not a physics simulation.

## Meta AI tooling and provenance

| Tool | Intended/verified role |
|---|---|
| Muse Spark (Meta Model API) | Scene/object understanding. A real call succeeded, but an unprompted sample misidentified the mug as a side table; an explicit object override is supported. |
| SAM 3.1 (Meta Model API) | Real mug segmentation including the handle; verified on the source frame. |
| SAM 3D Objects (Meta open weights) | Intended reconstruction route; not yet run because Modal requires a payment method for A100 functions. |
| Hunyuan3D-2 (Tencent open weights, hosted community Space) | Actual mug mesh backend used for the current reconstruction proof. Not a Meta model. |
| Muse Wonder, VGGT, Muse Image | Future/optional integrations; do not claim they were used in the verified run. |

## Judging-focused next milestones

See [the three-minute demo runbook](docs/DEMO_RUNBOOK.md) for the recording checklist, suggested narrative, rubric mapping, and milestone gates.

1. Calibrate real-world scale (A4 or coin reference) instead of today's unmeasured placeholder.
2. Improve mesh wall-thickness so `print_ready` can genuinely become `true`.
3. Extend from one object to a real multi-object scene.
4. Package the viewer into the planned `/app` Next.js PWA wrapper.
5. Record a concise, truthful three-minute demo: source footage → genuine reconstruction → actual edit (gizmo + natural language) → collision demo → validation/export → broader vision. Report measured results only.

The hackathon judges impact (30%), Meta AI tooling (25%), technical execution (20%), and demo quality (25%). The priority is one complete, credible, *tested* workflow — not a list of unimplemented features.

## Repo structure

```
/pipeline Python AI pipeline (video -> mesh -> print-ready STL)
/viewer   Browser 3D viewer + editor (three.js + a small Flask backend)
/docs     Architecture notes, benchmark results, docs/DEMO_RUNBOOK.md
```

(`/app`, a Next.js PWA wrapping the viewer, is a planned next step — not built yet. The viewer runs standalone today via `viewer/server.py`.)

## License and author

See [LICENSE](LICENSE). Created by Abdullah Ali Alanazi ([@No6Name8](https://github.com/No6Name8)).
