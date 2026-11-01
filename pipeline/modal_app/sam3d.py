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
    # Real defect found on the first GPU launch (2026-10-28): the repo's notebook/inference.py does
    # os.environ["CUDA_HOME"] = os.environ["CONDA_PREFIX"] at import time, and this image is not conda.
    # Point it at the image's CUDA toolkit (what CUDA_HOME should be anyway).
    # Third real defect (2026-10-28, found by the CPU preflight): `lightning`, imported by
    # sam3d_objects/model/io.py, needs pkg_resources, which setuptools >= 81 no longer ships. Our own earlier
    # `pip install --upgrade setuptools` (done to build nvidia-pyindex) had pulled in 84.x. Pin a late layer
    # so the heavy earlier layers are not rebuilt.
    .run_commands("pip install --no-cache-dir 'setuptools==80.9.0'")
    # Fourth real defect (found by the CPU preflight): the repo's mesh post-processing calls
    # utils3d.torch.RastContext(backend="cuda"), which needs nvdiffrast -- never mentioned in the repo's own
    # requirements or docs. Its CUDA plugin JIT-compiles on first use; pre-compile it here on CPU
    # (nvcc is in the image; sm_80 = the A100) so a compile error surfaces cheaply and the GPU run
    # doesn't pay for compilation. CONDA_PREFIX/CUDA_HOME are set explicitly for this layer.
    .run_commands(
        "pip install --no-cache-dir --no-build-isolation 'git+https://github.com/NVlabs/nvdiffrast.git@v0.3.3'",
        # nvdiffrast's _get_plugin() deliberately blanks TORCH_CUDA_ARCH_LIST (assuming a visible GPU), which
        # crashes torch's arch detection on a GPU-less build host. Emit exactly the flags torch would derive
        # on an A100 (8.0+PTX) so the build is validated here and its cached .so matches the runtime build.
        "CUDA_HOME=/usr/local/cuda python -c "
        "\"import torch.utils.cpp_extension as ce; "
        "ce._get_cuda_arch_flags = lambda cflags=None: ['-gencode=arch=compute_80,code=sm_80', '-gencode=arch=compute_80,code=compute_80']; "
        "import nvdiffrast.torch.ops as o; o._get_plugin(gl=False); print('nvdiffrast CUDA plugin precompiled')\"",
    )
    # Fifth real defect -- the first one the A100 itself exposed (2026-10-28 GPU run; model sampling and decoding
    # had already run on the GPU): texture baking renders multi-view Gaussians through
    # gaussian_render.render(backend="inria"), which needs `diff_gaussian_rasterization` -- the mip-splatting
    # fork (it takes kernel_size/subpixel_offset), the same dependency TRELLIS-style code uses. The repo's own
    # requirements omit it and its import is try/except-guarded, which is why my first CPU preflight wrongly
    # treated it as optional. The CUDA build honours TORCH_CUDA_ARCH_LIST, so build it here for the A100 (8.0).
    .run_commands(
        "git clone --recursive --depth 1 https://github.com/autonomousvision/mip-splatting.git /opt/mip-splatting",
        "cd /opt/mip-splatting/submodules/diff-gaussian-rasterization && "
        "CXX=g++ CC=gcc CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST=8.0 pip install --no-cache-dir --no-build-isolation .",  # g++: python's sysconfig links with a clang++ this image lacks
    )
    .env({"CONDA_PREFIX": "/usr/local/cuda"})
    # Second real defect (2026-10-28, found on the A100 after the first fix): open3d, imported by
    # sam3d_objects.pipeline.inference_utils, needs libGL.so.1 and friends, absent from the CUDA base image.
    .apt_install("libgl1", "libglib2.0-0", "libsm6", "libxext6", "libxrender1")
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
    volumes={CHECKPOINT_DIR: checkpoint_volume},
    timeout=60 * 60,
    secrets=[modal.Secret.from_name("huggingface")],
)
def download_checkpoints() -> str:
    """CPU-only: fetch the 13 GB gated checkpoints into the Volume once.

    Runs WITHOUT a GPU so the download is billed at CPU rates instead of A100
    rates (previously this happened inside the GPU function). Idempotent.
    """
    import os

    _ensure_checkpoints()
    return ", ".join(sorted(os.listdir(f"{CHECKPOINT_DIR}/hf")))


