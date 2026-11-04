# WorldKit v1.0-submission

WorldKit turns a short phone video of an object next to a coin into a textured 3D model and a print STL in millimetres, with the scale estimated from the coin. The model can be inspected, edited by typing (the AI Edit agent: Muse Wonder tool calls, SAM 3.1 parts, Muse Spark verification) and printed. Built for the Meta Global AI Developer Hackathon 2026.

## What is in this release

- Object mode, Map mode (several objects on a desk at one scale), the three.js viewer with Source, Viewer, AI Edit and Accuracy tabs, and a phone web app with a laptop worker.
- Accuracy against a ruler on one mug, two independent runs: mean error 2.5% and 4.6%, worst single dimension 6.0% (`docs/accuracy.md`). A second object (a Hydro Flask bottle) could not be scored: SAM 3.1 found the coin in 1 of 12 frames.
- AI Edit: 10 of 10 rehearsed edits with the real models accepted on the first attempt; traces in `docs/agent_traces/`.
- Muse Spark Open (open weights, served by Baseten) as an optional AI Edit verifier: same decision as Muse Spark on 5 of 5 rehearsed edits (`docs/results/spark_open_agreement.json`).

## How to verify

```bash
pipeline/.venv/Scripts/python.exe scripts/verify_claims.py      # no API, no GPU: accuracy tables, SHA-256, README numbers, traces, links
python -m pytest pipeline/tests -q                               # about 4 min, no API calls
```

Hosted demo, no account: https://web-flame-psi-45.vercel.app/viewer/index.html?demo=1

## Links

- Evidence index for judges: `docs/JUDGING.md`
- The AI Edit agent: `docs/AGENTIC.md`; Muse Wonder: `docs/WONDER.md`; every Meta model: `docs/MODELS.md`
- Live site: https://web-flame-psi-45.vercel.app
- Demo video: TODO

## Attached files

- `mug_coin_scaled_mm.stl`: the coin-scaled mug print STL (`viewer/assets/demo/run_mug/model_print.stl`); 71.3 mm mug plus a 1.6 mm print plate. The scale is estimated from a coin, not measured.
- `desk_diorama_1-4.8.stl`: the coin desk scene as one watertight diorama scaled to a 256 mm bed (`viewer/assets/demo/run_desk/diorama.stl`, scale 1:4.8).
- `accuracy.md`: the accuracy tables (`docs/accuracy.md`).
