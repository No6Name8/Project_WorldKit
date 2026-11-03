# worldkit (Python package)

Run modules from `pipeline/` with `.venv\Scripts\python.exe -m worldkit.<module>`. Meta model call sites are listed in `../../AGENTS.md`.

| Group | Modules |
|---|---|
| Command lines | `object.py` (Object mode), `scene.py` (two-object scene, or Map mode via `mapmode.py`), `desk.py` (desk scene v2: VGGT cameras, stadium table, triangulated placement), `parts.py` (SAM 3.1 finds a part on the mesh), `edit.py` (AI Edit), `serve.py` (FastAPI viewer + AI Edit), `worker.py` (laptop worker for the web app) |
| Meta Model API | `meta_api.py` (client, Muse Spark), `detect.py`, `understand.py`, `editcmd.py` (Muse Spark), `segment.py` (SAM 3.1), `edit.py` (Muse Wonder + Spark verify) |
| Reconstruction | `reconstruct.py` (TRELLIS fallback, mesh import; SAM 3D is in `../modal_app/sam3d.py`), `mesh_qc.py` |
| Scale | `scale_cal.py` (coin, grounded reshape), `refscale.py` (coin / card / object references), `coindetect.py`, `carddetect.py`, `vggt_cams.py` (VGGT cameras, perspective correction) |
| Appearance | `appearance.py`, `video_appearance.py`, `texture_project.py`, `cyl_texture.py`, `pattern_model.py`, `fidelity.py`, `qc.py` (reconstruction quality) |
| Output | `printprep.py` (upright, base plate, STL in mm), `render.py` (preview PNGs), `edit_tools.py` (geometry tools for AI Edit) |
| Support | `frames.py` (sharp frames), `budget.py` (caps on real API calls), `webserver.py` (Flask app behind `viewer/server.py`) |
