"""Modal-independent reconstruction fallback: a hosted Space, or an imported mesh.

``modal_app/sam3d.py`` (SAM 3D Objects, a Meta open-weights model) is the
primary reconstruction path, but Modal requires a payment method on the
account before any GPU function can run there (see CLAUDE.md status) -- a
real, unresolved blocker, not something this module works around.

This module provides two Modal-independent ways to get a real mesh instead:

1. ``reconstruct_via_hosted_space`` -- calls a free, publicly hosted
   Hugging Face Space running Hunyuan3D-2 (Tencent's open-weights
   image-to-3D model). **This is NOT a Meta model and NOT SAM 3D Objects.**
   Callers must record the actual backend used (see ``object.py``'s
   ``report["mesh_method"]``) -- never label hosted-Space output as SAM 3D
   Objects or any Meta model output. The hosted Space is a third party's
   free community deployment: it can disappear, change its API, or sit in
   a GPU queue with no SLA. Treat any failure here as expected, not a bug.
2. ``load_external_mesh`` -- reads an already-generated GLB/OBJ file
   (e.g. downloaded by hand from a hosted demo) so the rest of the
   pipeline (printprep/preview) can run on it without any reconstruction
   call at all.
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

from PIL import Image

DEFAULT_SPACE_ID = "frogleo/Image-to-3D"  # a community-hosted Hunyuan3D-2 Gradio Space
DEFAULT_API_NAME = "/gen_shape"
DEFAULT_TRELLIS_SPACE_ID = "trellis-community/TRELLIS"  # community deployment of Microsoft's TRELLIS


class ReconstructionError(RuntimeError):
    pass


def crop_to_alpha_bbox(rgba_png_bytes: bytes, pad_frac: float = 0.15, out_size: int = 1024) -> bytes:
    """Tightly crop an RGBA image to its opaque (alpha>0) bounding box, pad to a square, upscale.

    Real defect found and fixed against the real mug image (2026-10-27):
    hosted reconstruction Spaces work at a fixed input resolution. SAM
    3.1's ``object_rgba.png`` keeps the object at its true position in the
    full video frame, so if the masked object only occupies a small
    fraction of a much larger frame (measured: a 155x140px mug in an
    856x480 frame), naive resizing shrinks the actual subject to near-
    nothing and the Space reconstructs noise instead of the object --
    confirmed directly: the uncropped frame produced an unrecognizable
    shelf-like shape, while the same image cropped to the mug's real
    bounding box and upscaled produced a recognizable mug with a closed
    handle loop.
    """
    img = Image.open(io.BytesIO(rgba_png_bytes)).convert("RGBA")
    bbox = img.getbbox()
    if bbox is None:
        raise ReconstructionError("RGBA image has no opaque pixels to crop to")

    left, top, right, bottom = bbox
    w, h = right - left, bottom - top
    pad = int(max(w, h) * pad_frac)
    l2, t2 = max(0, left - pad), max(0, top - pad)
    r2, b2 = min(img.width, right + pad), min(img.height, bottom + pad)
    cropped = img.crop((l2, t2, r2, b2))

    side = max(cropped.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - cropped.width) // 2, (side - cropped.height) // 2)
    square.paste(cropped, offset)
    square = square.resize((out_size, out_size), Image.LANCZOS)

    buf = io.BytesIO()
    square.save(buf, format="PNG")
    return buf.getvalue()


def _import_gradio_client():
    """Lazy import so this module (and its pure functions) work without gradio_client installed."""
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise ReconstructionError(
            "gradio_client is not installed (pip install gradio_client) -- "
            "required for the hosted-Space reconstruction fallback"
        ) from exc
    return Client, handle_file


def _download_glb(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed hf.space URL built from space_id
            data = resp.read()
    except Exception as exc:  # noqa: BLE001 - any network/HTTP failure
        raise ReconstructionError(f"failed to download GLB from {url}: {exc}") from exc
    if not data:
        raise ReconstructionError(f"downloaded GLB from {url} is empty")
    return data


def reconstruct_via_hosted_space(
    rgba_png_bytes: bytes,
    *,
    space_id: str = DEFAULT_SPACE_ID,
    hf_token: str | None = None,
    crop_to_object: bool = True,
) -> bytes:
    """Call a hosted Hugging Face Space to reconstruct a textured mesh (GLB).

    Raises ReconstructionError on any failure (Space down, GPU queue
    timeout, unexpected API shape) -- never returns a fabricated or
    placeholder mesh. ``hf_token`` (e.g. the same ``HF_TOKEN`` used for the
    Modal secret) gets priority in the Space's ZeroGPU queue over an
    anonymous request; without it, requests commonly fail with "No GPU was
    available after 60s" even when the Space itself is healthy.
    """
    Client, handle_file = _import_gradio_client()

    if crop_to_object:
        rgba_png_bytes = crop_to_alpha_bbox(rgba_png_bytes)

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(rgba_png_bytes)
        tmp_path = f.name

    try:
        client = Client(space_id, token=hf_token)
        result = client.predict(
            image=handle_file(tmp_path),
            steps=5,
            guidance_scale=5.5,
            seed=1234,
            octree_resolution=256,
            num_chunks=8000,
            target_face_num=10000,
            randomize_seed=False,
            api_name=DEFAULT_API_NAME,
        )
    except ReconstructionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any Space/network failure
        raise ReconstructionError(f"hosted Space '{space_id}' reconstruction failed: {_hint_for_space_error(str(exc))}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    try:
        _html, _download, glb_static_path, _obj_static_path = result
    except (TypeError, ValueError) as exc:
        raise ReconstructionError(
            f"hosted Space '{space_id}' returned an unexpected result shape: {result!r}"
        ) from exc

    glb_url = f"https://{space_id.replace('/', '-').lower()}.hf.space{glb_static_path}"
    return _download_glb(glb_url)


def _hint_for_space_error(message: str) -> str:
    if "quota" in message.lower():
        return (
            f"{message} -- the free ZeroGPU quota for this Hugging Face account is used up; it resets "
            "daily. Retry later, or import a mesh you already have with --mesh-backend import. "
            "(Upgrading to a paid plan to get more quota is a billing decision this code never makes.)"
        )
    return message


def reconstruct_via_trellis_space(
    rgba_png_bytes: bytes,
    *,
    space_id: str = DEFAULT_TRELLIS_SPACE_ID,
    hf_token: str | None = None,
    seed: int = 1234,
    texture_size: int = 1024,
    crop_to_object: bool = True,
) -> bytes:
    """Reconstruct a TEXTURED mesh (GLB) of the masked object with TRELLIS on a hosted Space.

    TRELLIS is Microsoft's open-weights image-to-3D model -- NOT a Meta model, NOT SAM 3D Objects.
    Unlike the Hunyuan3D "white mesh" endpoint it returns a UV-mapped base-colour texture, a
    closed surface with real wall thickness, and a handle loop -- but the texture is *generated*
    (invented on every side the photo doesn't show); see texture_project.py for replacing the
    parts real video frames can see with real pixels.

    The call sequence mirrors what the Space's UI does (and was verified live): start a session,
    run the Space's own preprocessing, then generate and extract the GLB. Raises
    ReconstructionError on any failure (including an exhausted free ZeroGPU quota); never returns
    a placeholder mesh.
    """
    Client, handle_file = _import_gradio_client()
    if crop_to_object:
        rgba_png_bytes = crop_to_alpha_bbox(rgba_png_bytes)

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(rgba_png_bytes)
        tmp_path = f.name

    try:
        client = Client(space_id, token=hf_token)
        client.predict(api_name="/start_session")
        pre = client.predict(image=handle_file(tmp_path), api_name="/preprocess_image")
        pre_path = pre["path"] if isinstance(pre, dict) else pre
        result = client.predict(
            image=handle_file(pre_path),
            multiimages=[],
            seed=seed,
            ss_guidance_strength=7.5,
            ss_sampling_steps=12,
            slat_guidance_strength=3.0,
            slat_sampling_steps=12,
            multiimage_algo="stochastic",
            mesh_simplify=0.95,
            texture_size=texture_size,
            api_name="/generate_and_extract_glb",
        )
    except ReconstructionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any Space/network failure
        raise ReconstructionError(
            f"hosted Space '{space_id}' reconstruction failed: {_hint_for_space_error(str(exc))}"
        ) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    try:
        glb_path = Path(result[2])
    except (TypeError, IndexError) as exc:
        raise ReconstructionError(f"hosted Space '{space_id}' returned an unexpected result shape: {result!r}") from exc
    if not glb_path.is_file() or glb_path.stat().st_size == 0:
        raise ReconstructionError(f"hosted Space '{space_id}' did not produce a GLB file (got {glb_path})")
    return glb_path.read_bytes()


def load_external_mesh(path: Path) -> bytes:
    """Read an already-generated GLB/OBJ file as bytes, for ``--mesh-backend import``.

    Only validates the path exists and is non-empty here; object.py's
    printprep stage already validates the bytes parse as a real,
    non-degenerate mesh via ``printprep.load_mesh``.
    """
    path = Path(path)
    if not path.is_file():
        raise ReconstructionError(f"mesh file not found: {path}")
    data = path.read_bytes()
    if len(data) == 0:
        raise ReconstructionError(f"mesh file is empty: {path}")
    return data
