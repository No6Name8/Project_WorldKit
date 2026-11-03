# WorldKit: submission text (ready to paste)

Every number here is measured and traceable to `docs/accuracy.md`, `docs/TECHNICAL_NOTES.md` or a run in `pipeline/runs/`.

## Project name

WorldKit

## Tagline

Film an object, get a real-size 3D model you can edit by typing and print.

## Track

Indie Developer

## Description (250 words)

WorldKit turns a short phone video into a real-size 3D model you can inspect, edit by typing, and 3D print.

You film an object with a coin beside it. Muse Spark names the object and picks the best frame. SAM 3.1 segments the object and the coin in every frame, and SAM 3D Objects, running on a Modal A100, rebuilds the object as a textured mesh. The coin fixes the scale and camera angle, and the mesh's proportions are fitted to the SAM silhouettes. On a real mug measured with a ruler, the model's height, rim and base came within +1.9%, +3.7% and -1.9%.

The browser viewer shows the model with millimetre dimensions and where its scale came from. You can type an edit such as "make the handle 2 mm thicker". Muse Wonder chooses and calls exact geometry tools, SAM 3.1 has already located the handle on the mesh, and Muse Spark compares before and after renders. Deterministic checks must also pass, otherwise the edit is rejected. Every version can be undone, and the STL downloads in millimetres.

Scene mode finds several objects on a desk, builds each one, and places them together at one scale. VGGT cameras are used to check that layout.

A phone web app uploads videos; a laptop worker processes them.

WorldKit reports its failures too: a run scaled by a bank card instead of a coin missed by up to 17%, published next to the good result.

## How each Meta model is used

- **Muse Spark:** names the object and picks the best frame, discovers the objects in a scene, and judges before/after renders of every AI Edit.
- **Muse Wonder:** turns a typed edit into calls to geometry tools (scale, engrave text, drill, flatten, split, thicken, mirror, thicken a part).
- **SAM 3.1:** segments the object, the coin and every scene object in every frame, and finds the mug handle for part edits.
- **SAM 3D Objects:** reconstructs each object's textured 3D mesh from one masked frame, on a Modal A100.
- **VGGT:** estimates per-frame cameras on a Modal A100 to check the scene layout (silhouette agreement) and to correct perspective per object.

## What is new in the hackathon

Everything in the repository was built between Oct 26 and Nov 4, 2026; the commit history shows it. The first commit holds only the README, CLAUDE.md, .gitignore and the license. That covers:

- the video-to-model pipeline;
- coin-grounded scale with a ruler check;
- the stripe-pattern texture model with held-out validation;
- AI Edit (Muse Wonder tools, Muse Spark verification, versions and undo);
- SAM-located part edits;
- scene mode with VGGT verification;
- the three.js viewer;
- the Supabase web app and laptop worker;
- the tests.

Only the models and the vendored libraries (three.js, Rapier, fonts) come from outside.

## Tech stack

- **Python 3.10:** NumPy, OpenCV, trimesh, manifold3d, FastAPI, Flask, the OpenAI SDK pointed at the Meta Model API
- **GPU:** Modal (A100) for SAM 3D Objects and VGGT
- **Viewer:** three.js, with Rapier physics
- **Web app:** Vite, supabase-js; Supabase (Auth, Postgres with Row Level Security, Storage, Realtime); Vercel
- **Tests:** pytest and Playwright

## Links

- Repository: https://github.com/No6Name8/Project_WorldKit
- Live site: https://web-flame-psi-45.vercel.app (demo viewer without an account: https://web-flame-psi-45.vercel.app/viewer/index.html?demo=1)
- Demo video: TODO (add the link after upload)

## What makes it different

WorldKit checks its output against the real object instead of only showing a 3D model. Scale comes from a visible reference and is scored against a ruler; the model reports which parts are observed and which are generated. AI edits are made by geometry tools rather than by regenerating the mesh, so "2 mm thicker" means 2 mm. Each edit is checked twice: by deterministic tests and by a second model looking at renders. Failures are kept in the open; the card run's 17% error is published next to the 2% result.
