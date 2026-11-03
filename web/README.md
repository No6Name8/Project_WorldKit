# web

The phone web app (Vite, no framework, PWA). It signs you in, uploads a video, shows each project's status live, and opens finished results in the viewer. It never runs the pipeline; the laptop worker does (`python -m worldkit.worker`).

Live: https://web-flame-psi-45.vercel.app (deploys on every push to `main`; see `docs/DEPLOY.md`).

| Path | What |
|---|---|
| `src/main.js` | Shell and hash routes: home, sign in, new project, projects, help |
| `src/pages/` | One file per page |
| `src/supabase.js`, `src/upload.js`, `src/lib.js` | Supabase client, upload with progress, helpers |
| `public/` | `manifest.webmanifest`, `sw.js` (caches the app shell only), `icons/` |
| `scripts/copy-viewer.mjs` | Copies `../viewer` into the build at `/viewer/` |
| `tests/` | `lib.test.js` (unit), `e2e_mock.mjs` (mocked Supabase), `e2e_real.mjs` (real Supabase + worker dry run) |

```powershell
cd web
npm install
npm run dev                # http://localhost:5173 (needs web/.env.local, see README Configuration)
npm test                   # unit tests
npm run check:live         # checks the deployed site (scripts/check_live.mjs)
```
