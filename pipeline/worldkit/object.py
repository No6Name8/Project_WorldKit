"""Object mode CLI: phone video in, print-ready STL out.

    python -m worldkit.object <video> --out <dir> [--object "mug"] [--height-cm 9.5]

Pipeline stages (see report.json's "timings_sec" for real timings):
    1. frames   - extract + pick sharpest frames (frames.py)
    2. detect   - Muse Spark names the object + picks the best frame, or falls
                  back to the sharpest frame + --object if the API is unavailable (detect.py)
    3. segment  - SAM 3.1 masks the object, and the A4 sheet if present, or
                  falls back to a local background-removal mask (segment.py)
    4. sam3d    - SAM 3D Objects on Modal builds a textured mesh (modal_app/sam3d.py)
    5. printprep - scale, repair, base, orient, thickness-check, STL (printprep.py)
    6. preview  - 4-angle PNG renders of the final STL (render.py)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from . import detect, frames, printprep, render, segment
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
    return parser.parse_args(argv)


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

    # --- 4. sam3d (Modal) ----------------------------------------------------
    def _sam3d_stage():
        from modal_app.sam3d import run_remote

        glb_bytes = run_remote(rgba_path.read_bytes())
        glb_path = out_dir / "model.glb"
        glb_path.write_bytes(glb_bytes)
        return glb_path

    glb_path = _timed("sam3d", timings, _sam3d_stage)
    report["mesh_method"] = "sam3d_builtin_mesh_postprocess_and_texture_baking"

    # --- 5. printprep ----------------------------------------------------
    stl_path = out_dir / "model_print.stl"
    prep_report = _timed(
        "printprep", timings, printprep.prepare_for_print,
        glb_path, stl_path,
        height_cm=args.height_cm,
        estimated_height_cm=estimated_height_cm,
        min_wall_mm=args.min_wall_mm,
    )
    report["print_prep"] = prep_report.to_dict()

    # --- 6. preview --------------------------------------------------------
    import trimesh

    final_mesh = trimesh.load(stl_path, force="mesh")
    preview_paths = _timed(
        "preview", timings, render.render_preview_angles, final_mesh, out_dir / "previews"
    )
    report["preview_renders"] = [str(p) for p in preview_paths]

    report["model_glb"] = str(glb_path)
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
            printprep.PrintPrepError) as exc:
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
