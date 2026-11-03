# Docs index

| File | What it is |
|---|---|
| [accuracy.md](accuracy.md) | The model compared with a ruler: headline Object-mode table, scene runs with a coin and a card, and how the ground truth was measured. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Mermaid diagrams: system, pipeline, AI Edit loop with Muse Spark verification, phone-to-laptop sync. |
| [SUBMISSION.md](SUBMISSION.md) | Hackathon submission text, ready to paste. |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | Timed 3-minute demo script, shot list and pre-recording checklist. |
| [DEPLOY.md](DEPLOY.md) | How the web app deploys to Vercel (push to main), the prebuilt fallback, the live-site check, the Supabase settings. |
| [TECHNICAL_NOTES.md](TECHNICAL_NOTES.md) | Measurements and diagnosis: geometry, texture fidelity, scale, scenes, bugs found by looking. |
| [what_we_tried.md](what_we_tried.md) | Experiments that did not make it in (room layer, VGGT on a room tour, card scale) and the freeze decision. |
| [design-spec.md](design-spec.md) | UI design spec and the changes in each UI step (tokens, layout, states). |

| Folder | Contents |
|---|---|
| [assets/](assets/) | Logo (`logo.svg`, `logo-mark.svg`), `banner.png`, `social-preview.png`; made by `scripts/make_brand.mjs`. |
| [images/](images/) | Comparison images used by the notes: SAM 3D vs TRELLIS, texture fidelity, desk-scene verify renders. |
| [ui/](ui/) | Viewer and web app screenshots at 1440 and 390 px (`node viewer/tests/ui_screens.js`), the card-run images and the card preflight sheet. |
| [web/](web/) | Web app screenshots from the end-to-end tests; `live_*.png` come from `scripts/check_live.mjs` on the deployed site. |
