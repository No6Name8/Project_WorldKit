// Demo-readiness check: the viewer must work OFFLINE (vendored three.js + Rapier), open a pipeline run directory passed to
// `server.py --open`, and load the bundled demo assets without a GPU or API key. All external network requests are blocked.
//
//   node viewer/tests/demo_readiness_test.js          (starts its own servers on ports 5071/5072; needs pipeline\.venv)
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = path.join(ROOT, "pipeline", ".venv", "Scripts", "python.exe");
const RUN = path.resolve(process.env.OPEN_RUN || path.join(ROOT, "viewer", "assets", "demo", "mug"));
const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond });
  console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : ""));
}
function start(port, args) {
  const p = spawn(PY, [path.join(ROOT, "viewer", "server.py"), "--port", String(port), ...args], { cwd: ROOT, stdio: process.env.DEBUG_SERVERS ? "inherit" : "ignore" });
  return p;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitUp(port) {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/health`); if (r.ok) return; } catch (e) { if (i % 10 === 9) console.log("waiting", port, String(e.cause || e)); }
    await sleep(500);
  }
  throw new Error("server did not start on " + port);
}

(async () => {
  const s1 = start(5071, []);
  const s2 = start(5072, ["--open", RUN]);
  let browser;
  try {
    await waitUp(5071); await waitUp(5072);
    browser = await chromium.launch();
    const external = [];
    async function newPage() {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      await page.addInitScript(() => { try { localStorage.setItem("wk-advanced", "1"); } catch (e) { /* ignore */ } });
      await page.route("**/*", (route) => {
        const u = new URL(route.request().url());
        if (u.hostname === "127.0.0.1" || u.hostname === "localhost" || u.protocol === "data:" || u.protocol === "blob:") return route.continue();
        external.push(u.href);
        return route.abort();
      });
      return page;
    }
    const errors = [];

    // 1. `--open` loads the run directory by itself
    let page = await newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto("http://127.0.0.1:5072/", { waitUntil: "networkidle" });
    await page.waitForFunction(() => window.__lastLoadLabel === "model.glb", { timeout: 30000 });
    const mat = await page.evaluate(() => window.__getMaterialInfo());
    check("server.py --open <run> loads that run's model.glb on start (no clicks)", mat.meshes === 1 && mat.textured === 1);
    const prov = await page.locator("#provenance-readout").innerText();
    check("...and its report.json provenance (SAM 3D, fidelity stage)", prov.includes("SAM 3D Objects") && prov.includes("fidelity stage:"), prov.split("\n").slice(0, 4).join(" | "));
    await page.close();

    // 2. default server: bundled demos work offline
    page = await newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto("http://127.0.0.1:5071/", { waitUntil: "networkidle" });
    await page.waitForFunction(() => window.__sceneReady === true, { timeout: 30000 });
    check("a fresh viewer with no run opened shows the bundled sample", (await page.evaluate(() => window.__lastLoadLabel)) === "sample_mug.glb");
    await page.click("#btn-demo-mug");
    await page.waitForFunction(() => window.__lastLoadLabel === "model.glb" && document.getElementById("provenance-readout").innerText.includes("SAM 3D"), { timeout: 30000 });
    check("'Demo: mug' loads the bundled SAM 3D mug with provenance", true);
    await page.click("#btn-demo-scene");
    await page.waitForFunction(() => (window.__lastLoadLabel || "").startsWith("scene:") && window.__getSceneObjects().length === 2, { timeout: 30000 });
    const objs = await page.evaluate(() => window.__getSceneObjects());
    check("'Demo: mug + table' loads two separate objects", objs.map((o) => o.id).sort().join() === "mug,round_side_table" && objs.every((o) => !o.missing), JSON.stringify(objs.map((o) => o.id)));

    // 3. the whole interaction loop on the demo scene, offline
    await page.evaluate(() => { window.__selectObject("mug"); window.__calibrateManual(9); }); // test-only height, labelled as the user's number
    const pre = await page.evaluate(() => window.__getSceneObjects());
    await page.evaluate(() => window.__applyEdit({ translate_cm: [2, 0, 1], rotate_deg: [0, 30, 0], scale_factor: 1 }));
    const edited = await page.evaluate(() => window.__getSceneObjects());
    check("selecting and moving the mug changes only the mug", edited.find((o) => o.id === "mug").position[0] !== pre.find((o) => o.id === "mug").position[0] && JSON.stringify(edited.find((o) => o.id === "round_side_table").position) === JSON.stringify(pre.find((o) => o.id === "round_side_table").position));
    await page.evaluate(() => window.__resetTransform());
    const st0 = await page.evaluate(() => window.__physicsStart({ dropHeightCm: 12, massKg: 0.3, autoRun: false }));
    const st1 = await page.evaluate(() => window.__physicsStepN(600));
    check("Rapier (vendored, offline) drops the mug onto the table and it settles", st1.settled && Math.abs(st1.bodies[0].bottom_y_m - st1.support.top_y_m) < 0.006, `gap=${(st1.bodies[0].bottom_y_m - st1.support.top_y_m).toFixed(4)} m`);
    await page.evaluate(() => window.__physicsReset());
    const exported = await page.evaluate(async () => { const b = await window.__exportScene(); await window.__loadSceneGLB(window.__lastExportedScene, "roundtrip.glb"); return { bytes: b.byteLength, objs: window.__getSceneObjects().map((o) => o.id).sort() }; });
    check("export + reload of the scene keeps both objects", exported.bytes > 500000 && exported.objs.join() === "mug,round_side_table", JSON.stringify(exported));
    await page.screenshot({ path: path.join(require("os").tmpdir(), "worldkit_demo_screenshot.png") });

    check("no request ever left localhost (three.js and Rapier are vendored)", external.length === 0, external.slice(0, 3).join(", "));
    check("no page errors", errors.length === 0, errors.join(" | ").slice(0, 300));
  } finally {
    if (browser) await browser.close();
    s1.kill(); s2.kill();
  }
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("CRASH", e); process.exit(2); });
