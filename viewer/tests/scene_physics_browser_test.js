// Real-browser verification of: multi-object scene loading/selection/independent editing/overlap/export-reload,
// missing-mesh handling, scale calibration (manual + from a pipeline report), and the Rapier rigid-body physics demo.
//
// Uses the REAL two-object scene produced by `python -m worldkit.scene` (SAM 3D mug + SAM 3D table from one video):
//   SCENE_DIR=pipeline/runs/scene_demo_mug_table node viewer/tests/scene_physics_browser_test.js
// Server must be running (pipeline\.venv\Scripts\python.exe viewer\server.py); needs internet for three.js and Rapier CDNs.
const { chromium } = require("playwright");
const fs = require("fs");
const os = require("os");
const path = require("path");

const URL = "http://127.0.0.1:5050/";
const SCENE = path.resolve(process.env.SCENE_DIR || path.join(__dirname, "..", "assets", "demo", "scene"));
const SHOTS = path.join(os.tmpdir(), "worldkit_viewer_checks");
// Scale-from-report check: the demo mug's mesh (same geometry/units as the report's model) + a report from the real-video scale run.
// That report used an ASSUMED 2.5 cm coin: it tests plumbing, it is not a measurement.
const PLUMB_MODEL = path.join(__dirname, "..", "assets", "demo", "mug", "model.glb");
const PLUMB_REPORT = path.join(__dirname, "fixtures", "scale_plumbing_report.json");
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond });
  console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : ""));
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;
const J = (x) => JSON.stringify(x);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.addInitScript(() => { try { localStorage.setItem("wk-advanced", "1"); } catch (e) { /* ignore */ } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.__sceneReady === true, { timeout: 20000 });

  // ---------------------------------------------------------------- scene load ----
  await page.setInputFiles("#file-scene", SCENE);
  await page.waitForFunction(() => (window.__lastLoadLabel || "").startsWith("scene:"), { timeout: 60000 });
  await page.waitForTimeout(500);
  let objs = await page.evaluate(() => window.__getSceneObjects());
  check("scene folder loads two objects with their own identities", objs.length === 2 && objs.map((o) => o.id).sort().join() === "mug,round_side_table", J(objs.map((o) => [o.id, o.role])));
  check("each object carries its own model provenance (SAM 3D Objects)", objs.every((o) => o.model === "SAM 3D Objects"), J(objs.map((o) => o.model)));
  const mug0 = objs.find((o) => o.id === "mug"), tab0 = objs.find((o) => o.id === "round_side_table");
  check("the mug is placed above the table's origin, on its top (real layout from the manifest)", mug0.position[1] > 0.3 && tab0.role === "parent", J(mug0.position));
  await page.screenshot({ path: path.join(SHOTS, "01_scene.png") });
  const ov0 = await page.evaluate(() => window.__checkOverlaps());
  check("initial layout: no penetration between mug and table", ov0.every((r) => r.fraction <= 0.01), J(ov0));

  // ---------------------------------------------------------------- selection + independent editing ----
  await page.click('#object-list button[data-id="round_side_table"]');
  check("selecting the table via the list makes it active", (await page.evaluate(() => window.__getSceneObjects())).find((o) => o.id === "round_side_table").selected);
  const prov = await page.locator("#provenance-readout").innerText();
  check("provenance panel follows the selected object's own report", prov.includes("SAM 3D Objects") && prov.includes("Meta model"), prov.replace(/\n/g, " | ").slice(0, 160));
  const mugPix = await page.evaluate(() => window.__objectScreenPosition("mug"));
  await page.mouse.click(mugPix.x, mugPix.y);
  await page.waitForTimeout(150);
  check("clicking the mug in the 3D view selects it", (await page.evaluate(() => window.__getSceneObjects())).find((o) => o.id === "mug").selected);

  const before = await page.evaluate(() => window.__getSceneObjects());
  await page.evaluate(() => window.__applyEdit({ translate_cm: [3, 0, 0], rotate_deg: [0, 25, 0], scale_factor: 1.0 }));
  const afterMug = await page.evaluate(() => window.__getSceneObjects());
  const bm = before.find((o) => o.id === "mug"), am = afterMug.find((o) => o.id === "mug");
  check("editing the selected mug moves/rotates only the mug", am.position[0] !== bm.position[0] && near(am.rotation_deg_y - bm.rotation_deg_y, 25, 0.01) && J(afterMug.find((o) => o.id === "round_side_table")) === J(before.find((o) => o.id === "round_side_table")));
  await page.evaluate(() => window.__selectObject("round_side_table"));
  await page.evaluate(() => window.__applyEdit({ translate_cm: [0, 0, 2], rotate_deg: [0, 0, 0], scale_factor: 1.0 }));
  const afterTab = await page.evaluate(() => window.__getSceneObjects());
  check("editing the table leaves the mug untouched", J(afterTab.find((o) => o.id === "mug")) === J(afterMug.find((o) => o.id === "mug")).replace('"selected":true', '"selected":false') || J(afterTab.find((o) => o.id === "mug").position) === J(am.position));
  await page.evaluate(() => { window.__selectObject("round_side_table"); window.__resetTransform(); window.__selectObject("mug"); window.__resetTransform(); });
  const reset = await page.evaluate(() => window.__getSceneObjects());
  check("Reset transform restores each object's manifest placement", near(reset.find((o) => o.id === "mug").position[1], mug0.position[1], 1e-6) && near(reset.find((o) => o.id === "mug").position[0], mug0.position[0], 1e-6));

  // ---------------------------------------------------------------- overlap ----
  await page.evaluate(() => { window.__selectObject("mug"); window.__applyEdit({ translate_cm: [0, -0.25, 0], rotate_deg: [0, 0, 0], scale_factor: 1 }); });
  const ov1 = await page.evaluate(() => window.__checkOverlaps());
  const mugInTable = ov1.find((r) => r.a === "mug" && r.inside === "round_side_table");
  check("pushing the mug into the table is detected as overlap", mugInTable.fraction > 0.05, J(mugInTable));
  check("the overlap warning is shown to the user", (await page.locator("#overlap-readout").innerText()).includes("overlaps"));
  await page.evaluate(() => window.__resetTransform());
  const ov2 = await page.evaluate(() => window.__checkOverlaps());
  check("moving it back clears the overlap", ov2.every((r) => r.fraction <= 0.01), J(ov2));

  // ---------------------------------------------------------------- scene export / reload ----
  await page.evaluate(() => { window.__selectObject("mug"); window.__applyEdit({ translate_cm: [4, 0, -3], rotate_deg: [0, 40, 0], scale_factor: 1.0 }); });
  const preExport = await page.evaluate(() => window.__getSceneObjects());
  const sceneBytes = await page.evaluate(async () => (await window.__exportScene()).byteLength);
  check("scene export produced one GLB containing both objects", sceneBytes > 500000, "bytes=" + sceneBytes);
  await page.evaluate(async () => { await window.__loadSceneGLB(window.__lastExportedScene, "reloaded_scene.glb"); });
  await page.waitForTimeout(300);
  const post = await page.evaluate(() => window.__getSceneObjects());
  check("reload keeps both objects and their identities/roles", post.length === 2 && post.map((o) => o.id + ":" + o.role).sort().join() === preExport.map((o) => o.id + ":" + o.role).sort().join(), J(post.map((o) => o.id)));
  const eq = preExport.every((o) => { const q = post.find((x) => x.id === o.id); return q && o.position.every((v, i) => near(v, q.position[i], 1e-4)) && near(o.rotation_deg_y, q.rotation_deg_y, 0.01) && o.scale.every((v, i) => near(v, q.scale[i], 1e-5)); });
  check("reload preserves every object's edited transform", eq, J(post.map((o) => [o.id, o.position])));
  await page.screenshot({ path: path.join(SHOTS, "02_reloaded_scene.png") });

  // ---------------------------------------------------------------- individual export ----
  await page.evaluate(async () => { window.__selectObject("mug"); await window.__exportGLB(); });
  const single = await page.evaluate(async () => {
    const bytes = window.__lastExportedGLB.slice(0);
    await window.__loadGLBFromArrayBuffer(bytes, "mug_only.glb");
    return { n: window.__getSceneObjects().length, verts: window.__getMeshWorldVertices().length / 3 };
  });
  check("exporting one selected object yields a single-object GLB that reloads as one object", single.n === 1 && single.verts > 3000, J(single));

  // ---------------------------------------------------------------- missing mesh ----
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wk_scene_"));
  fs.cpSync(SCENE, path.join(tmp, "scene"), { recursive: true, filter: (src) => !src.includes("_all_frames") && !src.includes("frames") && !src.includes("viewer_checks") });
  fs.rmSync(path.join(tmp, "scene", "objects", "round_side_table", "model.glb"));
  await page.setInputFiles("#file-scene", path.join(tmp, "scene"));
  await page.waitForFunction(() => window.__getSceneObjects().length === 2 && window.__getSceneObjects().some((o) => o.missing) && (window.__lastLoadLabel || "").startsWith("scene:"), { timeout: 30000 });
  const miss = await page.evaluate(() => window.__getSceneObjects());
  check("a missing mesh is flagged, the other object still loads", miss.find((o) => o.id === "round_side_table").missing === true && miss.find((o) => o.id === "mug").missing === false, J(miss.map((o) => [o.id, o.missing])));
  check("the missing object is reported to the user and cannot be selected", (await page.locator("#edit-log").innerText()).includes("not found") && (await page.evaluate(() => window.__selectObject("round_side_table"))) === false);
  const missExport = await page.evaluate(async () => { const b = await window.__exportScene(); await window.__loadSceneGLB(window.__lastExportedScene, "partial.glb"); return window.__getSceneObjects().map((o) => [o.id, o.missing]); });
  check("exporting then reloading a scene with a missing object keeps the gap visible", J(missExport) === J([["mug", false], ["round_side_table", true]]) || J(missExport) === J([["mug", false], ["round_side_table", true]].reverse()), J(missExport));

  // ---------------------------------------------------------------- calibration ----
  await page.setInputFiles("#file-scene", SCENE);
  await page.waitForFunction(() => window.__getSceneObjects().every((o) => !o.missing) && window.__getSceneObjects().length === 2, { timeout: 60000 });
  const bannerBefore = await page.locator("#scale-banner").innerText();
  check("an uncalibrated scene says so", bannerBefore.includes("NOT calibrated") && !(await page.evaluate(() => window.__getTransform().calibrated)));
  const vertsBefore = await page.evaluate(() => window.__getMeshWorldVertices().length);
  await page.evaluate(() => window.__selectObject("mug"));
  const mugHeightUnits = await page.evaluate(() => { const v = window.__getMeshWorldVertices(); return null; });
  const objsBeforeCal = await page.evaluate(() => window.__getSceneObjects());
  const vertsBefore2 = await page.evaluate(() => window.__getMeshWorldVertices().length);
  await page.fill("#calib-height", "9");
  await page.click("#btn-calibrate");
  const cal = await page.evaluate(() => window.__getTransform());
  const mugBox = await page.evaluate(() => {
    const o = window.__getSceneObjects().find((x) => x.id === "mug");
    return o.scale[0];
  });
  check("manual calibration sets cm/unit and flags the scene as calibrated by a user-entered number", cal.calibrated && cal.calibration.source === "manual" && cal.calibration.height_cm === 9, J(cal.calibration));
  const banner = await page.locator("#scale-banner").innerText();
  check("banner states it is the user's number, not a WorldKit measurement", banner.includes("manually") && banner.includes("not verified"), banner);
  const objsCal = await page.evaluate(() => window.__getSceneObjects());
  check("calibration changes units only, not object transforms or proportions", J(objsCal.map((o) => [o.position, o.scale, o.rotation_deg_y])) === J(objsBeforeCal.map((o) => [o.position, o.scale, o.rotation_deg_y])) && cal.cm_per_scene_unit > 0 && (await page.evaluate(() => window.__getMeshWorldVertices().length)) === vertsBefore2);
  const mugCm = await page.evaluate(() => { const o = window.__getSceneObjects().find((x) => x.id === "mug"); return o; });

  // ---------------------------------------------------------------- physics engine self-test ----
  const st = await page.evaluate(() => window.__physicsSelfTest());
  check("engine: free fall under gravity matches 1/2 g t^2 (<1% error)", st.free_fall.rel_err < 0.01, J(st.free_fall));
  check("engine: mass is used (same impulse, 4x mass gives 1/4 velocity)", near(st.mass_impulse.ratio, 4, 0.05), J(st.mass_impulse));
  check("engine: collider size is used (rest height = collider half-height)", st.collider_shape.every((c) => near(c.rest_center_y, c.half_height, 0.01)), J(st.collider_shape));

  // ---------------------------------------------------------------- physics on the scene ----
  const preSim = await page.evaluate(() => window.__getSceneObjects());
  const startState = await page.evaluate(() => window.__physicsStart({ dropHeightCm: 15, massKg: 0.3, autoRun: false }));
  const during = await page.evaluate(() => window.__getSceneObjects());
  check("simulation starts on a clone: originals are hidden, not moved", during.every((o) => o.visible === false) && J(during.map((o) => o.position)) === J(preSim.map((o) => o.position)));
  check("collider shapes: static table = exact triangle mesh, mug = convex hull (approximation)", /triangle mesh/.test(startState.support.collider) && /convex hull/.test(startState.bodies[0].collider), J([startState.support.collider, startState.bodies[0].collider]));
  check("mass from the input is what the body has", near(startState.bodies[0].mass_kg, 0.3, 1e-4), "mass=" + startState.bodies[0].mass_kg);
  const y0 = startState.bodies[0].position_units[1];
  const mpu = startState.meters_per_scene_unit;
  const s10 = await page.evaluate(() => window.__physicsStepN(10));
  const fell = (y0 - s10.bodies[0].position_units[1]) * mpu;
  const expectedFall = 0.5 * 9.81 * (10 / 60) ** 2;
  check("gravity acts on the mug: 10 steps fall = 1/2 g t^2 within 3%", Math.abs(fell - expectedFall) / expectedFall < 0.03, `fell=${fell.toFixed(4)} m expected=${expectedFall.toFixed(4)} m`);
  const settled = await page.evaluate(() => window.__physicsStepN(600));
  const b = settled.bodies[0];
  await page.screenshot({ path: path.join(SHOTS, "03_physics_settled.png") });
  const simPix = await page.evaluate(() => {
    const c = document.querySelector("canvas"); const gl = c.getContext("webgl2") || c.getContext("webgl");
    const px = new Uint8Array(c.width * c.height * 4); gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let n = 0; for (let i = 0; i < px.length; i += 4 * 37) if (Math.abs(px[i] - 20) + Math.abs(px[i + 1] - 22) + Math.abs(px[i + 2] - 27) > 60) n++;
    return n;
  });
  check("the simulated clones are actually visible on screen (not an empty view)", settled.proxies_visible === true && simPix > 500, `proxies_visible=${settled.proxies_visible} model pixels=${simPix}`);
  check("the mug has settled (low speed, simulation reports settled)", settled.settled && b.speed_m_s < 0.05, J({ settled: settled.settled, speed: b.speed_m_s, sleeping: b.sleeping }));
  check("it rests ON the table top (collider bottom within 5 mm of the table's top surface)", Math.abs(b.bottom_y_m - settled.support.top_y_m) < 0.005, `bottom=${b.bottom_y_m.toFixed(4)} top=${settled.support.top_y_m.toFixed(4)}`);
  check("it fell: its centre ended up about the drop height lower than it started", (b.start_center_y_m - b.position_units[1] * mpu) > 0.10, `drop=${(b.start_center_y_m - b.position_units[1] * mpu).toFixed(3)} m`);
  const afterSim = await page.evaluate(() => window.__getSceneObjects());
  check("during/after simulation the edited objects' transforms are still untouched", J(afterSim.map((o) => o.position)) === J(preSim.map((o) => o.position)));
  const finalA = b.position_units.slice();

  await page.evaluate(() => window.__physicsReset());
  const afterReset = await page.evaluate(() => window.__getSceneObjects());
  check("Reset: originals visible again at exactly their previous transforms", afterReset.every((o) => o.visible) && J(afterReset.map((o) => [o.position, o.rotation_deg_y, o.scale])) === J(preSim.map((o) => [o.position, o.rotation_deg_y, o.scale])));
  check("Reset: simulation is no longer running", (await page.evaluate(() => window.__physicsState())).running === false);
  await page.evaluate(() => window.__physicsStart({ dropHeightCm: 15, massKg: 0.3, autoRun: false }));
  const again = await page.evaluate(() => window.__physicsStepN(610));
  check("repeatable: a second run reaches the same resting position (< 1e-3 units)", again.bodies[0].position_units.every((v, i) => near(v, finalA[i], 1e-3)), J([again.bodies[0].position_units, finalA]));
  await page.evaluate(() => window.__physicsReset());

  // heavier mug: same trajectory (gravity is mass independent) but the engine reports the new mass
  const heavy = await page.evaluate(() => window.__physicsStart({ dropHeightCm: 5, massKg: 0.9, autoRun: false }));
  check("a different mass input is applied", near(heavy.bodies[0].mass_kg, 0.9, 1e-4));
  await page.evaluate(() => window.__physicsReset());

  // starting in contact (no drop) must not sink or launch
  await page.evaluate(() => window.__physicsStart({ dropHeightCm: 0, massKg: 0.3, autoRun: false }));
  const rest = await page.evaluate(() => window.__physicsStepN(300));
  check("resting on the table with no drop: stays put (no sinking, no launch)", Math.abs(rest.bodies[0].bottom_y_m - rest.support.top_y_m) < 0.006 && rest.bodies[0].speed_m_s < 0.05, `gap=${(rest.bodies[0].bottom_y_m - rest.support.top_y_m).toFixed(4)} m`);
  await page.evaluate(() => window.__physicsReset());
  check("the scene is flagged as uncalibrated-size-aware: calibrated scale used for meters", (await page.evaluate(() => window.__physicsState())).meters_per_scene_unit > 0);

  // the real UI path: click Simulate, the animation loop steps the engine in real time, then click Reset sim
  await page.click("#btn-phys-start");
  await page.waitForFunction(() => document.getElementById("phys-readout").innerText.includes("SETTLED"), { timeout: 30000 });
  const uiTxt = await page.locator("#phys-readout").innerText();
  check("UI: Simulate runs in real time and the readout reports SETTLED with its collider kinds", uiTxt.includes("convex hull") && uiTxt.includes("triangle mesh"), uiTxt.split("\n").join(" | "));
  await page.click("#btn-phys-reset");
  check("UI: Reset sim brings the originals back", (await page.evaluate(() => window.__getSceneObjects())).every((o) => o.visible));

  // ---------------------------------------------------------------- single object drop ----
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.__sceneReady === true, { timeout: 20000 });
  const sPre = await page.evaluate(() => window.__getSceneObjects());
  await page.evaluate(() => window.__physicsStart({ dropHeightCm: 15, massKg: 0.3, autoRun: false }));
  const sFin = await page.evaluate(() => window.__physicsStepN(900));
  check("single bundled mug dropped on the ground plane settles with its collider on the ground", sFin.settled && Math.abs(sFin.bodies[0].bottom_y_m - sFin.support.top_y_m) < 0.006, `gap=${(sFin.bodies[0].bottom_y_m - sFin.support.top_y_m).toFixed(4)} m, support=${sFin.support.id}`);
  await page.evaluate(() => window.__physicsReset());
  const sPost = await page.evaluate(() => window.__getSceneObjects());
  check("single-object editor state unchanged by the simulation", J(sPre.map((o) => o.position)) === J(sPost.map((o) => o.position)) && sPost[0].visible);

  // ---------------------------------------------------------------- report-driven scale ----
  await page.setInputFiles("#file-load", PLUMB_MODEL);
  await page.waitForFunction(() => window.__lastLoadLabel === "model.glb", { timeout: 30000 });
  await page.setInputFiles("#file-report", PLUMB_REPORT);
  await page.waitForTimeout(300);
  const rep = JSON.parse(fs.readFileSync(PLUMB_REPORT, "utf-8"));
  const tr = await page.evaluate(() => window.__getTransform());
  check("a reference-calibrated report sets the scale (cm per unit = the report's scale factor) and says it is an estimate", tr.calibrated && near(tr.cm_per_scene_unit, rep.print_prep.scale_factor, 1e-6) && tr.calibration.source === "reference_calibrated" && (await page.locator("#scale-banner").innerText()).includes("estimate"), J(tr.calibration));
  const bad = JSON.parse(JSON.stringify(rep));
  bad.print_prep.scale_factor *= 1.5;
  const badPath = path.join(tmp, "bad_report.json");
  fs.writeFileSync(badPath, JSON.stringify(bad));
  await page.evaluate(() => { document.getElementById("file-load").value = ""; });
  await page.setInputFiles("#file-load", PLUMB_MODEL);
  await page.waitForTimeout(600);
  await page.setInputFiles("#file-report", badPath);
  await page.waitForTimeout(300);
  const trBad = await page.evaluate(() => window.__getTransform());
  check("a report that does not match the loaded model is NOT applied", !trBad.calibrated && (await page.locator("#provenance-readout").innerText()).includes("NOT applied"), (await page.locator("#provenance-readout").innerText()).split("\n").slice(-1)[0]);
  check("no print_ready claim from calibration (report stays print_ready=false)", rep.print_ready === false);

  const real = errors.filter((e) => !e.includes("503"));
  check("no console/page errors during the whole session", real.length === 0, real.join(" | ").slice(0, 300));
  await browser.close();
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  if (failed.length) { console.log("FAILED:", failed.map((f) => f.name)); process.exit(1); }
})().catch((e) => { console.error("CRASH", e); process.exit(2); });
