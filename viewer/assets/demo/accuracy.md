# Real vs model accuracy

Model sizes are measured on the exported print STLs (mm) by `scripts/accuracy_table.py`: height with the added base plate excluded,
rim and base diameters from a trimmed least-squares circle fit of a horizontal slice 4% below the top and 3% above the bottom, so the
handle does not inflate them. Ground truth is evaluation data only: it is never used to scale, tune or adjust a model. This page was
assembled by hand from that script's output (2026-11-03); rerunning the script overwrites it.

## How ground truth was measured

The user measured the one physical mug that appears in all three videos below (the striped mug with wavy vertical stripes, tapered: wider at the top)
with a ruler: height 70 mm, rim diameter 85 mm (taken as the outer diameter; the model is measured on its outer wall), base
diameter 65 mm. These are the only measured dimensions; the handle is not measured or scored. The base plate that `printprep` adds under every print STL is excluded on the model side. A ruler reads to
about 1 mm, which is 1.2-1.5% of these sizes. The values are stored in `pipeline/samples/ground_truth.json`. The coin was not measured:
its 21.5 mm diameter (Saudi 25 halalas) is a looked-up value. The card uses the ISO ID-1 size, 85.6 x 54 mm.

## a) HEADLINE: Object mode, coin scale, real mug vs ruler, two independent runs

Video `pipeline/samples/mug_on_table.mp4` (mug and coin on a round table, 16 s). Scale from the coin
(21.5 mm, 4 frames) with the coin-grounded reshape (`scale_cal.calibrate_grounded`: the coin ellipse fixes the camera elevation; the
height aspect and taper of the generated mesh are fitted to SAM 3.1 silhouettes, IoU only). Two runs of the same video and the same
code: run 1 (`runs/mug_grounded`, the bundled demo mug) used a SAM 3D mesh generated earlier (`runs/final_e2e`); run 2 is the real
upload on the live site processed by the laptop worker (2026-11-04), which generated a new SAM 3D mesh. Each run that builds a new
SAM 3D mesh gets slightly different geometry, so the numbers vary by a few percent from run to run.

| object | dimension | real mm | run 1: stored SAM 3D mesh (CLI), mm (error) | run 2: fresh SAM 3D mesh (web upload + worker), mm (error) | run 2 vs run 1 |
|---|---|---:|---:|---:|---:|
| mug | height | 70 | 71.3 (+1.9%) | 74.2 (+6.0%) | +4.1% |
| mug | rim_diameter | 85 | 88.1 (+3.7%) | 87.0 (+2.4%) | -1.2% |
| mug | base_diameter | 65 | 63.8 (-1.9%) | 61.4 (-5.5%) | -3.8% |

Run 1: mean absolute error 2.5%, worst 3.7%. Run 2: mean absolute error 4.6%, worst 6.0%. So the mean error is within about 5%
across two independent runs; the worst single dimension is 6.0% (run 2, height), and the two runs differ from each other by up to
4.1% (height). The CLI with run 1's mesh reproduces 71.3 / 88.1 / 63.8 mm exactly from the worker's own
copy of the video (`runs/cli_repro`, byte-identical input), so the difference between the runs is the generated mesh, not the web path
(`docs/results/e2e_web_real.json`). Caveats: one mug only; the silhouette IoU is flat over heights 70.5-72.9 mm; the taper stage
was added after seeing run 1's base-diameter error (its fit uses no ground truth), so run 1 is partly tuned by hindsight. Run 2 used
the same code after that change and is the fairer estimate of a new run.

Sources: `docs/results/object_mug_coin.json` and `docs/results/object_mug_coin_web_run.json` (command, input SHA-256, measured STL SHA-256).

## b) Scene mode, coin scale, desk run (`runs/desk_coin`): layout, not metrology

Video `pipeline/samples/desk_scene_coin.mp4`. Scale from the coin (21.5 mm, disk ellipse major axis, weak perspective). The coin is
only 10-27 px across in these frames. Scene mode places several objects at a common scale; these numbers show that the layout is
plausible, not that the object sizes are measured.

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 65.6 | -6.3 | coin, 21.5 mm, disk ellipse (weak perspective); layout, not metrology |
| mug | rim_diameter | 85 | 87.7 | +3.2 | coin, 21.5 mm, disk ellipse (weak perspective); layout, not metrology |
| mug | base_diameter | 65 | 66.8 | +2.7 | coin, 21.5 mm, disk ellipse (weak perspective); layout, not metrology |

