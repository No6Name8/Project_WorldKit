// Real-browser test of the Map-mode scene UI against worldkit.serve opened on a scene folder (serve_fake_scene.py):
// loading overlay, hover highlight, click select + outline + labels, object panel (dimensions, Print this, Edit with AI),
// scene-level Print whole scene, verify images, reset camera, per-object AI Edit that only changes that object.
//   node viewer/tests/map_scene_browser_test.js        (starts its own server on port 5082; needs pipeline\.venv)
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = path.join(ROOT, "pipeline", ".venv", "Scripts", "python.exe");
const PORT = 5082;
const results = [];
const check = (name, cond, detail) => { results.push({ name, pass: !!cond }); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const srv = spawn(PY, [path.join(__dirname, "serve_fake_scene.py"), String(PORT)], { cwd: ROOT, stdio: process.env.DEBUG_SERVERS ? "inherit" : "ignore" });
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/api/health`)).ok) break; } catch (e) { /* starting */ } await sleep(500); }
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, acceptDownloads: true });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__sceneReady === true && window.__mapUi && window.__mapUi().bar === "flex", { timeout: 40000 });
    await sleep(600);
    check("the loading overlay is gone once the scene is ready", await page.evaluate(() => !window.__isLoading()));
    check("the map toolbar is shown with the diorama ratio and a warning mark", (await page.evaluate(() => window.__mapUi())).info.includes("1:4.0") && (await page.evaluate(() => window.__mapUi())).info.includes("⚠"));
    check("Print whole scene is enabled", !(await page.evaluate(() => window.__mapUi())).printSceneDisabled);
    const labels = await page.evaluate(() => window.__labelsVisible());
    check("labels are shown for both objects", labels.length === 2 && labels.includes("mug"), JSON.stringify(labels));
    await page.uncheck("#chk-labels");
    await sleep(200);
    check("the Labels toggle hides them", (await page.evaluate(() => window.__labelsVisible())).length === 0);
    await page.check("#chk-labels");

    const mp = await page.evaluate(() => window.__objectScreenPosition("mug"));
    await page.mouse.move(mp.x, mp.y);
    await sleep(200);
    check("hovering an object highlights it", (await page.evaluate(() => window.__hoveredId())) === "mug");
    await page.mouse.move(5, 5);
    await sleep(100);
    const tp = await page.evaluate(() => window.__objectScreenPosition("round_side_table"));
    let ui;
    for (const [dx, dy] of [[0, 40], [90, 30], [-90, 30], [0, 80], [140, 0], [-140, 0], [60, 60]]) {  // the mug sits on the table: find a point on the table itself
      await page.mouse.click(tp.x + dx, tp.y + dy);
      await sleep(250);
      ui = await page.evaluate(() => window.__mapUi());
      if (ui.name === "round side table") break;
    }
    check("clicking the table selects it: panel shows its name and is not printable", ui.name === "round side table" && ui.printObjDisabled && ui.objPanel === "block", JSON.stringify(ui));
    await page.mouse.click(mp.x, mp.y);
    await sleep(300);
    ui = await page.evaluate(() => window.__mapUi());
    check("clicking the mug selects it: name, real dimensions in cm, outline drawn", ui.name === "mug" && ui.dims.includes("cm") && (await page.evaluate(() => window.__outlineCount())) > 0, ui.dims);
    check("Print this is enabled for the mug", !ui.printObjDisabled);
    check("the panel states the scale source", ui.scale.includes("scale source: striped mug, 70 mm height"), ui.scale);
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#btn-print-obj")]);
    const stl = await dl.path();
    const size = require("fs").statSync(stl).size;
    check("Print this downloads the mug STL", size > 1000 && dl.suggestedFilename().endsWith(".stl"), dl.suggestedFilename() + " " + size + " B");
    const [dl2] = await Promise.all([page.waitForEvent("download"), page.click("#btn-print-scene")]);
    check("Print whole scene downloads the diorama STL", dl2.suggestedFilename() === "diorama.stl", dl2.suggestedFilename());

    await page.click("#btn-verify");
    await sleep(500);
    check("the verify toggle shows the comparison image", (await page.evaluate(() => window.__mapUi())).verifyVisible && (await page.evaluate(() => document.querySelector("#verify-imgs img").naturalWidth)) > 0);
    await page.click("#btn-verify-close");

    // Edit with AI on the selected object only
    check("Edit with AI is enabled for the mug", !(await page.evaluate(() => window.__mapUi())).editDisabled);
    const tableBefore = await page.evaluate(() => { const o = window.__sceneObjects ? window.__sceneObjects() : null; return o ? JSON.stringify(o.find((x) => x.id === "round_side_table")) : null; });
    await page.click("#btn-edit-obj");
    await page.waitForFunction(() => window.__aiState && window.__aiState() && document.getElementById("aiedit-panel").style.display === "block", { timeout: 10000 });
    await page.fill("#ai-input", "make it exactly 12 cm tall");
    await page.click("#ai-send");
    await page.waitForFunction(() => document.querySelector("#ai-log .entry.ok"), { timeout: 60000 });
    check("the edit applies to the mug's own session", (await page.locator("#ai-log .entry.ok").first().innerText()).includes("applied as v1"));
    await sleep(1500);
    const tableAfter = await page.evaluate(() => { const o = window.__sceneObjects ? window.__sceneObjects() : null; return o ? JSON.stringify(o.find((x) => x.id === "round_side_table")) : null; });
    check("the other object is untouched", tableBefore === tableAfter);
    const dims = await page.evaluate(() => window.__mapUi().dims);
    check("the panel notes the edit", dims.includes("edited"), dims);
    await page.click("#btn-reset-cam");
    check("Reset camera does not break the scene", await page.evaluate(() => window.__sceneReady === true));
    await page.screenshot({ path: path.join(ROOT, "docs", "map_viewer_ui.png") });
    check("no page or console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  } catch (e) {
    check("test ran to the end", false, String(e).slice(0, 300));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const failed = results.filter((r) => !r.pass).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
