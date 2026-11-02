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
