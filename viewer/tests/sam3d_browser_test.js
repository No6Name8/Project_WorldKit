// Real-browser verification of the viewer against a REAL SAM 3D Objects output (not the bundled TRELLIS sample).
//
// Loads the pipeline's own viewer-ready GLB (default: pipeline/runs/sam3d_run3_e2e/model.glb, produced by
// `python -m worldkit.object ... --mesh-backend modal`) through the page's real file input, and checks what the
// bundled-sample browser_test.js cannot: orientation/proportions from the real vertices, lighting, orbit/pan/zoom,
// gizmo edits, a real Muse Spark edit, manual editing after a simulated API failure, collision against the real
// handle (a probe passes through the handle opening and is blocked by the handle bar), export+reload, and the
// provenance panel against the matching report.json.
//
// Run (server must be up: pipeline\.venv\Scripts\python.exe viewer\server.py; needs Node + playwright + internet for
// the three.js CDN; MODEL_API_KEY needed for the one real Muse Spark call):
//   SAM3D_RUN=pipeline/runs/sam3d_run3_e2e node viewer/tests/sam3d_browser_test.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const URL = "http://127.0.0.1:5050/";
const RUN = path.resolve(process.env.SAM3D_RUN || path.join(__dirname, "..", "assets", "demo", "mug"));
const GLB = path.join(RUN, "model.glb");
const REPORT = path.join(RUN, "report.json");
const SHOTS = process.env.SAM3D_SHOTS || path.join(require("os").tmpdir(), "worldkit_viewer_checks");
const VIDEO = process.env.SAM3D_VIDEO || "";
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail });
  console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : ""));
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;

