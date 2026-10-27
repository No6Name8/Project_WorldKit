# WorldKit — Film reality. Build worlds. Run simulations.

**WorldKit's goal:** transform real-world video into editable 3D environments that people can inspect, modify, and eventually use to test scenarios.

This repository is a hackathon prototype, not a finished city simulator or engineering tool. The current verified milestone is a single-object pipeline demonstrated on a mug: Meta Muse Spark scene/object understanding and SAM 3.1 segmentation feed a reconstruction fallback that uses **Hunyuan3D-2 through a community-hosted Hugging Face Space**. That reconstruction backend is from Tencent's open-weights model family; it is **not a Meta model and not SAM 3D Objects**.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track, Push the Frontier theme). Build period: Oct 27–Nov 4, 2026. Submission deadline: Nov 4, 9:00 PM Eastern / Nov 5, 5:00 AM Riyadh.

## What exists now

- **Python object pipeline:** video frame extraction, Muse Spark object understanding with explicit-object override, SAM 3.1 segmentation, reconstruction backend selection, mesh print-prep, and preview rendering.
- **Real reconstruction proof:** a genuine mug GLB generated with Hunyuan3D-2 from a tightly cropped SAM 3.1 object image. The generated local artifact is kept under the git-ignored `pipeline/runs/mug_local/` directory; large run artifacts are not committed.
- **Print-prep validation:** the mug mesh was exported to STL and sampled for wall thickness. It is **not print-ready**: many sampled points failed the 1.2 mm wall-thickness threshold, and its 10 cm scale is an explicitly flagged placeholder estimate.
- **Interactive GLB workbench:** `viewer/index.html` is a browser viewer for loading a local GLB, orbiting around it, applying a real object transform (move/rotate/scale), viewing mesh statistics, optionally loading the run report/source video, and exporting the edited GLB.
- **Automated tests:** the latest reported run was 41/41 passing. The hosted-model integration tests mock the network call; the test suite does not claim to run model inference.

## What is not yet implemented or verified

- Full multi-object or room/city reconstruction from video.
- SAM 3D Objects inference: the Modal image builds, but Modal blocks A100 functions until a payment method is added. No payment method was added.
- A genuine collision-aware simulation.
- Engineering-grade scale/geometry accuracy, printer-ready wall thickness, or reliable export to Unity/Unreal/CAD.
- Muse Wonder, VGGT, or Muse Image integration in the verified mug run.
- A deployed, production-ready web application or account/device sync.

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

## Open the interactive workbench

Open `viewer/index.html` in a modern browser with internet access (the page loads Three.js modules from jsDelivr). Choose the actual generated `model.glb` from your local `pipeline/runs/mug_local/` folder. Optionally load `report.json` and the source video. Use the gizmo to move, rotate, or scale the model and export an edited GLB.

The viewer is a lightweight prototype. It edits the loaded object's transform, not its internal topology, and does not implement simulation.

## Meta AI tooling and provenance

| Tool | Intended/verified role |
|---|---|
| Muse Spark (Meta Model API) | Scene/object understanding. A real call succeeded, but an unprompted sample misidentified the mug as a side table; an explicit object override is supported. |
| SAM 3.1 (Meta Model API) | Real mug segmentation including the handle; verified on the source frame. |
| SAM 3D Objects (Meta open weights) | Intended reconstruction route; not yet run because Modal requires a payment method for A100 functions. |
| Hunyuan3D-2 (Tencent open weights, hosted community Space) | Actual mug mesh backend used for the current reconstruction proof. Not a Meta model. |
| Muse Wonder, VGGT, Muse Image | Future/optional integrations; do not claim they were used in the verified run. |

## Judging-focused next milestones

1. Demonstrate a real source-to-mesh workflow and inspect the mesh from multiple views.
2. Demonstrate a genuine object selection/edit in the viewer and export the changed GLB.
3. Add a meaningful Muse Spark-assisted edit-intent or before/after verification step; show the real request/response and validate the resulting change in code.
4. Only if usable scene/collision geometry is available, add one small real simulation such as collision-aware path planning. Otherwise clearly label simulation as future work.
5. Record a concise, truthful three-minute demo: source footage → genuine reconstruction → actual edit → validation/export → broader vision. Report measured results only.

The hackathon judges impact (30%), Meta AI tooling (25%), technical execution (20%), and demo quality (25%). The priority is one complete, credible workflow—not a list of unimplemented features.

## License and author

See [LICENSE](LICENSE). Created by Abdullah Ali Alanazi ([@No6Name8](https://github.com/No6Name8)).
