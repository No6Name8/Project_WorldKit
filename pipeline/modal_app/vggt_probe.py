"""VGGT probe on Modal (EXPERIMENT, not part of the pipeline):  modal run modal_app/vggt_probe.py --frames-dir runs/vggt_probe/frames --out runs/vggt_probe/vggt_out.npz

Runs facebook/VGGT-1B on a set of frames and returns cameras, depth, confidence and world points as one .npz. Reports GPU wall seconds.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import modal

app = modal.App("worldkit-vggt-probe")

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install("torch==2.3.1", "torchvision==0.18.1", extra_index_url="https://download.pytorch.org/whl/cu121")
    .pip_install("numpy==1.26.1", "pillow", "huggingface_hub", "einops", "safetensors", "opencv-python-headless")
    .run_commands("pip install git+https://github.com/facebookresearch/vggt.git")
)


@app.function(image=image, gpu="A100-40GB", timeout=1200)
def run_vggt(frames: list[bytes], mode: str = "crop") -> dict:
    import os
    import tempfile

    import numpy as np
    import torch
    from vggt.models.vggt import VGGT
    from vggt.utils.geometry import unproject_depth_map_to_point_map
    from vggt.utils.load_fn import load_and_preprocess_images
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri

    t0 = time.time()
    d = tempfile.mkdtemp()
    paths = []
    for i, b in enumerate(frames):
        p = os.path.join(d, f"f{i:03d}.jpg")
        open(p, "wb").write(b)
        paths.append(p)
    device = "cuda"
    dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    model = VGGT.from_pretrained("facebook/VGGT-1B").to(device)
    t_load = time.time() - t0
    images = load_and_preprocess_images(paths, mode=mode).to(device)
    with torch.no_grad(), torch.cuda.amp.autocast(dtype=dtype):
        agg, ps_idx = model.aggregator(images[None])
        pose_enc = model.camera_head(agg)[-1]
        extr, intr = pose_encoding_to_extri_intri(pose_enc, images.shape[-2:])
        depth, conf = model.depth_head(agg, images[None], ps_idx)
    extr_np, intr_np = extr[0].float().cpu().numpy(), intr[0].float().cpu().numpy()
    depth_np, conf_np = depth[0].float().cpu().numpy(), conf[0].float().cpu().numpy()
    pts = unproject_depth_map_to_point_map(depth_np, extr_np, intr_np)
    out = {"extrinsic": extr_np, "intrinsic": intr_np, "depth": depth_np[..., 0].astype(np.float16), "depth_conf": conf_np.astype(np.float16),
           "world_points": np.asarray(pts, np.float16), "images": (images.float().cpu().numpy() * 255).astype(np.uint8)}
    buf = io.BytesIO()
    np.savez_compressed(buf, **out)
    return {"npz": buf.getvalue(), "gpu_seconds": time.time() - t0, "load_seconds": t_load, "n": len(frames), "peak_mem_gb": torch.cuda.max_memory_allocated() / 1e9}


@app.local_entrypoint()
def main(frames_dir: str = "runs/vggt_probe/frames", out: str = "runs/vggt_probe/vggt_out.npz") -> None:
    files = sorted(Path(frames_dir).glob("*.jpg"))
    print(f"{len(files)} frames")
    r = run_vggt.remote([f.read_bytes() for f in files])
    Path(out).write_bytes(r["npz"])
    print({k: v for k, v in r.items() if k != "npz"})
