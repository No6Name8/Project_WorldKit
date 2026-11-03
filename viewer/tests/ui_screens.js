// Screenshots of the key screens (docs/ui/*.png) and a console-cleanliness check, all from the SAME tracked demo data:
// the coin-scaled mug (assets/demo/run_mug, real SAM handle part) for Object and the coin-scaled desk scene (assets/demo/run_desk) for Map.
//   node viewer/tests/ui_screens.js        (needs pipeline\.venv; uses ports 5091-5092)
// 1_object, 2_scene, 3_ai_edit, 4_accuracy at 1440 and 390 px; 1_object and 3_ai_edit also at 1920x1080 and 1280x720; 5_parts_overlay at 1440 and 390.
// Dark theme (default) names are unchanged; the light theme repeats the 1440 and 390 screens as *_light.png.
// The AI Edit screen uses serve_fake_real.py: real geometry edits on the real mug, with a SCRIPTED Wonder and Spark (no tokens spent).
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = path.join(ROOT, "pipeline", ".venv", "Scripts", "python.exe");
const OUT = path.join(ROOT, "docs", "ui");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SIZES = [
  { w: 1440, h: 900, tag: "1440", all: true }, { w: 390, h: 844, tag: "390", all: true },
  { w: 1920, h: 1080, tag: "1080p", all: false }, { w: 1280, h: 720, tag: "720p", all: false },
  { w: 1440, h: 900, tag: "1440_light", all: true, light: true }, { w: 390, h: 844, tag: "390_light", all: true, light: true },
];
const errors = [];

