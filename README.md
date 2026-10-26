# WorldKit — Film it. Print it.

Film anything with your phone — an object, a desk, a room — and WorldKit turns it into a print-ready 3D model you can edit by chatting with AI.

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track, Push the Frontier theme). All code in this repository was written during the hackathon building period (Oct 27 – Nov 4, 2026).

## Features

- **Object mode** — Turn a single object into a print-ready STL.
- **Map mode** — Turn a scene into a printable diorama, or tap any object in the scene to print it alone.
- **AI Edit** — Change the model by chatting: resize, add text, drill holes, thicken parts, edit a named part, or apply creative restyles. Every edit is verified and undoable.
- **Print-readiness checks** — Real-world scale recovered from an A4 sheet in frame, watertight geometry, minimum wall thickness, a flat base, and fit to the printer bed.
- **Phone + laptop sync** — One account keeps your captures and edits in sync across devices.

## How it works (pipeline)

1. Video → frames
2. Muse Spark lists the objects in the scene
3. SAM 3.1 tracks and masks each object across frames
4. The best view of each object is selected
5. SAM 3D Objects builds a textured 3D model
6. VGGT recovers camera poses and real-world scale for scenes
7. Muse Wonder organizes the scene and runs AI Edit using geometry tools
8. Muse Spark verifies renders against the real video
9. Output: print-ready STL / GLB

## Meta models used

| Model | Role |
|---|---|
| Muse Spark (Meta Model API) | Lists objects in the scene; verifies renders against the source video |
| Muse Wonder (Meta Model API) | Organizes the scene; runs AI Edit with geometry tools |
| Muse Image (Meta Model API) | Image generation/editing support for AI Edit |
| SAM 3.1 (Meta Model API) | Tracks and masks objects across video frames |
| SAM 3D Objects (open weights) | Builds textured 3D models from the selected best view |
| VGGT (open weights) | Recovers camera poses and real-world scale for scenes |

## Tech stack

- **App**: Next.js + react-three-fiber (installable PWA)
- **Backend**: Supabase (auth, storage, database)
- **Hosting**: Vercel
- **Pipeline**: Python, running on Modal GPUs
- **Geometry**: trimesh + manifold3d

## Repo structure

```
/app      Web app (Next.js PWA)
/pipeline Python AI pipeline
/docs     Architecture notes and benchmark results
```

## Status

In active development — hackathon build in progress.

## Author

Created by Abdullah Ali Alanazi ([@No6Name8](https://github.com/No6Name8)).
