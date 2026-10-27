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
        "generated + real video pixels where observed" if "projection" in info else "generated by the reconstruction model"
    )

    glb, finalize = appearance.finalize_textured_glb(glb)
    info["finalize"] = finalize
    report["appearance"] = info
    final_path = mesh_path.with_name("model.glb")
    final_path.write_bytes(glb)
    return final_path


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
            from modal_app.sam3d import run_remote

            mesh_bytes = run_remote(rgba_path.read_bytes())
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

    # --- 4b. appearance: real video pixels onto the texture, then a display-correct material -------
    mesh_path = _timed(
        "appearance", timings, _appearance_stage,
        mesh_path, args, report, sharp_frames, detection.object_name,
        chosen_frame_path.stem if args.mesh_backend == "trellis" else None,
    )

    # --- 5. printprep ----------------------------------------------------
    stl_path = out_dir / "model_print.stl"
    prep_report = _timed(
        "printprep", timings, printprep.prepare_for_print,
        mesh_path, stl_path,
        height_cm=args.height_cm,
        estimated_height_cm=estimated_height_cm,
        min_wall_mm=args.min_wall_mm,
        up_axis=args.up_axis,
    )
    report["print_prep"] = prep_report.to_dict()
    report["print_ready"] = prep_report.print_ready

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
