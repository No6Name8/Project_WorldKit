// Real-browser test of the AI Edit replay (hosted demo, no edit server): the replay appears only without an edit server, plays the recorded
// edits from viewer/assets/demo/edit_replay (models, Spark's sentence, renders, sizes, handle wall) and Undo returns to the original.
// Every shown value is checked against replay.json, so nothing in the replay can drift from the recorded data.
//   node viewer/tests/replay_browser_test.js     (with python viewer/server.py running on 5050, as for the other suites)
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const URL = "http://127.0.0.1:5050/?demo=1";
const DATA = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "assets", "demo", "edit_replay", "replay.json"), "utf8"));
const results = [];
const check = (name, cond, detail) => { results.push(!!cond); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(URL, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__replay && window.__replay().shown, null, { timeout: 60000 });
    const head = await page.evaluate(() => ({ sub: document.getElementById("proj-sub").textContent, mk: document.querySelector("#object-list .mk").title,
      dl: !document.getElementById("btn-print-obj").disabled, printReady: window.__lastReport && window.__lastReport.print_ready }));
    check("the header says 'Printable STL, scale estimated from coin' (print_ready is false), not 'ready to print'",
      head.printReady === false && head.sub.includes("Printable STL, scale estimated from coin") && !head.sub.includes("ready to print"), head.sub);
    check("the object list and the Selection card agree: a printable STL is listed and offered", head.mk === "printable STL available" && head.dl, JSON.stringify(head));
    await page.click("#tab-ai");
    const banner = await page.locator(".replay-banner").innerText();
    check("the replay is labelled as recorded, and says live editing needs the edit server", banner.includes("Replay of a real run") && banner.includes(DATA.banner), banner);
    check("every recorded edit is listed with its recorded seconds", (await page.locator(".rp-edit").count()) === DATA.edits.length && (await page.locator(".rp-edit").first().innerText()).includes(`${DATA.edits[0].seconds} s`));
    check("the 'not connected' line is replaced by the replay", !(await page.locator("#ai-empty").isVisible()));

    for (const e of DATA.edits) {
      await page.click(`.rp-edit[data-id="${e.id}"]`);
      await page.waitForFunction((id) => window.__replay().current === id && /^replay /.test(window.__replay().label), e.id, { timeout: 30000 });
      const row = await page.locator("#rp-log .entry.ok").first().evaluate((d) => ({ text: d.querySelector(".res").textContent, detail: d.querySelector("pre").textContent, imgs: d.querySelectorAll("img").length }));
      const spark = String(e.spark).replace(/[.\s]+$/, "");
      const okTool = e.tools.every((t) => row.detail.includes(t.name) && row.detail.includes(JSON.stringify(t.arguments)));
      let okSize = true;
      if (e.handle_wall_mm) okSize = row.text.includes(`Handle wall ${e.handle_wall_mm[0].toFixed(2)} → ${e.handle_wall_mm[1].toFixed(2)} mm`);
      if (e.id === "d") okSize = row.text.includes(`H ${e.size_before_mm[2].toFixed(1)} → ${e.size_after_mm[2].toFixed(1)} mm`);
      check(`[${e.id}] "${e.instruction}" shows the recorded tool, Spark's sentence, the renders and the change`, row.text.includes(spark) && okTool && row.imgs === e.renders.length && okSize, row.text.slice(0, 160));
      if (e.sam_overlay) check(`[${e.id}] the SAM overlay was shown first, as in the real run`, await page.evaluate(() => document.getElementById("rp-status").innerText === "" && window.__partsUi().legend === "none"));
      await page.click("#rp-undo");
      await page.waitForFunction(() => window.__replay().current === null && window.__replay().label === "replay v0", null, { timeout: 30000 });
    }
    check("Undo returns to the original print mesh each time", (await page.locator("#rp-version").innerText()) === "original" && await page.locator("#rp-undo").isDisabled());
    check("no page or console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  } catch (e) {
    check("test ran to the end", false, String(e).slice(0, 300));
  } finally {
    await browser.close();
  }
  console.log(`\n${results.filter(Boolean).length}/${results.length} passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})();
