// End-to-end test against the REAL Supabase project (no mocks), with the real laptop worker in --dry-run (no GPU, no Meta calls):
// sign in in the browser, upload a real sample video with progress, watch the project go uploaded -> processing -> done live, open it in the viewer
// through signed URLs, delete it (video + results + row) and check that nothing is left.
//   node web/tests/e2e_real.mjs      needs web/.env.local (URL + publishable key) and WORKER_EMAIL / WORKER_PASSWORD in pipeline/.env
// Credentials are read from the files and never printed. Screenshots go to docs/web/.
import { createRequire } from "node:module";
import { spawn, spawnSync, execFileSync } from "node:child_process";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "..");
const root = join(web, "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const envOf = (f) => Object.fromEntries(readFileSync(f, "utf8").split(/\r?\n/).map((l) => l.match(/^\s*([A-Z_]+)\s*=\s*(.*)$/)).filter(Boolean).map((m) => [m[1], m[2].trim()]));
const site = envOf(join(web, ".env.local")), wenv = envOf(join(root, "pipeline", ".env"));
const URL_ = site.VITE_SUPABASE_URL, KEY = site.VITE_SUPABASE_PUBLISHABLE_KEY, EMAIL = wenv.WORKER_EMAIL, PASS = wenv.WORKER_PASSWORD;
if (!URL_ || !KEY || !EMAIL || !PASS) { console.error("Missing settings (web/.env.local or pipeline/.env)."); process.exit(2); }
const VIDEO = join(root, "pipeline", "samples", "mug_on_table.mp4");
const SHOTS = join(root, "docs", "web");
mkdirSync(SHOTS, { recursive: true });
const PORT = 4175, BASE = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, cond, detail) => { results.push({ name, pass: !!cond }); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
execFileSync(npm, ["run", "build"], { cwd: web, stdio: "ignore", shell: process.platform === "win32" });
const srv = spawn(process.execPath, [join(web, "node_modules", "vite", "bin", "vite.js"), "preview", "--host", "127.0.0.1", "--port", String(PORT), "--strictPort"], { cwd: web, stdio: "ignore" });

// REST helpers as the signed-in user (RLS applies), used only to verify state and to set one status by hand for a screenshot
let token = null;
async function signIn() {
  const r = await fetch(`${URL_}/auth/v1/token?grant_type=password`, { method: "POST", headers: { apikey: KEY, "content-type": "application/json" }, body: JSON.stringify({ email: EMAIL, password: PASS }) });
  token = (await r.json()).access_token;
  return !!token;
}
const rest = (path, init = {}) => fetch(`${URL_}${path}`, { ...init, headers: { apikey: KEY, authorization: `Bearer ${token}`, "content-type": "application/json", ...(init.headers || {}) } });

(async () => {
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE)).ok) break; } catch (e) { /* starting */ } await sleep(300); }
  const browser = await chromium.launch();
  const errors = [];
  let pid = null;
  try {
    check("worker account can sign in (REST)", await signIn());
    const before = await (await rest("/rest/v1/projects?select=id")).json();
    check("the projects table is readable with the user's token (migration applied)", Array.isArray(before), `existing rows: ${before.length}`);

    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/i.test(m.text())) errors.push(m.text()); });
    const shot = async (name, tag = "390") => { await page.evaluate(() => { for (const id of ["who", "user-mail"]) { const e = document.getElementById(id); if (e) e.textContent = "signed-in account"; } }).catch(() => {}); await page.screenshot({ path: join(SHOTS, `${name}_${tag}.png`) }); };  // never publish the account email

    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    await shot("home");
    await page.goto(BASE + "/#/projects");
    await page.waitForURL(/#\/login/);
    await page.fill("#email", EMAIL); await page.fill("#password", "definitely-wrong-1"); await page.click("#submit");
    await page.waitForSelector(".msg.err");
    check("a wrong password shows the real server reason", /invalid/i.test(await page.locator(".msg.err").innerText()), await page.locator(".msg.err").innerText());
    await shot("login");
    await page.fill("#email", EMAIL); await page.fill("#password", PASS); await page.click("#submit");
    try { await page.waitForURL(/#\/projects/, { timeout: 20000 }); } catch (e) { console.log("sign-in page text:", (await page.locator("main").innerText()).replace(/\s+/g, " ").slice(0, 300)); throw e; }
    await page.waitForSelector(".empty, .card");
    check("real sign-in lands on the projects list", true);

    // upload the real sample video through the page
    await page.click('a[href="#/new"]');
    await page.fill("#name", "e2e test mug");
    await page.setInputFiles("#f-choose", VIDEO);
    await shot("new");
    const t0 = Date.now();
    let maxPct = 0;
    const poll = setInterval(async () => { try { const v = await page.locator("#bar").getAttribute("aria-valuenow", { timeout: 200 }); maxPct = Math.max(maxPct, +v || 0); } catch (e) { /* page moved on */ } }, 100);
    await page.click("#go");
    await page.waitForURL(/#\/projects/, { timeout: 120000 });
    clearInterval(poll);
    check("upload of the 3.4 MB sample finished and returned to the list", true, `${((Date.now() - t0) / 1000).toFixed(1)} s, progress bar reached ${maxPct}%`);
    await page.waitForSelector('.card:has-text("e2e test mug")');
    const rows = await (await rest("/rest/v1/projects?select=*&name=eq.e2e%20test%20mug")).json();
    check("exactly one row was created, status uploaded, in the user's folder", rows.length === 1 && rows[0].status === "uploaded" && rows[0].video_path.startsWith(rows[0].user_id + "/" + rows[0].id + "/"), rows[0] && rows[0].status);
    pid = rows[0].id;
    const vid = await rest(`/storage/v1/object/info/authenticated/videos/${rows[0].video_path}`);
    check("the video is in the private videos bucket", vid.ok, `HTTP ${vid.status}`);
    const anon = await fetch(`${URL_}/storage/v1/object/public/videos/${rows[0].video_path}`);
    check("the video is NOT readable without signing in (bucket is private)", !anon.ok, `HTTP ${anon.status}`);
    const anonRows = await (await fetch(`${URL_}/rest/v1/projects?select=id`, { headers: { apikey: KEY } })).json();
    check("an anonymous caller sees no projects (RLS)", Array.isArray(anonRows) && anonRows.length === 0);
    await sleep(1500);
    check("the live channel connected (Realtime)", (await page.locator("#live").innerText()).includes("live"), await page.locator("#live").innerText());
    await shot("projects_uploaded");

    // a "processing" screenshot: the dry run is too fast to catch, so set the status by hand for the picture and put it back
    await rest(`/rest/v1/projects?id=eq.${pid}`, { method: "PATCH", body: JSON.stringify({ status: "processing" }) });
    await page.waitForSelector('.card .chip.processing', { timeout: 15000 });
    check("a status change made elsewhere appears in the open page without reloading", true);
    await shot("projects_processing");
    await rest(`/rest/v1/projects?id=eq.${pid}`, { method: "PATCH", body: JSON.stringify({ status: "uploaded" }) });
    await page.waitForSelector('.card .chip.uploaded', { timeout: 15000 });

    // the real worker, dry run, claims it and publishes the demo result
    const seen = [];
    await page.exposeFunction("__seen", (s) => seen.push(s));
    await page.evaluate(() => { const o = new MutationObserver(() => { const c = document.querySelector(".card .chip"); if (c) window.__seen(c.textContent.trim()); }); o.observe(document.body, { subtree: true, childList: true, characterData: true }); });
    const py = join(root, "pipeline", ".venv", "Scripts", "python.exe");
    const w = spawnSync(py, ["-m", "worldkit.worker", "--once", "--dry-run"], { cwd: join(root, "pipeline"), encoding: "utf8", timeout: 180000 });
    const wout = (w.stdout || "") + (w.stderr || "");
    check("the laptop worker (--once --dry-run) exited 0", w.status === 0, wout.trim().split("\n").slice(-3).join(" | ").replace(/\s+/g, " ").slice(0, 300));
    await page.waitForSelector('.card .chip.done', { timeout: 20000 });
    const order = [...new Set(seen)];
    check("the open page showed the project reach done", order.includes("done"), `chip texts seen: ${order.join(" > ")}`);
    const after = (await (await rest(`/rest/v1/projects?select=*&id=eq.${pid}`)).json())[0];
    check("the row is done with result files recorded", after.status === "done" && after.result_path && after.report && Array.isArray(after.report.files) && after.report.files.length > 0, `files: ${after.report && after.report.files && after.report.files.length}`);
    await shot("projects_done");

    // open in the viewer through signed URLs
    await page.click('a.btn[href^="#/project/"]');
    const frame = page.frameLocator("#viewer-frame");
    await frame.locator("#obj-name").filter({ hasText: "e2e test mug" }).waitFor({ timeout: 60000 });
    const dims = await frame.locator("#obj-dims").innerText();
    check("the done project opens in the viewer with its name and millimetre sizes", /mm/.test(dims), dims.replace(/\s+/g, " "));
    const fr = page.frames().find((f) => f.url().includes("/viewer/"));
    check("the STL download and SAM part overlay work from signed URLs", await fr.evaluate(() => !document.getElementById("btn-print-obj").disabled && window.__partsUi().enabled));
    await sleep(1500);
    await shot("viewer");
    await frame.locator("#btn-back").click();
    await page.waitForURL(/#\/projects/);

    // desktop screenshots with the same session
    const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const p2 = await ctx2.newPage();
    p2.on("pageerror", (e) => errors.push(String(e)));
    const s2 = async (name) => { await p2.evaluate(() => { for (const id of ["who", "user-mail"]) { const e = document.getElementById(id); if (e) e.textContent = "signed-in account"; } }).catch(() => {}); await p2.screenshot({ path: join(SHOTS, `${name}_1440.png`) }); };
    await p2.goto(BASE + "/", { waitUntil: "networkidle" }); await s2("home");
    await p2.goto(BASE + "/#/login"); await p2.waitForSelector("#email"); await s2("login");
    await p2.fill("#email", EMAIL); await p2.fill("#password", PASS); await p2.click("#submit");
    await p2.waitForURL(/#\/projects/); await p2.waitForSelector(".card"); await sleep(1200); await s2("projects_done");
    await p2.click('a[href="#/new"]'); await s2("new");
    await p2.goto(BASE + "/#/projects"); await p2.click('a.btn[href^="#/project/"]');
    await p2.frameLocator("#viewer-frame").locator("#obj-name").filter({ hasText: "e2e test mug" }).waitFor({ timeout: 60000 });
    await sleep(2000); await s2("viewer");
    await ctx2.close();

    // delete through the UI: row, video and results must all be gone
    page.once("dialog", (d) => d.accept());
    await page.locator(`[data-del="${pid}"]`).click();
    await page.waitForSelector(`[data-del="${pid}"]`, { state: "detached", timeout: 15000 });
    await sleep(1000);
    const gone = await (await rest(`/rest/v1/projects?select=id&id=eq.${pid}`)).json();
    check("delete removed the row", gone.length === 0);
    const list = async (bucket, prefix) => (await (await rest(`/storage/v1/object/list/${bucket}`, { method: "POST", body: JSON.stringify({ prefix, limit: 100 }) })).json());
    const uid = rows[0].user_id;
    const left = [...(await list("videos", `${uid}/${pid}`)), ...(await list("results", `${uid}/${pid}`)), ...(await list("results", `${uid}/${pid}/parts`))].filter((o) => o.id);
    check("delete removed the video and result files from storage", left.length === 0, `left: ${left.map((o) => o.name).join(",")}`);
    pid = null;
    check("no page or console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
    await ctx.close();
  } catch (e) {
    check("test ran to the end", false, String(e).slice(0, 500));
  } finally {
    if (pid && token) { try { await rest(`/rest/v1/projects?id=eq.${pid}`, { method: "DELETE" }); console.log("cleanup: removed the test row (files may remain)"); } catch (e) { /* best effort */ } }
    await browser.close();
    srv.kill();
  }
  const failed = results.filter((r) => !r.pass).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})();
