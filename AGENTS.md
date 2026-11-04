# AGENTS.md

Orientation for AI agents and human reviewers. The same text is in `llms.txt`.

## What this project is

WorldKit turns a short phone video of a real object into a 3D model with its scale estimated from a coin or card. You can inspect the model, edit it by typing, and download it as an STL in millimetres. Built for the Meta Global AI Developer Hackathon 2026 (commits from 2026-10-26).

- **Input:** a video of an object next to a coin, or a bank card.
- **Output:** a textured GLB, a print STL in mm, and `report.json`. The report records which model produced what, what was observed vs generated, and the scale source.
- **Headline result:** one real mug against a ruler, Object mode, coin scale, two independent runs of the same video. Run 1 (stored SAM 3D mesh, the demo mug): height +1.9%, rim +3.7%, base -1.9%. Run 2 (fresh SAM 3D mesh, live web upload + worker): +6.0 / +2.4 / -5.5%. Mean error within about 5% across the two runs (2.5% and 4.6%), worst single dimension 6.0%; each run builds a new SAM 3D mesh, so numbers vary by a few percent (`docs/accuracy.md`, table a; `docs/results/object_mug_coin*.json`).

## Repo map

```
pipeline/            Python 3.10 pipeline (package worldkit), Modal apps, tests, sample video, ground truth
  worldkit/          pipeline modules: object/scene/map mode, Meta API clients, scale, print prep, AI Edit, servers, worker
  modal_app/         GPU code run on Modal: SAM 3D Objects (sam3d.py), VGGT (vggt_probe.py)
  tests/             pytest suite (Meta API and Modal mocked)
  samples/           mug_on_table.mp4 (the headline video), ground_truth.json (ruler; evaluation only)
viewer/              three.js viewer (index.html), Rapier physics, Flask dev server, bundled demo runs, Playwright suites
web/                 Vite web app (phone upload, projects, viewer embed) on Supabase; deployed to Vercel
supabase/            database migration (projects table, RLS, storage buckets, Realtime)
scripts/             accuracy table, live-site check, brand assets, test runners, analysis helpers
docs/                accuracy, architecture, submission text, demo script, deploy, technical notes, screenshots, logo
```

## Where each Meta model is called

| Model | File | Functions |
|---|---|---|
| Muse Spark `muse-spark-1.3` (Meta Model API) | `pipeline/worldkit/meta_api.py` | `ask_muse_spark_with_usage` (the one client; counts calls against `WK_BUDGET_FILE`) |
| | `pipeline/worldkit/detect.py` | `detect_object`: names the object, picks the best frame |
| | `pipeline/worldkit/understand.py` | `analyse_frames`: structured frame analysis that gates the stripe-pattern stage |
| | `pipeline/worldkit/mapmode.py` | `discover_objects` (scene objects), `verify_loop` (layout corrections, kept only if silhouette IoU improves) |
| | `pipeline/worldkit/editcmd.py` | `interpret_edit_instruction`: typed move/rotate/scale in the viewer |
| | `pipeline/worldkit/edit.py` | `verify`: judges before/after renders of an AI Edit |
| Muse Wonder `muse-wonder-1.0` (Meta Model API, function calling) | `pipeline/worldkit/edit.py` | `run_edit`, `wonder_tool_step`; tools in `edit_tools.py` (`scale_to`, `emboss_text`, `drill_hole`, `flatten_base`, `split`, `thicken`, `mirror`, `thicken_part`) |
| SAM 3.1 `sam-3.1` (Meta Model API) | `pipeline/worldkit/segment.py` | `segment_concept` (one concept per call, cached by image hash) |
| | callers | `object.run`, `scene.run`/`scene.choose_frame`, `mapmode.cached_mask`/`build_tracks`, `parts.find_part` (mug handle) |
| SAM 3D Objects (open weights, Modal A100-40GB) | `pipeline/modal_app/sam3d.py` | `reconstruct_mesh` (GPU function), `run_remote`, `run_remote_many` |
| | callers | `object.run_sam3d_on_modal` (Object and two-object scene), `mapmode._run` (Map mode, parallel) |
| VGGT-1B (open weights, Modal A100) | `pipeline/modal_app/vggt_probe.py` | `run_vggt` (GPU function) |
| | `pipeline/worldkit/vggt_cams.py` | `run_vggt` (cached client), `verify_with_vggt`, `perspective_corrections`; used by Map mode with `--vggt` |
| | `pipeline/worldkit/desk.py` | desk scene v2: VGGT cameras for the table plane, fused table outline, triangulated placement and held-out verify |

Not Meta: TRELLIS (hosted fallback, `reconstruct.py`), trimesh/manifold3d, Rapier, three.js, Supabase, Vercel.

