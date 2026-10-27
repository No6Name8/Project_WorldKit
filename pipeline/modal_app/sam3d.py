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
        # Real defect found on two live build attempts (2026-10-27):
        # nvidia-pyindex (an sdist-only transitive dep pulled in by
        # sam-3d-objects, no prebuilt wheel available) has a setup.py that
        # shells out to `python3.11 -c "from pip._internal..."` to read pip's
        # config files. pip's own PEP 517 BUILD ISOLATION restricts that
        # subprocess's PYTHONPATH to an ephemeral overlay dir, so even a
        # properly-installed system pip (confirmed via ensurepip -- didn't
        # fix it, same error both times) is invisible to it ->
        # "ModuleNotFoundError: No module named 'pip'", failing the wheel
        # build every time it's built in isolation. Fix: pre-install it with
        # --no-build-isolation (builds against the ambient environment, which
        # does have a visible pip, instead of the restricted overlay) so the
        # later '.[dev]' install finds it already satisfied and never
        # attempts the isolated build at all.
        "python3.11 -m ensurepip --upgrade",
        "python3.11 -m pip install --no-cache-dir --upgrade pip",
        # --no-build-isolation builds against the ambient env, which needs
        # wheel/setuptools present itself (for bdist_wheel) -- a fresh pip
        # install doesn't bundle wheel by default -- and nvidia-pyindex's own
        # setup.py imports appdirs directly (not declared as a build
        # dependency it can rely on build isolation to provide).
        "pip install --no-cache-dir --upgrade setuptools wheel appdirs",
        "pip install --no-cache-dir --no-build-isolation nvidia-pyindex==1.0.9",
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
        # Real defect found on a live build attempt (2026-10-27): '.[p3d]'
        # pulls in flash-attn, whose setup.py does `import torch` at build
        # time to pick compute flags -- but pip resolving '.[p3d]' in one
        # command doesn't guarantee torch (also a '.[p3d]' dependency) is
        # actually installed into site-packages before flash-attn's build
        # runs -> "ModuleNotFoundError: No module named 'torch'". Pre-install
        # torch explicitly first (version matches the Kaolin find-links URL
        # below, torch-2.5.1_cu121) so it's already importable.
        "pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121",
        # Second real defect, found AFTER the torch pre-install above (v6,
        # 2026-10-27): pre-installing torch into the ambient env wasn't
        # enough on its own -- flash-attn's build still failed with the same
        # "ModuleNotFoundError: No module named 'torch'", because pip's PEP
        # 517 build isolation runs flash-attn's setup.py inside an ephemeral
        # overlay env (/tmp/pip-build-env-*/overlay) that does NOT inherit
        # the ambient site-packages, so the already-installed torch is
        # invisible to it -- the exact same isolation problem already fixed
        # for nvidia-pyindex above, recurring for a different package.
        # Third real defect (v7, 2026-10-27): putting --no-build-isolation on
        # the WHOLE '.[p3d]' command (not just flash-attn) also broke the
        # editable install of sam3d_objects itself: that package's own build
        # backend is hatchling, which build isolation would normally fetch
        # automatically -- without isolation pip looked for an
        # already-installed 'hatchling.build' and failed with
        # "BackendUnavailable: Cannot import 'hatchling.build'". Fix: scope
        # --no-build-isolation to flash-attn ALONE, as its own pre-install
        # (pinned to the exact version sam3d_objects' pyproject.toml
        # requires), so '.[p3d]' finds it already satisfied and never
        # attempts to build it itself -- leaving the rest of '.[p3d]'
        # (including sam3d_objects' own hatchling-backed editable install)
        # on pip's normal, isolated build path.
        # Confirmed by reading requirements.p3d.txt directly (cloned the repo
        # locally to check, no Modal cost): '.[p3d]' pins exactly two
        # torch-at-build-time packages -- flash_attn==2.8.3 and pytorch3d
        # (pinned git commit) -- so both get the same pre-install treatment
        # up front, instead of discovering the second one as another failed
        # paid retry.
        "pip install --no-cache-dir --no-build-isolation flash-attn==2.8.3",
        # Fourth real defect (v9, 2026-10-27): pytorch3d's torch-cpp-extension
        # build compiled all 36 .o files fine (no CUDA runtime at image-build
        # time is expected/harmless -- no GPU is attached during the build
        # phase, only torch's CPU-only .cpp sources are compiled here), but
        # the final link step invoked `clang++`, which this image never
        # installed (apt_install above only has build-essential/g++) ->
        # "error: [Errno 2] No such file or directory: 'clang++'". Force the
        # standard g++ toolchain already present via CXX/CC instead of
        # relying on torch's compiler auto-detection.
        "CXX=g++ CC=gcc pip install --no-cache-dir --no-build-isolation "
        "'git+https://github.com/facebookresearch/pytorch3d.git@75ebeeaea0908c5527e7b1e305fbc7681382db47'",
        # Fifth real defect (v10, 2026-10-27): both pre-installs above
        # actually SUCCEEDED (confirmed in the log: "Successfully installed
        # pytorch3d-0.7.8"), but the next '.[p3d]' install still re-collected
        # pytorch3d from its git URL requirement in requirements.p3d.txt and
        # tried to rebuild it from source anyway (pip's resolver doesn't
        # treat a direct VCS-URL requirement as already-satisfied by a
        # same-version install the way it does for an ordinary pinned
        # version), landing back on the original "No module named 'torch'"
        # build-isolation error for that second, redundant build. Fix: strip
        # the two already-preinstalled lines out of requirements.p3d.txt so
        # '.[p3d]' never asks pip to build them a second time.
        "sed -i '/^pytorch3d /d; /^flash_attn/d' /opt/sam3d-objects/requirements.p3d.txt",
        "cd /opt/sam3d-objects && pip install --no-cache-dir -e '.[p3d]'",
        # Sixth real defect (v11, 2026-10-27): '.[inference]' hit the same
        # torch-at-build-time-under-isolation problem as flash-attn/
        # pytorch3d, this time for gsplat (another pinned git dependency, in
        # requirements.inference.txt). Checked requirements.inference.txt
        # directly (no Modal cost) for any other such package first --
        # kaolin==0.17.0 is plain-pinned and has a prebuilt wheel via the
        # PIP_FIND_LINKS Kaolin index already configured above, so it needs
        # no special handling; gsplat is the only other offender. Same fix
        # as pytorch3d: pre-install with --no-build-isolation (+ CXX=g++ in
        # case its C++ extension also defaults to a missing clang++), then
        # strip it from requirements.inference.txt so '.[inference]' doesn't
        # try to rebuild it a second time under normal isolation.
        # Seventh real defect (v12-v15, 2026-10-27): gsplat's CUDA-extension
        # build calls torch's _get_cuda_arch_flags(), which normally queries
        # the live GPU's compute capability -- but no GPU is attached during
        # the image-BUILD phase (only the later gpu="A100-40GB" function
        # call attaches one), so torch.cuda.get_arch_list() returns an empty
        # list and `arch_list[-1]` raises IndexError. Fix: set
        # TORCH_CUDA_ARCH_LIST explicitly so the extension targets the A100
        # (compute capability 8.0) without needing a live device to ask.
        "TORCH_CUDA_ARCH_LIST='8.0+PTX' CXX=g++ CC=gcc pip install --no-cache-dir --no-build-isolation "
        "'git+https://github.com/nerfstudio-project/gsplat.git@2323de5905d5e90e035f792fe65bad0fedd413e7'",
        "sed -i '/^gsplat /d' /opt/sam3d-objects/requirements.inference.txt",
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
