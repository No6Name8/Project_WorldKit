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