@app.function(image=sam3d_image, volumes={CHECKPOINT_DIR: checkpoint_volume}, timeout=15 * 60)
def check_environment() -> str:
    """CPU-ONLY preflight of everything the GPU run needs, collecting ALL failures (not just the first).

    Same image and Volume as ``reconstruct_mesh``, but no GPU and NO Hugging Face secret, so it cannot
    download anything or spend GPU money. Verifies: CUDA_HOME/CONDA_PREFIX, system libs (libGL),
    every Python import the inference + mesh/texture-baking path touches, that the Volume checkpoints
    exist and every path pipeline.yaml references resolves, the hydra safety check the repo runs, and
    that the entry-point signatures match what ``reconstruct_mesh`` calls.
    """
    import importlib
    import inspect
    import os
    import subprocess
    import sys
    import traceback

    report: dict = {"errors": []}

    def attempt(label, fn):
        try:
            report[label] = fn()
        except BaseException as exc:  # noqa: BLE001 - a preflight must survive and report every failure
            report["errors"].append(f"{label}: {type(exc).__name__}: {exc}")
            report[label] = "FAILED: " + traceback.format_exc()

    # 1. environment
    def env():
        cp = os.environ.get("CONDA_PREFIX")
        return {
            "CONDA_PREFIX": cp,
            "exists": bool(cp) and os.path.isdir(cp),
            "nvcc": bool(cp) and os.path.exists(os.path.join(cp, "bin", "nvcc")),
            "include_cuda_runtime_h": bool(cp) and os.path.exists(os.path.join(cp, "include", "cuda_runtime.h")),
        }

    attempt("env", env)
    attempt("libGL", lambda: subprocess.run("ldconfig -p | grep -E 'libGL\.so\.1|libglib-2'", shell=True, capture_output=True, text=True).stdout.strip() or "NOT FOUND")

    attempt("pip_info", lambda: subprocess.run(
        "python -m pip list 2>/dev/null | grep -iE '^(setuptools|wheel|utils3d|pkg.resources|pip) ' ; "
        "python -m pip show -f utils3d 2>&1 | head -40; ls -la /usr/local/lib/python3.11/site-packages/utils3d 2>&1 | head -20",
        shell=True, capture_output=True, text=True).stdout)

    attempt("nvdiffrast_plugin_built", lambda: subprocess.run(
        "find /root/.cache/torch_extensions -name '*.so' 2>/dev/null; echo ---; python -m pip show nvdiffrast 2>&1 | head -3",
        shell=True, capture_output=True, text=True).stdout)
    attempt("ninja_and_nvcc", lambda: subprocess.run("which ninja nvcc; nvcc --version | tail -2", shell=True, capture_output=True, text=True).stdout)

    def utils3d_api():
        # every utils3d function the repo calls (found by grepping sam3d_objects/ and notebook/)
        import utils3d

        need = [
            "torch.rasterize_triangle_faces", "torch.RastContext", "torch.intrinsics_from_fov_xy", "torch.extrinsics_look_at",
            "torch.compute_connected_components", "io.write_ply", "torch.view_look_at", "torch.remove_unreferenced_vertices",
            "torch.perspective_from_fov_xy", "torch.intrinsics_to_perspective", "torch.intrinsics_from_focal_center",
            "torch.extrinsics_to_view", "torch.depth_to_points", "torch.compute_edges", "torch.compute_edge_connected_components",
            "torch.compute_dual_graph",
        ]
        missing = []
        for dotted in need:
            obj = utils3d
            try:
                for part in dotted.split("."):
                    obj = getattr(obj, part)
            except BaseException as exc:  # noqa: BLE001
                missing.append(f"{dotted} ({type(exc).__name__}: {exc})")
        if missing:
            report["errors"].append(f"utils3d API missing: {missing}")
        return {"checked": len(need), "missing": missing}

    attempt("utils3d_api", utils3d_api)

    def gaussian_renderer():
        # The exact condition the A100 run failed on: the repo's gaussian_render module must have the name
        # `GaussianRasterizationSettings` bound, with the fields it passes (kernel_size, subpixel_offset).
        sys.path.append("/opt/sam3d-objects/notebook")
        importlib.import_module("inference")  # same order as the real run: sets LIDRA_SKIP_INIT before sam3d_objects loads
        mod = importlib.import_module("sam3d_objects.model.backbone.tdfy_dit.renderers.gaussian_render")
        bound = hasattr(mod, "GaussianRasterizationSettings")
        fields = list(getattr(getattr(mod, "GaussianRasterizationSettings", None), "_fields", []))
        needed = {"kernel_size", "subpixel_offset", "image_height", "image_width", "tanfovx", "tanfovy", "bg",
                  "scale_modifier", "viewmatrix", "projmatrix", "sh_degree", "campos", "prefiltered", "debug"}
        missing = sorted(needed - set(fields))
        # The repo calls  GaussianRasterizer(raster_settings=...)(means3D=, means2D=, shs=, colors_precomp=, opacities=,
        # scales=, rotations=, cov3D_precomp=)  and unpacks  (rendered_image, radii).  Check the installed fork's
        # real forward() signature and that its compiled extension exposes the kernel entry point.
        import diff_gaussian_rasterization as dgr

        fwd = set(inspect.signature(dgr.GaussianRasterizer.forward).parameters)
        call_kwargs = {"means3D", "means2D", "shs", "colors_precomp", "opacities", "scales", "rotations", "cov3D_precomp"}
        fwd_missing = sorted(call_kwargs - fwd)
        has_kernel = hasattr(dgr._C, "rasterize_gaussians")
        if not bound or missing or fwd_missing or not has_kernel:
            report["errors"].append(
                f"gaussian_render: bound={bound} missing_fields={missing} forward_missing={fwd_missing} kernel={has_kernel}"
            )
        return {
            "GaussianRasterizationSettings_bound": bound, "fields": fields, "missing_fields": missing,
            "forward_params": sorted(fwd), "forward_missing_kwargs": fwd_missing, "compiled_kernel_rasterize_gaussians": has_kernel,
            "package_file": dgr.__file__,
        }

    attempt("gaussian_renderer", gaussian_renderer)

    # 2. imports the inference + baking path needs
    versions = {}
    for mod in [
        "torch", "torchvision", "kaolin", "pytorch3d", "open3d", "flash_attn", "gsplat", "utils3d", "xatlas",
        "pyvista", "pymeshfix", "igraph", "hydra", "omegaconf", "seaborn", "gradio", "trimesh", "cv2", "spconv",
        "moge", "loguru", "safetensors", "lightning", "pkg_resources", "nvdiffrast.torch",
        "diff_gaussian_rasterization",  # REQUIRED: baked-texture multi-view render (the GPU run failed without it)
    ]:
        try:
            m = importlib.import_module(mod)
            try:
                versions[mod] = str(getattr(m, "__version__", "ok"))
            except BaseException:  # noqa: BLE001 - e.g. utils3d's module __getattr__ tries to import a submodule
                versions[mod] = "ok (imported; no __version__)"
        except BaseException as exc:  # noqa: BLE001
            versions[mod] = f"FAILED {type(exc).__name__}: {exc} || {traceback.format_exc()}"
            report["errors"].append(f"import {mod}: {type(exc).__name__}: {exc}")
    report["imports"] = versions
    try:
        import torch

        report["torch_cuda"] = {"built_for": torch.version.cuda, "available_here(expected False on CPU)": torch.cuda.is_available()}
    except BaseException:  # noqa: BLE001
        pass

    # 3. the repo's own inference module (also sets CUDA_HOME from CONDA_PREFIX) and baking utils
    sys.path.append("/opt/sam3d-objects/notebook")

    def inference_module():
        inf = importlib.import_module("inference")
        importlib.import_module("sam3d_objects.model.backbone.tdfy_dit.utils.postprocessing_utils")
        return {"CUDA_HOME_after_import": os.environ.get("CUDA_HOME"), "has_Inference": hasattr(inf, "Inference")}

    attempt("inference_module", inference_module)

    # 4. entry-point signatures vs. what reconstruct_mesh calls
    def signatures():
        inf = importlib.import_module("inference")
        pm = importlib.import_module("sam3d_objects.pipeline.inference_pipeline_pointmap")
        run_params = set(inspect.signature(pm.InferencePipelinePointMap.run).parameters)
        used = {"image", "mask", "seed", "with_mesh_postprocess", "with_texture_baking", "decode_formats"}
        init_params = set(inspect.signature(inf.Inference.__init__).parameters)
        return {
            "Inference.__init__": sorted(init_params),
            "run_accepts_all_args_we_pass": used <= run_params,
            "run_missing": sorted(used - run_params),
        }

    attempt("signatures", signatures)

    # 5. Volume checkpoints (NOT downloaded here) + every path pipeline.yaml references
    def checkpoints():
        root = f"{CHECKPOINT_DIR}/hf"
        files = {f: os.path.getsize(os.path.join(root, f)) for f in sorted(os.listdir(root))}
        cfg_path = os.path.join(root, "pipeline.yaml")
        from omegaconf import OmegaConf

        cfg = OmegaConf.load(cfg_path)
        container = OmegaConf.to_container(cfg, resolve=False)
        refs = []

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str) and any(x.endswith(e) for e in (".ckpt", ".yaml", ".safetensors", ".pt", ".pth")):
                refs.append(x)

        walk(container)
        missing = [r for r in refs if not (os.path.exists(r) or os.path.exists(os.path.join(root, r)))]
        return {
            "files_MB": {k: round(v / 1e6, 1) for k, v in files.items()},
            "total_GB": round(sum(files.values()) / 1e9, 2),
            "referenced_paths": refs,
            "referenced_but_missing": missing,
            "top_level_keys": list(container)[:40],
            "rendering_engine_in_yaml": container.get("rendering_engine"),
            "depth_model_config": container.get("depth_model"),  # the one model that may fetch weights at runtime
            "optional_diff_gaussian_rasterization_importable": importlib.util.find_spec("diff_gaussian_rasterization") is not None,
        }

    attempt("checkpoints", checkpoints)

    def hydra_safety():
        inf = importlib.import_module("inference")
        from omegaconf import OmegaConf

        cfg = OmegaConf.load(f"{CHECKPOINT_DIR}/hf/pipeline.yaml")
        cfg.rendering_engine = "pytorch3d"
        cfg.workspace_dir = f"{CHECKPOINT_DIR}/hf"
        inf.check_hydra_safety(cfg, inf.WHITELIST_FILTERS, inf.BLACKLIST_FILTERS)
        return "passed (same check Inference.__init__ runs before instantiating)"

    attempt("hydra_safety_check", hydra_safety)
    report["ok"] = not report["errors"]
    import json

    # Plain JSON text: returning objects such as torch.__version__ pickles torch types the (torch-less)
    # local client cannot unpickle.
    return json.dumps(report, default=str)


