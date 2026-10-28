# WorldKit demo runbook (3 minutes)

Everything in the script below runs from a fresh clone with no GPU and no Modal account; only the natural-language edit needs a Meta API key and internet. Rehearse once with the exact commands.

## Before recording

1. `pipeline\.venv\Scripts\python.exe viewer\server.py` -> open <http://127.0.0.1:5050/>. The console line says whether the Muse Spark key is configured.
2. Click **Demo: mug + table**: two objects appear, the provenance panel names SAM 3D Objects (Meta). Click **Demo: mug** to see the single-object result with the fidelity decision in the provenance panel.
3. Dry run the edit: select the mug, type "move it 10 cm to the left" (needs `pipeline/.env` with `MODEL_API_KEY`; if the API is down the box shows a real error and the gizmo still works).
4. Do not show `pipeline/.env`, tokens or the Modal dashboard credentials.
5. Optional live run (a few minutes, a few cents): `cd pipeline; .venv\Scripts\python.exe -m worldkit.object samples\mug_on_table.mp4 --out runs\live --mesh-backend modal --fidelity auto`, then `viewer\server.py --open pipeline\runs\live`. Keep the demo assets as the fallback if Modal is slow.
6. Keep a screen recording of one successful complete run as a backup.

## Script

| Time | Show | Say (all of it is verified; do not add claims) |
|---|---|---|
| 0:00-0:20 | the 16 s source video (`pipeline/samples/mug_on_table.mp4`) | Footage holds the spatial information developers need, but turning it into editable 3D is a fragmented workflow. WorldKit goes from a phone video to a 3D scene you can edit and simulate. |
| 0:20-0:45 | `docs/viewer_demo_scene.png` or a run's `mask_overlay.png` | Muse Spark names the main object ("coffee mug" from 12 frames) and picks the best frame; SAM 3.1 isolates it including the handle; SAM 3D Objects (Meta, on a Modal A100) builds the textured 3D model from that single view. |
| 0:45-1:15 | `docs/fidelity_heldout_real_vs_baseline_vs_improved.png` (real / generated / improved) | The generator's stripes are straight; the real mug's are wavy. Muse Spark's structured analysis says "vertical stripes on a cup", which lets WorldKit measure the pattern from the video: 14 wavy stripes. It is only accepted because it beat the generated texture on frames it never saw (held-out correlation 0.68 to 0.84, cross-validated). 37% of the surface is observed angles, 3% inferred, 61% still generated; all geometry is generated. |
| 1:15-2:00 | viewer, **Demo: mug + table** | Each object is separate with its own provenance. Select and move the mug; **Check overlaps**; ask Muse Spark "move it 10 cm to the left" (validated server-side before it touches the model). Scale is not calibrated until you type a real height: type the mug's measured height and the banner says it is your number. |
| 2:00-2:30 | **Simulate** then **Reset sim** | Real rigid-body physics (Rapier): gravity and mass, the table as its exact mesh, the mug as a convex hull; the editor's objects are never touched; reset restores them. Approximation, not engineering accuracy. |
| 2:30-3:00 | **Export scene**, then the limitations | One GLB with both objects and their provenance reloads exactly. Honest limits: one object in one video, geometry is generated, scale needs a measurement, scenes are two objects. Vision: observed vs inferred stays explicit as this grows to whole environments. |

## What each test suite covers (all run against real reconstructed meshes)

| Suite | What it covers |
|---|---|
| `pytest pipeline/tests` | geometry, scale, scene and fidelity logic on synthetic ground truth, mocked network calls, web server, CLI flags |
| `viewer/tests/browser_test.js` | bundled sample: load, material, orbit, gizmo, export/reload, Muse Spark edit and its failure path, collision probe, provenance |
| `integration_test.js` | the full viewer flow on one real pipeline artifact |
| `sam3d_browser_test.js` | real SAM 3D mug: orientation from vertices, lighting, camera, edits, handle collision, export/reload, provenance |
| `scene_physics_browser_test.js` | two-object scene: selection, independent edits, overlap, scene export/reload, missing mesh, calibration, physics engine self-tests, mug-on-table drop, UI |
| `demo_readiness_test.js` | offline (no external requests), `server.py --open`, bundled demos, physics and export on the demo scene |

Not covered by an automated test: the live Modal GPU path (verified by real runs: `pipeline/runs/sam3d_*`, `final_e2e`), the hosted TRELLIS Space (no recent live call; free quota), other videos or objects.

## Do not claim

Accurate real-world scale (unverified: the coin and mug were never measured); measured geometry (it is generated); generalisation beyond the mug; engineering-grade physics; use of Muse Wonder, Muse Image or VGGT; print readiness (`print_ready` is false without a measured height).

## If something fails live

| Symptom | Do |
|---|---|
| Muse Spark edit errors | the box shows the real error; use the gizmo; say so |
| Viewer blank | start the server from the repo root and hard-refresh; the vendored libraries need no internet |
| Live reconstruction slow or Modal error | the error message names the checklist; switch to the bundled demo assets |
