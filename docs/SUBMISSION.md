# WorldKit: submission text (ready to paste)

Every number here is measured and traceable to `docs/accuracy.md`, `docs/TECHNICAL_NOTES.md` or a run in `pipeline/runs/`.

## Project name

WorldKit

## Tagline

Film an object, get a 3D model, scaled from a coin or card, that you can edit by typing and print.

## Track

Indie Developer

## Description (250 words)

WorldKit turns a short phone video into a 3D model, with its scale estimated from a coin or card, that you can inspect, edit by typing, and 3D print.

You film an object beside a coin. Muse Spark names it and picks the best frame, SAM 3.1 segments the object and coin in every frame, and SAM 3D Objects on a Modal A100 rebuilds the object as a textured mesh. The coin fixes the scale and camera angle, and the mesh's proportions are fitted to the SAM silhouettes. On a real mug measured with a ruler, two independent runs had a mean error within about 5% (height, rim, base: +1.9/+3.7/-1.9% and +6.0/+2.4/-5.5%); each new SAM 3D mesh varies slightly.

The browser viewer shows the model with millimetre dimensions and where its scale came from. You can type an edit such as "make the handle 2 mm thicker". Muse Wonder chooses and calls exact geometry tools, SAM 3.1 has already located the handle on the mesh, and Muse Spark compares before and after renders. Deterministic checks must also pass. Every version can be undone, and the STL downloads in millimetres.

Scene mode finds several objects on a desk, builds each one, and places them together at one scale. VGGT cameras check that layout.

A phone web app uploads videos; a laptop worker processes them.

WorldKit reports its failures too: a run scaled by a bank card instead of a coin missed by up to 17%, and is published too.

## How each Meta model is used

- **Muse Spark:** names the object and picks the best frame, discovers the objects in a scene, and judges before/after renders of every AI Edit.
- **Muse Wonder:** turns a typed edit into calls to geometry tools (scale, engrave text, drill, flatten, split, thicken, mirror, thicken a part).
- **SAM 3.1:** segments the object, the coin and every scene object in every frame, and finds the mug handle for part edits.
- **SAM 3D Objects:** reconstructs each object's textured 3D mesh from one masked frame, on a Modal A100.
- **VGGT:** estimates per-frame cameras on a Modal A100 to check the scene layout (silhouette agreement) and to correct perspective per object.
- **Muse Spark Open** (open weights, served by Baseten): an optional second verifier for AI Edit (`--spark-backend baseten`); it gave the same accept decision as Muse Spark on all 5 rehearsed edits.

Details for each model (file, function, calls per run, fallback): [MODELS.md](MODELS.md).

## Prize fit

**Best Agentic Use-Case.** The AI Edit agent turns one typed instruction into a verified change of the print mesh: Muse Wonder picks one of 8 geometry tools and its arguments, SAM 3.1 supplies the part, and geometry checks plus Muse Spark decide whether to accept, retry once, or refuse ([AGENTIC.md](AGENTIC.md)). In the rehearsal with the real models, 10 of 10 edits were accepted on the first attempt in 13.9-33.6 s each, including "make the handle thinner" with no amount given, which became -2 mm ([DEMO_EDITS.md](DEMO_EDITS.md), [agent_traces/](agent_traces/)). It stops on its own below 50% SAM coverage of a part or a 1.2 mm wall, never edits the original, and keeps every version for undo ([agent_traces/refusal_wall_floor.json](agent_traces/refusal_wall_floor.json)).

**Best Creative Build (Muse Wonder).** Muse Wonder edits a 3D print of a real, filmed object from plain language: it converts "put a 5 mm hole through the handle side" into a hole position and direction taken from the measured handle, and "make it exactly 12 cm tall" into a target in the tool's own unit ([WONDER.md](WONDER.md)). It never touches geometry itself; every call goes through exact tools and is checked by geometry tests and Muse Spark before it is kept. Each rehearsed edit used 4,223-4,463 Wonder tokens, stored per edit with the tool calls ([agent_traces/](agent_traces/)).

**Best Build on Meta Open Weight Models.** Muse Spark Open (`meta-models/muse-spark-open-1026`), served by Baseten, is a selectable backend for the AI Edit verification step (`python -m worldkit.edit RUN_DIR "..." --spark-backend baseten`; code in `pipeline/worldkit/spark_open.py`). On the stored before/after renders of the 5 rehearsed edits it gave the same accept decision as Muse Spark 5 of 5 times; one verification needed a second call because the first used all 6,000 completion tokens without answering ([results/spark_open_agreement.json](results/spark_open_agreement.json), [MODELS.md](MODELS.md)). It covers verification only, on edits Muse Spark had accepted; the meshes themselves come from SAM 3D Objects, also an open-weight Meta model, on a Modal A100.

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
- Demo video (3 min): https://www.loom.com/share/55fd3cf835994a6a99e1fee8fc2b907d

## 3D environment

Map mode turns one phone video of a desk into a 3D environment. It reconstructs:

- a table with its scale estimated from the coin or card: a stadium top fused from SAM 3.1 masks on the VGGT plane, plus the gold base estimated from the frames;
- the objects: SAM 3D Objects meshes, or a revolved silhouette profile for the incense burner;
- the scale reference (coin or bank card) as known geometry;
- a printable diorama.

Objects are placed by triangulating their mask centres across VGGT cameras. The silhouette overlap is measured on 6 held-out frames:

| Desk | Held-out mean IoU | Reference | Mug vs ruler |
|---|---|---|---|
| Coin desk, 33 s (default demo) | 0.643 | coin | +6.1 / -8.9 / -5.1 % |
| Three-lap desk, 113 s | 0.542 | card | +22.6 / +2.8 / -1.8 % |

The two scales disagree on the table size by 2-6 %. The table height and base shapes are estimates.

## What makes it different

WorldKit checks its output against the real object instead of only showing a 3D model. Scale comes from a visible reference and is scored against a ruler; the model reports which parts are observed and which are generated. AI edits are made by geometry tools rather than by regenerating the mesh, so "2 mm thicker" means 2 mm. Each edit is checked twice: by deterministic tests and by a second model looking at renders. Failures are kept in the open; the card run's 17% error is published next to the 2% result.