@app.function(image=sam3d_image, gpu=GPU_TYPE, timeout=15 * 60, max_containers=1)
def gpu_smoke_test() -> str:
    """A100 smoke test of every CUDA extension on the real inference path -- WITHOUT loading the 13 GB models.

    Successful imports do not prove a compiled CUDA extension runs on the actual GPU, so this exercises each
    one through the repo's own call sites, on synthetic data: nvdiffrast (via utils3d), flash_attn, spconv, the
    Gaussian rasterizer (via the repo's render_multiview), and finally the repo's complete to_glb() -- mesh
    post-processing, UV parametrization, 100-view Gaussian rendering and the 2500-step texture bake -- on a
    synthetic sphere. That is the exact stage the first real GPU run died in, so a pass here means the
    expensive model run should get through baking. No secrets are attached and no checkpoints are read.
    """
    import importlib
    import json
    import os
    import sys
    import time
    import traceback

    results: dict = {}

    def run(name, fn):
        t0 = time.time()
        try:
            out = fn()
            results[name] = {"ok": True, "seconds": round(time.time() - t0, 1), "detail": out}
        except BaseException as exc:  # noqa: BLE001 - keep going so ONE run reports every broken extension
            results[name] = {"ok": False, "seconds": round(time.time() - t0, 1), "error": f"{type(exc).__name__}: {exc}",
                             "traceback": traceback.format_exc()[-1800:]}

    import torch

    sys.path.append("/opt/sam3d-objects/notebook")
    importlib.import_module("inference")  # sets CUDA_HOME / LIDRA_SKIP_INIT exactly like the real run

    run("cuda", lambda: {
        "device": torch.cuda.get_device_name(0), "capability": list(torch.cuda.get_device_capability(0)),
        "torch": torch.__version__, "cuda": torch.version.cuda,
    })

    def nvdiffrast_via_utils3d():
        import utils3d

        ctx = utils3d.torch.RastContext(backend="cuda")
        verts = torch.tensor([[[-0.8, -0.8], [0.8, -0.8], [0.0, 0.8]]], device="cuda")
        faces = torch.tensor([[0, 1, 2]], dtype=torch.int32, device="cuda")
        out = utils3d.torch.rasterize_triangle_faces(ctx, verts, faces, 64, 64)
        covered = float(out["mask"].sum())
        assert covered > 100, f"triangle rasterized to only {covered} pixels"
        return {"covered_pixels": covered}

    run("nvdiffrast_via_utils3d", nvdiffrast_via_utils3d)

    def flash_attention():
        from flash_attn import flash_attn_func

        q = torch.randn(1, 16, 4, 64, device="cuda", dtype=torch.float16)
        out = flash_attn_func(q, q, q)
        assert torch.isfinite(out).all()
        return {"shape": list(out.shape)}

    run("flash_attn", flash_attention)

    def spconv_conv():
        import spconv.pytorch as spconv

        coords = torch.randint(0, 8, (200, 4), dtype=torch.int32, device="cuda")
        coords[:, 0] = 0
        coords = torch.unique(coords, dim=0)
        feats = torch.randn(len(coords), 8, device="cuda")
        x = spconv.SparseConvTensor(feats, coords, [8, 8, 8], 1)
        y = spconv.SubMConv3d(8, 8, 3, indice_key="smoke").cuda()(x)
        assert torch.isfinite(y.features).all()
        return {"voxels": int(len(coords))}

    run("spconv", spconv_conv)

    def synthetic_scene():
        import numpy as np
        import trimesh

        from sam3d_objects.model.backbone.tdfy_dit.representations.gaussian import Gaussian
        from sam3d_objects.model.backbone.tdfy_dit.representations.mesh.cube2mesh import MeshExtractResult

        sphere = trimesh.creation.icosphere(subdivisions=4, radius=0.5)
        verts = torch.tensor(np.asarray(sphere.vertices), dtype=torch.float32, device="cuda")
        faces = torch.tensor(np.asarray(sphere.faces), dtype=torch.int32, device="cuda")
        attrs = torch.rand(len(verts), 6, device="cuda")
        mesh = MeshExtractResult(verts, faces, vertex_attrs=attrs)

        g = Gaussian(aabb=[-1.0, -1.0, -1.0, 2.0, 2.0, 2.0], sh_degree=0, device="cuda")
        pts = torch.tensor(np.asarray(sphere.sample(3000)), dtype=torch.float32, device="cuda")
        aabb = g.aabb
        g._xyz = (pts - aabb[None, :3]) / aabb[None, 3:]
        g._features_dc = (pts / 1.0 + 0.5).clamp(0, 1)[:, None, :].contiguous()
        g._features_rest = None
        g._scaling = torch.full((len(pts), 3), 0.7, device="cuda")
        g._rotation = torch.zeros(len(pts), 4, device="cuda")
        g._opacity = torch.full((len(pts), 1), 3.0, device="cuda")
        return g, mesh

    def gaussian_multiview():
        from sam3d_objects.model.backbone.tdfy_dit.utils.render_utils import render_multiview

        g, _ = synthetic_scene()
        obs, extr, intr = render_multiview(g, resolution=256, nviews=4)
        nonblank = [float((o > 0).any(axis=-1).mean()) for o in obs]
        assert max(nonblank) > 0.05, f"gaussian render is blank: {nonblank}"
        return {"views": len(obs), "nonblank_fraction": [round(n, 3) for n in nonblank]}

    run("gaussian_render_multiview_(diff_gaussian_rasterization)", gaussian_multiview)

    def full_to_glb():
        import io

        from sam3d_objects.model.backbone.tdfy_dit.utils import postprocessing_utils

        g, mesh = synthetic_scene()
        glb = postprocessing_utils.to_glb(
            g, mesh, simplify=0.95, texture_size=1024, verbose=False,
            with_mesh_postprocess=True, with_texture_baking=True, rendering_engine="pytorch3d",
        )
        buf = glb.export(file_type="glb")
        tex = glb.visual.material.baseColorTexture
        import numpy as np

        arr = np.array(tex)
        return {"faces": int(len(glb.faces)), "vertices": int(len(glb.vertices)), "glb_bytes": len(buf),
                "texture_size": list(tex.size), "texture_std": round(float(arr.std()), 2)}

    run("to_glb_full_stage_(postprocess+uv+bake)", full_to_glb)
    results["all_ok"] = all(v.get("ok") for k, v in results.items() if isinstance(v, dict))
    return json.dumps(results, default=str)


