# Architecture

Four views of the system. File names are relative to the repo root.

## 1. System

```mermaid
flowchart LR
  subgraph Phone["Phone or browser"]
    Web["web app (web/, Vite PWA)"]
    Viewer["viewer (viewer/index.html, three.js)"]
  end
  subgraph Cloud["Hosted services"]
    Vercel["Vercel: static web app + /viewer/"]
    SB["Supabase: Auth, projects table (RLS), private Storage, Realtime"]
  end
  subgraph Laptop["Laptop"]
    Worker["worker (worldkit.worker)"]
    Pipeline["pipeline (worldkit.object / worldkit.scene)"]
    Serve["edit server (worldkit.serve, FastAPI)"]
  end
  subgraph Meta["Meta Model API"]
    Spark["Muse Spark"]
    Wonder["Muse Wonder"]
    SAM31["SAM 3.1"]
  end
  subgraph Modal["Modal A100"]
    SAM3D["SAM 3D Objects"]
    VGGT["VGGT-1B"]
  end
  Vercel --> Web
  Web <--> SB
  Web --> Viewer
  Worker <--> SB
  Worker --> Pipeline
  Pipeline --> Spark
  Pipeline --> SAM31
  Pipeline --> SAM3D
  Pipeline --> VGGT
  Serve --> Viewer
  Serve --> Wonder
  Serve --> Spark
```

The web app never runs the pipeline. Heavy work runs on the laptop, which calls the Meta Model API and Modal. AI Edit needs the local edit server; the hosted viewer has none.

## 2. Pipeline (Object mode, `pipeline/worldkit/object.py`)

```mermaid
flowchart TD
  V["video"] --> F["frames.py: sharp frames at 2 fps"]
  F --> D["detect.py: Muse Spark names the object, picks the best frame"]
  D --> S["segment.py: SAM 3.1 masks the object (and the coin)"]
  S --> R["modal_app/sam3d.py: SAM 3D Objects builds a textured mesh"]
  R --> A["appearance / fidelity (optional --fidelity auto): stripe model kept only if it wins on held-out frames"]
  A --> K["scale_cal.py: coin ellipse fixes elevation + scale; grounded reshape fits height aspect and taper to SAM silhouettes"]
  K --> P["printprep.py: upright, base plate, watertight STL in mm"]
  P --> O["model.glb, model_print.stl, report.json"]
  O --> W["viewer: dimensions in mm, scale source, Download STL"]
  O --> E["AI Edit (edit.py)"]
```

Map mode (`mapmode.py`, `worldkit.scene` without `--objects`) runs these steps:

1. Muse Spark discovers the objects.
2. SAM 3.1 tracks each object across frames.
3. SAM 3D builds each object in parallel.
4. Mesh QC skips broken objects.
5. The scale reference (coin or card, `refscale.py`) places the objects at one scale.
6. A verify loop compares silhouettes from the original viewpoints, with weak-perspective cameras and optionally VGGT cameras. Muse Spark corrections are kept only if IoU improves.

## 3. AI Edit loop (`pipeline/worldkit/edit.py`, `edit_tools.py`)

```mermaid
sequenceDiagram
  participant U as User (viewer or CLI)
  participant E as edit.run_edit
  participant W as Muse Wonder
  participant T as edit_tools (trimesh, manifold3d)
  participant C as deterministic checks
  participant S as Muse Spark
  U->>E: instruction ("make the handle 2 mm thicker")
  E->>W: instruction + measured facts (size, wall, parts found by SAM) + tool schemas
  W->>T: tool calls (e.g. thicken_part handle by_mm=2)
  T-->>E: edited mesh
  E->>C: watertight, requested size, volume moved the right way
  E->>S: before/after renders (relief shading, views chosen per tool)
  S-->>E: verdict
  alt checks and verdict pass
    E-->>U: new version edits/vN.stl, row in edits/versions.json
  else fail (one retry)
    E->>W: failure reason, try again
    E-->>U: rejected, model unchanged
  end
  U->>E: undo (to any version, nothing deleted)
```

## 4. Phone-to-laptop sync (`web/`, `supabase/`, `pipeline/worldkit/worker.py`)

```mermaid
sequenceDiagram
  participant P as Phone (web app)
  participant SB as Supabase
  participant L as Laptop worker
  P->>SB: sign in (Auth)
  P->>SB: upload video to videos/<user id>/... (progress bar)
  P->>SB: insert projects row, status "uploaded"
  L->>SB: poll every 5 s, claim the oldest "uploaded" row, set "processing"
  SB-->>P: Realtime status update (8 s polling fallback)
  L->>SB: download video
  L->>L: run worldkit.object or worldkit.scene
  L->>SB: upload GLB, STL, report to results/<user id>/...
  L->>SB: set "done" (or "failed" with a sentence)
  SB-->>P: Realtime "done"
  P->>SB: signed URLs for the result files
  P->>P: open the viewer in an iframe, files passed by postMessage
```

Row Level Security limits every row and file to its owner. The worker signs in as a user with the publishable key; there is no service key anywhere.
