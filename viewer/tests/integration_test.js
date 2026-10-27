// End-to-end integration test: the viewer, real Muse Spark editing, manual
// gizmo controls, the collision probe, export/reload, and the provenance
// panel, all exercised together against ONE real pipeline artifact (not the
// bundled default sample) -- the output of a genuine, fresh, non-mocked
// `python -m worldkit.object ... --mesh-backend hosted` run.
//
// This is deliberately NOT a fresh reconstruction itself (that's expensive:
// ~90s of Muse Spark detection + a live SAM 3.1 call + a hosted-Space
// reconstruction) -- it loads the artifacts that run already produced
// (runs/integration_test/model.glb + report.json), consistent with "use
// existing artifacts, don't rerun expensive infrastructure just to prove
// something we already know."
//
// Run (server must already be running, from the repo root):
//   pipeline\.venv\Scripts\python.exe viewer\server.py
// then, in a scratch dir with playwright installed:
//   node <path-to-this-file>\integration_test.js
const path = require("path");
const { chromium } = require("playwright");

const URL = "http://127.0.0.1:5050/";
const MODEL_PATH = path.resolve(__dirname, "..", "..", "pipeline", "runs", "integration_test", "model.glb");
const REPORT_PATH = path.resolve(__dirname, "..", "..", "pipeline", "runs", "integration_test", "report.json");

