// Screenshots of the 4 key screens at 1440 px and 390 px (docs/ui/*.png) and a console-cleanliness check.
//   node viewer/tests/ui_screens.js [--object RUN_DIR] [--scene SCENE_DIR]   (needs pipeline\.venv; uses ports 5091-5093)
// Defaults use real local runs when they exist (pipeline/runs/mug_grounded, pipeline/runs/desk_coin), else the bundled demo assets.
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = path.join(ROOT, "pipeline", ".venv", "Scripts", "python.exe");
const OUT = path.join(ROOT, "docs", "ui");
const arg = (n) => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : null; };
const objRun = arg("--object") || (fs.existsSync(path.join(ROOT, "pipeline", "runs", "mug_grounded", "model.glb")) ? path.join(ROOT, "pipeline", "runs", "mug_grounded") : null);
const sceneRun = arg("--scene") || (fs.existsSync(path.join(ROOT, "pipeline", "runs", "desk_coin", "scene.json")) ? path.join(ROOT, "pipeline", "runs", "desk_coin") : null);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const WIDTHS = [{ w: 1440, h: 900, tag: "1440" }, { w: 390, h: 844, tag: "390" }];
const errors = [];

async function waitUp(port, url = "/api/health") { for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${port}${url}`)).ok) return; } catch (e) { /* starting */ } await sleep(500); } throw new Error("server " + port); }

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const procs = [];
  const start = (args, port, cwd = ROOT) => { const p = spawn(PY, args, { cwd, stdio: "ignore" }); procs.push(p); return waitUp(port); };
  await start([path.join("viewer", "server.py"), ...(objRun ? ["--open", objRun] : []), "--port", "5091"], 5091);
  await start(["-m", "worldkit.serve", ...(sceneRun ? [sceneRun] : []), "--port", "5093"].filter(Boolean), 5093, path.join(ROOT, "pipeline")).catch(() => null);
  await start([path.join("viewer", "tests", "serve_fake.py"), "5092"], 5092);
  const browser = await chromium.launch();
  try {
    for (const { w, h, tag } of WIDTHS) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      const page = await ctx.newPage();
      page.on("pageerror", (e) => errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error") errors.push(`${tag}: ${m.text()}`); });
      const ready = () => page.waitForFunction(() => window.__sceneReady === true && !window.__isLoading(), { timeout: 40000 });

      // 1. object view
      await page.goto(objRun ? "http://127.0.0.1:5091/" : "http://127.0.0.1:5091/", { waitUntil: "domcontentloaded" });
      if (!objRun) await page.click("#btn-demo-mug");
      await ready(); await sleep(900);
      await page.screenshot({ path: path.join(OUT, `1_object_${tag}.png`) });

      // 2. scene view (real desk scene through worldkit.serve when available, else the demo scene)
      if (sceneRun) await page.goto("http://127.0.0.1:5093/", { waitUntil: "domcontentloaded" }); else await page.click("#btn-demo-scene");
      await ready(); await sleep(1200);
      await page.screenshot({ path: path.join(OUT, `2_scene_${tag}.png`) });

      // 3. AI Edit panel (scripted Wonder/Spark so no tokens are spent)
      await page.goto("http://127.0.0.1:5092/", { waitUntil: "domcontentloaded" });
      await ready();
      await page.waitForFunction(() => window.__aiState && window.__aiState(), { timeout: 10000 });
      await page.fill("#ai-input", "make it exactly 12 cm tall"); await page.click("#ai-send");
      await page.waitForFunction(() => document.querySelector("#ai-log .entry.ok"), { timeout: 60000 });
      await page.waitForFunction(() => (window.__lastLoadLabel || "") === "edit v1", { timeout: 15000 });
      await page.fill("#ai-input", "this should be reject ed by the check"); await page.click("#ai-send");
      await page.waitForFunction(() => document.querySelector("#ai-log .entry.bad"), { timeout: 60000 });
      await page.locator("#ai-log .entry.ok").first().click();
      await sleep(600);
      await page.locator("#aiedit-panel").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(OUT, `3_ai_edit_${tag}.png`) });

      // 4. accuracy view
      await page.click("#btn-accuracy");
      await page.waitForSelector("table.acc", { timeout: 10000 });
      await sleep(300);
      await page.screenshot({ path: path.join(OUT, `4_accuracy_${tag}.png`) });
      await ctx.close();
    }
  } finally {
    await browser.close();
    procs.forEach((p) => p.kill());
  }
  console.log(`screenshots in ${path.relative(ROOT, OUT)}; console/page errors: ${errors.length}`);
  errors.slice(0, 8).forEach((e) => console.log("  " + e.slice(0, 200)));
  process.exit(errors.length ? 1 : 0);
})();
