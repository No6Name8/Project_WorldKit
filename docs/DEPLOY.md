# Deploy the web app (Vercel)

The web app is a static Vite build (`web/`) that also serves the viewer at `/viewer/`. The build copies `viewer/` into the app, so Vercel builds from the repository root. `vercel.json` sets the commands, headers and the fallback rewrite.

Live: https://web-flame-psi-45.vercel.app (Vercel team `no6-name8`, project `web`).

## Normal route: push to main (automatic)

The Vercel project is linked to GitHub `No6Name8/Project_WorldKit`, production branch `main`. Every push to `main` builds and deploys to production; nothing else is needed.

```powershell
git push origin main
npm --prefix web run check:live      # after the deploy shows Ready (about 1 minute)
```

Build settings (from `vercel.json`): install `npm --prefix web ci`, build `npm --prefix web run build`, output `web/dist`. Production environment variables on Vercel: `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (the publishable key, never the secret key).

**Why Git deploys failed from 48632e0 to 59c3e8c:** `.vercelignore` listed `scripts`, and an unanchored pattern also matches `web/scripts/`, so `web/scripts/copy-viewer.mjs` was never uploaded and the build stopped with `MODULE_NOT_FOUND`. The patterns are now anchored (`/scripts`). Keep every new entry in `.vercelignore` anchored with a leading `/`.

To see why a deploy failed: Vercel dashboard > project `web` > Deployments > the failed one > Build Logs.

## Fallback: prebuilt deploy from Windows

Use this only if a Git deploy fails and you need the site up now. `vercel --prod` (source upload) timed out from this laptop once, and `vercel build` fails on Windows because `npm ci` hits locked files.

```powershell
# 1. Windows npm-lock workaround: stop anything holding files in web/node_modules
#    (vite dev / vite preview / a node process), or npm ci fails with EPERM / EBUSY.
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'vite' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. build with web/.env.local present (the Supabase URL and publishable key are baked into the bundle)
npm --prefix web ci
npm --prefix web run build

# 3. Build Output API folder
Remove-Item -Recurse -Force .vercel\output -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force .vercel\output\static | Out-Null
Copy-Item -Recurse web\dist\* .vercel\output\static\
Set-Content -Encoding utf8 .vercel\output\config.json '{"version":3,"routes":[{"src":"/sw.js","headers":{"cache-control":"no-cache"},"continue":true},{"src":"/manifest.webmanifest","headers":{"content-type":"application/manifest+json"},"continue":true},{"handle":"filesystem"},{"src":"/(.*)","dest":"/index.html"}]}'

# 4. deploy (the token is read from the environment; never paste it into a file in the repo)
$env:VERCEL_TOKEN = (Select-String -Path pipeline\.env -Pattern '^VERCEL_TOKEN=(.*)$').Matches[0].Groups[1].Value
vercel deploy --prebuilt --prod --token $env:VERCEL_TOKEN --scope no6-name8
Remove-Item Env:VERCEL_TOKEN
```

`.vercel/` is gitignored.

## Check the live site

```powershell
cd viewer\tests; npm install; cd ..\..        # once: Playwright
npm --prefix web run check:live               # or: node scripts/check_live.mjs https://other-url
```

It runs at 390 and 1440 px and checks:

- the home page, with the demo viewer card and its size in mm;
- "Open demo viewer";
- the Help page;
- sign-in reaching the projects list (with `WORKER_EMAIL` / `WORKER_PASSWORD` from the environment or `pipeline/.env`);
- the PWA manifest and its icons;
- the service worker;
- the fallback for an unknown path;
- no console errors and no failed requests.

Screenshots go to `docs/web/live_*.png`.

## Supabase settings to confirm by hand

- **Storage limits (2026-11-04):** run `supabase/migrations/20261104000000_upload_limits.sql` once in the Supabase SQL editor (it is also at the end of `supabase/RUN_THIS_IN_SQL_EDITOR.sql`). It sets 50 MB and video-only types on `videos`, and 50 MB and GLB/STL/JSON/PNG/JPEG on `results`. Until it runs, only the web app and the worker enforce these limits.


Supabase dashboard > Authentication > URL Configuration:

- **Site URL:** `https://web-flame-psi-45.vercel.app`
- **Redirect URLs:** add `https://web-flame-psi-45.vercel.app/**`

Email-confirmation links point to the Site URL, so new sign-ups land on localhost until it is set.

## Notes

- The app uses hash routes (`#/projects`). The `/(.*) -> /index.html` rewrite only serves the app for unknown paths; real files win because Vercel checks the filesystem first.
- The service worker caches the app shell only. After a deploy, one reload picks up the new version.
- The worker is not deployed: it runs on the laptop (`python -m worldkit.worker`).
