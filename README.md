![WorldKit: Film it. Print it.](docs/assets/banner.png)

**WorldKit turns a short phone video of a real object into a 3D model, with its scale estimated from a coin or card, that you can edit by typing and 3D print.**

[![Live demo](https://img.shields.io/badge/live_demo-web--flame--psi--45.vercel.app-2f6ed2)](https://web-flame-psi-45.vercel.app)
[![License: MIT](https://img.shields.io/badge/license-MIT-a6adb8)](LICENSE)
[![Built for Meta Global AI Developer Hackathon 2026](https://img.shields.io/badge/built_for-Meta_Global_AI_Developer_Hackathon_2026-203754)](docs/SUBMISSION.md)

[Live app](https://web-flame-psi-45.vercel.app) · Demo video (TODO: link after upload) · [Submission text](docs/SUBMISSION.md) · [Accuracy](docs/accuracy.md)

## What it does

- **Object mode.** Film an object next to a coin. You get a textured 3D model, its size in mm, and an STL in millimetres. On the one mug measured with a ruler, two independent runs had a mean error within about 5% (2.5% and 4.6%; worst single dimension 6.0%).
- **AI Edit.** Type "make the handle 2 mm thicker". Muse Wonder applies exact geometry tools, Muse Spark checks before/after renders, and every version can be undone.
- **Map mode.** Film a desk. VGGT gives every frame a camera, the table top is fused from SAM masks into one stadium shape, each object (mug, case, candle) is rebuilt with SAM 3D and placed by triangulation, the coin is added as known geometry, and a diorama STL fits a 256 mm print bed. Held-out silhouette overlap: mean IoU 0.64.
- **Phone app.** Upload from the phone; a laptop worker processes the video, and the result opens in the same viewer.

**Source tab:** the original video next to the 3D model built from it, with the frames the pipeline used and each one's SAM 3.1 mask outline. Click a frame to jump the video there. The demo opens on this tab.

![Source tab, dark: the original mug video, the model built from it, and the frames used with their SAM 3.1 outlines](docs/ui/source_mug_1440.png)

Calm dark theme by default, and a light theme (sun/moon button in the viewer, account menu in the web app; the first visit follows your system setting).

| Dark | Light |
|---|---|
| ![Object mode, dark: the coin-scaled mug with W/H/D in mm](docs/ui/1_object_1440.png) | ![Object mode, light](docs/ui/1_object_1440_light.png) |
| ![AI Edit, dark: SAM found the handle (teal overlay)](docs/ui/5_parts_overlay_1440.png) | ![AI Edit, light: thicken_part on the handle, with version history](docs/ui/3_ai_edit_1440_light.png) |
| ![Map mode, dark: desk scene with three objects and the diorama button](docs/ui/2_scene_1440.png) | ![Map mode, light](docs/ui/2_scene_1440_light.png) |
| ![Web app home, dark, with the live demo card](docs/ui/home_1440.png) | ![Projects, light, with status chips](docs/ui/projects_1440_light.png) |

Phone width: [home](docs/ui/home_390.png), [Source](docs/ui/source_mug_390.png), [viewer](docs/ui/1_object_390.png), [AI Edit](docs/ui/3_ai_edit_390_light.png). Coin desk Source tab with its held-out verify frame: [1440](docs/ui/source_desk_1440.png), [390](docs/ui/source_desk_390.png). Motion (8 s clips): [home scroll](docs/ui/motion_home.webm), [tab change and an AI Edit row](docs/ui/motion_edit.webm).

The light AI Edit screenshot uses real geometry edits on the real mug, with scripted Wonder and Spark replies (`viewer/tests/ui_screens.js`). The live runs with real models are listed under Results.

## 3D environment

Map mode turns one phone video of a desk into a 3D environment: a table with its scale estimated from a coin or card, the objects on it, the scale reference object as known geometry, and a printable diorama. Two scenes are bundled. In the viewer, **Demo run** in Map loads the coin desk, and **Demo: desk, three laps** (Developer tools) loads the second.

| Coin desk (`desk_scene_coin.mp4`, 33 s) | Three-lap desk (`desk_scene_card.mp4`, 113 s) |
|---|---|
| ![Coin desk: stadium table, coin, candle, mug, case](docs/ui/desk_v2_view2.png) | ![Three-lap desk: table with its gold base, card, incense burner, bottle, mug, case](docs/ui/desk3_view4.png) |
| ![Coin desk, second angle](docs/ui/desk_v2_view3.png) | ![Three-lap desk, low angle on the base](docs/ui/desk3_view2_low.png) |
| ![Coin desk, third angle](docs/ui/desk_v2_view1.png) | ![Three-lap desk, from the end](docs/ui/desk3_view3_low.png) |
| ![Verify: real, before, after with VGGT cameras](docs/ui/desk_v2_verify_vggt_view0.png) | ![Verify: real, before, after with VGGT cameras](docs/ui/desk3_verify_vggt.png) |
| ![Coin desk diorama](docs/ui/desk_v2_diorama.png) | ![Three-lap desk diorama with the base](docs/ui/desk3_diorama.png) |

| | Coin desk (default demo) | Three-lap desk |
|---|---|---|
| Objects | table top, coin (reference), mug, case, candle in holder: 3 SAM 3D meshes | table top + base, card (reference), mug, case, bottle (SAM 3D), incense burner (revolved profile) |
| Scale | coin 21.5 mm measured on the table plane, 5 frames | card 85.6 x 54 mm, 4 corners on the table plane, 7 frames |
| Held-out silhouette IoU (6 frames, VGGT cameras) | mean 0.643 (mug 0.61, case 0.61, candle 0.71; table 0.58) | mean 0.542 (burner 0.64, case 0.63, bottle 0.38, mug 0.19; table 0.50) |
| Mug vs ruler (70 / 85 / 65 mm) | +6.1 / -8.9 / -5.1 % | +22.6 / +2.8 / -1.8 % (SAM found the mug in only 4 frames) |
| Table top | 121.0 x 60.0 cm stadium | 118.3 x 56.3 cm (card vs coin scale: -2.2 % length, -6.2 % width) |
| Not measured, estimated | table thickness 2 cm; table size not ruler-checked | table height 14.6 cm (likely low), ring and post sizes, burner shape |

How it is built (`pipeline/worldkit/desk.py`):

1. VGGT gives every frame a camera.
2. The table top is fused from SAM 3.1 masks projected onto the fitted plane, then fitted as a stadium.
3. Objects are rebuilt with SAM 3D and placed by triangulating their mask centres across views.
4. Scale and rotation are fitted on some frames; the IoU above is measured on 6 others.

Details and the full tables are in [docs/accuracy.md](docs/accuracy.md).

## How it works

```mermaid
flowchart LR
  Phone["Phone (web app)"] -- "upload video, read status" --> SB["Supabase: Auth, projects, Storage"]
  SB -- "Realtime status" --> Phone
  Worker["Laptop worker<br/>worldkit.worker"] -- "claim, download video, upload results" --> SB
  Worker --> Pipeline["worldkit.object / worldkit.scene"]
  Pipeline --> Meta["Meta Model API: Muse Spark, SAM 3.1"]
  Pipeline --> Modal["Modal A100: SAM 3D Objects, VGGT"]
  Phone -- "signed URLs" --> Viewer["three.js viewer"]
  Viewer -- "AI Edit (laptop: worldkit.serve)" --> Edit["Muse Wonder + Muse Spark"]
```

1. Video in. The pipeline keeps the 12 sharpest frames (sampled at 2 fps).
2. Muse Spark names the object and picks the best frame.
3. SAM 3.1 masks the object and the coin in every frame.
4. SAM 3D Objects builds a textured mesh from the best masked frame (Modal A100).
5. The coin sets the scale and camera angle, the mesh proportions are fitted to the SAM silhouettes, and print prep writes a watertight STL in mm.
6. The viewer shows it, and AI Edit changes it (Muse Wonder tools, Muse Spark verification, undo).

More diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Meta models used

| Model | What it does here | File |
|---|---|---|
| Muse Spark (`muse-spark-1.3`) | Names the object and picks the best frame; analyses the frames; discovers scene objects and proposes layout fixes; turns typed moves into transforms; judges every AI Edit from renders | [detect.py](pipeline/worldkit/detect.py), [understand.py](pipeline/worldkit/understand.py), [mapmode.py](pipeline/worldkit/mapmode.py), [editcmd.py](pipeline/worldkit/editcmd.py), [edit.py](pipeline/worldkit/edit.py) |
| Muse Wonder (`muse-wonder-1.0`) | Turns a typed edit into geometry tool calls (scale, engrave, drill, flatten, split, thicken, mirror, thicken a part) | [edit.py](pipeline/worldkit/edit.py), [edit_tools.py](pipeline/worldkit/edit_tools.py) |
| SAM 3.1 (`sam-3.1`) | Masks the object, the coin and each scene object in every frame; finds the mug handle for part edits | [segment.py](pipeline/worldkit/segment.py), [parts.py](pipeline/worldkit/parts.py) |
| SAM 3D Objects | Builds each object's textured 3D mesh from one masked frame, on a Modal A100 | [modal_app/sam3d.py](pipeline/modal_app/sam3d.py) |
| VGGT-1B | Per-frame cameras to check scene layout and correct perspective per object (Map mode, `--vggt`) | [modal_app/vggt_probe.py](pipeline/modal_app/vggt_probe.py), [vggt_cams.py](pipeline/worldkit/vggt_cams.py) |

Function-level call sites are listed in [AGENTS.md](AGENTS.md).

## Results

Object mode, scale estimated from a coin, against a ruler. The same mug video, run twice:

| Object | Dimension | Ruler | Run 1: stored SAM 3D mesh (CLI) | Run 2: fresh SAM 3D mesh (live web upload + worker) |
|---|---|---:|---:|---:|
| Mug | Height | 70 mm | 71.3 mm (+1.9%) | 74.2 mm (+6.0%) |
| | Rim diameter | 85 mm | 88.1 mm (+3.7%) | 87.0 mm (+2.4%) |
| | Base diameter | 65 mm | 63.8 mm (-1.9%) | 61.4 mm (-5.5%) |
| | Mean absolute error | | 2.5% | 4.6% |
| Hydro Flask 32 oz bottle (`runs/object2`) | Height, body diameter, cap rim width | 240 / 90 / 70 mm | not scored | - |

**The mean error is within about 5% across two independent runs; the worst single dimension is 6.0% (run 2's height).** Each run that builds a new SAM 3D mesh gets slightly different geometry, so the numbers vary by a few percent: the two runs differ from each other by +4.1 / -1.2 / -3.8%. Run 1 (`runs/mug_grounded`) is the bundled demo mug. It used a mesh generated earlier, and the CLI reproduces it exactly from the same video. Run 2 is a real upload on the live site, processed by the laptop worker with a new mesh ([docs/results/e2e_web_real.json](docs/results/e2e_web_real.json)). Run 1 is partly tuned by hindsight: the taper fit was added after seeing its base error. Run 2 used the same code afterwards, so it is the fairer estimate of a new run.

The Hydro Flask, a second object filmed the same way, could not be scored. SAM 3.1 found the coin in only 1 of 12 frames, so its scale stayed a placeholder. That leaves one mug as the only scored object.

The full tables, and how the ground truth was measured, are in [docs/accuracy.md](docs/accuracy.md). With a bank card instead of a coin (scene mode), the error rose to +16.9 / -8.8 / -12.3%: the mug was only about 65 px wide in the card frames, and SAM 3.1 never found the card.

**Reproducible numbers.** Every accuracy claim has a machine-readable file in [docs/results/](docs/results/), and `docs/accuracy.md` is generated from them (`python scripts/accuracy_table.py --from-results`). Each file holds:

- the command;
- the input video's name and SHA-256;
- the measured STL's path and SHA-256;
- the measured values, the ruler values and the errors.

The small run reports are copied to `docs/results/reports/`. Not included: the run folders in `pipeline/runs/` (frames, caches, SAM 3D meshes and VGGT outputs, hundreds of MB per run) and most sample videos. The repo carries only `mug_on_table.mp4` and the demo meshes the viewer bundles. The SHA-256 values identify the exact files the numbers came from.

Run live with the real models:

- **AI Edit on the mug** (`pipeline/runs/edit_demo`):
  - engraved text on the base
  - set the height to exactly 120.0 mm
  - drilled a 5 mm hole
  - undo
- **Part edit** (`pipeline/runs/part_live`): the handle wall went from 6.9 to 9.4 mm, with the body unchanged (0.0 mm).
- **Rehearsed demo edits** ([docs/DEMO_EDITS.md](docs/DEMO_EDITS.md)): five instructions, twice each, all applied. They can be replayed on the public site: open the demo viewer, then the AI Edit tab. It is labelled as a replay of the recorded run.

## Try it

Live: https://web-flame-psi-45.vercel.app. The demo viewer needs no account. Uploads need an account and a running laptop worker.

Locally (Windows, Python 3.10, no GPU or API key needed for the demo):

```powershell
git clone https://github.com/No6Name8/Project_WorldKit.git
cd Project_WorldKit
py -3.10 -m venv pipeline\.venv
pipeline\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
pipeline\.venv\Scripts\python.exe viewer\server.py
```

Open http://127.0.0.1:5050/ and click **Demo run**: the bundled coin-scaled mug (Object) or the desk scene (Map). It works offline.

Your own video (needs `MODEL_API_KEY` and Modal, see Configuration):

```powershell
cd pipeline
.venv\Scripts\python.exe -m worldkit.object samples\mug_on_table.mp4 --out runs\my_mug --ref-diameter-cm 2.15
.venv\Scripts\python.exe -m worldkit.serve runs\my_mug --port 5070     # viewer with AI Edit
```

Laptop worker for the phone app (needs `WORKER_EMAIL`, `WORKER_PASSWORD` and the Supabase values):

```powershell
cd pipeline
.venv\Scripts\python.exe -m worldkit.worker --once --dry-run   # test the chain: publishes the bundled demo, no GPU, no API
.venv\Scripts\python.exe -m worldkit.worker                    # process uploads as they arrive
```

## Configuration

Values live in gitignored files (`pipeline/.env`, `web/.env.local`) or in Modal; none are committed.

| Variable | For | Where to get it | File |
|---|---|---|---|
| `MODEL_API_KEY` | Muse Spark, Muse Wonder, SAM 3.1 (Meta Model API) | https://dev.meta.ai | `pipeline/.env` |
| `HF_TOKEN` | Gated SAM 3D Objects weights (as Modal secret `huggingface`); TRELLIS fallback | huggingface.co > Settings > Access Tokens, after access to `facebook/sam-3d-objects` | Modal secret; optional in `pipeline/.env` |
| Modal login | Runs SAM 3D Objects and VGGT on an A100 | `python -m modal setup` (stored by Modal in your user folder) | none in the repo |
| `VITE_SUPABASE_URL` | Web app and worker: the Supabase project | Supabase > Project Settings > API | `web/.env.local` |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Web app and worker (publishable key only, never the secret key) | Supabase > Project Settings > API Keys | `web/.env.local` |
| `WORKER_EMAIL`, `WORKER_PASSWORD` | Laptop worker signs in as this user; also used by `check:live` | An account you create in the web app | `pipeline/.env` |
| `VERCEL_TOKEN` | Optional: prebuilt deploy fallback only | Vercel > Account Settings > Tokens | `pipeline/.env` |

## Tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_all.ps1     # or: bash scripts/test_all.sh
npm --prefix web run check:live                                    # the deployed site
```

Latest run (2026-11-03):

| Suite | Result |
|---|---|
| pytest (`pipeline/tests`) | 243 passed |
| Browser: scene-physics, demo-readiness, AI Edit, map scene, parts | 50/50, 10/10, 14/14, 19/19, 10/10 |
| Browser: viewer, integration, SAM 3D | 31 checks passed; each stops at its one real Muse Spark call, which was not made (pipeline frozen, no API calls) |
| Web unit, web e2e (mocked Supabase) | 8/8, 46/46 |
| Colour contrast (`scripts/check_contrast.mjs`), dark and light | 88/88 token pairs at WCAG AA |
| Web e2e (real Supabase), live site (`check:live`) | 19/19 (2026-11-03), 16/16 |

The map-scene suite timed out once under heavy machine load and passed 19/19 on two reruns.

## Built during the hackathon

Everything in this repository was written between 2026-10-26 (first commit `e674b46`: README, CLAUDE.md, .gitignore and license only) and 2026-11-03 (74 commits when this README was written), for the Meta Global AI Developer Hackathon 2026. The commit history is the record. Not written here: the models, and the vendored three.js, Rapier and fonts.

## Limitations and what is next

- **One mug is the only measured object.** The headline result is partly tuned by hindsight: the taper fit was added after seeing the base error, though it uses no ground truth.
- **Card scale is not reliable yet** (+16.9 / -8.8 / -12.3%). Next: pick frames where the object and the reference are both large.
- **All unseen geometry is generated by SAM 3D, not measured.** Scene placement uses a weak-perspective fit (layout, not metrology).
- **Part edits work on the mug handle only.** Scene AI Edit is tested but has not been run live.
- **The hosted viewer has no AI Edit**; it needs `worldkit.serve` on the laptop.
- **The upload → worker path has been run for real once** (2026-11-04, 715 s on the laptop). It worked end to end, and the fresh SAM 3D mesh came out +6.0 / +2.4 / -5.5% against the ruler.
- Next: a second measured object, a 360-degree capture guide, and multi-view geometry (VGGT depth) so shape is grounded in the footage.

## Repo map

```
pipeline/             Python pipeline (README)
  worldkit/           modules: modes, Meta API clients, scale, print prep, AI Edit, servers, worker (README)
  modal_app/          SAM 3D Objects and VGGT on Modal A100
  tests/              pytest suite
  samples/            headline video, ruler ground truth
viewer/               three.js viewer, demo data, Playwright suites (README)
web/                  phone web app, Supabase client, PWA (README)
supabase/             database migration and policies
scripts/              test runners, live check, accuracy table, logo (README)
docs/                 accuracy, architecture, submission, demo script, deploy, notes, screenshots (README)
AGENTS.md, llms.txt   orientation for AI reviewers
CLAUDE.md             working notes for the coding agent
```

MIT License, see [LICENSE](LICENSE). Created by Abdullah Ali Alanazi ([@No6Name8](https://github.com/No6Name8)).
