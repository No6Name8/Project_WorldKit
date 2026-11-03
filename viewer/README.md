# viewer

The three.js viewer used everywhere: locally (`viewer/server.py`, `worldkit.serve`) and on the live site (`/viewer/`, copied into the web build by `web/scripts/copy-viewer.mjs`).

| Path | What |
|---|---|
| `index.html` | The whole viewer: Object and Map views, tabs Viewer / AI Edit / Accuracy, mm dimensions, SAM overlay, export. Query flags: `?demo=1`, `?embed=1`, `?preview=1`. |
| `physics.js` | Rapier rigid-body drop test. Dynamic objects use a convex hull and static ones an exact mesh; not engineering accuracy. |
| `server.py` | Flask dev server (`pipeline/worldkit/webserver.py`) on port 5050; `--open RUN_DIR` loads a pipeline output. |
| `assets/` | `tokens.css` (design tokens), `app.css`, `logo.svg` (favicon), `fonts/` (Inter, IBM Plex Mono, OFL), `sample_mug.glb` (used by `browser_test.js`). |
| `assets/demo/` | Bundled real pipeline output so the demo works offline. `run_mug` is the headline coin-scaled mug, `run_desk` the desk scene, `mug` and `scene` older runs, `accuracy.md` a copy of `docs/accuracy.md`. |
| `vendor/` | three.js (MIT) and Rapier (Apache-2.0), vendored so there is no CDN dependency. |
| `tests/` | 8 Playwright suites plus `ui_screens.js` (screenshots). Run `npm install`, start `server.py`, then `npm run test:all`, or use `scripts/test_all.ps1`. |

```powershell
pipeline\.venv\Scripts\python.exe viewer\server.py        # http://127.0.0.1:5050/, click "Demo run"
```
