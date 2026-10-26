"""Run SAM 3D Objects on a Modal GPU: image + mask -> textured mesh (GLB).

Source of truth (read before writing this file, per facebookresearch/sam-3d-objects):
  - doc/setup.md: needs an NVIDIA GPU with >= 32 GB VRAM; checkpoints are
    GATED on Hugging Face (facebook/sam-3d-objects). We use Modal's
    "A100-40GB" and the Modal secret "huggingface" (HF_TOKEN) for the token.
  - notebook/inference.py: the demo's ``Inference.__call__`` wrapper calls
    ``self._pipeline.run(..., with_mesh_postprocess=False, with_texture_baking=False)``
    -- i.e. the convenience wrapper used in demo.py/demo_single_object.ipynb
    deliberately disables mesh output and only returns a Gaussian splat.
  - sam3d_objects/pipeline/inference_pipeline.py (``postprocess_slat_output``,
    ``decode_slat`` default ``formats=["mesh", "gaussian"]``) and
    inference_pipeline_pointmap.py (the actual pipeline class instantiated,
    ``InferencePipelinePointMap``, which inherits postprocess_slat_output):
    the underlying ``run()`` method DEFAULTS to
    ``with_mesh_postprocess=True, with_texture_baking=True``, and when those
    are true, ``outputs["glb"]`` is a ready, textured mesh object (built via
    ``postprocessing_utils.to_glb(gaussian, mesh, texture_size=1024, ...)``)
    with a plain ``.export(path)`` method.

So: SAM 3D Objects DOES have a built-in textured-mesh path. We bypass the
``Inference.__call__`` convenience wrapper and call ``inference._pipeline.run()``
directly with mesh postprocessing + texture baking left at their (True)
defaults, instead of writing our own splat -> mesh conversion. If a future
checkpoint/config ever returns no "glb" (e.g. "mesh" not in decode_slat
output), ``reconstruct_mesh`` raises a clear RuntimeError rather than
silently guessing a conversion.

Checkpoints are cached in a Modal Volume so they download once across runs.
"""

from __future__ import annotations

import modal

APP_NAME = "worldkit-sam3d"
GPU_TYPE = "A100-40GB"  # sam-3d-objects requires >=32GB VRAM
CHECKPOINT_VOLUME_NAME = "worldkit-sam3d-checkpoints"
HF_REPO = "facebook/sam-3d-objects"

app = modal.App(APP_NAME)

checkpoint_volume = modal.Volume.from_name(CHECKPOINT_VOLUME_NAME, create_if_missing=True)
CHECKPOINT_DIR = "/checkpoints"

