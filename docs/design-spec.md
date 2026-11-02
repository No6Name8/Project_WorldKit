# WorldKit viewer: design spec

Figma was not available in this session (no Figma tools connected), so this file is the spec. The tokens are real code: `viewer/assets/tokens.css`; styles are `viewer/assets/app.css`; screenshots are in `docs/ui/` (`node viewer/tests/ui_screens.js`).

## Direction

A dark, neutral engineering workspace (reference feel: Bambu Studio, Figma, Linear). Flat panels, 1 px borders, square-ish corners (2 and 4 px radius only), no gradients, no blur, no glow, no emoji, no sparkle or "AI" iconography.

**Accent: blue (`--blue-500` #4C8DF6, solid fill `--blue-600` #2F6BD0).** One hue, used only for things you can act on or that are selected: the primary button, the selected row, the 3D selection outline, the active mode, focus rings, the logo mark. Blue was chosen over amber because amber is already the "warning" hue (uncalibrated scale, overlaps, diorama warnings); an amber accent would blur "interactive" and "be careful". Green, amber and red are status colours only.

## Tokens (three layers)

- Primitive: grey ramp 1000..100, blue, green, amber, red; type, space, radius, motion values.
- Semantic: `--bg-app/surface/raised/field/hover/selected`, `--text-primary/secondary/muted`, `--border-subtle/default/strong`, `--accent`, `--focus-ring`, `--status-ok/warn/err/idle` (+ `-bg`), viewport colours `--vp-*` (read by three.js at start).
- Component: button, field and row heights; sidebar and inspector widths.
- Light theme: `<html data-theme="light">`, 14 semantic overrides. Not the default, not yet tested visually.
- Type: one UI font (system stack: Segoe UI Variable, system-ui) and one monospace (Cascadia Mono, JetBrains Mono, Consolas) for every number: mm values, IoU, version ids, readouts, table cells. `font-variant-numeric: tabular-nums`. Four sizes only: 11 (labels, captions), 12 (dense body, tables), 13 (controls), 16 (titles).
- Space: 4 px scale (4, 8, 12, 16, 24, 32). Radius: 2 and 4 px. Elevation: only the bottom-sheet and overlays.
- Motion: 120 ms (hover, press), 160 ms (disclosure), 180 ms (sheet). One curve, `cubic-bezier(0.23, 1, 0.32, 1)` (ease-out). Buttons scale to 0.98 on press. Only transform, opacity and colour are animated. `prefers-reduced-motion` sets all durations to 0 and stops the loading bar.
- Contrast: body text on surface is above 12:1, muted text 5.2:1, white on the primary blue 5:1 (WCAG AA). Focus ring: 2 px `--focus-ring` on `:focus-visible` for every control.

## Shell

Top bar (44 px): logo mark (cube outline, SVG, 1.6 px stroke), wordmark "WorldKit", small tagline "Film it. Print it.", segmented control Object | Map, Muse Spark status, Accuracy button. Below: three columns, objects list (264 px), viewport, inspector (336 px). At 900 px and below the two side columns become one bottom sheet (max 46 vh, collapsible by the "Panels" handle).

## Screens

1. **Object view.** Viewport centre. Left: object list with a status marker. Right "Selection": name (16 px), `W x H x D mm` in mono, "Dimensions exclude the print base plate", scale source line, primary button "Download STL (mm)", note that geometry is generated, not measured. "Scale" shows the calibration banner (amber = placeholder, green = estimated from a reference).
2. **Scene (Map) view.** Left top: "Print whole scene (diorama)" primary button, diorama ratio, size and warning count, Reset camera, Verify images, Labels. List markers: green = printable STL, amber = no STL, grey = support surface, red = missing mesh; "ref" tag on the scale-reference object. The 256 mm bed is drawn as a dashed square on the desk at real size (256 mm divided by the diorama ratio) with a legend. Right: the same Selection panel plus "Edit" (opens AI Edit for that object).
3. **AI Edit.** Right panel: version chips (v0, v1, ... current outlined, others undo to that version), a compact row per edit (version tag, instruction, tool names, verdict, Spark verdict and time), click a row for the before/after render, status line, instruction field, Undo, "Current version STL (mm)". Empty state explains the edit server is needed. "Show what SAM found" (Parts section, built after the first pass): a toggle that draws the part's faces in the accent colour at 60% over the model, a legend in the viewport corner ("SAM part: handle, found in 11 of 12 views. Edges are approximate."), and a row per part with a block bar of views found. A part edit appears in the log as one plain sentence ("Thickened handle by 2 mm (SAM found 7 of 8 views)") plus the version and verdict; tool-call JSON is never shown for it. The overlay marks the original mesh, so it is dropped when an edited version is loaded. Screenshot: `docs/ui/5_parts_overlay_1440.png` (real mug).
4. **Accuracy.** Full-width view from the top bar: tables of real mm, model mm, error % per run with the scale source; the error cell is tinted green (within 3%), amber (3 to 8%), red (over 8%). A note states how ground truth was measured (ruler, mug only, evaluation only) and that the coin diameter is a looked-up preset. Source: `docs/accuracy.md` (copied to `viewer/assets/demo/accuracy.md`).

## States

Empty (list, selection, AI Edit, ai-log), loading (a 2 px progress bar plus a mono status line over the viewport, skeleton blocks in the accuracy view), error (red-edged log rows, red status dot, "FAILED:" prefix), disabled (muted, no hover), focus (ring).

## Not done

Figma file; light theme review; PWA manifest (none existed); the Object | Map switch loads the demo scene when no scene is open and otherwise isolates or shows all objects.

## Step 2 changes

Inspector order is Selection, Scale, AI Edit (open, the largest section, with the Parts block at its top), then one closed "Advanced" section (Transform, Move by text, Physics, Overlap check, Provenance; it remembers if you opened it). The move gizmo is hidden until Move, Rotate or Scale is chosen (Esc or "Hide gizmo" hides it). The edit log shows one plain sentence per edit ("Scaled to 120 mm tall. Spark: looks right."); the tool name and arguments sit under a "details" expander. Numbers that are results (accuracy %, views found, IoU) are monospace chips. Type is 14 px controls, 13 px dense body, 12 px labels; muted text is `#98a4af` (7:1 on the surface). Scenes open framed on the printable objects, the 256 mm bed is a solid 1 px accent outline, and "Reset view" is always in the viewport corner. The object list shows the run's own label (report.json `object_name`). "Demo run" loads one coin-scaled run (mug for Object, desk scene for Map) from tracked assets so screenshots and videos share data. On phones the measurements come first and the object list last, in the bottom sheet.

## Step 3 changes (palette, shell, states) - NOT matched to Figma

Figma (file SOea5KbOenp8kUMI7nEdtO) was requested as the source of truth, but this session had no Figma tools, so none of it was read or compared. What was built follows the written brief and the layout notes only. Re-check against the frames when Figma access exists.

- **Palette** (`tokens.css`): bg #0F1115, panel #161A21, raised #1C212A, border #242A33, text #E6EAF0, muted #9AA6B2 (7.0:1 on the panel). Accent #3B82F6, hover #5B9BFF, used for the primary action, focus rings, the active tab underline and the selection outline only. Primary buttons use dark text (#0A0F1A, 5.2:1); white on this blue is only 3.7:1. SAM overlay #2DD4BF at 55% (`--sam`, never the accent). Status: success #34D399, warning #F5B544, error #F87171, each with a 12% alpha tint token. Chips: uploaded = muted, processing = accent, done = success, failed = error, tinted background plus matching text.
- **Shell**: top bar = logo, breadcrumb (Projects / name), tabs Viewer | AI Edit | Accuracy, status, Object | Map. Bottom status bar: "<project> / <version>" left, "Units: mm . Saved" right ("Local session" when nothing is persisted). Phones drop the wordmark, breadcrumb text and status from the top bar to fit the tabs.
- **Object panel**: W, H, D as three mono cards, scale source line with an amber "estimate" chip, mesh check chip from the report (green watertight, amber thin-wall warnings), one primary "Download STL (mm)" (secondary in a scene, where the diorama button is the primary).
- **AI Edit**: version chips, Undo, SAM toggle in teal, plain-sentence log rows (red tint for a refused edit), "details" expander, input with Send (accent outline, so the panel has one filled primary), helper text. States: empty (v0, no edits), skeleton (edit state loading), error (refused edit row), disabled (no edit server: input and Send disabled).
- **Web app**: breadcrumb, account menu (email, Help, Log out), signed-out Help and Sign in, a Help page, Open disabled until a project is done, "Open" as an accent outline so New is the one primary on Projects.
- **Motion**: 120 to 180 ms ease-out on hover, press (0.98), tabs, section open, log rows, account menu; skeletons shimmer over 2.4 s; "Reset view" and "Reset camera" ease the camera over 420 ms (ease-out, no overshoot); `prefers-reduced-motion` turns all of it off.
- Screenshots: `docs/ui/` (`node viewer/tests/ui_screens.js`, and `SHOTS_DIR=../docs/ui node web/tests/e2e_mock.mjs` for the web screens).