@app.function(
    image=sam3d_image,
    gpu=GPU_TYPE,
    volumes={CHECKPOINT_DIR: checkpoint_volume},
    timeout=30 * 60,  # enforced by Modal: worst case 30 min x $2.10/h = $1.05 of GPU time
    max_containers=3,  # Map mode builds several objects in parallel; worst case 3 x 30 min x $2.10/h. Single-object runs still use one.
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
def check_env() -> None:
    """CPU-only preflight: `modal run modal_app/sam3d.py::check_env`. Prints the full report."""
    import json

    report = json.loads(check_environment.remote())
    print(json.dumps(report, indent=1, default=str))
    print("PREFLIGHT_OK" if report["ok"] else f"PREFLIGHT_FAILED ({len(report['errors'])} errors)")


@app.local_entrypoint()
def gpu_smoke() -> None:
    """A100 smoke test of the CUDA extensions (no models loaded): `modal run modal_app/sam3d.py::gpu_smoke`."""
    import json

    report = json.loads(gpu_smoke_test.remote())
    print(json.dumps(report, indent=2))
    print("GPU_SMOKE_OK" if report.get("all_ok") else "GPU_SMOKE_FAILED")


@app.local_entrypoint()
def main(input_image: str, output_glb: str = "model.glb") -> None:
    """CLI smoke test: `modal run pipeline/modal_app/sam3d.py --input-image path.png`."""
    from pathlib import Path

    image_bytes = Path(input_image).read_bytes()
    print("checkpoints (CPU step):", download_checkpoints.remote())
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


def run_remote_many(rgba_png_list: list[bytes]) -> list:
    """Reconstruct several objects in ONE Modal app run, in parallel (up to max_containers A100s).

    Returns one entry per input, in order: the GLB bytes, or the Exception that object raised. One failing object never
    affects the others (``return_exceptions=True``).
    """
    if not rgba_png_list:
        return []
    with app.run():
        return list(reconstruct_mesh.map(rgba_png_list, return_exceptions=True))
