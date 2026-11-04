# WorldKit demo script (3:00)

Screen recording at 1080p, voice-over. Every line in the "Say" column is backed by `docs/accuracy.md` or a recorded run. Do not add
claims while recording. Where a number comes from the live screen (AI Edit), read it off the screen, not from this page.

## Pre-recording checklist

- [ ] Clean screen: close unrelated windows and tabs, hide the desktop icons, use a plain wallpaper, and no terminal history with keys in it.
- [ ] Turn notifications off: Windows Focus / Do not disturb, phone in Do not disturb, close Slack, mail and WhatsApp desktop.
- [ ] Record at 1920x1080, browser zoom 100%, Chrome in full screen (F11) for the viewer shots.
- [ ] Never show `pipeline/.env`, `web/.env.local`, the Modal dashboard or the Supabase dashboard.
- [ ] Reset the demo (a clean copy of the coin-scaled mug at v0, the edit server on 5070, the scene viewer on 5050, the browser opened):
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts/demo_reset.ps1
  ```
  Tab 1 opens at <http://127.0.0.1:5070/?demo=1>: demo mode hides Developer tools and sets the UI to 110%. The mug must load at v0 with
  mm dimensions, and "Show what SAM found" must highlight the handle. Run the script again before every take.
- [ ] Tab 2: <http://127.0.0.1:5050/?demo=1>, Map, so the desk scene is loaded once.
- [ ] On the phone, sign in at https://web-flame-psi-45.vercel.app and have a 10-20 s video of the mug and coin ready in the gallery (under 50 MB).
- [ ] Terminal 3, worker, still stopped:
  ```powershell
  cd pipeline
  .venv\Scripts\python.exe -m worldkit.worker --once
  ```
  Decide before recording which way you will show it:
  - **Real.** It runs SAM 3D on Modal and the Meta API. It takes several minutes and uses credits. The real pipeline through this hosted path took 715 s in its one real run (2026-11-04), so do one complete rehearsal first. Cut the waiting time in the edit and say "sped up".
  - **`--dry-run`.** It publishes the bundled mug instead of processing the video. If you use it, the voice-over must say so.
- [ ] Open a slicer (PrusaSlicer, Cura or Bambu Studio) with an empty plate, set to mm.
- [ ] Rehearse once (the reset script gives a throwaway copy). The rehearsed wordings and timings are in `docs/DEMO_EDITS.md`. Use the exact wording.
- [ ] Keep a backup recording of one complete successful take.

## Files and windows to have open

| Window | What |
|---|---|
| Phone (screen mirrored or recorded separately) | live site, signed in, New project |
| Terminal 3 | `pipeline\.venv\Scripts\python.exe -m worldkit.worker --once` ready to run |
| Chrome tab 1 | <http://127.0.0.1:5070/?demo=1>: `worldkit.serve` on `pipeline/runs/demo_live` (Object result, AI Edit, SAM overlay) |
| Chrome tab 2 | <http://127.0.0.1:5050/?demo=1>: Map view (desk scene) |
| Slicer | empty plate |
| Downloads folder | where "Download STL (mm)" saves |

## Script

| Time | Shot | Say |
|---|---|---|
| 0:00-0:10 | the mug on the desk, phone in hand | "Getting a real object into 3D at its real size still takes a scanner or hours of modelling." |
| 0:10-0:30 | phone: live site → New project → Object → pick the video → upload progress bar → status "uploaded" | "WorldKit starts with a phone video. I film the mug with a coin next to it and upload it from the web app." |
| 0:30-0:45 | terminal 3: start the worker; it claims the project; phone status turns "processing" then "done" (cut the wait) | "A worker on my laptop picks it up. Muse Spark names the object, SAM 3.1 cuts it out in every frame, and SAM 3D Objects rebuilds it on a GPU." With `--dry-run`, say instead: "This take uses a dry run that publishes an earlier result." |
| 0:45-1:10 | tab 1, Viewer tab: orbit the mug, point at the W/H/D cards and the scale source; then the Accuracy tab, table a | "The coin sets the scale. Against a ruler, this mug's model is within 1.9% on height, 3.7% on the rim and 1.9% on the base. That's one mug, measured by hand." |
| 1:10-1:20 | tab 1, AI Edit tab: switch on **Show what SAM found** (the handle turns teal) | "SAM 3.1 found the handle in 11 of 12 video frames, and those votes are mapped onto the mesh." |
| 1:20-1:45 | type `make the handle 2 mm thicker` → Send. The panel shows Choosing tool / Applying / Checking with Spark and the seconds (about 20 s; cut the wait if needed); the row appears; open "Details" | "Now I edit it by typing. Muse Wonder picks a geometry tool, here thicken_part on the handle, and Muse Spark checks before-and-after renders before it's accepted. The handle wall goes from about 5 to about 7 millimetres." |
| 1:45-1:55 | click **Undo**; the handle returns to v0 | "Every edit is a version; undo goes back without deleting anything." |
| 1:55-2:05 | optional, only if time allows: `make it exactly 12 cm tall`. The H card highlights, and the row reads H 72.9 → 120.0 mm. Then Undo | "Exact sizes work the same way: 120 millimetres, checked." |
| 2:05-2:25 | tab 2, Map view: the desk scene, orbit, then a verify image (real / before / after) | "Scene mode does several objects on a desk and lays them out at one scale. This is layout, not measurement. VGGT cameras check the placement against the frames." |
| 2:25-2:45 | tab 1: **Download STL (mm)** → drag into the slicer → show the size readout | "The STL is in millimetres, so the slicer shows real size: about 73 mm tall, 71 for the mug plus a thin base plate." |
| 2:45-2:55 | `docs/accuracy.md` table c, or a title card | "What doesn't work yet: with a bank card instead of a coin, the scale was off by up to 17%, and all of this is tested on one mug." |
| 2:55-3:00 | title card: WorldKit, live URL, repo URL | "WorldKit: film it, print it." |

## If something fails while recording

| Symptom | Do |
|---|---|
| AI Edit refuses or Spark rejects | keep it in: "the verifier rejected it, nothing changed", then use the safe wording in `docs/DEMO_EDITS.md` or move on |
| Viewer blank on 5070 | check terminal 1; restart `worldkit.serve` on another port (not 5060/5061) |
| Worker fails | show the failed status sentence on the phone, then use `--dry-run` and say so |
| Slicer shows 0.73 or 730 | the slicer's unit setting is wrong; set mm and re-import |
