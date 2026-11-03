# WorldKit pipeline

Python package `worldkit` (command line): video in, textured 3D model(s) + `report.json` out. The viewer lives in `../viewer`.

## Setup

```powershell
cd pipeline
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Content .env "MODEL_API_KEY=<your key>"   # pipeline/.env is gitignored; other variables: README, Configuration
```

Use Python 3.10. Frame extraction uses a bundled ffmpeg (`imageio-ffmpeg`); no system install. Without a key the pipeline still runs
with clearly tagged fallbacks (local masks, sharpest frame; you must pass `--object`), but real SAM 3.1 / Muse Spark results need
`MODEL_API_KEY` from <https://dev.meta.ai>.

### SAM 3D Objects on Modal (one-time, needs a GPU account)

SAM 3D Objects needs a large NVIDIA GPU, so the reconstruction stage runs on Modal (A100-40GB, one container, 30 min timeout).

1. `.venv\Scripts\python.exe -m modal setup` (authenticate once).
2. Request access to the gated weights [facebook/sam-3d-objects](https://huggingface.co/facebook/sam-3d-objects), then store a token as a Modal secret: `modal secret create huggingface HF_TOKEN=hf_...`
3. Preflight, free (CPU only): `modal run "modal_app/sam3d.py::check_env"` must end with `PREFLIGHT_OK`. Optional GPU check of the CUDA extensions without loading the models (about $0.12): `modal run "modal_app/sam3d.py::gpu_smoke"`.
4. The first real run builds the container image and downloads about 13 GB of checkpoints into a Modal Volume (slow once, fast after). Check usage with `modal billing summary`.

No GPU or Modal account? `--mesh-backend trellis` uses a hosted community Space (small free daily quota) and `--mesh-backend import --mesh-file X.glb` reuses an existing mesh.

## Commands

```powershell
# one object
.venv\Scripts\python.exe -m worldkit.object samples\mug_on_table.mp4 --out runs\my_mug --mesh-backend modal --fidelity auto [--object mug] [--ref-diameter-cm D | --height-cm H]
# several objects in one scene
.venv\Scripts\python.exe -m worldkit.scene samples\mug_on_table.mp4 --out runs\my_scene --objects mug "round side table"
# look at the result
..\pipeline\.venv\Scripts\python.exe ..\viewer\server.py --open runs\my_mug
```

Output of `worldkit.object` in `--out`:

| File | Contents |
|---|---|
| `model.glb` | the viewer-ready textured mesh (display-correct material, normals; scale units are arbitrary) |
| `report.json` | models used (and whether Meta's), observed vs generated texture shares, `fidelity` decisions and held-out numbers, scale calibration, print-prep checks, per-stage timings, warnings |
| `model_print.stl`, `previews/` | scaled, repaired, based STL and 4-angle renders (`print_ready` is true only with a measured height and passing checks) |
| `chosen_frame.png`, `mask_overlay.png`, `object_rgba.png`, `frames/` | the inputs each stage actually used |
| `fidelity/` | the frame crops sent to Muse Spark for the structured analysis |

`worldkit.scene` writes `scene.json` (objects, transforms, layout overlap with the real masks, overlaps), `objects/<id>/{model.glb,report.json}` and `layout_check.png`.

Runs are cached where it is safe: SAM 3.1 results and Muse Spark analyses are stored by content hash in `pipeline/.cache/` (gitignored), so re-running on the same frames makes no new API calls.

## Backends

| `--mesh-backend` | Model | Notes |
|---|---|---|
| `modal` (default) | SAM 3D Objects (Meta) | best geometry here; needs Modal |
| `trellis` | TRELLIS (Microsoft) | no GPU of your own; lumpier handle/rim; hosted-Space quota |
| `hosted` | Hunyuan3D-2 (Tencent) | untextured thin shell; baseline only |
| `import` | whatever you pass | `--mesh-file`, optionally `--mesh-source-report` to keep the original model attribution (verified by file hash) |

## Troubleshooting

- `SAM 3D Objects on Modal failed ...`: the message lists the checklist (Modal auth, the `huggingface` secret and gated-weights access, credits, `check_env`). Fall back to `--mesh-backend trellis`.
- `no frame shows all of [...] with a genuine SAM 3.1 mask` (scene): the concept names must be things SAM 3.1 can find in the same frame; try shorter nouns.
- `print_ready: false`: expected unless you pass `--height-cm`; reference-based scale is an estimate.
- Failures are also written to `report.json` (`error`, `error_type`).

Tests: `python -m pytest tests` from this folder (about 4 minutes).