async function waitUp(port) { for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/api/health`)).ok) return; } catch (e) { /* starting */ } await sleep(500); } throw new Error("server " + port); }

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const procs = [];
  const start = (args, port) => { const p = spawn(PY, args, { cwd: ROOT, stdio: "ignore" }); procs.push(p); return waitUp(port); };
  await start([path.join("viewer", "server.py"), "--port", "5091"], 5091);
  await start([path.join("viewer", "tests", "serve_fake_real.py"), "5092"], 5092);
  const browser = await chromium.launch();
  try {
    for (const { w, h, tag, all, light } of SIZES) {
      console.log("size", tag);
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      await ctx.addInitScript((t) => { try { localStorage.setItem("wk-theme", t); } catch (e) { /* ignore */ } }, light ? "light" : "dark");
      const page = await ctx.newPage();
      page.on("pageerror", (e) => errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error") errors.push(`${tag}: ${m.text()}`); });
      const sheetTop = () => page.evaluate(() => { const d = document.getElementById("dock"); if (d) d.scrollTop = 0; });
      const ready = () => page.waitForFunction(() => window.__sceneReady === true && !window.__isLoading(), { timeout: 40000 });

      // 1. object view (Demo run in Object mode)
      await page.goto("http://127.0.0.1:5091/", { waitUntil: "domcontentloaded" });
      await ready();
      await page.evaluate(() => document.getElementById("btn-demo-run").click());
      await page.waitForFunction(() => window.__partsUi && window.__partsUi().enabled, { timeout: 20000 });
      await ready(); await sleep(900);
      await sheetTop();
      await page.screenshot({ path: path.join(OUT, `1_object_${tag}.png`) });

      if (all) {
        // 5. "show what SAM found" on the real mug, orbited so the handle faces the camera
        await page.click("#tab-ai"); await sleep(300);
        await page.click("#btn-parts");
        const box = await page.locator("canvas").boundingBox();
        for (const dx of [250, 400]) {
          await page.mouse.move(box.x + 36, box.y + 256); await page.mouse.down();
          await page.mouse.move(box.x + 36 + dx * Math.min(1, box.width / 840), box.y + 256, { steps: 12 }); await page.mouse.up();
        }
        await sleep(500);
        await page.screenshot({ path: path.join(OUT, `5_parts_overlay_${tag}.png`) });
        await page.click("#btn-parts");
        await page.click("#btn-vp-reset");

        // 2. scene view (Map)
        await page.click("#tab-viewer");
        await page.click("#mode-map");
        await page.waitForFunction(() => window.__getSceneObjects && window.__getSceneObjects().length > 1, { timeout: 40000 });
        await ready(); await sleep(1200);
        await sheetTop();
        await page.screenshot({ path: path.join(OUT, `2_scene_${tag}.png`) });
      }

      // 3. AI Edit with real geometry edits on the real mug (scripted Wonder and Spark)
      await page.goto("http://127.0.0.1:5092/", { waitUntil: "domcontentloaded" });
      await page.evaluate(async () => { for (let i = 0; i < 12; i++) { const st = await (await fetch("/edit/state")).json(); if (!st.current) break; await fetch("/undo", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } });  // back to v0
      await page.reload({ waitUntil: "domcontentloaded" });
      await ready();
      await page.waitForFunction(() => window.__aiState && window.__aiState() && window.__partsUi && window.__partsUi().enabled, { timeout: 15000 });
      if (all && !light) {  // AI Edit states: empty (v0, no edits yet), skeleton (edit state still loading), error (a refused edit), disabled (no edit server)
        const toAi = () => page.click("#tab-ai");
        await toAi(); await sleep(300);
        await page.screenshot({ path: path.join(OUT, `3a_ai_empty_${tag}.png`) });
        await page.fill("#ai-input", "make the handle thinner"); await page.click("#ai-send");
        await page.waitForFunction(() => document.querySelector("#ai-log .entry.bad"), null, { timeout: 60000 });
        await sleep(400); await toAi();
        await page.screenshot({ path: path.join(OUT, `3c_ai_error_${tag}.png`) });
        const sk = await ctx.newPage();
        await sk.route("**/edit/state", async (r) => { await sleep(6000); r.continue().catch(() => {}); });
        await sk.goto("http://127.0.0.1:5092/", { waitUntil: "domcontentloaded" });
        await sk.waitForFunction(() => window.__sceneReady === true, { timeout: 40000 });
        await sk.click("#tab-ai"); await sleep(500);
        await sk.screenshot({ path: path.join(OUT, `3b_ai_skeleton_${tag}.png`) });
        await sk.close();
        const dis = await ctx.newPage();
        await dis.goto("http://127.0.0.1:5091/", { waitUntil: "domcontentloaded" });
        await dis.waitForFunction(() => window.__sceneReady === true && !window.__isLoading(), { timeout: 40000 });
        await dis.evaluate(() => document.getElementById("btn-demo-run").click()); await sleep(1500);
        await dis.click("#tab-ai"); await sleep(400);
        await dis.screenshot({ path: path.join(OUT, `3d_ai_disabled_${tag}.png`) });
        await dis.close();
        await page.reload({ waitUntil: "domcontentloaded" });
        await ready();
        await page.waitForFunction(() => window.__aiState && window.__aiState() && window.__partsUi && window.__partsUi().enabled, { timeout: 15000 });
      }
      await page.click("#tab-ai");
      for (const [text, sel] of [["make the handle 2 mm thicker", ".entry.ok"], ["make the handle thinner", ".entry.bad"]]) {
        await page.fill("#ai-input", text); await page.click("#ai-send");
        await page.waitForFunction((s) => document.querySelector("#ai-log " + s), sel, { timeout: 60000 });
        await sleep(600);
      }
      await page.waitForFunction(() => /^edit v[1-9]/.test(window.__lastLoadLabel || ""), { timeout: 15000 });
      await page.locator("#ai-log .entry.ok").first().click();
      await page.locator("#ai-log .entry.ok details.dt summary").first().click();
      await sleep(500);
      await page.locator("#ai-input").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(OUT, `3_ai_edit_${tag}.png`) });

      if (all) {
        // 4. accuracy view
        await page.click("#btn-accuracy");
        await page.waitForSelector("table.acc", { timeout: 10000 });
        await sleep(700);  // the % values count up over 400 ms
        await page.screenshot({ path: path.join(OUT, `4_accuracy_${tag}.png`) });
      }
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
