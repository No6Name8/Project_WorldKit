# AGENTS.md

Orientation for AI agents and human reviewers. The same text is in `llms.txt`.

## What this project is

WorldKit turns a short phone video of a real object into a 3D model at real size. You can inspect the model, edit it by typing, and download it as an STL in millimetres. Built for the Meta Global AI Developer Hackathon 2026 (commits from 2026-10-26).

- **Input:** a video of an object next to a coin, or a bank card.
- **Output:** a textured GLB, a print STL in mm, and `report.json`. The report records which model produced what, what was observed vs generated, and the scale source.
- **Headline result:** one real mug against a ruler, Object mode, coin scale. Height +1.9%, rim +3.7%, base -1.9% (`docs/accuracy.md`, table a).

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

- **Accuracy vs a ruler:** `docs/accuracy.md` (three tables: headline, scene with coin, scene with card).
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
- **The real pipeline has not run through the web upload → worker path.** Only `--dry-run` was used there.

## Rules for agents working here

See `CLAUDE.md`:

- Never print or commit keys. They live in gitignored `pipeline/.env` and `web/.env.local`.
- No commits or pushes without approval.
- Never report a number that was not measured.