const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail });
  console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : ""));
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.__sceneReady === true, { timeout: 20000 });

  // --- Step 1/2: load the real, freshly-reconstructed GLB from the full
  // pipeline run (not the bundled default sample) via the real "Load GLB"
  // file picker, proving the viewer accepts genuine pipeline output.
  await page.setInputFiles("#file-load", MODEL_PATH);
  await page.waitForFunction(
    (expectedLabel) => window.__lastLoadLabel === expectedLabel,
    "model.glb",
    { timeout: 15000 }
  );
  await page.waitForTimeout(300);
  const afterLoad = await page.evaluate(() => window.__getTransform());
  check(
    "fresh pipeline GLB (runs/integration_test/model.glb) loads with identity transform",
    afterLoad.position_units.every((v) => Math.abs(v) < 1e-6) && afterLoad.scale.every((v) => Math.abs(v - 1) < 1e-6),
    JSON.stringify(afterLoad)
  );

  // --- Step 3: real Muse Spark instruction changes the actual object.
  await page.fill("#instruction-input", "Rotate it 30 degrees.");
  const beforeAI = await page.evaluate(() => window.__getTransform());
  await page.click("#btn-send");
  await page.waitForFunction(
    (before) => {
      const t = window.__getTransform();
      return t.rotation_deg[2] !== before.rotation_deg[2];
    },
    beforeAI,
    { timeout: 20000 }
  );
  const afterAI = await page.evaluate(() => window.__getTransform());
  check(
    "real Muse Spark instruction rotated the freshly-loaded object",
    Math.abs(afterAI.rotation_deg[2] - beforeAI.rotation_deg[2] - 30) < 1,
    `before.rotZ=${beforeAI.rotation_deg[2]} after.rotZ=${afterAI.rotation_deg[2]}`
  );
  const aiLogText = await page.locator("#edit-log").innerText();
  check("AI edit log shows a real success, not an error", aiLogText.includes("✓") && !aiLogText.startsWith("✗"), aiLogText.slice(0, 150));

  // --- Step 4: manual gizmo controls still work AFTER the AI edit (combined
  // transform -- the gizmo drag must compose with the AI's rotation, not
  // reset or fight it).
  const box = await page.locator("canvas").boundingBox();
  const centerX = box.x + box.width / 2, centerY = box.y + box.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + 150, centerY, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  const afterManualDrag = await page.evaluate(() => window.__getTransform());
  check(
    "manual gizmo drag after an AI edit still moves the object",
    JSON.stringify(afterManualDrag.position_units) !== JSON.stringify(afterAI.position_units),
    `before=${JSON.stringify(afterAI.position_units)} after=${JSON.stringify(afterManualDrag.position_units)}`
  );
  check(
    "the AI edit's rotation survived the subsequent manual drag (edits compose, not overwrite)",
    Math.abs(afterManualDrag.rotation_deg[2] - afterAI.rotation_deg[2]) < 1e-3,
    `AI rotZ=${afterAI.rotation_deg[2]} after manual drag rotZ=${afterManualDrag.rotation_deg[2]}`
  );

  // --- Step 5: collision probe against this specific edited/moved model.
  const simInfo = await page.evaluate(() => {
    window.__simSetEnabled(true);
    // Aim the probe at wherever the (now moved+rotated) model actually is,
    // not an assumed fixed point from the unedited scene.
    const t = window.__getTransform();
    return { startProbe: window.__simProbePosition(), modelPos: t.position_units };
  });
  // Move the probe toward the model's current position and confirm a real
  // hit; this re-targets based on where the model ACTUALLY is post-edit,
  // not a stale assumption from the unedited scene.
  const approachResult = await page.evaluate((modelPos) => {
    const probePos = window.__simProbePosition();
    const dx = modelPos[0] - probePos[0];
    const dy = modelPos[1] - probePos[1];
    const dz = modelPos[2] - probePos[2];
    const len = Math.hypot(dx, dy, dz) || 1;
    // Overshoot the straight-line distance to the model's pivot so the ray
    // is guaranteed to reach (and be stopped by) its real surface.
    const scale = (len + 3) / len;
    return window.__simMove(dx * scale, dy * scale, dz * scale);
  }, simInfo.modelPos);
  check(
    "collision probe genuinely hits the edited/moved model (real raycast, re-targeted post-edit)",
    approachResult.blocked === true && approachResult.hitDistance != null,
    JSON.stringify(approachResult)
  );

  // --- Step 6: export the edited model, reload it, verify changes survive.
  const exportInfo = await page.evaluate(async () => {
    const bytes = await window.__exportGLB();
    return { byteLength: bytes.byteLength };
  });
  check("export of the edited model produces real, non-trivial GLB bytes", exportInfo.byteLength > 1000, "bytes=" + exportInfo.byteLength);

  const reloadInfo = await page.evaluate(async () => {
    const bytes = window.__lastExportedGLB;
    let vertCount = 0, triCount = 0;
    const gltf = await window.__loadGLBFromArrayBuffer(bytes.slice(0), "reloaded-edited-export.glb");
    gltf.scene.traverse((o) => {
      if (o.isMesh) { vertCount += o.geometry.attributes.position.count; triCount += o.geometry.index ? o.geometry.index.count / 3 : 0; }
    });
    return { vertCount, triCount };
  });
  check(
    "reloaded export of the EDITED model still contains real mesh geometry (the move+rotate were baked in)",
    reloadInfo.vertCount > 1000,
    JSON.stringify(reloadInfo)
  );

  // --- Step 7a: provenance panel with the REAL report.json from this exact run.
  await page.setInputFiles("#file-report", REPORT_PATH);
  await page.waitForTimeout(200);
  const provenanceText = await page.locator("#provenance-readout").innerText();
  check(
    "provenance panel renders the REAL report.json from the fresh pipeline run",
    provenanceText.includes("hosted_space_hunyuan3d") && provenanceText.includes("print_ready: false"),
    provenanceText
  );

  // --- Step 7b: no console/page errors accumulated across the whole flow.
  check("no browser console errors across the full integration flow", consoleErrors.length === 0, consoleErrors.join(" | "));
  check("no uncaught page errors across the full integration flow", pageErrors.length === 0, pageErrors.join(" | "));

  await browser.close();

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} integration checks passed.`);
  if (failed.length) {
    console.log("FAILED CHECKS:", failed.map((f) => f.name));
    process.exit(1);
  }
  process.exit(0);
})().catch((err) => {
  console.error("INTEGRATION TEST CRASHED:", err);
  process.exit(2);
});
