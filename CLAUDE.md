# CLAUDE.md

Instructions for Claude Code in this repository. Read this at the start of every session.

## Product

WorldKit — Film it. Print it.

Film anything with a phone — an object, a desk, a room — and WorldKit turns it into a print-ready 3D model that can be edited by chatting with AI.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track, Push the Frontier theme). All code in this repository was written during the hackathon building period (Oct 27 – Nov 4, 2026).

### Features

- **Object mode** — single object → print-ready STL.
- **Map mode** — scene → printable diorama, or tap any object in the scene to print it alone.
- **AI Edit** — chat to change the model: resize, add text, drill holes, thicken parts, edit a named part, creative restyles. Every edit is verified and undoable.
- **Print-readiness checks** — real-world scale recovered from an A4 sheet in frame, watertight geometry, minimum wall thickness, flat base, fit to printer bed.
- **Phone + laptop sync** — one account syncs captures and edits across devices.

### Pipeline

video → frames → Muse Spark lists objects → SAM 3.1 tracks and masks them → best view selected → SAM 3D Objects builds textured 3D models → VGGT recovers camera poses and scale for scenes → Muse Wonder organizes the scene and runs AI Edit with geometry tools → Muse Spark verifies renders against the real video → print-ready STL / GLB.

### Meta models

- **Muse Spark** (Meta Model API) — lists objects in the scene; verifies renders against the source video.
- **Muse Wonder** (Meta Model API) — organizes the scene; runs AI Edit with geometry tools.
- **Muse Image** (Meta Model API) — image generation/editing support for AI Edit.
- **SAM 3.1** (Meta Model API) — tracks and masks objects across video frames.
- **SAM 3D Objects** (Meta open weights) — builds textured 3D models from the selected best view.
- **VGGT** (Meta open weights) — recovers camera poses and real-world scale for scenes.

### Tech stack

- Next.js + react-three-fiber (installable PWA)
- Supabase (auth, storage, database)
- Vercel (hosting)
- Python pipeline on Modal GPUs
- trimesh + manifold3d (geometry processing)

## Folder layout

- `/app` — Next.js PWA (the web app).
- `/pipeline` — Python, deployed to Modal.
- `/docs` — architecture notes and benchmark results.

## Rules

- This repo is **public**. Never commit secrets. All keys go in `.env` files that are gitignored; provide matching `.env.example` files that list variable names only, with no real values.
- The Meta Model API base URL is `https://api.meta.ai/v1` and is OpenAI-SDK compatible.
- Prefer small, working, testable steps. Commit after each working step with a clear message.
- Never claim something works without running it.
- Every number shown in the UI or in a demo must come from real measured output — no placeholder or invented numbers.

## Priorities if time is short

1. Object mode
2. Print pipeline
3. App + sync
4. AI Edit
5. Map mode
6. Stretch: diorama splitting, game/robot export

## Status

_Last updated: Object mode CLI build + API verification session._

**What works:** `/pipeline` has a full `worldkit` package (frames, meta_api,
detect, segment, Modal SAM 3D app, printprep, render, CLI) with 18/18 unit
tests passing against synthetic data. Modal is authenticated (workspace
`no6name8`) and confirmed reachable with a live remote call. `pipeline/.env`
exists with a real `MODEL_API_KEY`, and the key loads correctly.

**Meta API auth result:** the key itself is valid (not a 401), but the team
has **no billing configured**: a real `muse-spark-1.3` call returns
**HTTP 402 `billing_not_configured`**, pointing to
`https://dev.meta.ai/billing/?team_id=4497333240582980`. Until that's
resolved, every real Meta API call (Muse Spark, SAM 3.1) will fail the same
way, and the pipeline's `MetaAPIUnavailable` fallback path (local
`FALLBACK_`-tagged detection/segmentation) will trigger automatically.

**Not yet run end-to-end:** no video has been placed in `pipeline/samples/`
yet, so the pipeline has never been run against real footage, and the Modal
GPU (SAM 3D Objects) stage has never been exercised beyond a hello-world
connectivity check — its container build + checkpoint download + real
inference cost/timing are still unmeasured.

**Key decisions:**
- SAM 3D Objects has a built-in textured-mesh output (`with_mesh_postprocess`
  + `with_texture_baking`, both default `True` in the underlying pipeline);
  `modal_app/sam3d.py` calls that directly instead of writing a custom
  splat-to-mesh conversion.
- Fallback behavior (no key / billing / auth rejection only -- never on other
  errors) is built into `detect.py`/`segment.py` from the start, each
  fallback result unmistakably tagged (`used_fallback`, `FALLBACK_` filename
  prefix), so a demo run can never accidentally pass off fallback output as
  real Meta API/SAM 3.1 output.

**Known issues / next steps:** billing needs resolving before any real Meta
API call can succeed; a real test video needs to go in `pipeline/samples/`
before a genuine end-to-end run (with real measured timings/dimensions/cost)
is possible.
