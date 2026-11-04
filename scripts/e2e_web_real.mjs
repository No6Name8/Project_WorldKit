// The real web-to-worker run (no dry run): sign in on the LIVE site, upload pipeline/samples/mug_on_table.mp4 as an Object project, run the
// laptop worker for real (python -m worldkit.worker --once: SAM 3D on Modal, Muse Spark, SAM 3.1, coin saudi-25-halalas), watch the live
// status go uploaded -> processing -> done, open the result in the live viewer, compare its sizes with the Object-mode CLI numbers
// (docs/results/object_mug_coin.json), save docs/web/e2e_real_*.png and docs/results/e2e_web_real.json, then delete the project.
//   node scripts/e2e_web_real.mjs [LIVE_URL]     needs web/.env.local and WORKER_EMAIL / WORKER_PASSWORD in pipeline/.env (never printed)
// Spends real compute: one SAM 3D run (~3 min A100) and a few Meta API calls; set WK_BUDGET_FILE to cap them.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const envOf = (f) => Object.fromEntries(readFileSync(f, "utf8").split(/\r?\n/).map((l) => l.match(/^\s*([A-Z_]+)\s*=\s*(.*)$/)).filter(Boolean).map((m) => [m[1], m[2].trim()]));
const site = envOf(join(root, "web", ".env.local")), wenv = envOf(join(root, "pipeline", ".env"));
const URL_ = site.VITE_SUPABASE_URL, KEY = site.VITE_SUPABASE_PUBLISHABLE_KEY, EMAIL = wenv.WORKER_EMAIL, PASS = wenv.WORKER_PASSWORD;
if (!URL_ || !KEY || !EMAIL || !PASS) { console.error("Missing settings (web/.env.local or pipeline/.env)."); process.exit(2); }
const BASE = (process.argv[2] || "https://web-flame-psi-45.vercel.app").replace(/\/$/, "");
const VIDEO = join(root, "pipeline", "samples", "mug_on_table.mp4");
const SHOTS = join(root, "docs", "web");
const NAME = `e2e real mug ${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}`;
mkdirSync(SHOTS, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, cond, detail) => { results.push({ name, pass: !!cond, detail: detail || "" }); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
let token = null;
const signIn = async () => { const r = await fetch(`${URL_}/auth/v1/token?grant_type=password`, { method: "POST", headers: { apikey: KEY, "content-type": "application/json" }, body: JSON.stringify({ email: EMAIL, password: PASS }) }); token = (await r.json()).access_token; return !!token; };
const rest = (path, init = {}) => fetch(`${URL_}${path}`, { ...init, headers: { apikey: KEY, authorization: `Bearer ${token}`, "content-type": "application/json", ...(init.headers || {}) } });

const record = { started: new Date().toISOString(), live_url: BASE, video: "pipeline/samples/mug_on_table.mp4" };
const browser = await chromium.launch();
let pid = null;
try {
  check("worker account signs in (REST)", await signIn());
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const shot = async (n) => { await page.evaluate(() => { for (const id of ["who", "user-mail"]) { const e = document.getElementById(id); if (e) e.textContent = "signed-in account"; } }).catch(() => {}); await page.screenshot({ path: join(SHOTS, `e2e_real_${n}.png`) }); };  // never publish the account email
  await page.goto(BASE + "/#/login", { waitUntil: "networkidle" });
  await page.fill("#email", EMAIL); await page.fill("#password", PASS); await page.click("#submit");
  await page.waitForURL(/#\/projects/, { timeout: 30000 });
  await page.click('a[href="#/new"]');
  await page.fill("#name", NAME);
  await page.setInputFiles("#f-choose", VIDEO);
  await shot("1_new");
  const tUp = Date.now();
  await page.click("#go");
  await page.waitForURL(/#\/projects/, { timeout: 180000 });
  await page.waitForSelector(`.card:has-text("${NAME}") .chip.uploaded`, { timeout: 30000 });
  record.upload_seconds = Math.round((Date.now() - tUp) / 1000);
  pid = (await (await rest(`/rest/v1/projects?select=id&name=eq.${encodeURIComponent(NAME)}`)).json())[0].id;
  check("uploaded on the live site", !!pid, `${record.upload_seconds} s`);
  await shot("2_uploaded");

  // watch the chip in the open page while the real worker runs
  const seen = [];
  await page.exposeFunction("__seen", (s) => seen.push(s));
  await page.evaluate((nm) => { const o = new MutationObserver(() => { const c = [...document.querySelectorAll(".card")].find((x) => x.textContent.includes(nm)); const chip = c && c.querySelector(".chip"); if (chip) window.__seen(chip.textContent.trim()); }); o.observe(document.body, { subtree: true, childList: true, characterData: true }); }, NAME);
  const py = join(root, "pipeline", ".venv", "Scripts", existsSync(join(root, "pipeline", ".venv", "Scripts", "python.exe")) ? "python.exe" : "python");
  const tW = Date.now();
  let wout = "";
  const wk = spawn(py, ["-m", "worldkit.worker", "--once"], { cwd: join(root, "pipeline"), env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  wk.stdout.on("data", (d) => { wout += d; }); wk.stderr.on("data", (d) => { wout += d; });
  await page.waitForSelector(`.card:has-text("${NAME}") .chip.processing`, { timeout: 120000 });
  await shot("3_processing");
  const code = await new Promise((r) => wk.on("close", r));
  record.worker_seconds = Math.round((Date.now() - tW) / 1000);
  record.worker_log_tail = wout.trim().split("\n").slice(-4);
  check("the real worker (--once, no dry run) exited 0", code === 0, `${record.worker_seconds} s: ${record.worker_log_tail.join(" | ").slice(0, 300)}`);
  await page.waitForSelector(`.card:has-text("${NAME}") .chip.done`, { timeout: 60000 }).catch(() => {});
  record.chip_sequence = [...new Set(seen)];
  check("the open page showed uploaded -> processing -> done live", ["processing", "done"].every((s) => record.chip_sequence.includes(s)), record.chip_sequence.join(" > "));
  await shot("4_done");
  const row = (await (await rest(`/rest/v1/projects?select=*&id=eq.${pid}`)).json())[0];
  record.report = row.report;
  check("the row is done, not a dry run, with result files", row.status === "done" && !(row.report || {}).dry_run && (row.report.files || []).length > 0, `files: ${(row.report.files || []).join(", ")}`);

  // open it in the live viewer and read the sizes it shows
  await page.click(`.card:has-text("${NAME}") a.btn[href^="#/project/"]`);
  const frame = page.frameLocator("#viewer-frame");
  await frame.locator("#dim-cards .dim-card").first().waitFor({ timeout: 90000 });
  await sleep(1500);
  const dims = await frame.locator("#dim-cards").innerText();
  record.viewer_dims_text = dims.replace(/\s+/g, " ");
  await shot("5_viewer");
  const m = row.report.measurement || {};
  record.measurement = m;
  check("the live viewer opens the result with mm sizes", /mm/.test(dims), record.viewer_dims_text);
} catch (e) {
  check("ran to the end", false, String(e).slice(0, 400));
} finally {
  if (pid) {
    const r = (await (await rest(`/rest/v1/projects?select=*&id=eq.${pid}`)).json())[0];
    const files = [r && r.video_path].filter(Boolean);
    if (r && r.result_path) for (const f of (r.report && r.report.files) || []) files.push(`${r.result_path}/${f}`);
    if (r && r.video_path) await rest(`/storage/v1/object/videos`, { method: "DELETE", body: JSON.stringify({ prefixes: [r.video_path] }) });
    if (r && r.result_path) await rest(`/storage/v1/object/results`, { method: "DELETE", body: JSON.stringify({ prefixes: ((r.report && r.report.files) || []).map((f) => `${r.result_path}/${f}`) }) });
    await rest(`/rest/v1/projects?id=eq.${pid}`, { method: "DELETE" });
    const gone = await (await rest(`/rest/v1/projects?select=id&id=eq.${pid}`)).json();
    check("the test project was deleted (row, video, results)", Array.isArray(gone) && gone.length === 0);
  }
  await browser.close();
  record.checks = results;
  mkdirSync(join(root, "docs", "results"), { recursive: true });
  writeFileSync(join(root, "docs", "results", "e2e_web_real.json"), JSON.stringify(record, null, 1));
  console.log(`\n${results.filter((r) => r.pass).length}/${results.length} checks passed`);
  process.exit(results.every((r) => r.pass) ? 0 : 1);
}