Mean absolute error 4.1%, worst 6.3%. The height miss comes from the silhouette-fitted stretch reaching the edge of its search grid
(k = 0.80) with a tiny coin (coin-edge error about 5-8%, scale spread across frames 9.5%). The black case (56 mm tall in the model) has
no ground truth and is not scored.

Source: `docs/results/scene_desk_coin_v1.json` (command, input SHA-256, measured STL SHA-256).

## c) Scene mode, card scale, latest run (`runs/desk_card`, 2026-11-02): documented limitation

Video `pipeline/samples/desk_scene_card.mp4`. Scale from a bank card (85.6 x 54 mm), corner fit in 3 frames, corrected per object
with VGGT depth ratio and viewing elevation; the mug was built from one SAM 3D view (frame `sharp_09`).

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 81.8 | +16.9 | card (85.6 x 54 mm), corner fit, 3 frames, VGGT perspective correction; documented limitation |
| mug | rim_diameter | 85 | 77.5 | -8.8 | card (85.6 x 54 mm), corner fit, 3 frames, VGGT perspective correction; documented limitation |
| mug | base_diameter | 65 | 57.0 | -12.3 | card (85.6 x 54 mm), corner fit, 3 frames, VGGT perspective correction; documented limitation |

Mean absolute error 12.7%, worst 16.9%. Height and base diameter are more than 10% off. This is a documented limitation, not a
working feature. Causes, plainly:

- **The mug is about 65 px wide** in the only frames that also show the card (it sits about 20% farther from the camera than the card).
  At that size its silhouettes barely constrain its proportions (mean IoU over the height-aspect grid 0.878-0.895, nearly flat), so the
  generated shape decides the height-to-width ratio: the model is about as tall as it is wide; the real mug is 70 tall and 85 wide.
