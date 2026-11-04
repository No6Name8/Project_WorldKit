// Checks a DEPLOYED WorldKit site at 390 and 1440 px; run it before submitting.
//   node scripts/check_live.mjs [URL]          (default https://web-flame-psi-45.vercel.app)
//   npm --prefix web run check:live [-- URL]
// Checks: home with the demo viewer card, "Open demo viewer", sign-in reaches the projects list, Help page, PWA manifest and its
// icons, service worker, an unknown path falls back to the app, no page/console errors, no failed same-origin requests.
// Sign-in uses WORKER_EMAIL / WORKER_PASSWORD from the environment or from pipeline/.env (gitignored); they are never printed.
// Screenshots: docs/web/live_<page>_<width>.png. Needs playwright from viewer/tests (cd viewer/tests; npm install).
import { createRequire } from "node:module";
import { existsSync, readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const envFile = join(root, "pipeline", ".env");
const fileEnv = existsSync(envFile)
  ? Object.fromEntries(readFileSync(envFile, "utf8").split(/\r?\n/).map((l) => l.match(/^\s*([A-Z_]+)\s*=\s*(.*)$/)).filter(Boolean).map((m) => [m[1], m[2].trim()]))
  : {};
const cred = (k) => process.env[k] || fileEnv[k] || "";
const BASE = (process.argv[2] || "https://web-flame-psi-45.vercel.app").replace(/\/$/, "");
const SHOTS = join(root, "docs", "web");
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, cond, detail) => { results.push(!!cond); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const errors = [], failed = [];
  try {
    for (const [tag, vp] of [["390", { width: 390, height: 844 }], ["1440", { width: 1440, height: 900 }]]) {
      const ctx = await browser.newContext({ viewport: vp });
      const page = await ctx.newPage();
      page.on("pageerror", (e) => errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/i.test(m.text())) errors.push(`${tag}: ${m.text()}`); });
      page.on("response", (r) => { if (r.url().startsWith(BASE) && r.status() >= 400) failed.push(`${tag}: ${r.status()} ${r.url().slice(BASE.length)}`); });
      const shot = async (n) => { await page.evaluate(() => { for (const id of ["who", "user-mail"]) { const e = document.getElementById(id); if (e) e.textContent = "signed-in account"; } }).catch(() => {}); await page.screenshot({ path: join(SHOTS, `live_${n}_${tag}.png`) }); };  // never publish the account email

      // home + demo viewer card (a live preview of the demo mug whose size comes from the viewer itself)
      await page.goto(BASE + "/", { waitUntil: "networkidle" });
      check(`[${tag}] home loads`, (await page.locator("main").innerText()).includes("Film it. Print it."));
      await page.locator("#demo-frame").scrollIntoViewIfNeeded();
      const size = await page.waitForFunction(() => /mm/.test(document.getElementById("demo-size")?.textContent || ""), null, { timeout: 45000 })
        .then(() => page.locator("#demo-size").innerText()).catch(() => "");
      check(`[${tag}] home shows the demo viewer card with a size in mm`, /mm/.test(size), size);
      await page.evaluate(() => window.scrollTo(0, 0));
      await sleep(800);
      await shot("home");

      // Open demo viewer
      await Promise.all([page.waitForNavigation(), page.click('a[href="/viewer/index.html?demo=1"]')]);
      await page.locator("#obj-name").waitFor({ timeout: 60000 });
      await page.waitForFunction(() => /mm/.test(document.getElementById("obj-dims").innerText), null, { timeout: 30000 }).catch(() => {});
      const dims = await page.locator("#obj-dims").innerText();
      check(`[${tag}] "Open demo viewer" opens the viewer with the demo model`, /mm/.test(dims), dims.replace(/\s+/g, " "));
      await sleep(2000);
      await shot("demo_viewer");

      // Help page
      await page.goto(BASE + "/#/help", { waitUntil: "networkidle" });
      const helpOk = await page.locator("h1", { hasText: "Help" }).isVisible().catch(() => false);
      check(`[${tag}] Help page renders`, helpOk);
      await shot("help");

      // sign in
      const email = cred("WORKER_EMAIL"), password = cred("WORKER_PASSWORD");
      if (!email || !password) {
        check(`[${tag}] sign-in reaches the projects list`, false, "WORKER_EMAIL / WORKER_PASSWORD not set");
      } else {
        await page.goto(BASE + "/#/login", { waitUntil: "networkidle" });
        await page.fill("#email", email); await page.fill("#password", password); await page.click("#submit");
        let ok = true;
        try { await page.waitForURL(/#\/projects/, { timeout: 20000 }); await page.waitForSelector(".empty, .card", { timeout: 20000 }); } catch (e) { ok = false; }
        check(`[${tag}] sign-in reaches the projects list`, ok);
        await sleep(1200);
        await shot("projects");
      }

      // PWA: manifest + icons, service worker
      if (tag === "1440") {
        const man = await page.evaluate(async () => {
          const r = await fetch("/manifest.webmanifest", { cache: "no-store" });
          const m = r.ok ? await r.json() : null;
          const icons = m ? await Promise.all((m.icons || []).map((i) => fetch(i.src, { cache: "no-store" }).then((x) => x.ok))) : [];
          return { status: r.status, name: m?.name, display: m?.display, icons: icons.length, iconsOk: icons.every(Boolean) };
        });
        check("PWA manifest loads with its icons", man.status === 200 && man.name && man.icons > 0 && man.iconsOk, JSON.stringify(man));
        await page.goto(BASE + "/", { waitUntil: "networkidle" });
        const sw = await page.evaluate(async () => { await navigator.serviceWorker.ready; const r = await navigator.serviceWorker.getRegistration(); return r?.active?.scriptURL || null; }).catch(() => null);
        check("the service worker registers and activates", !!sw, sw || "");
      }

      // unknown path falls back to the app
      const resp = await page.goto(BASE + "/this/path/does-not-exist", { waitUntil: "networkidle" });
      const fallback = resp.status() === 200 && (await page.locator("#brand").isVisible().catch(() => false));
      check(`[${tag}] an unknown path falls back to the app`, fallback, `status ${resp.status()}`);
      await ctx.close();
    }
    check("no page or console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
    check("no failed same-origin requests", failed.length === 0, failed.slice(0, 5).join(" | "));
  } catch (e) {
    check("check ran to the end", false, String(e).slice(0, 400));
  } finally {
    await browser.close();
  }
  console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed (${BASE})`);
  process.exit(results.every(Boolean) ? 0 : 1);
})();
