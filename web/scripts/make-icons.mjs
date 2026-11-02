// Renders the logo mark (viewer/assets/logo.svg) to the PWA icons with the design tokens' colours (background --gray-950, mark --blue-500).
// Run once: node scripts/make-icons.mjs   (needs playwright from ../viewer/tests/node_modules)
import { createRequire } from "node:module";
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "..", "..", "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const tokens = readFileSync(join(here, "..", "..", "viewer", "assets", "tokens.css"), "utf8");
const token = (n) => new RegExp(n + ": *(#[0-9a-fA-F]{6})").exec(tokens)[1];
const bg = token("--gray-950"), fg = token("--blue-500");
const svg = readFileSync(join(here, "..", "..", "viewer", "assets", "logo.svg"), "utf8").replace(/<title>.*<\/title>/, "").replace(/width="24" height="24"/, 'width="100%" height="100%"');

const out = join(here, "..", "public", "icons");
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
for (const [file, size, pad] of [["icon-192.png", 192, 0.2], ["icon-512.png", 512, 0.2], ["icon-maskable-512.png", 512, 0.3]]) {
  const page = await browser.newPage({ viewport: { width: size, height: size } });
  await page.setContent(`<body style="margin:0;background:${bg};display:flex;align-items:center;justify-content:center;width:${size}px;height:${size}px">
    <div style="width:${size * (1 - 2 * pad)}px;height:${size * (1 - 2 * pad)}px;color:${fg}">${svg}</div></body>`);
  await page.screenshot({ path: join(out, file) });
  await page.close();
}
await browser.close();
console.log("icons written to public/icons");