sam3d_image = (
    modal.Image.from_registry("nvidia/cuda:12.1.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("git", "build-essential", "ninja-build")
    .run_commands(
        "pip install --no-cache-dir 'huggingface-hub[cli]<1.0'",
        "git clone --depth 1 https://github.com/facebookresearch/sam-3d-objects /opt/sam3d-objects",
    )
    .env(
        {
            "PIP_EXTRA_INDEX_URL": "https://pypi.ngc.nvidia.com https://download.pytorch.org/whl/cu121",
            "PIP_FIND_LINKS": "https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.5.1_cu121.html",
        }
    )
    .run_commands(
        "cd /opt/sam3d-objects && pip install --no-cache-dir -e '.[dev]'",
        "cd /opt/sam3d-objects && pip install --no-cache-dir -e '.[p3d]'",
        "cd /opt/sam3d-objects && pip install --no-cache-dir -e '.[inference]'",
        "cd /opt/sam3d-objects && ./patching/hydra || true",
    )
    .pip_install("pillow", "numpy<2")
)


def _ensure_checkpoints() -> None:
    import os
    import subprocess

    tag_dir = f"{CHECKPOINT_DIR}/hf"
    if os.path.isdir(tag_dir) and os.listdir(tag_dir):
        return  # already cached in the Modal Volume

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError(
            "HF_TOKEN is not set. SAM 3D Objects checkpoints are gated on "
            f"Hugging Face ({HF_REPO}); the Modal secret 'huggingface' must "
            "provide HF_TOKEN."
        )
    download_dir = f"{CHECKPOINT_DIR}/hf-download"
    subprocess.run(
        [
            "hf",
            "download",
            "--repo-type",
            "model",
            "--local-dir",
            download_dir,
            "--max-workers",
            "1",
            HF_REPO,
        ],
        check=True,
    )
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    subprocess.run(["mv", f"{download_dir}/checkpoints", tag_dir], check=True)
    subprocess.run(["rm", "-rf", download_dir], check=True)
    checkpoint_volume.commit()


@app.function(
    image=sam3d_image,
    gpu=GPU_TYPE,
    volumes={CHECKPOINT_DIR: checkpoint_volume},
    timeout=30 * 60,
    secrets=[modal.Secret.from_name("huggingface")],
)
def reconstruct_mesh(rgba_png_bytes: bytes) -> bytes:
    """Run SAM 3D Objects on one RGBA image (mask in the alpha channel).

    Returns a textured mesh as GLB bytes, using the repo's own built-in
    mesh + texture-baking path (see module docstring) -- not a custom
    splat-to-mesh conversion. Raises RuntimeError with a clear message if
    checkpoints are missing/ungated, or if decode_slat/postprocessing
    unexpectedly returns no mesh.
    """
    import sys
    import tempfile
    from pathlib import Path

    import numpy as np
    from PIL import Image

    _ensure_checkpoints()

    sys.path.append("/opt/sam3d-objects/notebook")
    from inference import Inference  # type: ignore

    config_path = f"{CHECKPOINT_DIR}/hf/pipeline.yaml"
    inference = Inference(config_path, compile=False)

    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "input.png"
        img_path.write_bytes(rgba_png_bytes)
        image_rgba = np.array(Image.open(img_path).convert("RGBA"))
        if image_rgba.shape[-1] != 4 or not (image_rgba[..., 3] > 0).any():
            raise RuntimeError("input image has no usable alpha-channel mask")

        # Bypass Inference.__call__ (which forces with_mesh_postprocess=False,
        # with_texture_baking=False) and call the underlying pipeline directly
        # so we get the repo's own textured-mesh output instead of a splat.
        output = inference._pipeline.run(
            image_rgba,
            mask=None,  # alpha channel already carries the mask
            seed=42,
            with_mesh_postprocess=True,
            with_texture_baking=True,
            decode_formats=["mesh", "gaussian"],
        )

        glb_obj = output.get("glb")
        if glb_obj is None:
            raise RuntimeError(
                "sam-3d-objects returned no mesh (outputs['glb'] is None) despite "
                "requesting with_mesh_postprocess=True, with_texture_baking=True. "
                "This contradicts what the repo's own pipeline code documents -- "
                "check decode_slat output / pipeline.yaml config on this checkpoint."
            )

        glb_path = Path(tmp) / "model.glb"
        glb_obj.export(str(glb_path))
        return glb_path.read_bytes()


@app.local_entrypoint()
def main(input_image: str, output_glb: str = "model.glb") -> None:
    """CLI smoke test: `modal run pipeline/modal_app/sam3d.py --input-image path.png`."""
    from pathlib import Path

    image_bytes = Path(input_image).read_bytes()
    glb_bytes = reconstruct_mesh.remote(image_bytes)
    Path(output_glb).write_bytes(glb_bytes)
    print(f"wrote {output_glb} ({len(glb_bytes)} bytes)")


def run_remote(rgba_png_bytes: bytes) -> bytes:
    """Entry point used by worldkit.object (the CLI) to call this Modal function.

    Starts an ephemeral Modal app run, invokes reconstruct_mesh remotely, and
    returns the GLB bytes. Requires `modal setup` to have been run once on
    this machine so the Modal CLI is authenticated (already done; workspace
    no6name8).
    """
    with app.run():
        return reconstruct_mesh.remote(rgba_png_bytes)
