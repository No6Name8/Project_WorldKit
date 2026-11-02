// Real-browser test of the "show what SAM found" overlay and of part edits in the AI Edit log, against worldkit.serve with a scripted
// Wonder/Spark (serve_fake.py has a 'handle' part, 7 of 8 views): the overlay marks faces, the legend states the view count, a part edit reads as
// a plain sentence (never tool-call JSON), and the overlay is dropped once the model is replaced by an edited version.
//   node viewer/tests/parts_browser_test.js        (starts its own server on port 5083; needs pipeline\.venv)
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = path.join(ROOT, "pipeline", ".venv", "Scripts", "python.exe");
const PORT = 5083;
const results = [];
const check = (name, cond, detail) => { results.push({ name, pass: !!cond }); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const srv = spawn(PY, [path.join(__dirname, "serve_fake.py"), String(PORT)], { cwd: ROOT, stdio: process.env.DEBUG_SERVERS ? "inherit" : "ignore" });
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/api/health`)).ok) break; } catch (e) { /* starting */ } await sleep(500); }
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__sceneReady === true && window.__partsUi && window.__partsUi().enabled, { timeout: 40000 });
    let ui = await page.evaluate(() => window.__partsUi());
    check("the Parts section lists the part with its view count", ui.rows === 1 && (await page.locator("#part-list").innerText()).includes("7 of 8 views"));
    check("the overlay is off until asked", !ui.on && ui.overlayFaces === 0 && ui.legend === "none");
    await page.click("#btn-parts");
    await sleep(300);
    ui = await page.evaluate(() => window.__partsUi());
    check("Show what SAM found draws overlay faces on the model", ui.on && ui.overlayFaces > 10, `faces=${ui.overlayFaces}`);
    check("the legend says how many views found the part", ui.legend === "block" && ui.legendText.includes("handle") && ui.legendText.includes("7 of 8 views"), ui.legendText);
    await page.click("#btn-parts");
    await sleep(200);
    check("toggling off removes the overlay", (await page.evaluate(() => window.__partsUi())).overlayFaces === 0);
    await page.click("#btn-parts");
    await sleep(200);

    await page.fill("#ai-input", "make the handle 2 mm thicker");
    await page.click("#ai-send");
    await page.waitForFunction(() => document.querySelector("#ai-log .entry.ok"), { timeout: 60000 });
    const row = await page.locator("#ai-log .entry.ok").first().innerText();
    check("the part edit reads as a plain sentence", row.includes("Thickened handle by 2 mm (SAM found 7 of 8 views)"), row.replace(/\n/g, " | "));
    check("no raw tool-call JSON in the row", !/[{}]|by_mm|thicken_part|"part"/.test(row), row.replace(/\n/g, " | "));
    check("the row still shows the version and the verdict", row.includes("v1") && row.includes("applied as v1"));
    await page.waitForFunction(() => (window.__lastLoadLabel || "") === "edit v1", { timeout: 15000 });
    ui = await page.evaluate(() => window.__partsUi());
    check("the overlay is dropped when the edited mesh replaces the original", !ui.on && ui.overlayFaces === 0 && ui.legend === "none");
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