## 3D environment (Map mode)

Map mode turns one phone video of a desk into a 3D environment: a table with its scale estimated from a coin or card, the objects on it, the scale reference object as known geometry, and a printable diorama. Two scenes are bundled. In the viewer, **Demo run** in Map loads the coin desk, and **Demo: desk, three laps** (Developer tools) loads the second.

Screenshots: `docs/ui/desk_v2_*` (coin desk) and `docs/ui/desk3_*` (three-lap desk).

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

## Main commands (Windows)

From the repo root:

```powershell
pipeline\.venv\Scripts\python.exe viewer\server.py              # viewer + bundled demo, http://127.0.0.1:5050/
pipeline\.venv\Scripts\python.exe scripts\accuracy_table.py     # model vs ruler table
```

From `pipeline/` (the `worldkit` package lives there):

```powershell
.venv\Scripts\python.exe -m worldkit.object VIDEO --out runs\NAME --ref-diameter-cm 2.15          # Object mode
.venv\Scripts\python.exe -m worldkit.scene VIDEO --out runs\NAME --coin saudi-25-halalas --vggt   # Map mode
.venv\Scripts\python.exe -m worldkit.parts runs\NAME                                              # SAM 3.1 finds the handle
.venv\Scripts\python.exe -m worldkit.edit runs\NAME "make the handle 2 mm thicker"                # AI Edit (--undo, --list)
.venv\Scripts\python.exe -m worldkit.serve runs\NAME --port 5070                                  # viewer with AI Edit (Chrome blocks 5060)
.venv\Scripts\python.exe -m worldkit.worker --once [--dry-run]                                    # laptop worker for the web app
```

## Tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_all.ps1        # every offline suite, one summary line each
bash scripts/test_all.sh                                             # same, Git Bash / Linux
npm --prefix web run check:live                                      # the deployed site, 390 and 1440 px
```

| Suite | Where | Notes |
|---|---|---|
| pytest | `pipeline/tests/` | Meta API and Modal mocked |
| 8 Playwright suites | `viewer/tests/*.js` | Real Chromium, real reconstructed meshes. Needs `viewer/server.py` running (the test runners start it). `browser_test`, `integration_test` and `sam3d_browser_test` each make one real Muse Spark call and need a key. |
| web unit, web e2e (mocked Supabase) | `web/tests/` | offline |
| web e2e (real Supabase), live-site check | `web/tests/e2e_real.mjs`, `scripts/check_live.mjs` | Need credentials in `pipeline/.env` and `web/.env.local` |

## Evidence

- **Accuracy vs a ruler:** `docs/accuracy.md`, generated from one JSON per claim in `docs/results/` (command, input and STL SHA-256, measured and ruler values) by `python scripts/accuracy_table.py --from-results`; `python scripts/make_results.py` rebuilds the JSON from local runs. The second object (Hydro Flask) is recorded there as not scored: SAM 3.1 found the coin in 1 of 12 frames.
- **AI Edit replay:** `viewer/assets/demo/edit_replay/` (from `scripts/make_edit_replay.py`): five real recorded edits the hosted demo viewer can replay without an edit server.
- **Measurements and diagnosis:** `docs/TECHNICAL_NOTES.md`. Failed experiments: `docs/what_we_tried.md`.
- **Screenshots:**
  - viewer: `docs/ui/`
  - web app and live site: `docs/web/`
  - comparisons: `docs/images/`
  - card preflight: `docs/ui/card_preflight.png`
- **Run outputs** are in `pipeline/runs/` (gitignored, local only). The viewer's bundled demo copies are in `viewer/assets/demo/`: `run_mug` is the headline run.
- **Test counts:** latest pass counts are in `README.md` (Tests).

## Known limitations

- **One mug is the only measured object.** The headline result is partly tuned by hindsight: the taper stage was added after seeing the base error.
- **Card scale missed by +16.9 / -8.8 / -12.3%.** The cause is a small mug in the card frames, plus SAM 3.1 never finding the card.
- **Scene placement uses a weak-perspective fit.** It is layout, not metrology.
- **All unseen geometry is generated by SAM 3D**, not measured.
- **Part edits work on the mug handle only.**
- **Scene AI Edit has not been run live.**
- **The hosted viewer has no AI Edit.** It needs `worldkit.serve` on the laptop.
- **The web upload → worker path was run for real once** (2026-11-04, `docs/results/e2e_web_real.json`): it works, and its fresh SAM 3D mesh differs from run 1's mesh by up to 4.1% (height); it is run 2 in the headline table.

## Rules for agents working here

See `CLAUDE.md`:

- Never print or commit keys. They live in gitignored `pipeline/.env` and `web/.env.local`.
- No commits or pushes without approval.
- Never report a number that was not measured.
