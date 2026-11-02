# Deploy the web app (Vercel)

The web app is a static Vite build (`web/`) that also serves the viewer at `/viewer/`. It needs the whole repository at build time (it copies `viewer/` into the build), so deploy from the repository root. `vercel.json` already sets the commands.

## Shortest manual steps

1. Vercel dashboard > Add New > Project > import the GitHub repository `No6Name8/Project_WorldKit`.
2. Leave **Root Directory** empty (the repository root). Framework preset: Other. The build settings come from `vercel.json`:
   - Install: `npm --prefix web ci`
   - Build: `npm --prefix web run build`
   - Output directory: `web/dist`
3. Environment Variables (Production and Preview):
   - `VITE_SUPABASE_URL` = the project URL
   - `VITE_SUPABASE_PUBLISHABLE_KEY` = the publishable key (Supabase > Project Settings > API Keys). Never the secret key.
4. Deploy. Open the URL, sign up, and check the projects page loads.
5. Supabase > Authentication > URL Configuration: add the Vercel URL to Site URL and Redirect URLs (needed only for email confirmation links).

## CLI

```powershell
npm i -g vercel
vercel login
vercel link          # from the repository root
vercel env add VITE_SUPABASE_URL production
vercel env add VITE_SUPABASE_PUBLISHABLE_KEY production
vercel --prod
```

## Prebuilt deploy (what worked from Windows)

`vercel --prod` uploads every source file and timed out here, and `vercel build` fails when `npm ci` hits locked files. This works: build locally, then deploy the output.

```powershell
npm --prefix web run build
# copy web/dist to .vercel/output/static and write .vercel/output/config.json:
#   {"version":3,"routes":[{"src":"/sw.js","headers":{"cache-control":"no-cache"},"continue":true},
#     {"src":"/manifest.webmanifest","headers":{"content-type":"application/manifest+json"},"continue":true},{"handle":"filesystem"}]}
vercel deploy --prebuilt --prod --token $env:VERCEL_TOKEN
```

The build must run with `web/.env.local` present (the Supabase values are baked into the bundle). Stop any running `vite preview` first, or `npm ci` fails on locked files.

## Notes

- Hash routing (`#/projects`), so no rewrite rules are needed.
- The service worker caches the app shell only. After a deploy, one reload picks up the new version.
- The worker is not deployed: it runs on your laptop (`python -m worldkit.worker`).
