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

## a) HEADLINE: Object mode, coin scale, real mug vs ruler (`runs/mug_grounded`)

Video `pipeline/samples/mug_on_table.mp4` (mug and coin on a round table, 16 s). Scale from the coin
(21.5 mm, 4 frames) with the coin-grounded reshape (`scale_cal.calibrate_grounded`: the coin ellipse fixes the camera elevation; the
height aspect and taper of the generated mesh are fitted to SAM 3.1 silhouettes, IoU only).

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 71.3 | +1.9 | coin, 21.5 mm, 4 frames, grounded reshape |
| mug | rim_diameter | 85 | 88.1 | +3.7 | coin, 21.5 mm, 4 frames, grounded reshape |
| mug | base_diameter | 65 | 63.8 | -1.9 | coin, 21.5 mm, 4 frames, grounded reshape |

Mean absolute error 2.5%, worst 3.7%. Caveats: one mug only; the silhouette IoU is flat over heights 70.5-72.9 mm, so the real
uncertainty is about +-2%; the taper stage was added after seeing the base-diameter error (its fit uses no ground truth), so this result
is partly tuned by hindsight and needs a second object to confirm.

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

## Side by side (mug)

| dimension | real mm | a) coin, Object mode | b) coin, scene mode | c) card, scene mode |
|---|---:|---:|---:|---:|
| height | 70 | 71.3 (+1.9%) | 65.6 (-6.3%) | 81.8 (+16.9%) |
| rim diameter | 85 | 88.1 (+3.7%) | 87.7 (+3.2%) | 77.5 (-8.8%) |
| base diameter | 65 | 63.8 (-1.9%) | 66.8 (+2.7%) | 57.0 (-12.3%) |

## Other runs (not headline)

Same desk video as b), mug as the scale reference with its height set to 70 mm (`runs/desk_mugref`): rim 71.6 mm (-15.8%), base
59.6 mm (-8.3%); height is not scored because it defines the scale. The generated mug is too tall for its width, which is why an object
reference is the least reliable scale source.