(async () => {
  const report = JSON.parse(fs.readFileSync(REPORT, "utf-8"));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.addInitScript(() => { try { localStorage.setItem("wk-advanced", "1"); } catch (e) { /* ignore */ } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.__sceneReady === true, { timeout: 20000 });

  // 1. Load the SAM 3D GLB through the real file input.
  await page.setInputFiles("#file-load", GLB);
  await page.waitForFunction(() => window.__lastLoadLabel === "model.glb", { timeout: 30000 });
  check("SAM 3D GLB loads through the real file input without error", (await page.evaluate(() => window.__lastLoadError)) === null);
  await page.waitForTimeout(600);

  const mat = await page.evaluate(() => window.__getMaterialInfo());
  check("textured, dielectric, with normals", mat.meshes === 1 && mat.textured === 1 && mat.metalness[0] === 0 && mat.hasNormals, JSON.stringify(mat));

  // 2. Orientation / proportions from the real vertices: upright (rim wider than base), handle on one side.
  const verts = await page.evaluate(() => window.__getMeshWorldVertices());
  const n = verts.length / 3;
  let minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) { minY = Math.min(minY, verts[3 * i + 1]); maxY = Math.max(maxY, verts[3 * i + 1]); }
  const H = maxY - minY;
  const radialAt = (lo, hi) => {
    const pts = [];
    for (let i = 0; i < n; i++) { const y = (verts[3 * i + 1] - minY) / H; if (y >= lo && y <= hi) pts.push([verts[3 * i], verts[3 * i + 2]]); }
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length, cz = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    const r = pts.map((p) => Math.hypot(p[0] - cx, p[1] - cz)).sort((a, b) => a - b);
    return { cx, cz, median: r[Math.floor(r.length / 2)], p95: r[Math.floor(r.length * 0.95)] };
  };
  const top = radialAt(0.85, 0.97), bottom = radialAt(0.03, 0.15);
  // outer radius (95th percentile, so the thin rim's inner wall doesn't count): a tapered mug is wider at the rim
  check("mug is upright: outer radius at the rim band exceeds the base band", top.p95 > bottom.p95 * 1.05, `rim r95=${top.p95.toFixed(3)} base r95=${bottom.p95.toFixed(3)}`);
  check("model fits the default framing (max extent ~2 units; Y is the body height here, Z the handle span)", H > 1.2 && H < 2.05, "Y extent=" + H.toFixed(3));

  const body = radialAt(0.2, 0.7);
  const axis = { x: body.cx, z: body.cz };
  let far = null, farR = 0;
  const handlePts = [];
  for (let i = 0; i < n; i++) {
    const x = verts[3 * i], y = verts[3 * i + 1], z = verts[3 * i + 2];
    const r = Math.hypot(x - axis.x, z - axis.z);
    if (r > body.median * 1.25) handlePts.push([x, y, z, r]);
    if (r > farR) { farR = r; far = [x, y, z]; }
  }
  check("a handle protrudes from the body (vertices well outside the body radius)", handlePts.length > 100, `handle vertices=${handlePts.length}, farthest r=${farR.toFixed(3)} vs body r=${body.median.toFixed(3)}`);

  // 3. Screenshot + lighting check.
  await page.screenshot({ path: path.join(SHOTS, "01_loaded.png") });
  const medianLum = await page.evaluate(() => {
    const c = document.querySelector("canvas"); const gl = c.getContext("webgl2") || c.getContext("webgl");
    const px = new Uint8Array(c.width * c.height * 4); gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const lum = [];
    for (let i = 0; i < px.length; i += 4 * 37) {
      const d = Math.abs(px[i] - 20) + Math.abs(px[i + 1] - 22) + Math.abs(px[i + 2] - 27);
      if (d > 60) lum.push(0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]);
    }
    lum.sort((a, b) => a - b);
    return { median: lum.length ? lum[Math.floor(lum.length / 2)] : -1, count: lum.length };
  });
  check("model is lit, not near-black or blown out", medianLum.median > 70 && medianLum.median < 245 && medianLum.count > 500, JSON.stringify(medianLum));

  // 4. Orbit / zoom / pan on the camera.
  const cam0 = await page.evaluate(() => window.__getCameraState());
  const box = await page.locator("canvas").boundingBox();
  const t0 = await page.evaluate(() => window.__getTransform());
  await page.mouse.move(box.x + box.width * 0.7, box.y + 200); await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.85, box.y + 230, { steps: 10 }); await page.mouse.up();
  const cam1 = await page.evaluate(() => window.__getCameraState());
  check("orbit-drag moves the camera, not the model", JSON.stringify(cam0.position) !== JSON.stringify(cam1.position) && JSON.stringify(t0) === JSON.stringify(await page.evaluate(() => window.__getTransform())));
  await page.screenshot({ path: path.join(SHOTS, "02_orbited.png") });
  const dist = (c) => Math.hypot(...c.position.map((v, i) => v - c.target[i]));
  await page.mouse.move(box.x + box.width * 0.7, box.y + 200);
  await page.mouse.wheel(0, -400); await page.waitForTimeout(300);
  const cam2 = await page.evaluate(() => window.__getCameraState());
  check("scroll-zoom changes camera distance", dist(cam2) < dist(cam1) - 0.05, `${dist(cam1).toFixed(2)} -> ${dist(cam2).toFixed(2)}`);
  await page.mouse.move(box.x + box.width * 0.7, box.y + 200); await page.mouse.down({ button: "right" });
  await page.mouse.move(box.x + box.width * 0.85, box.y + 260, { steps: 8 }); await page.mouse.up({ button: "right" });
  const cam3 = await page.evaluate(() => window.__getCameraState());
  check("right-drag pans (orbit target moves)", JSON.stringify(cam3.target) !== JSON.stringify(cam2.target), `${cam2.target.map((v) => v.toFixed(2))} -> ${cam3.target.map((v) => v.toFixed(2))}`);
  await page.click("#btn-reset"); await page.waitForTimeout(200);
  check("no console/page errors during load + camera interaction", errors.length === 0, errors.join(" | "));

  // 5. Gizmo edits do not corrupt the mesh.
  const base = await page.evaluate(() => window.__getMeshWorldVertices());
  await page.evaluate(() => window.__applyEdit({ translate_cm: [5, 0, 0], rotate_deg: [0, 0, 0], scale_factor: 1.5 }));
  const edited = await page.evaluate(() => window.__getMeshWorldVertices());
  const d = (v, i, j) => Math.hypot(v[3 * i] - v[3 * j], v[3 * i + 1] - v[3 * j + 1], v[3 * i + 2] - v[3 * j + 2]);
  const ratio = d(edited, 10, n - 10) / d(base, 10, n - 10);
  check("scale edit scales the real geometry uniformly, vertex count unchanged", edited.length === base.length && near(ratio, 1.5, 1e-3), "ratio=" + ratio.toFixed(4));
  await page.evaluate(() => window.__resetTransform());
  const back = await page.evaluate(() => window.__getMeshWorldVertices());
  let maxDelta = 0; for (let i = 0; i < base.length; i++) maxDelta = Math.max(maxDelta, Math.abs(base[i] - back[i]));
  check("reset restores the exact original vertices", maxDelta < 1e-5, "max delta=" + maxDelta);
  await page.click("#btn-reset"); await page.waitForTimeout(150);
  const sp = await page.evaluate(() => window.__getModelScreenPosition());
  await page.click("#mode-translate"); await page.waitForTimeout(100); // the gizmo is hidden until a mode is chosen
  const g0 = await page.evaluate(() => window.__getTransform());
  await page.mouse.move(sp.x, sp.y); await page.mouse.down(); await page.mouse.move(sp.x + 120, sp.y, { steps: 8 }); await page.mouse.up();
  const g1 = await page.evaluate(() => window.__getTransform());
  check("a real mouse drag on the gizmo moves the SAM 3D model", JSON.stringify(g0.position_units) !== JSON.stringify(g1.position_units), `${g0.position_units} -> ${g1.position_units}`);
  await page.evaluate(() => window.__resetTransform());

  // 6. Real Muse Spark edit, then manual editing after a simulated API failure.
  await page.fill("#instruction-input", "Move the mug 10 cm to the left.");
  const b4 = await page.evaluate(() => window.__getTransform());
  await page.click("#btn-send");
  await page.waitForFunction((b) => window.__getTransform().position_units[0] !== b.position_units[0], b4, { timeout: 30000 });
  const a4 = await page.evaluate(() => window.__getTransform());
  check("real Muse Spark call moved the SAM 3D model in -X", a4.position_units[0] < b4.position_units[0], `${b4.position_units[0]} -> ${a4.position_units[0]}`);
  await page.evaluate(() => window.__resetTransform());
  await page.route("**/api/edit", (r) => r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ ok: false, error_type: "api_unavailable", error: "simulated outage" }) }));
  const f0 = await page.evaluate(() => window.__getTransform());
  await page.fill("#instruction-input", "Move it 5 cm forward."); await page.click("#btn-send"); await page.waitForTimeout(300);
  const log = await page.locator("#edit-log").innerText();
  check("simulated API outage is shown as an error and leaves the model untouched", log.includes("simulated outage") && JSON.stringify(f0) === JSON.stringify(await page.evaluate(() => window.__getTransform())));
  await page.unroute("**/api/edit");
  await page.evaluate(() => window.__applyEdit({ translate_cm: [3, 0, 0], rotate_deg: [0, 20, 0], scale_factor: 1 }));
  const m1 = await page.evaluate(() => window.__getTransform());
  check("manual editing still works after the API outage", near(m1.rotation_deg[1], 20, 1e-3) && m1.position_units[0] > 0);
  await page.evaluate(() => window.__resetTransform());

  // 7. Collision against the real handle: probe passes through the opening, is blocked by the bar and by the body.
  const ang = Math.atan2(far[2] - axis.z, far[0] - axis.x);
  const radial = [Math.cos(ang), Math.sin(ang)], t = [-Math.sin(ang), Math.cos(ang)];
  const hy = handlePts.map((p) => p[1]);
  const at = (r, y, off) => [axis.x + radial[0] * r + t[0] * off, y, axis.z + radial[1] * r + t[1] * off];
  await page.evaluate(() => window.__simSetEnabled(true));
  const run = (r, y) => page.evaluate(([p, dd]) => { window.__simSetProbePosition(...p); return window.__simMove(...dd); }, [at(r, y, -0.5), [t[0], 0, t[1]]]);
  // Scan the handle plane between the body wall and the outer bar: a clear pass through the whole handle thickness is the opening.
  const clear = [], blockedPts = [];
  for (let r = body.median * 1.12; r < farR * 0.97; r += 0.04) {
    for (let y = Math.min(...hy); y <= Math.max(...hy); y += 0.04) {
      const res = await run(r, y);
      (res.blocked ? blockedPts : clear).push([r, y]);
    }
  }
  check("probes aimed through the handle plane: some pass cleanly (the opening), some are blocked (the bars)",
    clear.length >= 5 && blockedPts.length >= 5, `clear=${clear.length} blocked=${blockedPts.length}`);
  const intoBar = await run(farR - 0.04, far[1]); // slightly inside the farthest vertex: a ray through an exact mesh vertex can slip between its triangles
  check("probe aimed at the outermost handle bar is blocked by real geometry", intoBar.blocked === true && intoBar.hitDistance < 0.7, JSON.stringify(intoBar));
  const intoBody = await page.evaluate(([p, dd]) => { window.__simSetProbePosition(...p); return window.__simMove(...dd); }, [[axis.x - 1.8, minY + 0.4 * H, axis.z], [3, 0, 0]]);
  check("probe aimed at the mug body is blocked", intoBody.blocked === true, JSON.stringify(intoBody));
  await page.screenshot({ path: path.join(SHOTS, "03_collision.png") });
  await page.evaluate(() => window.__simSetEnabled(false));

  // 8. Export + reload preserves geometry and appearance.
  const exp = await page.evaluate(async () => { const b = await window.__exportGLB(); return { bytes: b.byteLength }; });
  check("export produced a real GLB", exp.bytes > 100000, "bytes=" + exp.bytes);
  const re = await page.evaluate(async () => {
    const before = window.__getMeshWorldVertices().length / 3;
    await window.__loadGLBFromArrayBuffer(window.__lastExportedGLB.slice(0), "reload.glb");
    return { before, after: window.__getMeshWorldVertices().length / 3, mat: window.__getMaterialInfo() };
  });
  check("reload keeps the vertex count and the texture", re.after === re.before && re.mat.textured === 1, JSON.stringify(re));
  await page.waitForTimeout(400);
  const lum2 = await page.evaluate(() => {
    const c = document.querySelector("canvas"); const gl = c.getContext("webgl2") || c.getContext("webgl");
    const px = new Uint8Array(c.width * c.height * 4); gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const lum = [];
    for (let i = 0; i < px.length; i += 4 * 17) {
      if (Math.abs(px[i] - 20) + Math.abs(px[i + 1] - 22) + Math.abs(px[i + 2] - 27) > 60) lum.push(0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]);
    }
    const mean = lum.reduce((a, b) => a + b, 0) / Math.max(1, lum.length);
    const sd = Math.sqrt(lum.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, lum.length));
    return { pixels: lum.length, mean: Math.round(mean), sd: Math.round(sd) };
  });
  await page.screenshot({ path: path.join(SHOTS, "04_after_reload.png") });
  check("reloaded model still renders with texture contrast (lit, not flat)", lum2.pixels > 1500 && lum2.mean > 70 && lum2.sd > 15, JSON.stringify(lum2));

  // 9. Provenance: the report loaded is the one that produced this GLB.
  const reportMesh = path.resolve(path.dirname(REPORT), path.basename(report.model_mesh));
  check("report.json's model_mesh is this GLB (same bytes)", fs.existsSync(reportMesh) && fs.readFileSync(reportMesh).equals(fs.readFileSync(GLB)), report.model_mesh);
  await page.setInputFiles("#file-report", REPORT); await page.waitForTimeout(200);
  const prov = await page.locator("#provenance-readout").innerText();
  check("provenance names SAM 3D Objects as a Meta model, print_ready false",
    prov.includes("SAM 3D Objects") && prov.includes("Meta model") && !prov.includes("NOT a Meta model") && prov.includes("print_ready: false"), prov.replace(/\n/g, " | "));
  const projected = !!(report.appearance && report.appearance.projection);
  check("provenance matches the report: real-pixel share shown only if projection was applied",
    projected ? prov.includes("real video pixels") && prov.includes("geometry modified: false") : !prov.includes("real video pixels"), prov.replace(/\n/g, " | "));
  if (report.fidelity) check("provenance shows the fidelity stage decision and the held-out check",
    prov.includes("fidelity stage:") && (!report.fidelity.applied || (prov.includes("held-out check") && prov.includes("stripe model:"))), prov.split("\n").join(" | ").slice(0, 400));
  if (report.mesh_source) check("provenance shows where an imported mesh's attribution came from", prov.includes("mesh source:") && prov.includes(report.mesh_source.source_mesh_backend), prov.replace(/\n/g, " | "));
  if (VIDEO) {
    await page.setInputFiles("#file-video", VIDEO); await page.waitForTimeout(500);
    const vd = await page.evaluate(() => { const v = document.getElementById("provenance-video"); return { shown: getComputedStyle(v).display !== "none", w: v.videoWidth, h: v.videoHeight }; });
    check("source video loads in the provenance panel", vd.shown && vd.w === 480 && vd.h === 856, JSON.stringify(vd));
  }
  const realErrors = errors.filter((e) => !e.includes("503")); // the 503 is the simulated outage above
  check("no console/page errors for the whole session (apart from the deliberately simulated 503)", realErrors.length === 0, realErrors.join(" | "));

  await browser.close();
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  if (failed.length) { console.log("FAILED:", failed.map((f) => f.name)); process.exit(1); }
})().catch((e) => { console.error("CRASH", e); process.exit(2); });
