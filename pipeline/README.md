# WorldKit pipeline — Object mode

Command-line pipeline: a phone video of an object in, a print-ready STL out.
No web app involved; this is the `/pipeline` Python package, `worldkit`.

## Setup

This machine has two Pythons installed; always use 3.10 (`py -3.10`), never
the bare `python3` alias, which points to a different, unrelated install.

```bash
cd pipeline
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env
rem edit .env and set MODEL_API_KEY to your Meta Model API key
```

Frame extraction uses a bundled ffmpeg binary (via `imageio-ffmpeg`) — no
system ffmpeg install needed.

### SAM 3D Objects / Modal (one-time setup)

The mesh-reconstruction stage runs on a Modal GPU, not locally (SAM 3D
Objects needs an NVIDIA GPU with >= 32GB VRAM).

1. `.venv\Scripts\python.exe -m pip install modal` (already in
   requirements.txt), then `.venv\Scripts\python.exe -m modal setup` once to
   authenticate.
2. SAM 3D Objects' checkpoints are **gated** on Hugging Face. Request access
   to [facebook/sam-3d-objects](https://huggingface.co/facebook/sam-3d-objects),
   create an HF access token, then store it as a Modal secret:
   ```bash
   modal secret create huggingface HF_TOKEN=hf_xxxxxxxxxxxx
   ```
3. First real run builds the container image and downloads checkpoints into
   a cached Modal Volume (`worldkit-sam3d-checkpoints`) — slow once, fast
   after.

## Run

```bash
.venv\Scripts\python.exe -m worldkit.object path\to\video.mp4 --out runs\my_object --object "mug" --height-cm 9.5
```

Each run writes to `--out`:

| File | Contents |
|---|---|
| `chosen_frame.png` | the frame used to reconstruct the object |
| `mask_overlay.png` (or `FALLBACK_mask_overlay.png`) | the chosen frame with the object mask overlaid |
| `object_rgba.png` | chosen frame + mask, as the RGBA image fed to SAM 3D Objects |
| `model.glb` | textured mesh from SAM 3D Objects (via Modal) |
| `model_print.stl` | scaled, repaired, based, oriented, print-ready STL |
| `previews/preview_{000,090,180,270}.png` | 4-angle renders of the final STL |
| `report.json` | object name, dimensions, watertight/volume, thin-wall warnings, per-stage timings, mesh method, fallback flags, API token usage |
| `frames/`, `_all_frames/` | the sharp frames kept, and every frame ffmpeg extracted |

## If the Meta API key isn't active yet

`detect.py` and `segment.py` fall back automatically (no flag needed) when
`pipeline/.env` is missing or the API rejects the key for a billing/auth
reason (HTTP 401/402/403):

- Object naming falls back to the sharpest extracted frame + whatever you
  passed with `--object` (pass it, or the name stays a generic placeholder).
- Segmentation falls back to a local background-removal model (`rembg`/u2net,
  runs on CPU). It can't target a named concept, so A4-sheet detection is
  skipped in this mode — pass `--height-cm` instead of relying on the A4
  estimate.

Every fallback is marked unmistakably: `used_fallback_detect` /
`used_fallback_segment` in `report.json`, and the mask overlay filename gets
a `FALLBACK_` prefix. **This path is for unblocking development only — it
cannot appear in the submitted demo**, since the hackathon requires real use
of the Meta API/SAM 3.1.

Any other API failure (bad request, persistent 5xx, real network outage) is
NOT treated as a fallback trigger — it stops the run with a clear error,
since silently masking a real bug or outage would be worse than stopping.

## Mesh output: built-in SAM 3D Objects mesh, not a custom conversion

`facebookresearch/sam-3d-objects`' demo notebook (`demo.py`,
`demo_single_object.ipynb`) only shows a Gaussian-splat (`.ply`) export, via
a convenience wrapper (`Inference.__call__`) that explicitly disables mesh
postprocessing and texture baking. The underlying pipeline
(`InferencePipelinePointMap.run` / `sam3d_objects/pipeline/inference_pipeline.py`)
defaults those to `True` and, when enabled, returns a ready textured mesh in
`outputs["glb"]` (built via `postprocessing_utils.to_glb(...)`, with a plain
`.export(path)`). `modal_app/sam3d.py` bypasses the convenience wrapper and
calls the pipeline directly to get this built-in mesh — no custom
splat-to-mesh conversion (e.g. Poisson reconstruction) was needed or written.

## Tests

```bash
.venv\Scripts\python.exe -m pytest tests/ -v
```

`test_frames.py` and `test_printprep.py` run against a synthetic generated
video / mesh — no network, GPU, or API key required.
