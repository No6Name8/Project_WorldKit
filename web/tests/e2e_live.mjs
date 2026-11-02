// Checks the DEPLOYED site: home loads, the demo viewer opens, sign-in with the worker account reaches the projects list.
//   node web/tests/e2e_live.mjs [URL]     (default https://web-flame-psi-45.vercel.app). Credentials come from pipeline/.env, never printed.
import { createRequire } from "node:module";
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const envOf = (f) => Object.fromEntries(readFileSync(f, "utf8").split(/\r?\n/).map((l) => l.match(/^\s*([A-Z_]+)\s*=\s*(.*)$/)).filter(Boolean).map((m) => [m[1], m[2].trim()]));
const w = envOf(join(root, "pipeline", ".env"));
const BASE = process.argv[2] || "https://web-flame-psi-45.vercel.app";
const SHOTS = join(root, "docs", "web");
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, cond, detail) => { results.push(!!cond); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  try {
    for (const [tag, vp] of [["390", { width: 390, height: 844 }], ["1440", { width: 1440, height: 900 }]]) {
      const ctx = await browser.newContext({ viewport: vp });
      const page = await ctx.newPage();
      page.on("pageerror", (e) => errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/i.test(m.text())) errors.push(`${tag}: ${m.text()}`); });
      const shot = (n) => page.screenshot({ path: join(SHOTS, `live_${n}_${tag}.png`) });

      await page.goto(BASE + "/", { waitUntil: "networkidle" });
      check(`[${tag}] home loads on the live URL`, (await page.locator("main").innerText()).includes("Film it. Print it."));
      await shot("home");

      // demo viewer
      const [resp] = await Promise.all([page.waitForNavigation(), page.click('a[href="/viewer/index.html?demo=1"]')]);
      await page.locator("#obj-name").waitFor({ timeout: 60000 });
      await page.waitForFunction(() => /mm/.test(document.getElementById("obj-dims").innerText), null, { timeout: 30000 }).catch(() => {});
      const dims = await page.locator("#obj-dims").innerText();
      check(`[${tag}] the demo viewer opens and shows the demo model`, /mm/.test(dims), dims.replace(/\s+/g, " "));
      await sleep(2000);
      await shot("demo_viewer");

      // sign in
      await page.goto(BASE + "/#/login");
      await page.fill("#email", w.WORKER_EMAIL); await page.fill("#password", w.WORKER_PASSWORD); await page.click("#submit");
      let ok = true;
      try { await page.waitForURL(/#\/projects/, { timeout: 20000 }); await page.waitForSelector(".empty, .card", { timeout: 20000 }); } catch (e) { ok = false; console.log("page text:", (await page.locator("main").innerText()).replace(/\s+/g, " ").slice(0, 200)); }
      check(`[${tag}] sign-in with the real account reaches the projects list`, ok);
      await sleep(1200);
      await shot("projects");
      if (tag === "1440") {
        const live = await page.evaluate(async () => { const r = await navigator.serviceWorker.getRegistration(); return !!r; });
        check("the service worker registers on the live site", live);
      }
      await ctx.close();
    }
    check("no page or console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  } catch (e) {
    check("test ran to the end", false, String(e).slice(0, 400));
  } finally {
    await browser.close();
  }
  console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})();
