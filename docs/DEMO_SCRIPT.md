# WorldKit demo script (3:00)

Screen recording at 1080p, voice-over. Every line in the "Say" column is backed by `docs/accuracy.md` or a recorded run. Do not add
claims while recording. Where a number comes from the live screen (AI Edit), read it off the screen, not from this page.

## Pre-recording checklist

- [ ] Clean screen: close unrelated windows and tabs, hide the desktop icons, use a plain wallpaper, and no terminal history with keys in it.
- [ ] Turn notifications off: Windows Focus / Do not disturb, phone in Do not disturb, close Slack, mail and WhatsApp desktop.
- [ ] Record at 1920x1080, browser zoom 100%, Chrome in full screen (F11) for the viewer shots.
- [ ] Never show `pipeline/.env`, `web/.env.local`, the Modal dashboard or the Supabase dashboard.
- [ ] Make a fresh copy of the headline run so the edits of this take start from v0:
  ```powershell
  robocopy pipeline\runs\mug_grounded pipeline\runs\demo_take /E
  ```
- [ ] Start the edit server in terminal 1 and leave it running (port 5070: Chrome refuses the default port 5060):
  ```powershell
  pipeline\.venv\Scripts\python.exe -m worldkit.serve pipeline\runs\demo_take --port 5070
  ```
  Open <http://127.0.0.1:5070/>. The mug must load with mm dimensions, and "Show what SAM found" must highlight the handle.
- [ ] Load the demo run: in a second tab, open <http://127.0.0.1:5050/> after `pipeline\.venv\Scripts\python.exe viewer\server.py` (terminal 2), click **Demo run**, and switch to **Map** once so the desk scene is cached.
- [ ] On the phone, sign in at https://web-flame-psi-45.vercel.app and have a 10-20 s video of the mug and coin ready in the gallery (under 50 MB).
- [ ] Terminal 3, worker, still stopped:
  ```powershell
  cd pipeline
  .venv\Scripts\python.exe -m worldkit.worker --once
  ```
  Decide before recording which way you will show it:
  - **Real.** It runs SAM 3D on Modal and the Meta API. It takes several minutes and uses credits. The real pipeline has never been run through this hosted path, so do one complete rehearsal first. Cut the waiting time in the edit and say "sped up".
  - **`--dry-run`.** It publishes the bundled mug instead of processing the video. If you use it, the voice-over must say so.
- [ ] Open a slicer (PrusaSlicer, Cura or Bambu Studio) with an empty plate, set to mm.
- [ ] Rehearse once. Check the Muse Spark key works: one AI Edit in a throwaway copy, then delete that copy.
- [ ] Keep a backup recording of one complete successful take.

## Files and windows to have open

| Window | What |
|---|---|
| Phone (screen mirrored or recorded separately) | live site, signed in, New project |
| Terminal 3 | `pipeline\.venv\Scripts\python.exe -m worldkit.worker --once` ready to run |
| Chrome tab 1 | <http://127.0.0.1:5070/>: `worldkit.serve` on `pipeline\runs\demo_take` (Object result, AI Edit, SAM overlay) |
| Chrome tab 2 | <http://127.0.0.1:5050/>: viewer with **Demo run**, Map view (desk scene) |
| Slicer | empty plate |
| Downloads folder | where "Download STL (mm)" saves |

## Script

| Time | Shot | Say |
|---|---|---|
| 0:00-0:10 | the mug on the desk, phone in hand | "Getting a real object into 3D at its real size still takes a scanner or hours of modelling." |
| 0:10-0:30 | phone: live site → New project → Object → pick the video → upload progress bar → status "uploaded" | "WorldKit starts with a phone video. I film the mug with a coin next to it and upload it from the web app." |
| 0:30-0:45 | terminal 3: start the worker; it claims the project; phone status turns "processing" then "done" (cut the wait) | "A worker on my laptop picks it up. Muse Spark names the object, SAM 3.1 cuts it out in every frame, and SAM 3D Objects rebuilds it on a GPU." With `--dry-run`, say instead: "This take uses a dry run that publishes an earlier result." |
| 0:45-1:10 | tab 1, Viewer tab: orbit the mug, point at the W/H/D cards and the scale source; then the Accuracy tab, table a | "The coin sets the scale. Against a ruler, this mug's model is within 1.9% on height, 3.7% on the rim and 1.9% on the base. That's one mug, measured by hand." |
| 1:10-1:40 | tab 1, AI Edit tab: type `make the handle 2 mm thicker` → result row, open "details" for the tool call and Spark's verdict | "Now I edit it by typing. Muse Wonder picks a geometry tool, here thicken_part on the handle. Muse Spark checks before-and-after renders before it's accepted." Read the wall change from the result row. |
| 1:40-1:50 | click **Undo**; the handle returns | "Every edit is a version; undo goes back without deleting anything." |
| 1:50-2:05 | Viewer tab: **Show what SAM found** | "This is how it knew where the handle is: SAM 3.1 found it in the video frames, and those votes are mapped onto the mesh." |
| 2:05-2:25 | tab 2, Map view: the desk scene, orbit, then a verify image (real / before / after) | "Scene mode does several objects on a desk and lays them out at one scale. This is layout, not measurement. VGGT cameras check the placement against the frames." |
| 2:25-2:45 | tab 1: **Download STL (mm)** → drag into the slicer → show the size readout | "The STL is in millimetres, so the slicer shows real size: about 73 mm tall, 71 for the mug plus a thin base plate." |
| 2:45-2:55 | `docs/accuracy.md` table c, or a title card | "What doesn't work yet: with a bank card instead of a coin, the scale was off by up to 17%, and all of this is tested on one mug." |
| 2:55-3:00 | title card: WorldKit, live URL, repo URL | "WorldKit: film it, build it." |

## If something fails while recording

| Symptom | Do |
|---|---|
| AI Edit refuses or Spark rejects | keep it in: "the verifier rejected it, nothing changed", then try "make the handle 2 mm thicker" once more or move on |
| Viewer blank on 5070 | check terminal 1; restart `worldkit.serve` on another port (not 5060/5061) |
| Worker fails | show the failed status sentence on the phone, then use `--dry-run` and say so |
| Slicer shows 0.73 or 730 | the slicer's unit setting is wrong; set mm and re-import |
