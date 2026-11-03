// Real-browser test of the AI Edit chat panel against the real FastAPI server (worldkit.serve) with a scripted Wonder/Spark
// (serve_fake.py): panel visibility, status line, Spark verdict, model reload after an edit, rejection and decline paths, Undo,
// Download STL. The live Muse Wonder / Muse Spark path is exercised separately by the CLI (see docs) - not by this test.
//   node viewer/tests/ai_edit_browser_test.js        (starts its own server on port 5081; needs pipeline\.venv)
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const PY = path.join(ROOT, "pipeline", ".venv", "Scripts", "python.exe");
const PORT = 5081;
const results = [];
const check = (name, cond, detail) => { results.push({ name, pass: !!cond }); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const srv = spawn(PY, [path.join(__dirname, "serve_fake.py"), String(PORT)], { cwd: ROOT, stdio: process.env.DEBUG_SERVERS ? "inherit" : "ignore", env: { ...process.env, WK_FAKE_DELAY: "1.2" } });
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/api/health`)).ok) break; } catch (e) { /* starting */ } await sleep(500); }
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.addInitScript(() => { try { localStorage.setItem("wk-advanced", "1"); } catch (e) { /* ignore */ } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => window.__sceneReady === true, { timeout: 30000 });
    await page.waitForFunction(() => window.__aiState && window.__aiState(), { timeout: 10000 });
    await page.click("#tab-ai");
    check("the AI Edit panel is shown by the edit server", await page.locator("#aiedit-panel").isVisible());
    check("it shows the version and the units, and the scale caveat is stated", (await page.locator("#ai-version").innerText()).includes("v0") && (await page.locator("#sec-ai").innerText()).includes("PLACEHOLDER"));
    check("Undo is disabled at the original model", await page.locator("#ai-undo").isDisabled());

    const sizeBefore = await page.evaluate(() => window.__getMeshWorldVertices().length);
    await page.fill("#ai-input", "make it exactly 12 cm tall");
    await page.click("#ai-send");
    // live progress: the stages come from the server's /edit/progress (scripted Wonder and Spark each take 1.2 s here)
    const sawChoosing = await page.waitForFunction(() => { const a = document.querySelector("#ai-status .prog .st.active"); return a && a.textContent.startsWith("Choosing tool"); }, { timeout: 5000 }).then(() => true).catch(() => false);
    check("while an edit runs the panel shows the server's stage (Choosing tool)", sawChoosing);
    const sawChecking = await page.waitForFunction(() => { const a = document.querySelector("#ai-status .prog .st.active"); return a && a.textContent.startsWith("Checking with Spark") && document.querySelectorAll("#ai-status .prog .st.done").length === 2; }, { timeout: 15000 }).then(() => true).catch(() => false);
    check("the stage advances to Checking with Spark, with the earlier stages marked done", sawChecking);
    const el = await page.locator("#ai-status .prog .el").innerText().catch(() => "");
    check("an elapsed-seconds counter is shown", /^\d+ s$/.test(el), el);
    await page.waitForFunction(() => document.querySelector("#ai-log .entry.ok"), { timeout: 60000 });
    const ok = await page.locator("#ai-log .entry.ok").first().innerText();
    const okFull = await page.locator("#ai-log .entry.ok").first().evaluate((e) => e.textContent);
    check("an applied edit reads as a plain sentence with the version and Spark's verdict; the tool is under details", ok.includes("Scaled to 120 mm tall. Spark: looks right") && ok.includes("v1") && !/[{}]/.test(ok) && okFull.includes("scale_to"), ok.replace(/\n/g, " | ").slice(0, 160));
    check("the verification render is shown inline", await page.locator("#ai-log .entry.ok img").count() === 1);
    await page.waitForFunction(() => (window.__lastLoadLabel || "") === "edit v1", { timeout: 15000 });
    check("the model reloads after the edit (viewer shows the edited version)", (await page.locator("#ai-version").innerText()).includes("v1"));
    check("the log row names the dimension the edit changed", /H [\d.]+ → 120\.0 mm/.test(await page.locator("#ai-log .entry.ok .chg").first().innerText().catch(() => "")),
      await page.locator("#ai-log .entry.ok .chg").first().innerText().catch(() => "none"));
    check("the changed W/H/D card is highlighted", await page.evaluate(() => document.querySelectorAll("#dim-cards .dim-card.changed").length) >= 1);
    check("the status line clears and Undo becomes available", (await page.locator("#ai-status").innerText()) === "" && !(await page.locator("#ai-undo").isDisabled()));

    await page.fill("#ai-input", "this should be rejected by the check");
    await page.click("#ai-send");
    await page.waitForFunction(() => document.querySelector("#ai-log .entry.bad"), { timeout: 60000 });
    const bad = await page.locator("#ai-log .entry.bad").first().innerText();
    check("a rejected edit says so, gives Spark's reason and leaves the model unchanged", bad.includes("Not applied") && bad.includes("does not match") && (await page.locator("#ai-version").innerText()).includes("v1"), bad.replace(/\n/g, " | ").slice(0, 160));

    await page.fill("#ai-input", "make it taste better");
    await page.click("#ai-send");
    await page.waitForFunction(() => document.querySelectorAll("#ai-log .entry.bad").length >= 2, { timeout: 60000 });
    check("an instruction no tool can do is reported, not faked", (await page.locator("#ai-log .entry.bad").first().innerText()).includes("No tool can do that") || (await page.locator("#ai-log .entry.bad").first().innerText()).includes("no successful tool call"));

    await page.fill("#ai-input", "add the text WORLDKIT on the base");
    await page.click("#ai-send");
    await page.waitForFunction(() => (window.__aiState().current === 2), { timeout: 60000 });
    await page.waitForFunction(() => (window.__lastLoadLabel || "") === "edit v2", { timeout: 15000 });
    check("a second applied edit becomes v2", true);

    await page.click("#ai-undo");
    await page.waitForFunction(() => window.__aiState().current === 1 && window.__lastLoadLabel === "edit v1", { timeout: 15000 });
    check("Undo steps back to v1 and reloads that version", (await page.locator("#ai-log").innerText()).includes("undo: back to v1"));

    const dl = await fetch(`http://127.0.0.1:${PORT}/edit/current.stl`);
    const bytes = Buffer.from(await dl.arrayBuffer());
    check("Download STL serves the CURRENT version as a binary STL", dl.ok && bytes.length > 1000 && /v1_mm\.stl/.test(dl.headers.get("content-disposition") || ""), `${bytes.length} bytes`);
    const dlPromise = page.waitForEvent("download", { timeout: 15000 });
    await page.click("#ai-download");
    const download = await dlPromise;
    check("the Download STL button triggers a file download named for the version", /v1_mm\.stl$/.test(download.suggestedFilename()), download.suggestedFilename());
    await page.screenshot({ path: path.join(require("os").tmpdir(), "worldkit_ai_edit_panel.png") });
    check("no page errors", errors.length === 0, errors.join(" | ").slice(0, 300));

    // demo mode (?demo=1) on an edit server: the served run opens (not the bundled demo), developer tools hidden, UI at 110% on wide screens
    const demo = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await demo.goto(`http://127.0.0.1:${PORT}/?demo=1`, { waitUntil: "domcontentloaded" });
    await demo.waitForFunction(() => window.__sceneReady === true && !window.__isLoading() && window.__aiState && window.__aiState(), null, { timeout: 30000 });
    const dm = await demo.evaluate(() => ({ cls: document.documentElement.classList.contains("demo-mode"), dev: getComputedStyle(document.querySelector(".devtools")).display,
      zoom: getComputedStyle(document.documentElement).zoom, label: window.__lastLoadLabel || "" }));
    check("?demo=1 opens the served run with developer tools hidden and the UI at 110%", dm.cls && dm.dev === "none" && dm.zoom === "1.1" && !/run_mug/.test(dm.label), JSON.stringify(dm));
    await demo.close();
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error("CRASH", e); process.exit(2); });
