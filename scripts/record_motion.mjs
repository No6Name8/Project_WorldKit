// Records two short videos of the motion and measures layout shift (CLS) and long frames while they play.
//   node scripts/record_motion.mjs [WEB_URL]     WEB_URL defaults to http://127.0.0.1:4173 (npm --prefix web run build; npx vite preview)
// Writes docs/ui/motion_home.webm (home scroll) and docs/ui/motion_edit.webm (tab change + an AI Edit row appearing; the edit uses
// viewer/tests/serve_fake_real.py: real geometry, scripted Wonder and Spark, no tokens). Needs playwright from viewer/tests.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { renameSync, mkdirSync, rmSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const WEB = (process.argv[2] || "http://127.0.0.1:4173").replace(/\/$/, "");
const PY = join(root, "pipeline", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const OUT = join(root, "docs", "ui");
const TMP = join(OUT, "_video_tmp");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const VIEW = { width: 1280, height: 720 };
const REDUCED = Boolean(process.env.REDUCED);  // REDUCED=1: same flows with prefers-reduced-motion, as a baseline (videos not kept)

// layout shifts (not caused by input) and frames longer than 34 ms (two missed 60 Hz frames), collected inside the page
const PROBE = () => {
  window.__cls = 0; window.__longFrames = 0; window.__frames = 0;
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true });
  let last = performance.now();
  const tick = (now) => { window.__frames++; if (now - last > 34) window.__longFrames++; last = now; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
};

async function record(name, fn) {
  rmSync(TMP, { recursive: true, force: true }); mkdirSync(TMP, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: VIEW, recordVideo: { dir: TMP, size: VIEW }, reducedMotion: REDUCED ? "reduce" : "no-preference" });
  await ctx.addInitScript(PROBE);
  const page = await ctx.newPage();
  const stats = await fn(page);
  await ctx.close(); await browser.close();
  const f = readdirSync(TMP).find((x) => x.endsWith(".webm"));
  if (!REDUCED) renameSync(join(TMP, f), join(OUT, name));
  rmSync(TMP, { recursive: true, force: true });
  console.log(`${REDUCED ? "[reduced motion] " : ""}${name}: CLS ${stats.cls.toFixed(4)}, long frames ${stats.longFrames} of ${stats.frames}`);
  return stats;
}
const stats = (page) => page.evaluate(() => ({ cls: window.__cls, longFrames: window.__longFrames, frames: window.__frames }));

const results = {};
results.home = await record("motion_home.webm", async (page) => {
  await page.goto(WEB + "/", { waitUntil: "networkidle" });
  await sleep(1800);                                                                         // page entry, reveal, shimmer, preview turning
  for (const y of [260, 560, 900]) { await page.evaluate((v) => window.scrollTo({ top: v, behavior: "smooth" }), y); await sleep(1100); }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" })); await sleep(1300);
  await page.goto(WEB + "/#/help"); await sleep(1400);                                       // page change and the Help reveal
  return stats(page);
});

const srv = spawn(PY, [join(root, "viewer", "tests", "serve_fake_real.py"), "5093"], { cwd: root, stdio: "ignore" });
try {
  for (let i = 0; i < 80; i++) { try { if ((await fetch("http://127.0.0.1:5093/api/health")).ok) break; } catch (e) { /* starting */ } await sleep(500); }
  results.edit = await record("motion_edit.webm", async (page) => {
    await page.goto("http://127.0.0.1:5093/", { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__sceneReady === true && !window.__isLoading() && window.__aiState && window.__aiState(), { timeout: 40000 });
    await sleep(1200);
    await page.click("#tab-ai"); await sleep(900);                                            // tab underline slides, the cards settle
    await page.click("#btn-parts"); await sleep(900); await page.click("#btn-parts"); await sleep(500);  // SAM overlay fades in and out
    await page.fill("#ai-input", "make the handle 2 mm thicker"); await page.click("#ai-send");
    await page.waitForFunction(() => document.querySelector("#ai-log .entry.ok"), null, { timeout: 60000 });
    await sleep(1400);                                                                        // the new row slides in
    await page.click("#tab-viewer"); await sleep(1000);
    return stats(page);
  });
} finally { srv.kill(); }
console.log(JSON.stringify(results));
process.exit(Object.values(results).some((r) => r.cls > 0.02) ? 1 : 0);
