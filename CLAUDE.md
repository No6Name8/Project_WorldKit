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

**Real video now in `pipeline/samples/`:** `WhatsApp Video 2026-10-26 at
9.32.38 PM.mp4` (16s, 480x856, h264). Ran the local-only stages against it
(no Meta API calls, no Modal GPU, per explicit instruction):

- Frame extraction: real, works. 12 sharp frames in 0.67s, sharpness
  192-208 (Laplacian variance).
- Detect: only the fallback path was exercised directly (sharpest frame +
  `--object` override) -- calling the real `detect_object()`/
  `segment_concept()` wrappers was avoided since the key is now present and
  they'd attempt a real (billing-blocked) API call first.
- Segment: fallback (`rembg`/u2net) was tested directly on the real chosen
  frame. **Original result: it masked the whole round table, not the mug**
  (no concept-awareness -- it just finds the largest high-contrast
  foreground region). **Fixed with a refinement step**: added
  `segment.refine_mask_with_grabcut()` (classical OpenCV GrabCut, no new
  model/download) which locates the rough mask's highest-local-texture
  region that does NOT touch the rough mask's own boundary (the table's
  rim/legs are high-texture too, but touch that boundary; a sitting object
  doesn't), then runs GrabCut seeded from it. `_fallback_mask()` now applies
  this automatically. Measured on the real video: mask shrank from 207,736px
  (93% of frame width -- the whole table) to 11,516px (27% of frame width,
  tight to the mug body) -- see `runs/mug_local/COMPARISON_summary.json` and
  the before/after overlays (all gitignored, local only). Not pixel-perfect:
  the mug's white rim/interior is excluded where its color matches the
  marble table too closely, and no 2D segmentation method can recover
  geometry a single frame doesn't show (e.g. an occluded handle -- none is
  even visible in this frame). 3 new regression tests
  (`tests/test_segment.py`) cover the refinement function directly, with a
  synthetic image, no rembg/network/GPU needed.
- SAM 3D Objects / Modal: not invoked. No mesh was reconstructed from the
  real mug. Any mesh in `runs/mug_local/TEMP_TEST_*` is a hand-built
  cylinder+handle test shape, explicitly NOT a reconstruction -- used only
  to verify printprep/STL export/preview rendering still work (they do:
  watertight, 9.5cm upright height, one thin-wall point flagged at 0.367mm,
  4 preview angles rendered).

**Coin-for-scale: not implemented, confirmed by code search.** The real
video has a coin next to the mug on the table (visible in the chosen
frame), but the only scale-reference logic in the codebase is
`estimate_height_cm_from_a4()` (A4 sheet, grep for "coin" in `worldkit/` and
`modal_app/` returns nothing). Even with a working Meta API key, SAM 3.1
would only ever be asked to find "A4 sheet of paper", never a coin. **The
mug's scale currently comes only from `--height-cm` (user-measured); there
is no automatic real-world scale recovery for this video at all.**

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

**Known issues / next steps:**
1. Billing needs resolving (`https://dev.meta.ai/billing/?team_id=4497333240582980`)
   before any real Meta API call (Muse Spark naming, SAM 3.1 masking) can
   succeed.
2. The fallback local mask now isolates the mug from the table reasonably
   well (GrabCut refinement), but still excludes its white rim/interior and
   can't recover unseen geometry (e.g. a handle not visible in the chosen
   frame) -- still not a real stand-in for SAM 3.1 in the actual demo.
3. No coin-based (or any non-A4) scale recovery exists; add it, or keep
   requiring `--height-cm` for this kind of shot.
4. SAM 3D Objects on Modal is still completely unexercised against a real
   checkpoint/image -- first real run will also build the container image
   and download gated checkpoints (slow, costs Modal credits).
