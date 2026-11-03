// Writes the WorldKit logo files from one mark definition, using the app's tokens and vendored fonts.
//   node scripts/make_brand.mjs          (needs playwright from viewer/tests: cd viewer/tests; npm install)
// Outputs:
//   docs/assets/logo-mark.svg, docs/assets/logo.svg (mark + wordmark + tagline, fonts embedded, light/dark via prefers-color-scheme)
//   docs/assets/banner.png (1280x320), docs/assets/social-preview.png (1280x640)
//   viewer/assets/logo.svg and web/public/icons/logo.svg (favicons), web/public/icons/icon-*.png (PWA)
// The mark: an isometric outline cube, 1.5 px line in currentColor, the front vertical edge in the accent blue.
import { createRequire } from "node:module";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const tokens = readFileSync(join(root, "viewer", "assets", "tokens.css"), "utf8");
const token = (n) => new RegExp(n + ": *(#[0-9a-fA-F]{6})").exec(tokens)[1];
const ACCENT = token("--fg-accent"), TEXT = token("--fg-text"), MUTED = token("--fg-muted"), BG = token("--fg-bg");
const INK = "#16181c"; // the mark on light backgrounds
const BANNER_BG = "#0f1115";
const font = (f) => readFileSync(join(root, "viewer", "assets", "fonts", f)).toString("base64");

// geometry on a 24 x 24 grid
const OUTLINE = "M12 2.5 20.5 7v10L12 21.5 3.5 17V7z";
const TOP = "M3.5 7 12 11.5 20.5 7";
const EDGE = "M12 11.5v10";
const markPaths = (accent = ACCENT) =>
  `<g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round">` +
  `<path d="${OUTLINE}"/><path d="${TOP}"/><path d="${EDGE}" stroke="${accent}"/></g>`;
const schemeStyle = `<style>:root{color:${INK}}@media (prefers-color-scheme:dark){:root{color:${TEXT}}}</style>`;
const markSvg = (extra = "") =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><title>WorldKit</title>${extra}${markPaths()}</svg>\n`;

// favicons: the same cube snapped to a 16 px grid (odd width, half-pixel coordinates, 1 px line) so it stays crisp at 16 px
const favSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16"><title>WorldKit</title>${schemeStyle}` +
  `<g fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="miter">` +
  `<path d="M7.5 1.5 13.5 4.5v7l-6 3-6-3v-7z"/><path d="M1.5 4.5 7.5 7.5 13.5 4.5"/><path d="M7.5 7.5v7" stroke="${ACCENT}"/></g></svg>
`;

const out = (p, s) => { mkdirSync(dirname(join(root, p)), { recursive: true }); writeFileSync(join(root, p), s); console.log("wrote", p); };

// SVGs
out("docs/assets/logo-mark.svg", markSvg(schemeStyle));
out("viewer/assets/logo.svg", favSvg);
out("web/public/icons/logo.svg", favSvg);
const logoSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 112" width="400" height="112">
<title>WorldKit: Film it. Print it.</title>
<style>
@font-face{font-family:"WK Inter";font-weight:600;src:url(data:font/woff2;base64,${font("inter-latin-600-normal.woff2")}) format("woff2")}
@font-face{font-family:"WK Mono";font-weight:400;src:url(data:font/woff2;base64,${font("ibm-plex-mono-latin-400-normal.woff2")}) format("woff2")}
:root{color:${INK}}@media (prefers-color-scheme:dark){:root{color:${TEXT}}}
.w{font:600 50px "WK Inter",Inter,"Segoe UI",system-ui,sans-serif;letter-spacing:-1px;fill:currentColor}
.t{font:400 17px "WK Mono","IBM Plex Mono",Consolas,monospace;fill:currentColor;opacity:.7}
</style>
<g transform="translate(8 16) scale(3.333)">${markPaths()}</g>
<text class="w" x="104" y="60">WorldKit</text>
<text class="t" x="106" y="90">Film it. Print it.</text>
</svg>
`;
out("docs/assets/logo.svg", logoSvg);

// PNGs, rendered with the vendored fonts
const fontFace = (fam, w, f) => `@font-face{font-family:"${fam}";font-weight:${w};src:url(data:font/woff2;base64,${font(f)}) format("woff2")}`;
const css = `${fontFace("Inter", 600, "inter-latin-600-normal.woff2")}${fontFace("Inter", 400, "inter-latin-400-normal.woff2")}
${fontFace("Plex", 400, "ibm-plex-mono-latin-400-normal.woff2")}
*{margin:0;box-sizing:border-box}body{background:${BANNER_BG};color:${TEXT};font-family:Inter,sans-serif;-webkit-font-smoothing:antialiased}
.mark svg{display:block;width:100%;height:100%}.word{font-weight:600;letter-spacing:-.02em}.mono{font-family:Plex,monospace;color:${MUTED}}`;
const mark = (px) => `<div class="mark" style="width:${px}px;height:${px}px;color:${TEXT}"><svg viewBox="0 0 24 24">${markPaths()}</svg></div>`;
const pages = {
  "docs/assets/banner.png": [1280, 320, `
    <div style="display:flex;align-items:center;justify-content:space-between;height:320px;padding:0 96px">
      <div style="display:flex;align-items:center;gap:28px">${mark(112)}<div class="word" style="font-size:76px">WorldKit</div></div>
      <div style="text-align:right">
        <div class="word" style="font-size:44px">Film it. Print it.</div>
        <div class="mono" style="font-size:18px;margin-top:14px">phone video &rarr; real-size 3D model &rarr; STL</div>
      </div>
    </div>`],
  "docs/assets/social-preview.png": [1280, 640, `
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:640px;gap:30px">
      <div style="display:flex;align-items:center;gap:32px">${mark(136)}<div class="word" style="font-size:96px">WorldKit</div></div>
      <div class="word" style="font-size:44px">Film it. Print it.</div>
      <div class="mono" style="font-size:20px">phone video &rarr; real-size 3D model &rarr; STL &nbsp;/&nbsp; Meta Global AI Developer Hackathon 2026</div>
    </div>`],
};
const browser = await chromium.launch();
for (const [file, [w, h, body]] of Object.entries(pages)) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.setContent(`<style>${css}</style>${body}`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: join(root, file) });
  await page.close();
  console.log("wrote", file);
}
// PWA icons: light mark with the accent edge on the app background
for (const [file, size, pad] of [["icon-192.png", 192, 0.2], ["icon-512.png", 512, 0.2], ["icon-maskable-512.png", 512, 0.3]]) {
  const page = await browser.newPage({ viewport: { width: size, height: size } });
  await page.setContent(`<style>${css}body{background:${BG}}</style>
    <div style="display:flex;align-items:center;justify-content:center;width:${size}px;height:${size}px">${mark(size * (1 - 2 * pad))}</div>`);
  await page.screenshot({ path: join(root, "web", "public", "icons", file) });
  await page.close();
  console.log("wrote web/public/icons/" + file);
}
await browser.close();