- **SAM 3.1 found the card in 0 of 12 frames.** The rectangle detector fallback (`worldkit/carddetect.py`) found it in 5; 2 of those
  were wrong (the incense burner's glass panel; a card half hidden by the bottle) and were rejected by eye, leaving 3 frames.
- **The perspective correction was tuned on this one run.** For the record (not chosen by ground truth): no correction gave
  -10.4 / -25.2 / -28.1% (mean 21.2%); the VGGT depth ratio alone +10.9 / -7.4 / -10.9% (mean 9.7%); depth ratio and elevation
  (kept, the physically complete correction) +16.9 / -8.8 / -12.3% (mean 12.7%).
- The frames with a large mug and the card together (43-47 of the 2 fps extraction) were not among the 12 kept, and the SAM call cap
  for the run (25) was spent.

Source: `docs/results/scene_desk_card_v1.json` (command, input SHA-256, measured STL SHA-256).

## d) Scene mode v2, coin scale, VGGT cameras, triangulated placement (`runs/desk_v2`, 2026-11-03)

Video `pipeline/samples/desk_scene_coin.mp4` (33 s, about one lap). VGGT-1B cameras on all 66 frames (2 fps). Scale from the coin measured
on the fitted table plane in 5 frames (plane method vs depth method 1.1% apart, spread 0.2%). The coin edge is re-segmented at half
intensity because the colour detector kept only the coin's bright core (about 20% short in diameter). That was found while looking at why
the first mug came out too large, so this result is partly informed by hindsight; the correction itself uses only the coin pixels.
Objects are placed by triangulating their mask centroids over 6-7 fit frames; scale and yaw are fitted to SAM silhouettes in those frames
only. The IoU below is measured on 6 other, held-out frames.

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 74.3 | +6.1 | coin, 21.5 mm, on the VGGT table plane, 5 frames; layout, not metrology |
| mug | rim_diameter | 85 | 77.4 | -8.9 | coin, 21.5 mm, on the VGGT table plane, 5 frames; layout, not metrology |
| mug | base_diameter | 65 | 61.7 | -5.1 | coin, 21.5 mm, on the VGGT table plane, 5 frames; layout, not metrology |

Mean absolute error 6.7%, worst 8.9%.

Table top (stadium fitted to 15 fused SAM 3.1 masks on the VGGT plane, fit IoU 0.975): 121.0 x 60.0 cm, corner radius 29.4 cm (a full
stadium). Not checked against a ruler: no table measurement was given. Thickness 2 cm is an estimate.

Silhouette IoU on the 6 held-out frames (VGGT cameras): mug 0.608, black case 0.609, candle in red holder 0.710, mean 0.643; table top
0.582. One Muse Spark correction round proposed 3 moves; none improved IoU, so before and after are equal. The mug's worst view (0.265) is
one where the mug is cut off at the image edge.

Source: `docs/results/scene_desk_v2.json` (command, input SHA-256, measured STL SHA-256).

## e) Scene mode, card scale, three laps (`runs/desk_card3`, 2026-11-03)

Video `pipeline/samples/desk_scene_card.mp4` (113 s, three laps), VGGT on 113 frames (1 fps). Scale from the bank card: the rectangle
detector found it in 42 frames; 7 passed the checks on the table plane (rectangular, ID-1 aspect within 6%), spread 1.1%. The mug was
found by SAM 3.1 in only 4 of 15 frames, so its scale rests on 3 fit frames.

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 85.8 | +22.6 | card 85.6 x 54 mm, 4 corners on the VGGT table plane, 7 frames; layout, not metrology |
| mug | rim_diameter | 85 | 87.4 | +2.8 | card 85.6 x 54 mm, 4 corners on the VGGT table plane, 7 frames; layout, not metrology |
| mug | base_diameter | 65 | 63.8 | -1.8 | card 85.6 x 54 mm, 4 corners on the VGGT table plane, 7 frames; layout, not metrology |

Mean absolute error 9.1%, worst 22.6% (height).

Table top 118.3 x 56.3 cm (corner radius 21.6 cm, stadium fit IoU 0.879). The same table measured from the coin video (d) is
121.0 x 60.0 cm: card vs coin scale differ by -2.2% on length and -6.2% on width. Neither is ruler-checked. Table height 14.6 cm and the
gold base (lower ring, 4 posts, rim) are estimated from VGGT points under SAM 3.1 frame masks; VGGT depth below the top looks compressed,
so the height is likely low (a camera plane sweep was tried and was inconclusive).

Silhouette IoU on 6 held-out frames (VGGT cameras): incense burner 0.639 (revolved profile: its SAM 3D mesh was not watertight),
black case 0.627, water bottle 0.381, mug 0.185 (one held-out frame); mean 0.542; table top 0.501. One Muse Spark round: no correction
improved IoU.

Source: `docs/results/scene_desk_card3.json` (command, input SHA-256, measured STL SHA-256).

## Second object: Hydro Flask 32 oz, coin scale, Object mode (`runs/object2`, 2026-11-04): not scored

Video `pipeline/samples/object2.mp4` (67 s; the bottle with the coin on a small round table), run exactly as the mug: `--object "water bottle" --coin saudi-25-halalas`, no other flags. Ruler: height 240 mm (cap on), widest body diameter 90 mm, cap rim width 70 mm.

**Not scored.** SAM 3.1 found the coin in only 1 of the 12 frames the run kept (the coin calibration needs 3), so the scale stayed a placeholder and no size from this run means anything. Muse Spark picked the best frame and SAM 3D Objects built a mesh, but without a scale there is nothing to compare with the ruler. The stage was stopped without tuning, as briefed. What is different about this object: it is tall (240 mm, more than three times the mug), so the camera stays farther back and the coin is smaller in every frame than in the mug video. Source: `docs/results/object_hydroflask_coin.json`.

## Side by side (mug)

| dimension | real mm | a) coin, Object mode, run 1 | a) coin, Object mode, run 2 (fresh mesh) | b) coin, scene mode | c) card, scene mode | d) coin, scene v2 | e) card, three laps |
|---|---:|---:|---:|---:|---:|---:|---:|
| height | 70 | 71.3 (+1.9%) | 74.2 (+6.0%) | 65.6 (-6.3%) | 81.8 (+16.9%) | 74.3 (+6.1%) | 85.8 (+22.6%) |
| rim diameter | 85 | 88.1 (+3.7%) | 87.0 (+2.4%) | 87.7 (+3.2%) | 77.5 (-8.8%) | 77.4 (-8.9%) | 87.4 (+2.8%) |
| base diameter | 65 | 63.8 (-1.9%) | 61.4 (-5.5%) | 66.8 (+2.7%) | 57.0 (-12.3%) | 61.7 (-5.1%) | 63.8 (-1.8%) |

## Other runs (not headline)

Same desk video as b), mug as the scale reference with its height set to 70 mm (`runs/desk_mugref`): rim 71.6 mm (-15.8%), base
59.6 mm (-8.3%); height is not scored because it defines the scale. The generated mug is too tall for its width, which is why an object
reference is the least reliable scale source.
