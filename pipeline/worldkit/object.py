"""Object mode CLI: phone video in, print-ready STL out.

    python -m worldkit.object <video> --out <dir> [--object "mug"] [--height-cm 9.5]

Pipeline stages (see report.json's "timings_sec" for real timings):
    1. frames   - extract + pick sharpest frames (frames.py)
    2. detect   - Muse Spark names the object + picks the best frame, or falls
                  back to the sharpest frame + --object if the API is unavailable (detect.py)
    3. segment  - SAM 3.1 masks the object, and the A4 sheet if present, or
                  falls back to a local background-removal mask (segment.py)
    4. reconstruct - mesh from the masked object: SAM 3D Objects on Modal (modal_app/sam3d.py),
                  or a Modal-independent hosted Space (TRELLIS textured / Hunyuan3D-2 untextured), or
                  an imported GLB/OBJ (reconstruct.py)
    5. appearance - for textured GLBs: replace the texels real video frames can see with their real
                  pixels (texture_project.py), then make the material display correctly (appearance.py)
    6. printprep - scale, repair, base, orient, thickness-check, STL (printprep.py)
    7. preview  - 4-angle PNG renders of the final STL (render.py)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from . import appearance, detect, frames, printprep, reconstruct, render, segment, texture_project, video_appearance
from .meta_api import MetaAPIError


class PipelineError(RuntimeError):
    pass


def _timed(label: str, timings: dict, fn, *args, **kwargs):
    start = time.time()
    try:
        return fn(*args, **kwargs)
    finally:
        timings[label] = round(time.time() - start, 2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m worldkit.object",
        description="Film it, print it: turn a phone video of an object into a print-ready STL.",
    )
    parser.add_argument("video", type=Path, help="path to the input video")
    parser.add_argument("--out", type=Path, required=True, help="output directory for this run")
    parser.add_argument("--object", dest="object_name", default=None, help="override the detected object name")
    parser.add_argument("--height-cm", dest="height_cm", type=float, default=None, help="exact real-world height, in cm")
    parser.add_argument("--fps", type=float, default=2.0, help="frame sampling rate for extraction (default: 2.0)")
    parser.add_argument("--keep-frames", type=int, default=12, help="how many sharp frames to keep (default: 12)")
    parser.add_argument("--min-wall-mm", type=float, default=printprep.DEFAULT_MIN_WALL_MM, help="minimum wall thickness to flag, in mm")
    parser.add_argument(
        "--mesh-backend", choices=["modal", "hosted", "trellis", "import"], default="modal",
        help=(
            "how to turn the segmented object into a mesh (default: modal). "
            "'modal' calls SAM 3D Objects (a Meta model) on Modal GPUs -- requires Modal to be "
            "usable (it currently is not; see CLAUDE.md). 'trellis' calls a free community Hugging "
            "Face Space running TRELLIS (Microsoft, NOT a Meta model): a TEXTURED mesh with real wall "
            "thickness. 'hosted' calls a Space running Hunyuan3D-2 (Tencent, NOT a Meta model): an "
            "untextured, paper-thin shell. 'import' skips reconstruction and uses an already-generated "
            "mesh via --mesh-file. The hosted Spaces share a small free daily GPU quota."
        ),
    )
    parser.add_argument("--hosted-space-id", default=None, help="Hugging Face Space id for --mesh-backend hosted/trellis (defaults per backend)")
    parser.add_argument(
        "--project-frames", type=int, default=None,
        help=(
            "for textured meshes: use up to N real video frames to replace the texture's invented "
            "colours with real pixels where they can be seen (default: 8 for --mesh-backend trellis, "
            "0 = off otherwise). Geometry is never modified."
        ),
    )
    parser.add_argument("--up-axis", choices=["auto", "y", "z"], default="auto", help="which mesh axis is 'up' for scaling (auto: glTF/GLB is Y-up, other formats Z-up)")
    parser.add_argument("--mesh-file", type=Path, default=None, help="path to an existing GLB/OBJ mesh, required with --mesh-backend import")
    parser.add_argument(
        "--mesh-source-report", type=Path, default=None,
        help=(
            "with --mesh-backend import: the report.json of the run that produced --mesh-file. Its recorded "
            "reconstruction model is inherited ONLY if that report's model_mesh is byte-identical to --mesh-file; "
            "otherwise the run fails rather than attribute the mesh to the wrong model."
        ),
    )
    parser.add_argument(
        "--ref-diameter-cm", type=float, default=None,
        help=(
            "real diameter (cm) of a circular reference visible in the video (e.g. a coin you measured). Enables scale "
            "calibration from the reference; the result is an ESTIMATE (never print-ready by itself). Use --height-cm for "
            "a directly measured height."
        ),
    )
    parser.add_argument(
        "--fidelity", choices=["off", "auto"], default="off",
        help=(
            "auto: for textured striped cups/mugs, analyse the frames (Muse Spark), measure the mesh, build a stripe model from "
            "the real frames and keep it ONLY if it beats the generated texture on held-out frames (report.json 'fidelity'). "
            "off (default): unchanged behaviour."
        ),
    )
    parser.add_argument("--no-aspect-fit", dest="no_aspect_fit", action="store_true", help="do not restretch the generated mesh height to the silhouettes (coin-grounded scale)")
    parser.add_argument("--coin", default=None, help="a known reference coin (scale_cal.COINS), e.g. saudi-25-halalas; sets --ref-diameter-cm")
    parser.add_argument("--ref-concept", default="coin", help="what SAM 3.1 should find as the circular reference (default: coin)")
    return parser.parse_args(argv)


# Which model actually produced the mesh -- recorded in every report so a demo can never
# attribute a non-Meta model's output to Meta (or vice versa).
RECONSTRUCTION_MODELS = {
    "modal": {"name": "SAM 3D Objects", "vendor": "Meta", "is_meta_model": True},
    "hosted": {"name": "Hunyuan3D-2 (community-hosted Space, shape only)", "vendor": "Tencent", "is_meta_model": False},
    "trellis": {"name": "TRELLIS (community-hosted Space)", "vendor": "Microsoft", "is_meta_model": False},
    "import": {"name": "unknown (imported mesh)", "vendor": "unknown", "is_meta_model": None},
}

DEFAULT_PROJECT_FRAMES = {"trellis": 8}


def inherit_model_from_source_report(mesh_file: Path, source_report: Path) -> tuple[dict, dict]:
    """Model provenance for an imported mesh, taken from the report of the run that made it -- verified by bytes.

    Returns (reconstruction_model, mesh_source). Raises PipelineError if the report does not describe this exact
    file, so a re-textured SAM 3D mesh can keep its attribution but an unrelated mesh can never borrow it.
    """
    import hashlib
    import json

    try:
        src = json.loads(Path(source_report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PipelineError(f"--mesh-source-report {source_report} is not readable JSON: {exc}") from exc
    model = src.get("reconstruction_model")
    recorded = src.get("model_mesh")
    if not model or not recorded:
        raise PipelineError(f"--mesh-source-report {source_report} has no reconstruction_model/model_mesh to inherit")
    recorded_path = Path(source_report).parent / Path(str(recorded).replace("\\", "/")).name
    if not recorded_path.is_file():
        raise PipelineError(f"mesh recorded in {source_report} ({recorded_path}) no longer exists, cannot verify it")
    want = hashlib.sha256(recorded_path.read_bytes()).hexdigest()
    got = hashlib.sha256(Path(mesh_file).read_bytes()).hexdigest()
    if want != got:
        raise PipelineError(
            f"--mesh-file is not the mesh recorded in {source_report} (sha256 differs); refusing to inherit its model attribution"
        )
    return dict(model), {
        "source_report": str(source_report),
        "source_mesh_backend": src.get("mesh_backend"),
        "source_mesh_method": src.get("mesh_method"),
        "mesh_sha256": got,
        "note": "model attribution inherited from the source report; the mesh file is byte-identical to the one it records",
    }


def _appearance_stage(
    mesh_path: Path, args: argparse.Namespace, report: dict, sharp_frames: list, object_name: str,
    anchor: str | None,
) -> Path:
    """Textured GLBs only: project real frames onto the texture, then fix the material for display.

    Untextured meshes (Hunyuan3D, SAM 3D without baking, an imported OBJ) pass through unchanged.
    """
    if mesh_path.suffix.lower() != ".glb":
        return mesh_path
    glb = mesh_path.read_bytes()
    if not appearance.glb_is_textured(glb):
        report["appearance"] = {"textured": False, "note": "mesh has no texture; appearance stage skipped"}
        return mesh_path

    info: dict = {"textured": True}
    if getattr(args, "fidelity", "off") == "auto":
        glb, fid = _fidelity_stage(glb, args, report, sharp_frames, object_name, mesh_path.parent)
        info["fidelity_applied"] = bool(fid.get("applied"))
        if fid.get("applied"):
            args = argparse.Namespace(**{**vars(args), "project_frames": 0})  # real-pixel projection would overwrite the pattern model
    n_frames = args.project_frames if args.project_frames is not None else DEFAULT_PROJECT_FRAMES.get(args.mesh_backend, 0)
    if n_frames > 0:
        inputs, skipped = video_appearance.build_frame_inputs([f.path for f in sharp_frames], object_name)
        info["frames_skipped"] = skipped
        if len(inputs) < 2:
            report["warnings"].append(
                f"Texture projection skipped: only {len(inputs)} frame(s) had a genuine SAM 3.1 segmentation (need >= 2)."
            )
        else:
            anchor_name = anchor if anchor in {i.name for i in inputs} else None
            glb, projection = texture_project.reproject_frames_onto_glb(
                glb, inputs, anchor=anchor_name, max_views=n_frames, texture_format="JPEG"
            )
            info["projection"] = projection
            report["warnings"].append(
                f"Texture: {projection['observed_fraction']:.0%} of the surface is real video pixels; "
                f"{projection['generated_fraction']:.0%} (unseen sides) is generated, and all geometry is generated."
            )
    info["texture_source"] = (
        "generated + real video pixels where observed" if "projection" in info
        else "generated, with the body pattern replaced by a stripe model measured from real frames (see fidelity)" if info.get("fidelity_applied")
        else "generated by the reconstruction model"
    )

    glb, finalize = appearance.finalize_textured_glb(glb)
    info["finalize"] = finalize
    report["appearance"] = info
    final_path = mesh_path.with_name("model.glb")
    final_path.write_bytes(glb)
    return final_path


def _scale_stage(mesh_path: Path, args: argparse.Namespace, report: dict, sharp_frames: list, object_name: str) -> float | None:
    """Reference-based scale: returns the estimated upright height in cm, or None (with a recorded reason)."""
    import numpy as np
    import trimesh

    from . import scale_cal

    scene = trimesh.load(mesh_path, force="mesh", process=False)
    verts, faces = np.asarray(scene.vertices, float), np.asarray(scene.faces)
    inputs, skipped = video_appearance.build_frame_inputs([f.path for f in sharp_frames], object_name)
    frames_in = []
    for inp in inputs:
        path = next(f.path for f in sharp_frames if Path(f.path).stem == inp.name)
        ref_mask = None
        try:
            r = segment.segment_concept(path, args.ref_concept)
            if not r.used_fallback:
                ref_mask = segment.full_frame_mask(path, r)
        except segment.SegmentationError:
            ref_mask = None
        frames_in.append((inp.name, inp.mask, ref_mask))
    grounded = None
    if not getattr(args, "no_aspect_fit", False):
        try:
            grounded = scale_cal.calibrate_grounded(frames_in, verts, faces, args.ref_diameter_cm)
        except scale_cal.ScaleCalibrationError as exc:
            report["warnings"].append(f"Coin-grounded calibration unavailable, using the silhouette-pose calibration: {exc}")
    try:
        cal = scale_cal.calibrate(frames_in, verts, faces, args.ref_concept, args.ref_diameter_cm)
    except scale_cal.ScaleCalibrationError as exc:
        report["scale_calibration"] = {"status": "failed", "reason": str(exc), "reference": args.ref_concept}
        report["warnings"].append(f"Scale calibration failed, scale stays a placeholder: {exc}")
        return None
    height_cm = cal.cm_per_unit * float(verts[:, 1].max() - verts[:, 1].min())
    info = cal.to_dict()
    if grounded is not None:
        info["silhouette_pose_only"] = {"cm_per_mesh_unit": cal.cm_per_unit, "height_cm": height_cm}
        info["grounded"] = grounded.to_dict()
        if grounded.applied:
            _reshape_glb(mesh_path, grounded.aspect_k, grounded.taper_t)
            info["mesh_reshaped"] = (f"generated mesh reshaped: height x{grounded.aspect_k:.3f}, taper {grounded.taper_t:+.2f} (fitted to the silhouettes at the "
                                     f"coin-derived camera elevation; mean IoU {grounded.mean_iou_generated:.3f} -> {grounded.mean_iou_final:.3f}; lateral scale from the coin)")
            report["warnings"].append(info["mesh_reshaped"])
        height_cm = grounded.height_cm
        info["cm_per_mesh_unit"] = grounded.cm_per_unit
        info["height_range_cm"] = list(grounded.height_range_cm)
    info.update({"status": "ok", "estimated_upright_height_cm": height_cm, "frames_without_object_mask": skipped})
    if args.height_cm is not None:
        info["cross_check_against_user_height"] = {
            "user_height_cm": args.height_cm, "estimate_error_pct": 100.0 * (height_cm - args.height_cm) / args.height_cm,
        }
    if not cal.consistent:
        report["warnings"].append(
            f"Scale calibration frames disagree (spread {cal.rel_spread:.0%} > {scale_cal.MAX_REL_SPREAD:.0%}); treat the scale as rough."
        )
    report["scale_calibration"] = info
    return height_cm


def _reshape_glb(path: Path, k: float, t: float) -> None:
    """Apply the silhouette-fitted height stretch and taper to every geometry of a GLB (textures and UVs untouched)."""
    import numpy as np
    import trimesh

    from . import scale_cal

    sc = trimesh.load(path, process=False)
    geoms = list(sc.geometry.values())
    ref = scale_cal.shape_reference(np.vstack([np.asarray(g.vertices, float) for g in geoms]))
    for g in geoms:
        g.vertices = scale_cal.reshape_vertices(g.vertices, k, t, ref)
    path.write_bytes(sc.export(file_type="glb"))


def _fidelity_stage(glb: bytes, args: argparse.Namespace, report: dict, sharp_frames: list, object_name: str, out_dir: Path):
    """Vision analysis + evidence-gated cup fidelity improvement (worldkit.fidelity). Never raises on 'not applicable'."""
    import numpy as np
    from PIL import Image

    from . import fidelity, understand

    inputs, skipped = video_appearance.build_frame_inputs([f.path for f in sharp_frames], object_name)
    fid: dict = {"frames_without_genuine_mask": skipped}
    if len(inputs) < 5:
        fid.update({"applied": False, "decisions": [f"only {len(inputs)} frame(s) with a genuine SAM 3.1 mask; fidelity stage skipped"]})
        report["fidelity"] = fid
        return glb, fid
    crop_dir = out_dir / "fidelity" / "understand_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    picks = [inputs[i] for i in np.linspace(0, len(inputs) - 1, min(6, len(inputs))).round().astype(int)]
    crops = []
    for fi in picks:
        rgba = np.dstack([fi.rgb, (fi.mask * 255).astype(np.uint8)])
        im = Image.fromarray(rgba, "RGBA")
        bb = im.getbbox()
        im = im.crop(bb)
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        side = max(bg.size)
        sq = Image.new("RGB", (side, side), (255, 255, 255))
        sq.paste(bg.convert("RGB"), ((side - bg.width) // 2, (side - bg.height) // 2))
        path = crop_dir / f"{fi.name}.png"
        sq.resize((512, 512)).save(path)
        crops.append(path)
    try:
        und = understand.analyse_frames(crops)
    except understand.UnderstandingError as exc:
        und = None
        report["warnings"].append(f"Vision analysis failed, fidelity stage skipped: {exc}")
    glb2, rep = fidelity.improve_striped_cup(glb, inputs, und)
    fid.update(rep)
    report["fidelity"] = fid
    if rep.get("applied"):
        report["warnings"].append("Fidelity stage applied: " + "; ".join(rep.get("decisions", [])))
    return glb2, fid


def run_sam3d_on_modal(rgba_png_bytes: bytes) -> bytes:
    """Call SAM 3D Objects on Modal; turn the many ways this can fail into one actionable message."""
    try:
        from modal_app.sam3d import run_remote

        return run_remote(rgba_png_bytes)
    except Exception as exc:  # noqa: BLE001 - modal raises many unrelated exception types (auth, quota, timeout, build)
        raise reconstruct.ReconstructionError(
            f"SAM 3D Objects on Modal failed ({type(exc).__name__}: {str(exc)[:300]}). Checklist: `modal setup` done? "
            "Modal secret `huggingface` created and access to facebook/sam-3d-objects granted? credits left "
            "(`modal billing summary`)? CPU preflight: `modal run \"modal_app/sam3d.py::check_env\"` from pipeline/. "
            "Without a GPU you can rerun with --mesh-backend trellis."
        ) from exc


def measurement_summary(report: dict, prep_report, args) -> dict:
    """What size the exported model has, how that size was obtained, and how uncertain it is (all in the report, never implied)."""
    h_cm = round(float(prep_report.upright_height_cm), 3)
    base = {"height_cm": h_cm, "height_mm": round(h_cm * 10, 1), "height_definition": "upright object height, before the flat print base is added",
            "stl_units": "mm", "stl_bbox_mm": [round(float(v) * 10, 1) for v in prep_report.dimensions_cm]}
    if prep_report.height_source == "user":
        base.update({"method": "--height-cm (measured by the user)", "uncertainty_pct": None,
                     "uncertainty_note": "the user's own measurement; its error is the user's instrument error"})
        sc = report.get("scale_calibration") or {}
        if sc.get("status") == "ok":
            base["coin_cross_check"] = sc.get("cross_check_against_user_height")
        return base
    if prep_report.height_source == "reference_calibrated":
        sc = report["scale_calibration"]
        import math

        px = [f["px_per_cm"] * sc["reference_diameter_cm"] for f in sc["per_frame"]]  # coin major axis in pixels per frame
        quant = 1.0 / max(float(min(px)), 1.0)  # +-1 px on the smallest coin measurement
        spread = float(sc["relative_spread_mad"])
        systematic = 0.037  # mean error of this method on synthetic scenes with known sizes (docs/TECHNICAL_NOTES.md)
        u = math.sqrt(spread**2 + quant**2 + systematic**2)
        base.update({"method": f"coin ({sc['reference']}, {sc['reference_diameter_cm']} cm diameter, {sc['frames_used']} frames)",
                     "uncertainty_pct": round(100 * u, 1),
                     "uncertainty_components_pct": {"frame_to_frame_spread": round(100 * spread, 2), "coin_edge_1px": round(100 * quant, 2), "method_systematic_synthetic": round(100 * systematic, 1)},
                     "uncertainty_note": "ESTIMATE: root-sum-square of the measured frame spread, a 1 px coin-edge error and the method's mean error on synthetic scenes; worst synthetic case was 11.5%. Real accuracy is only known where a measured height is also given."})
        return base
    base.update({"method": "placeholder (no coin and no --height-cm): the size is NOT a measurement", "uncertainty_pct": None})
    return base


def prepare_modal_input(rgba_png_bytes: bytes) -> bytes:
    """Crop SAM 3.1's full-frame RGBA to the object (same crop the hosted backends use) before SAM 3D Objects.

    Measured on the real mug video: the full frame holds the mug at ~110x140 px of 480x856, and the resulting
    SAM 3D texture came out blotchy; the validated 1024 px object crop gave clean stripes and a smooth handle.
    """
    return reconstruct.crop_to_alpha_bbox(rgba_png_bytes)


def run(args: argparse.Namespace) -> dict:
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    report: dict = {"timings_sec": timings, "warnings": []}

    # --- 1. frames -------------------------------------------------------
    sharp_frames = _timed(
        "frames", timings, frames.extract_sharp_frames,
        args.video, out_dir, fps=args.fps, keep_n=args.keep_frames,
    )
    report["frames_extracted"] = len(sharp_frames)

    # --- 2. detect -----------------------------------------------------
    detection = _timed(
        "detect", timings, detect.detect_object,
        sharp_frames, object_name_override=args.object_name,
    )
    report["object_name"] = detection.object_name
    report["detect_reason"] = detection.reason
    report["used_fallback_detect"] = detection.used_fallback
    report["api_usage"] = {
        "muse_spark_detect_tokens": detection.usage,
        "note": (
            "The Meta Model API does not return a dollar cost per call, only "
            "token usage (shown here where the API provided it). No per-request "
            "pricing was found in the docs, so a dollar cost cannot be computed."
        ),
    }
    if detection.used_fallback:
        report["warnings"].append(
            "FALLBACK: Meta API unavailable for object detection; used sharpest frame + --object override."
        )

    chosen_frame_path = detection.best_frame.path
    chosen_frame_dest = out_dir / "chosen_frame.png"
    chosen_frame_dest.write_bytes(chosen_frame_path.read_bytes())
    report["chosen_frame"] = str(chosen_frame_dest)

    # --- 3. segment ----------------------------------------------------------
    def _segment_stage():
        obj_result = segment.segment_concept(chosen_frame_path, detection.object_name)
        a4_box = None
        if not obj_result.used_fallback:
            try:
                a4_result = segment.segment_concept(chosen_frame_path, "A4 sheet of paper")
                a4_box = a4_result.box
            except segment.SegmentationError as exc:
                report["warnings"].append(f"A4 sheet not found/segmented: {exc}")
        else:
            report["warnings"].append(
                "FALLBACK: A4-sheet segmentation skipped (local fallback mask can't target a named concept); rely on --height-cm."
            )
        return obj_result, a4_box

    obj_mask_result, a4_box = _timed("segment", timings, _segment_stage)
    report["used_fallback_segment"] = obj_mask_result.used_fallback
    report["segment_from_cache"] = obj_mask_result.from_cache
    report["segment_source"] = (
        "fallback_local_mask" if obj_mask_result.used_fallback
        else "sam-3.1_cached" if obj_mask_result.from_cache
        else "sam-3.1_live"
    )

    overlay_name = "mask_overlay.png"
    overlay_path = out_dir / overlay_name
    overlay_path = segment.save_mask_overlay(
        chosen_frame_path, obj_mask_result.mask, overlay_path,
        used_fallback=obj_mask_result.used_fallback, box=obj_mask_result.box,
    )
    report["mask_overlay"] = str(overlay_path)

    rgba_path = out_dir / "object_rgba.png"
    segment.save_rgba_with_mask(chosen_frame_path, obj_mask_result.mask, rgba_path, box=obj_mask_result.box)

    # --- height: user-given > A4 estimate > fallback ------------------------
    estimated_height_cm = None
    estimated_height_source = "a4_estimate"
    if args.height_cm is None and obj_mask_result.box and a4_box:
        obj_top, obj_bottom = obj_mask_result.box[1], obj_mask_result.box[3]
        obj_pixel_height = obj_bottom - obj_top
        a4_pixel_width = a4_box[2] - a4_box[0]
        try:
            estimated_height_cm = printprep.estimate_height_cm_from_a4(obj_pixel_height, a4_pixel_width)
        except printprep.PrintPrepError as exc:
            report["warnings"].append(f"A4-based height estimate failed: {exc}")

    # --- 4. reconstruct: modal (SAM 3D Objects) / hosted (fallback) / import --
    def _reconstruct_stage():
        backend = args.mesh_backend
        if backend == "modal":
            mesh_bytes = run_sam3d_on_modal(prepare_modal_input(rgba_path.read_bytes()))
            suffix, method = ".glb", "modal_sam3d_builtin_mesh_postprocess_and_texture_baking"
        elif backend == "hosted":
            import os

            space_id = args.hosted_space_id or reconstruct.DEFAULT_SPACE_ID
            mesh_bytes = reconstruct.reconstruct_via_hosted_space(
                rgba_path.read_bytes(),
                space_id=space_id,
                hf_token=os.environ.get("HF_TOKEN"),
            )
            suffix, method = ".glb", f"hosted_space_hunyuan3d:{space_id}"
        elif backend == "trellis":
            import os

            space_id = args.hosted_space_id or reconstruct.DEFAULT_TRELLIS_SPACE_ID
            mesh_bytes = reconstruct.reconstruct_via_trellis_space(
                rgba_path.read_bytes(),
                space_id=space_id,
                hf_token=os.environ.get("HF_TOKEN"),
            )
            suffix, method = ".glb", f"hosted_space_trellis:{space_id}"
        elif backend == "import":
            if args.mesh_file is None:
                raise PipelineError("--mesh-backend import requires --mesh-file")
            mesh_bytes = reconstruct.load_external_mesh(args.mesh_file)
            suffix = Path(args.mesh_file).suffix or ".glb"
            method = f"imported_external_mesh:{Path(args.mesh_file).name}"
        else:
            raise PipelineError(f"unknown --mesh-backend: {backend}")

        mesh_path = out_dir / f"model{suffix}"
        mesh_path.write_bytes(mesh_bytes)
        return mesh_path, method

    (mesh_path, mesh_method) = _timed("reconstruct", timings, _reconstruct_stage)
    report["mesh_method"] = mesh_method
    report["mesh_backend"] = args.mesh_backend
    report["reconstruction_model"] = RECONSTRUCTION_MODELS[args.mesh_backend]
    if args.mesh_source_report is not None:
        if args.mesh_backend != "import":
            raise PipelineError("--mesh-source-report only applies to --mesh-backend import")
        report["reconstruction_model"], report["mesh_source"] = inherit_model_from_source_report(
            args.mesh_file, args.mesh_source_report
        )

    # --- 4b. appearance: real video pixels onto the texture, then a display-correct material -------
    mesh_path = _timed(
        "appearance", timings, _appearance_stage,
        mesh_path, args, report, sharp_frames, detection.object_name,
        chosen_frame_path.stem if args.mesh_backend == "trellis" else None,
    )

    if args.ref_diameter_cm is not None:
        calibrated_height_cm = _timed("scale", timings, _scale_stage, mesh_path, args, report, sharp_frames, detection.object_name)
        if calibrated_height_cm is not None:
            estimated_height_cm = calibrated_height_cm
            estimated_height_source = "reference_calibrated"

    # --- 5. printprep ----------------------------------------------------
    stl_path = out_dir / "model_print.stl"
    prep_report = _timed(
        "printprep", timings, printprep.prepare_for_print,
        mesh_path, stl_path,
        height_cm=args.height_cm,
        estimated_height_cm=estimated_height_cm,
        estimated_height_source=estimated_height_source,
        min_wall_mm=args.min_wall_mm,
        up_axis=args.up_axis,
    )
    report["print_prep"] = prep_report.to_dict()
    report["print_ready"] = prep_report.print_ready
    report["measurement"] = measurement_summary(report, prep_report, args)

    # --- 6. preview --------------------------------------------------------
    import trimesh

    final_mesh = trimesh.load(stl_path, force="mesh")
    preview_paths = _timed(
        "preview", timings, render.render_preview_angles, final_mesh, out_dir / "previews"
    )
    report["preview_renders"] = [str(p) for p in preview_paths]

    report["model_mesh"] = str(mesh_path)
    report["model_stl"] = str(stl_path)
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.coin and args.ref_diameter_cm is None:
        from . import scale_cal

        args.ref_diameter_cm = scale_cal.coin_diameter_cm(args.coin)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.json"

    try:
        report = run(args)
    except (PipelineError, MetaAPIError, frames.FrameExtractionError,
            detect.DetectionError, segment.SegmentationError,
            reconstruct.ReconstructionError, printprep.PrintPrepError) as exc:
        report = {"error": str(exc), "error_type": type(exc).__name__}
        report_path.write_text(json.dumps(report, indent=2))
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - last resort, never fail silently
        report = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        report_path.write_text(json.dumps(report, indent=2))
        print(f"FAILED (unexpected): {exc}", file=sys.stderr)
        return 1

    report_path.write_text(json.dumps(report, indent=2))
    print(f"Done. Report: {report_path}")
    print(f"print_ready: {report.get('print_ready')}  (true needs a measured --height-cm and passing geometry checks)")
    print(f"View it:  python viewer/server.py --open {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
