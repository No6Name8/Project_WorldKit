# Demo edits: rehearsed with the real models

Rehearsed on 2026-11-03 through the viewer's AI Edit panel (Playwright, `scripts/rehearse_edits.mjs`), with real Muse Wonder
(`muse-wonder-1.0`) and real Muse Spark. The model was a copy of the coin-scaled mug run (`pipeline/runs/rehearse`, a copy of
`runs/mug_grounded`). Each instruction ran twice, with Undo back to v0 after every edit. All 10 edits were applied on the first
attempt (0 retries, 0 rejections) and used 20 Wonder calls and 10 Spark calls. Raw records: `docs/demo_edits.json` (client side) and
`pipeline/runs/rehearse/edits/versions.json` (server side, gitignored).

"Seconds" is the server's time for the edit (Wonder + geometry + checks + Spark), run 1 / run 2. The panel showed the stages Choosing
tool, Applying and Checking with Spark in every run. The handle wall is the thickness measured at the SAM-found handle points
(`edit_tools.part_wall_mm`) on the print mesh.

| | Say exactly | Tool Wonder chose | Result (both runs) | Seconds | Spark said (run 1) |
|---|---|---|---|---|---|
| a | `make the handle 2 mm thicker` (SAM overlay on) | `thicken_part` handle, +2 mm | handle wall 5.22 → 7.34 mm, rest unchanged | 19.6 / 18.4 | "Close-up shows handle visibly wider while cup and saucer remain unchanged." |
| b | `put a 5 mm hole through the handle side` | `drill_hole` 5 mm, through, along the handle-side normal | through-hole in the handle side, size unchanged | 19.0 / 15.1 | "Close-up shows a clear through-hole in the handle side with no other visible changes." |
| c | `make the handle thinner` | `thicken_part` handle, -2 mm | handle wall 5.22 → 3.16 mm (above the 1.2 mm floor, so not refused) | 20.0 / 33.6 | "Close-up shows handle narrower while cup and saucer look unchanged." |
| d | `make it exactly 12 cm tall` | `scale_to` height 12 cm | print mesh 72.9 → 120.0 mm tall (95.3 → 157.0 mm wide), uniform | 18.1 / 17.2 | "After Z is 120 mm with proportional scaling and no shape change." |
| e | `add the text WORLDKIT on the base` | `emboss_text` "WORLDKIT", base | text engraved on the underside | 15.7 / 13.9 | "AFTER underside clearly shows WORLDKIT with no other visible changes." |
| f | Undo (after each) | - | back to v0 every time | under 2 s | - |

Notes:

- Spark calls the print base plate a "saucer". The plate is the flat print plate that `printprep` adds.
- The print mesh includes that plate, so its height (72.9 mm) is the 71.3 mm mug plus about 1.6 mm of plate. After "12 cm tall" the whole print mesh, plate included, is 120.0 mm.
- (c) was the first live test of a negative `thicken_part`. It thinned by 2 mm both times. Wonder chose -2 mm without being told an amount.

No instruction was flaky, so no tool description or prompt was changed. Safe alternatives, in case a take goes wrong:

| Instead of | Say |
|---|---|
| make the handle 2 mm thicker | make the handle thicker (defaults to 2 mm, tested live on 2026-11-02) |
| put a 5 mm hole through the handle side | put a 5 mm hole through the handle side for a keychain (tested live on 2026-11-01) |
| make the handle thinner | make the handle 1 mm thinner (gives an explicit amount; not rehearsed) |
| make it exactly 12 cm tall | scale it to 120 mm tall (not rehearsed) |
