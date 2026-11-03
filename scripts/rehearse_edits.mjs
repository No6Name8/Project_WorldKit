// Rehearses the demo edits through the viewer's AI Edit panel (real Muse Wonder and Muse Spark) and records what happened.
//   1. a COPY of a run with a clean edit history, served:  python -m worldkit.serve runs/rehearse --port 5070   (WK_BUDGET_FILE caps the calls)
//   2. node scripts/rehearse_edits.mjs [URL] [--runs 2] [--only a,c]
// Each instruction is sent N times; after each one that was applied, Undo returns to v0. Writes docs/demo_edits.json
// (client-side seconds, the panel's row, the server's tool calls, Spark's reason, and the print-mesh size before/after).
import { createRequire } from "node:module";
import { writeFileSync, existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const args = process.argv.slice(2);
const URL = (args.find((a) => a.startsWith("http")) || "http://127.0.0.1:5070").replace(/\/$/, "");
const RUNS = Number((args[args.indexOf("--runs") + 1]) || 2) || 2;
const ONLY = args.includes("--only") ? args[args.indexOf("--only") + 1].split(",") : null;
const EDITS = [
  ["a", "make the handle 2 mm thicker", { sam: true }],
  ["b", "put a 5 mm hole through the handle side", {}],
  ["c", "make the handle thinner", {}],
  ["d", "make it exactly 12 cm tall", {}],
  ["e", "add the text WORLDKIT on the base", {}],
];
const OUT = join(root, "docs", "demo_edits.json");
const results = existsSync(OUT) && ONLY ? JSON.parse(readFileSync(OUT, "utf8")) : [];

const b = await chromium.launch();
const page = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.goto(URL + "/", { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => window.__sceneReady === true && window.__aiState && window.__aiState(), null, { timeout: 60000 });
await page.click("#tab-ai");
const state = () => page.evaluate(async () => (await (await fetch("/edit/state")).json()));
for (const [id, text, opt] of EDITS) {
  if (ONLY && !ONLY.includes(id)) continue;
  for (let run = 1; run <= RUNS; run++) {
    const st0 = await state();
    if (opt.sam && !(await page.evaluate(() => window.__partsUi().on))) await page.click("#btn-parts");
    const n0 = await page.locator("#ai-log .entry.ok, #ai-log .entry.bad").count();
    const stages = new Set();
    await page.fill("#ai-input", text);
    const t0 = Date.now();
    await page.click("#ai-send");
    const watch = setInterval(async () => { try { const s = await page.locator("#ai-status .prog .st.active").first().innerText({ timeout: 200 }); stages.add(s.split(" ")[0] + (s.includes("Spark") ? " Spark" : "")); } catch (e) { /* between stages */ } }, 300);
    await page.waitForFunction((n) => document.querySelectorAll("#ai-log .entry.ok, #ai-log .entry.bad").length > n && !document.getElementById("ai-send").disabled, n0, { timeout: 300000 });
    clearInterval(watch);
    const secs = (Date.now() - t0) / 1000;
    const row = await page.locator("#ai-log .entry.ok, #ai-log .entry.bad").first().evaluate((e) => ({ ok: e.classList.contains("ok"), text: e.querySelector(".res").textContent.trim(), detail: (e.querySelector("pre") || {}).textContent || "" }));
    const st1 = await state();
    const cur1 = st1.versions.find((v) => v.version === st1.current), cur0 = st0.versions.find((v) => v.version === st0.current);
    const rec = { id, instruction: text, run, applied: row.ok, seconds_client: Math.round(secs), row: row.text, detail: row.detail, stages_seen: [...stages],
                  version: row.ok ? st1.current : null, server_seconds: row.ok ? cur1.seconds : null, size_before_mm: cur0.size_mm, size_after_mm: row.ok ? cur1.size_mm : null };
    results.push(rec);
    console.log(`${id}${run} ${row.ok ? "APPLIED" : "NOT APPLIED"} ${Math.round(secs)} s: ${row.text.slice(0, 140)}`);
    if (row.ok) { await page.click("#ai-undo"); await page.waitForFunction(() => window.__aiState().current === 0 && !document.getElementById("ai-send").disabled, null, { timeout: 60000 }); }
    writeFileSync(OUT, JSON.stringify(results, null, 1));
  }
}
await b.close();
