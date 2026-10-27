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
detect, segment, Modal SAM 3D app, printprep, render, CLI) with 31/31 unit
tests passing against synthetic data (`.venv\Scripts\python.exe -m pytest
tests/ -v`). Modal is authenticated (workspace
`no6name8`) and confirmed reachable with a live remote call. `pipeline/.env`
exists with a real `MODEL_API_KEY`, and the key loads correctly.

**Meta API: BILLING RESOLVED, confirmed working (2026-10-27).** The prior
HTTP 402 `billing_not_configured` block is gone. One minimal
`muse-spark-1.3` call via the existing `meta_api.get_client()` (no pipeline
code changed): **HTTP 200**, latency 2.32s, usage
`{prompt_tokens: 13, completion_tokens: 5, total_tokens: 18}` (of the 5
completion tokens, 3 were internal reasoning tokens, leaving none for
visible text at `max_tokens=5` -- content came back empty; not an error, a
token-budget artifact of a deliberately tiny test call). The
`MetaAPIUnavailable` fallback path will no longer trigger for billing
reasons going forward -- real Muse Spark/SAM 3.1 calls should now work in
the actual pipeline.

**Baseten (new, hackathon offer) also verified working.** One minimal call
to `https://inference.baseten.co/v1`, model `meta-models/muse-spark-open-1026`,
key from `BASETEN_API_KEY` (not yet used anywhere in the pipeline -- this
was a standalone connectivity check only): **HTTP 200**, latency 1.43s,
usage `{prompt_tokens: 150, completion_tokens: 5, total_tokens: 155,
cached_tokens: 128}` (same empty-content-at-max_tokens=5 artifact as
above). No billing/credit-balance endpoint is documented for either
provider -- Meta only exposes a web dashboard, Baseten's docs don't mention
one at all.

**Genuine Meta detection + SAM 3.1 segmentation tested on the real mug
video (2026-10-27).** One frame (`sharp_01.png`, handle visible). No Modal/
GPU involved. Not fallback -- `used_fallback=False` confirmed on both calls.

- **Muse Spark detection (1 real call, no `--object` override, to see its
  genuine judgment):** HTTP 200, 9.48s, 2525 total tokens (1801 reasoning).
  **Named the main object "side table", not "mug"** -- its own reasoning:
  "The table fills and centers the frame while the mug and coin are small
  items on top." A real, measured limitation of single-frame detection
  without a hint, not a fabricated success and not treated as one.
