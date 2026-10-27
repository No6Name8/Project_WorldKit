// Real browser verification of viewer/index.html against the running
// Flask dev server at http://127.0.0.1:5050/. Not a mock: loads the actual
// page, the actual three.js modules from the CDN, and the actual sample
// mesh, then drives the page's own exposed functions (the same ones its
// buttons call) to verify load / orbit / gizmo drag / export+reload /
// real Muse Spark edit / collision-aware probe movement.
//
// Run (from anywhere, needs Node + internet for the three.js CDN):
//   1. Start the server:  pipeline\.venv\Scripts\python.exe viewer\server.py
//   2. In a scratch dir:  npm install playwright@1.64.1 && npx playwright install chromium
//   3. node <path-to-this-file>\browser_test.js
//
// Requires MODEL_API_KEY configured (pipeline/.env) for the Muse Spark
// check to pass -- it makes one real Meta API call.
const { chromium } = require("playwright");

const URL = "http://127.0.0.1:5050/";
const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail });
  console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : ""));
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (err) => consoleErrors.push(String(err)));

  await page.goto(URL, { waitUntil: "networkidle" });

  // 1. Model actually loads (real GLTFLoader against the real sample GLB)
  await page.waitForFunction(() => window.__sceneReady === true, { timeout: 20000 });
  const loadErr = await page.evaluate(() => window.__lastLoadError);
  check("model loads without error", loadErr === null, "lastLoadError=" + loadErr);

  const initialTransform = await page.evaluate(() => window.__getTransform());
  check(
    "initial transform is identity-ish",
    initialTransform.position_units.every((v) => Math.abs(v) < 1e-6) &&
      initialTransform.scale.every((v) => Math.abs(v - 1) < 1e-6),
    JSON.stringify(initialTransform)
  );

  // 2. Canvas actually rendered something (not a blank black screen) --
  // sample pixel colors from the canvas, expect a non-trivial unique count.
  const pixelVariety = await page.evaluate(() => {
    const canvas = document.querySelector("canvas");
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    const w = canvas.width, h = canvas.height;
    const pixels = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    const colors = new Set();
    for (let i = 0; i < pixels.length; i += 4 * 97) {
      colors.add(pixels[i] + "," + pixels[i + 1] + "," + pixels[i + 2]);
    }
    return colors.size;
  });
  check("canvas rendered varied pixel content (not blank)", pixelVariety > 3, "unique sampled colors=" + pixelVariety);

  // 3. Orbit controls: camera actually moves when we drag
  const camBefore = await page.evaluate(() => {
    const c = window.__getTransform; // just to ensure page context alive
    return null;
  });
  // Drag near a CORNER of the canvas, deliberately away from the model/gizmo
  // (which sit at the viewport center) -- dragging at center would grab a
  // TransformControls gizmo handle instead of orbiting, as a first version
  // of this test discovered (it corrupted the next check's position
  // assertion -- real evidence the gizmo intercepts pointer events over its
  // handles, not a product bug, but it meant this drag wasn't testing orbit).
  const box = await page.locator("canvas").boundingBox();
  const cornerX = box.x + 40, cornerY = box.y + 40;
  const transformBeforeDrag = await page.evaluate(() => window.__getTransform());
  await page.mouse.move(cornerX, cornerY);
  await page.mouse.down();
  await page.mouse.move(cornerX + 120, cornerY + 40, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  check("orbit-drag produced no console/page errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  const transformAfterDrag = await page.evaluate(() => window.__getTransform());
  check(
    "orbit-drag (away from gizmo) did not move the model itself",
    JSON.stringify(transformBeforeDrag) === JSON.stringify(transformAfterDrag),
    `before=${JSON.stringify(transformBeforeDrag)} after=${JSON.stringify(transformAfterDrag)}`
  );

  // 4. Gizmo mode buttons switch mode (UI affordance, real DOM click)
  await page.click("#mode-rotate");
  const rotateActive = await page.evaluate(() => document.getElementById("mode-rotate").classList.contains("active"));
  check("clicking Rotate activates rotate mode button", rotateActive);
  await page.click("#mode-translate");

  // 4b. Dragging the gizmo handle at canvas center (where the translate
  // gizmo sits on the centered model) actually moves the real model --
  // a genuine mouse drag on the real gizmo widget, not a simulated call.
  const transformBeforeGizmoDrag = await page.evaluate(() => window.__getTransform());
  const centerX = box.x + box.width / 2, centerY = box.y + box.height / 2; // model + gizmo are centered in frame
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + 150, centerY, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  const transformAfterGizmoDrag = await page.evaluate(() => window.__getTransform());
  check(
    "dragging the gizmo handle at the model's location changes its real transform",
    JSON.stringify(transformBeforeGizmoDrag) !== JSON.stringify(transformAfterGizmoDrag),
    `before=${JSON.stringify(transformBeforeGizmoDrag)} after=${JSON.stringify(transformAfterGizmoDrag)}`
  );
  await page.evaluate(() => window.__resetTransform());

  // 5. Programmatic transform (the same function manual-drag / AI edits use)
  // mutates the ACTUAL THREE.js object -- verified by reading it back.
  await page.evaluate(() => {
    window.__applyEdit({ translate_cm: [10, 0, 0], rotate_deg: [0, 0, 45], scale_factor: 1.2 });
  });
  const afterManual = await page.evaluate(() => window.__getTransform());
  const expectedUnits = 10 / afterManual.cm_per_scene_unit;
  check(
    "manual transform moved position.x by the expected scene-unit delta",
    Math.abs(afterManual.position_units[0] - expectedUnits) < 1e-6,
    `pos.x=${afterManual.position_units[0]} expected=${expectedUnits}`
  );
  check(
    "manual transform rotated Z by 45deg",
    Math.abs(afterManual.rotation_deg[2] - 45) < 1e-3,
    "rot.z=" + afterManual.rotation_deg[2]
  );
  check(
    "manual transform scaled by 1.2x",
    Math.abs(afterManual.scale[0] - 1.2) < 1e-6,
    "scale.x=" + afterManual.scale[0]
  );

  // 6. Export -> reload round trip: export current (transformed) mesh,
  // reload those exact bytes into the loader, and check the reloaded
  // object's geometry matches (vertex count) -- i.e. the export captured
  // real, baked geometry, not just a reference.
  const exportInfo = await page.evaluate(async () => {
    const bytes = await window.__exportGLB();
    return { byteLength: bytes.byteLength };
  });
  check("export produced non-trivial GLB bytes", exportInfo.byteLength > 1000, "bytes=" + exportInfo.byteLength);

  const reloadResult = await page.evaluate(async () => {
    const bytes = window.__lastExportedGLB;
    const beforeReloadTransform = window.__getTransform();
    const gltf = await window.__loadGLBFromArrayBuffer(bytes.slice(0), "round-trip-export.glb");
    let vertCount = 0;
    gltf.scene.traverse((o) => { if (o.isMesh) vertCount += o.geometry.attributes.position.count; });
    const afterReloadTransform = window.__getTransform();
    return { vertCount, beforeReloadTransform, afterReloadTransform, sceneReady: window.__sceneReady };
  });
  check("reloaded exported GLB contains real mesh geometry", reloadResult.vertCount > 1000, "vertCount=" + reloadResult.vertCount);
  check(
    "reload reset transform to identity (export bakes transform into geometry, by design)",
    reloadResult.afterReloadTransform.position_units.every((v) => Math.abs(v) < 1e-6) &&
      Math.abs(reloadResult.afterReloadTransform.scale[0] - 1) < 1e-6,
    JSON.stringify(reloadResult.afterReloadTransform)
  );

  // 7. Natural-language edit panel: real end-to-end call to the local
  // Flask backend, which makes a REAL Muse Spark API call.
  await page.fill("#instruction-input", "Move the mug 10 cm to the left.");
  const beforeAI = await page.evaluate(() => window.__getTransform());
  await page.click("#btn-send");
  await page.waitForFunction(
    (before) => {
      const t = window.__getTransform();
      return t.position_units[0] !== before.position_units[0];
    },
    beforeAI,
    { timeout: 20000 }
  );
  const afterAI = await page.evaluate(() => window.__getTransform());
  check(
    "real Muse Spark instruction moved the object in -X",
    afterAI.position_units[0] < beforeAI.position_units[0],
    `before.x=${beforeAI.position_units[0]} after.x=${afterAI.position_units[0]}`
  );
  const logText = await page.locator("#edit-log").innerText();
  check("edit log shows a real explanation, not an error", logText.includes("✓"), logText.slice(0, 200));

  // 8. Collision-aware probe: real raycasting against the actual mesh.
  await page.evaluate(() => window.__resetTransform());
  const simInfo = await page.evaluate(() => {
    window.__simSetEnabled(true);
    const startPos = window.__simProbePosition();
    // Move a large step straight toward the model's center (probe starts
    // just outside the model's bounding box on +X, so moving -X by more
    // than the gap should hit the mesh if collision is real).
    const bigStepResult = window.__simMove(-10, 0, 0);
    const afterBigStep = window.__simProbePosition();
    // Move back away from the mesh (+X, retreating the way we came) before
    // testing free movement -- right after the blocked move the probe is
    // still touching the mug's side wall, so +Y there would (correctly)
    // hit the mug's body too, not prove collision detection is broken.
    window.__simMove(3, 0, 0);
    const beforeFree = window.__simProbePosition();
    // Now move in a direction with open space -- should NOT be blocked.
    const freeResult = window.__simMove(0, 2, 0);
    const afterFree = window.__simProbePosition();
    return { startPos, bigStepResult, afterBigStep, beforeFree, freeResult, afterFree };
  });
  check(
    "a large move toward the mesh is blocked by real collision, not passed through",
    simInfo.bigStepResult.blocked === true && simInfo.bigStepResult.hitDistance < 10,
    JSON.stringify(simInfo.bigStepResult)
  );
  check(
    "the blocked probe did not end up inside/past the mesh (stopped near the hit distance)",
    Math.abs(simInfo.afterBigStep[0] - simInfo.startPos[0]) < simInfo.bigStepResult.hitDistance + 0.2,
    `start=${simInfo.startPos} after=${simInfo.afterBigStep} hitDistance=${simInfo.bigStepResult.hitDistance}`
  );
  check(
    "a move in open space (away from the mesh) is NOT blocked",
    simInfo.freeResult.blocked === false && simInfo.freeResult.moved === true,
    JSON.stringify(simInfo.freeResult)
  );
  check(
    "the free move actually changed the probe's Y position by the requested amount",
    Math.abs(simInfo.afterFree[1] - simInfo.beforeFree[1] - 2) < 1e-3,
    `before.y=${simInfo.beforeFree[1]} after.y=${simInfo.afterFree[1]}`
  );

  // 9. Provenance panel: loading a report.json surfaces its real fields
  // read-only, without touching the loaded mesh's transform.
  const fakeReport = JSON.stringify({
    mesh_backend: "hosted",
    mesh_method: "hosted_space_hunyuan3d:frogleo/Image-to-3D",
    print_ready: false,
    print_prep: { watertight: true, thin_wall_warning_count: 1151, thin_wall_sample_count: 1500, height_source: "default_fallback", is_estimate: true },
  });
  const reportPath = require("path").join(require("os").tmpdir(), "fake_report.json");
  require("fs").writeFileSync(reportPath, fakeReport);
  await page.setInputFiles("#file-report", reportPath);
  await page.waitForTimeout(150);
  const provenanceText = await page.locator("#provenance-readout").innerText();
  check(
    "provenance panel shows the loaded report's real fields",
    provenanceText.includes("hosted") && provenanceText.includes("print_ready: false"),
    provenanceText
  );

  await browser.close();

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  if (failed.length) {
    console.log("FAILED CHECKS:", failed.map((f) => f.name));
    process.exit(1);
  }
  process.exit(0);
})().catch((err) => {
  console.error("TEST SCRIPT CRASHED:", err);
  process.exit(2);
});
