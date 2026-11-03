# Recording checklist

For the 3-minute demo video. The script and what to say are in [DEMO_SCRIPT.md](DEMO_SCRIPT.md); the rehearsed edit wording and
timings are in [DEMO_EDITS.md](DEMO_EDITS.md).

## Screen and system

- **Screen:** 1920 x 1080. Record the whole screen, or the Chrome window full screen (F11).
- **Browser:**
  - Chrome at 100% zoom. The `?demo=1` URLs set the WorldKit UI itself to 110% on wide screens, so leave Chrome at 100%.
  - Dark theme: if the page is light, click the moon button in the viewer's top bar once (it is remembered).
- **Notifications off:**
  - Windows Focus / Do not disturb on.
  - Phone in Do not disturb.
  - Slack, mail, WhatsApp desktop and the Teams tray closed.
- **Desktop:**
  - Clean: unrelated windows and tabs closed, desktop icons hidden, a plain wallpaper.
  - Never on screen: `pipeline/.env`, `web/.env.local`, the Modal or Supabase dashboards, or any terminal history that shows keys.

## Before the first take

```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo_reset.ps1
```

This copies the clean coin-scaled mug to `pipeline\runs\demo_live` at v0, then starts the edit server (`worldkit.serve`, port 5070) and the scene
viewer (`viewer\server.py`, port 5050), and opens <http://127.0.0.1:5070/?demo=1>. Run it again before every take: it stops the old
servers and resets the edit history. On Git Bash, macOS or Linux, use `bash scripts/demo_reset.sh`.

Check the edit server: the top bar must say "Muse Spark ready". If it says unavailable, `MODEL_API_KEY` is missing from `pipeline\.env`.

## Open, in this order

1. **Phone** (mirrored, or recorded separately): https://web-flame-psi-45.vercel.app, signed in, on **New project**, with a 10-20 s mug-and-coin video in the gallery (under 50 MB).
2. **Terminal** (small, bottom left), worker ready but not started:
   ```powershell
   cd pipeline
   .venv\Scripts\python.exe -m worldkit.worker --once
   ```
   Decide before recording: a real run takes several minutes and Modal credits (cut the wait in the edit), and the real pipeline has not been run through the hosted path yet, so rehearse it once. `--dry-run` publishes the bundled mug instead, and the voice-over must say so.
3. **Chrome tab 1:** <http://127.0.0.1:5070/?demo=1>. The mug at v0, with Object, AI Edit and the SAM overlay.
4. **Chrome tab 2:** <http://127.0.0.1:5050/?demo=1>, switched to **Map**: the coin desk scene. For the second scene, use the Developer tools button "Demo: desk, three laps" on <http://127.0.0.1:5050/>, without `?demo=1`.
5. **Slicer** (PrusaSlicer, Cura or Bambu Studio), units mm, an empty plate. Before the take, drop in the STL from tab 1's "Download STL (mm)" and check that it reads about 72.9 mm tall: the mug's 71.3 mm plus the base plate. Then clear the plate.
6. **Downloads folder:** where "Download STL (mm)" saves, ready to drag into the slicer.

## Takes, in order (from DEMO_SCRIPT.md)

| Take | What | Rough duration on screen | Real time to allow |
|---|---|---|---|
| 1 | Problem line: the mug on the desk, phone in hand | 10 s | 1 min |
| 2 | Phone upload on the live site: New project → Object → video → progress → "uploaded" | 20 s | 2 min |
| 3 | Worker on the laptop: claims the project → "processing" → "done" | 15 s (cut) | 5-10 min real, or 1 min with `--dry-run` |
| 4 | Object result in tab 1: orbit, W/H/D cards, scale source; then the Accuracy tab, table a | 25 s | 2 min |
| 5 | AI Edit: SAM overlay on, `make the handle 2 mm thicker`, stages and seconds, the row, Details | 35 s | about 20 s per edit, 2 min with retakes |
| 6 | Undo back to v0 | 10 s | 30 s |
| 7 | Optional: `make it exactly 12 cm tall`, the H card highlights (H 72.9 → 120.0 mm), then Undo | 10 s | 1 min |
| 8 | Map in tab 2: desk scene, orbit, Verify images (real / before / after) | 20 s | 2 min |
| 9 | Download STL (mm) → slicer → about 73 mm tall | 20 s | 2 min |
| 10 | Limitations line (card scale, one mug), then the title card | 15 s | 1 min |

Run `scripts\demo_reset.ps1` between takes 5-7 if an edit needs a retake: the history goes back to v0 and the AI Edit log empties.
