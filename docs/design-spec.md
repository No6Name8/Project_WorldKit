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
3. **AI Edit.** Right panel: version chips (v0, v1, ... current outlined, others undo to that version), a compact row per edit (version tag, instruction, tool names, verdict, Spark verdict and time), click a row for the before/after render, status line, instruction field, Undo, "Current version STL (mm)". Empty state explains the edit server is needed. A "show what SAM found" overlay was not built in the previous step, so it is not here.
4. **Accuracy.** Full-width view from the top bar: tables of real mm, model mm, error % per run with the scale source; the error cell is tinted green (within 3%), amber (3 to 8%), red (over 8%). A note states how ground truth was measured (ruler, mug only, evaluation only) and that the coin diameter is a looked-up preset. Source: `docs/accuracy.md` (copied to `viewer/assets/demo/accuracy.md`).

## States

Empty (list, selection, AI Edit, ai-log), loading (a 2 px progress bar plus a mono status line over the viewport, skeleton blocks in the accuracy view), error (red-edged log rows, red status dot, "FAILED:" prefix), disabled (muted, no hover), focus (ring).

## Not done

Figma file; light theme review; PWA manifest (none existed); the Object | Map switch loads the demo scene when no scene is open and otherwise isolates or shows all objects.
