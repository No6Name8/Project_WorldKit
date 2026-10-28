# WorldKit — submission text

(Draft for the hackathon form. Items marked TODO need you: demo video link, final repository state.)

**Tagline:** Film it. Build it. A phone video becomes editable 3D objects and scenes you can inspect, change and drop under physics.

## What it does

WorldKit takes a short phone video and builds 3D objects from it. Muse Spark names the main object and picks the best frame, SAM 3.1 segments each object, and SAM 3D Objects reconstructs a textured 3D mesh for each. Several objects from one video are composed into a scene. A browser viewer lets you select each object, move/rotate/scale it, ask Muse Spark to edit by chat ("move it 10 cm to the left"), check overlaps, calibrate real-world scale, run a rigid-body physics drop, and export/reload the scene. Every result carries a report of which model made it and what is observed from the video versus generated.

## How Meta AI is used

- **Muse Spark** (Meta Model API): object naming and best-frame choice; structured frame analysis (category, rim, pattern type, stripe count, pattern region) that decides whether a pattern model may be attempted; natural-language scene edits, validated server-side before they touch the model.
- **SAM 3.1**: segmentation of objects (handle included) and a coin reference across all frames.
- **SAM 3D Objects** (open weights, run on a Modal A100): the 3D reconstruction of every object.

Not used: Muse Wonder, Muse Image, VGGT. Non-Meta components are recorded as such in each report (TRELLIS fallback, Rapier physics, three.js).

## Why it is credible

- **Honest evaluation.** Texture changes are accepted only if they beat the generated texture on frames held out of their construction (3-fold cross-validated on the real video: stripe correlation 0.68 generated, 0.80 pixel projection, 0.84 cylindrical stripe model). The pipeline keeps the original when a candidate does not win.
- **It caught its own mistakes.** An evaluation helper silently reused another mesh's camera poses; the resulting SAM 3D texture numbers were retracted and re-measured. Silhouette-only camera elevations turned out to be off by up to 30 degrees, which the pipeline now corrects against the texture.
- **Provenance everywhere.** Observed, inferred and generated surface shares are reported (37% / 3% / 61% on the mug); scale is a flagged placeholder unless measured; `print_ready` stays false without a measured height.
- **Reproducible.** The viewer, bundled real demo assets and physics run offline from a fresh clone; 162 Python tests and 129 real-browser checks run against real reconstructed meshes.

## Challenges

Getting SAM 3D to run (CUDA extension builds, a missing Gaussian rasterizer, headless GL) took many logged iterations; the camera/texture problems above were found by looking at renders, not by metrics. Geometry is still generated rather than measured, and the pipeline says so.

## What is next

A full 360-degree capture protocol (closes the angular gap and lets pattern counts be verified), measured real-world scale, scenes with more than two objects and true layout, and using VGGT-style multi-view geometry so shape is grounded in the footage and not only generated.

## Limitations

One object type in one video; the stripe model handles periodic vertical stripes on bodies of revolution only; scale accuracy on real footage is unverified; the two-object scene assumes the smaller object rests on the larger.

## Links

- Repository: https://github.com/No6Name8/Project_WorldKit
- Demo video: TODO
- Footage: `pipeline/samples/mug_on_table.mp4` (filmed by the author; confirm before submitting)

## Built with

Python, Modal (A100), Meta Model API (Muse Spark, SAM 3.1), SAM 3D Objects, trimesh/manifold3d, OpenCV, Flask, three.js, Rapier, Playwright.