- **SAM 3.1 segmentation (2 real calls, concept forced to "mug" per this
  round's task), result: genuinely isolates the mug, INCLUDING the
  handle.** 14,394px, bbox 32%/16% of frame width/height -- tighter than
  and strictly better than the local GrabCut fallback (11,516px, which
  misses the handle entirely). See
  `runs/mug_local/REAL_SAM31_mask_overlay.png` (gitignored, local only).

**Bug found and fixed while verifying this: mask placement.** SAM 3.1's
mask raster is sized to its own bounding box (confirmed: a 155x140 mask for
box (164,254,319,394) on a 480x856 frame -- 155 and 140 match the box
exactly), not the full frame. The old `save_mask_overlay`/
`save_rgba_with_mask` stretched any differently-shaped mask to fill the
*whole* image, smearing a small correct mug mask across most of the frame
(visually confirmed before the fix, then corrected -- see commit history).
Fixed with `_place_mask_on_canvas()`, which pastes a box-shaped mask at its
real position instead of stretching it; the fallback path (always
full-frame-sized) is unaffected. 3 new regression tests added
(`tests/test_segment.py`), 24/24 total passing. This also means
`object_rgba.png` (SAM 3D Objects' real input, once Modal runs) would have
been silently wrong before this fix -- worth knowing even though SAM 3D
hasn't been run yet.
one at all; neither was checked via a paid call.

Both `MODEL_API_KEY` and `BASETEN_API_KEY` confirmed present and loading
correctly in `pipeline/.env` (lengths checked, values never printed, logged,
or committed).

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
  geometry a single frame doesn't show. 3 new regression tests
  (`tests/test_segment.py`) cover the refinement function directly, with a
  synthetic image, no rembg/network/GPU needed.

**Frame selection investigated -- correction to a prior claim.** An earlier
status entry said no handle was visible "in this frame"; that was based only
on the auto-selected frame and was wrong as a general statement. Built a
labeled contact sheet of all 12 kept frames
(`runs/mug_local/CONTACT_SHEET_all_frames.png`) and checked by eye: **the
mug DOES have a visible handle, and it shows clearly in 6 of the 12 kept
frames** (sharp_01/04/05/08/09/10) -- just not in the one the fallback
selection picks (sharp_00, the single sharpest frame, where the handle
happens to be rotated out of view). Confirmed, not assumed: full-resolution
spot checks on sharp_00/01/09.

Tried to fix this with a local, non-ML metric -- convex-hull defect depth on
the fallback mask, as a "has a protruding handle" proxy. It separates
synthetic test shapes cleanly (plain rectangle 0.0 vs rectangle+tab 0.26),
but **measured against the real frames it does not reliably track actual
handle visibility** (e.g. sharp_07, no handle, scored higher than sharp_01
and sharp_09, which do show it) -- the GrabCut mask's own edge noise
contaminates the signal. **Left `detect.py`'s selection algorithm
unchanged** rather than wire in an unreliable rule; full scores and the
visual ground truth are in `runs/mug_local/SELECTION_ANALYSIS.json`
(gitignored, local only). Manually identified `sharp_01.png` (sharpness
195.8) as the best available frame for continued local testing -- sharp,
full cup in frame, handle clearly visible -- but this was a human judgment
call, not something the pipeline picks on its own. This is exactly the kind
of call Muse Spark is meant to make once billing is resolved; it's also
exactly why `detect_object()`'s real path already asks it to.

**SAM 3D Objects / Modal: not invoked. No mesh has been reconstructed from
the real mug, and no genuine 3D reconstruction of it exists anywhere in this
repo.** Any mesh under `runs/mug_local/TEMP_TEST_*` is a hand-built
cylinder+handle test shape, explicitly NOT a reconstruction -- used only to
verify printprep/STL export/preview rendering still work (they do:
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
1. ~~Billing needs resolving~~ **RESOLVED 2026-10-27**, and genuine Muse
   Spark + SAM 3.1 calls now confirmed working against the real video (see
   above). `detect.py`/`segment.py`'s real paths have each been exercised
   once directly; the full `object.py` CLI has not been run end-to-end with
   the real API yet (only ad hoc direct calls, per each round's "keep calls
   limited" constraint).
2. **Muse Spark's own object-naming judgment picked "side table", not
   "mug", when given one frame with no hint.** `detect_object()` without
   `--object` would currently misname this object if run as-is. Passing
   `--object "mug"` sidesteps it; whether this generalizes to other framing/
   objects is untested.
3. The fallback local mask isolates the mug from the table reasonably well
   (GrabCut refinement) but still misses the handle and the white rim/
   interior -- real SAM 3.1 gets both; fallback remains dev-only, not a
   stand-in for the actual demo.
4. Fallback frame selection still just picks the sharpest frame, which can
   (and here, does) pick an angle where the handle is rotated out of view,
   even though better frames exist in the same kept set. No reliable local
   fix was found (see Frame selection investigated, above). Now that Muse
   Spark works, its real best-frame judgment hasn't been tested on the full
   12-frame set yet (only ever run 1 frame at a time, to keep calls
   limited).
5. No coin-based (or any non-A4) scale recovery exists; add it, or keep
   requiring `--height-cm` for this kind of shot.
6. SAM 3D Objects on Modal is still completely unexercised against a real
   checkpoint/image -- not launched yet (2026-10-27 cost check below).

**Modal cost check (2026-10-27), before launching anything:**
`modal billing summary` -- $0.00 billed so far (confirms nothing has run).
`modal billing rates` -- A100 (40GB) = **$2.10/hour**. **No CLI command
exposes available/granted credit balance** (`modal billing`/`workspace`
have no such subcommand) -- only actual spend, which is $0. Whether the
hackathon's $100 Modal credit offer has actually posted to this workspace
is **unverified** and needs checking on the Modal web dashboard's
Billing page (`modal billing summary`/`report` don't show it). Rough cost
bound from the code (`gpu="A100-40GB"`, `timeout=30*60`): worst case
**$1.05** if the function ran the full 30-minute timeout; realistically
likely well under $1 for one run, but the actual checkpoint size and image
build time are unknown, so this is a bound, not a precise estimate.
**Did not launch the job** -- funding couldn't be confirmed.

**Implementation issue found (reported, not fixed, per this round's
instructions):** `_ensure_checkpoints()` (the gated HF checkpoint download)
runs *inside* `reconstruct_mesh`, the GPU-attached function -- so checkpoint
download time is billed at the A100 rate, not done more cheaply beforehand.
Also unverified: whether the Modal secret `huggingface`'s token actually has
working access to the gated `facebook/sam-3d-objects` repo (never
download-tested) -- if not, the first run would fail after the container
(and GPU) has already started.

---

**2026-10-27, later: real-SAM-3.1-result cache added; reconstruction still
blocked externally, not attempted with a fake mesh.**

**Phase 1 -- reproducible reuse of the real SAM 3.1 result (DONE, tested):**
`segment.py` gained a content-hash-keyed cache (`segment_concept(...,
use_cache=True)`, the default) under `pipeline/.cache/sam31/` (gitignored).
Identity is SHA-256 of the image bytes + the concept string, re-verified
inside the cached JSON on every load -- a different frame, a different
video, or a different concept is always a cache miss (never silently
served stale data); only real (non-fallback) results are ever cached.
Seeded from the already-verified real result (no new API call): the
existing `runs/mug_local/REAL_SAM31_mask_raw.npy` + its box, for
`sharp_01.png` + concept "mug". Verified live: `segment_concept(sharp_01,
"mug")` now returns in 0.002s with `from_cache=True`, `used_fallback=False`,
identical mask -- vs. ~3s for a real API call. `object.py`'s report.json
now also records `segment_from_cache` and `segment_source`
("sam-3.1_live" / "sam-3.1_cached" / "fallback_local_mask") for provenance.
`--object` override already existed and needed no change. 7 new cache
tests (hit, miss-on-different-image, miss-on-different-concept, never
caches fallback results, rejects tampered metadata, `segment_concept` skips
the network on a hit, `use_cache=False` bypass) -- **31/31 tests passing**
(`.venv\Scripts\python.exe -m pytest tests/ -v`). Also added
`pipeline/runs/` and `pipeline/.cache/` to `.gitignore` (previously kept out
of commits by hand, every round -- now enforced).

**Phase 2 -- SAM 3D input/config verified by inspection (DONE, no GPU
needed):** `REAL_SAM31_object_rgba.png` re-confirmed correct (480x856 RGBA,
full-canvas, mask properly embedded in alpha at its real position -- exactly
the format `facebookresearch/sam-3d-objects`' own `load_image`/
`merge_image_and_mask` expects, no pre-cropping needed or done). Checkpoint/
secret/Volume flow in `modal_app/sam3d.py` re-read end to end; matches its
own docstring, no new defect found beyond the already-reported
checkpoint-inside-GPU-function cost inefficiency (left as-is rather than
refactoring untested, cost-sensitive infrastructure code right before a
real paid run). **Quality note, not a defect:** the mug occupies only
155x140px of the 480x856 frame (the frame itself is what SAM 3.1 segmented,
unchanged) -- low effective resolution for the subject, worth knowing if
reconstruction quality turns out poor.

**Phase 3 -- funding/access still block launching, not re-investigated
where already known:** Modal credit balance remains unverifiable via CLI
(unchanged from the check above -- not repeated, per instruction). HF gated
access for the `huggingface` Modal secret is still untested -- doing so
needs at least a CPU-only Modal function call (no GPU), which is a real,
if tiny, possible charge; **not run without asking first.** Confirmed no
local GPU exists on this machine (checked earlier in the project:
`nvidia-smi` not found). No hosted inference service offering SAM 3D
Objects (or an equivalent) was found or assumed -- Meta Model API and
Baseten (`muse-spark-open-1026`) host different models, confirmed in the
prior investigation round, not re-researched here.

**No reconstruction attempted. No mesh fabricated or substituted.** Phase 4
(validate mesh, print-prep) does not apply -- its precondition ("once a
genuine mesh exists") was never met. The earlier `TEMP_TEST_*` placeholder
mesh from a prior round already demonstrated printprep/STL/preview work in
isolation; it was not regenerated or re-presented as reconstruction
progress.

**Next action, in order:** (1) you confirm Modal credit balance on the
web dashboard; (2) you approve a small Modal CPU-only call to test HF gated
access (cost: a few seconds of CPU time, a small fraction of a cent, not
a GPU charge); (3) only then, one controlled `reconstruct_mesh.remote()`
call using the already-cached, already-verified `REAL_SAM31_object_rgba.png`
as input.
