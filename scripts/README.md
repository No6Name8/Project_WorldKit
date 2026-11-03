# scripts

| Script | What it does |
|---|---|
| `test_all.ps1` / `test_all.sh` | Runs every test suite (pytest, 8 browser suites, web unit, web mocked e2e; `-Live` / `--live` adds real Supabase and the live site) and prints one line per suite. |
| `check_live.mjs` | Checks the deployed site at 390 and 1440 px: home and demo card, demo viewer, Help, sign-in, PWA manifest and service worker, 404 fallback. Writes `docs/web/live_*.png`. Also `npm --prefix web run check:live`. |
| `accuracy_table.py` | Measures print STLs (base plate excluded) and compares them with `pipeline/samples/ground_truth.json`; writes `docs/accuracy.md`. |
| `check_contrast.mjs` | WCAG contrast of every text/background token pair in both themes (`viewer/assets/tokens.css`); exits 1 below 4.5:1 (3:1 for non-text). |
| `record_motion.mjs` | Records `docs/ui/motion_home.webm` and `motion_edit.webm` and reports layout shift (CLS) and long frames; `REDUCED=1` measures the same flows with motion off. |
| `make_brand.mjs` | Writes the logo, banner, social preview, favicons and PWA icons from the app tokens. |
| `card_preflight.py` | Checks whether a bank card is found (SAM 3.1, then the rectangle detector) in the frames a scene run will use; writes a contact sheet. |
| `quick_views.py` | Shaded views of a GLB/STL from several angles, without a GPU or browser. |
| `part_edit_live.py` | Live check of part edits with real Muse Wonder and Muse Spark (call counts capped). Spends API calls. |
| `vggt_probe_analyze.py` | Analysis of the VGGT room-tour experiment (`docs/what_we_tried.md`). |

Python scripts run with `pipeline\.venv\Scripts\python.exe`. The Node scripts use Playwright from `viewer/tests` (`cd viewer/tests; npm install`).
