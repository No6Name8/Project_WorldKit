# What we tried

Short notes on experiments that are not part of the pipeline, so nobody repeats them blind.

## Room layer (dropped)

The idea was a room-scale layer (floor, walls, room size) from a room-tour video, with the desk objects placed in it. It is no longer planned: the product focus is objects first, then the desk scene.

### VGGT on a full room tour (2026-11-02, probe only, not integrated)

- Setup: `modal_app/vggt_probe.py` (facebook/VGGT-1B, A100), 30 frames sampled every 2 s from a 60 s room tour; analysis in `scripts/vggt_probe_analyze.py`.
- It ran: 47 s of compute, 10.6 GB peak memory.
- Camera poses were plausible: the camera path closes the loop (start vs the same view 42 s later: 0.5 median steps apart, 8 deg).
- The table region was dense and clean; walls were sparse and the confidence output was saturated (about 1.0 everywhere), so it was not a useful filter.
- Plane fitting found 2 horizontal planes and only 1 vertical plane, no parallel wall pair, so no room width, length or height could be derived.
- Scale from the mug (measured on VGGT points, about 2.9 m per unit) put the camera path at roughly 5 x 2.3 m, but the implied camera height above the floor was implausible, so that scale is not trustworthy.
- Conclusion: VGGT camera poses are usable (it is now used for the desk verify step, with scale from the coin); room reconstruction from it was not.

### VGGT on the desk (kept, for verification only)

- VGGT-1B runs on the same Modal A100 (`modal_app/vggt_probe.py`, about 57 s GPU for the desk card run). In scene mode (`--vggt`) it
  gives per-frame cameras, aligned to the desk world with scale from the coin or the card corners; the verify step measures silhouette
  IoU with both these cameras and the weak-perspective ones.
- On the card run it also gave the per-object perspective correction (depth ratio and viewing elevation). In 3D the card's
  long/short ratio came out 1.39-1.50 instead of 1.585, so either the card outline or VGGT's depth is somewhat off.
- Placement still uses the weak-perspective fit; in the VGGT views the cup sits about 3 cm off. VGGT does not build any geometry here.

## Card as the scale reference (2026-11-02, `runs/desk_card`; worked end to end, accuracy poor)

- Video `pipeline/samples/desk_scene_card.mp4` (a second take, `desk_scene_card2.mp4`, was never recorded).
- **SAM 3.1 misses small references.** Asked for "bank card", SAM 3.1 found it in 0 of 12 frames. The same pattern held for the
  coin (12-27 px across): SAM 3.1 returns nothing for a reference that small, which is why there are colour (`coindetect.py`) and
  rectangle (`carddetect.py`) fallbacks. The rectangle detector found the card in 5 frames but also accepted the incense burner's glass
  panel and a card half hidden by the bottle; those 2 were rejected by eye on `docs/ui/card_preflight.png`.
- The old card reading assumed the long side faces the camera and was about 15% wrong when it points away. It was replaced by a
  4-corner weak-perspective fit (`refscale.card_from_quad`); the 3 card frames then agree on about 29 deg camera elevation.
- Only the mug was placed. The black case was built but never shares a frame with the card; the burner and the bottle were skipped as
  shiny/transparent. The mug's first two SAM 3D builds (from frames 06 and 07) came back shattered, so it was built from frame 09,
  which shows no handle.
- Result vs ruler: height +16.9%, rim -8.8%, base -12.3% (`docs/accuracy.md`, table c). Main cause: the mug is about 65 px wide in the
  frames that show the card, so the generated proportions decide its height. Frames with a large mug and the card together (43-47 at
  2 fps) were not among the 12 sharpest kept.
- Lesson: pick frames where the object and the reference are both large, not just the sharpest frames. Not implemented.
- Budget guard added for the unattended run (`worldkit/budget.py`, `WK_BUDGET_FILE`): Spark 5 of 40 calls, SAM 25 of 25 (cap
  reached), Modal at most 13.4 A100-minutes of 20.

## Freeze (2026-11-03)

Decision: the pipeline is frozen for submission. No more GPU runs, Meta API calls or changes to pipeline logic. Reasons: the headline
result (Object mode, coin, +1.9 / +3.7 / -1.9%) is already measured; the card run showed that the next gain needs a new capture (object
and reference both large in the same frames), not more code on the current footage; and every further run would be tuned on the same
one mug. Remaining time goes to documentation, the demo and an honesty pass.
