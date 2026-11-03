// Browser test of the web app against a MOCKED Supabase (no real project needed): home, sign in, projects in every status, empty and error
// states, upload with progress, opening a done project in the viewer through signed URLs, and the PWA installability criteria.
//   node web/tests/e2e_mock.mjs        (builds web/dist-mock with a fake Supabase URL, serves it on port 4174, needs viewer/tests/node_modules)
import { createRequire } from "node:module";
import { spawn, execFileSync } from "node:child_process";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "..");
const root = join(web, "..");
const require = createRequire(join(root, "viewer", "tests", "package.json"));
const { chromium } = require("playwright");
const MOCK = "https://mock.supabase.test";
const UID = "00000000-0000-4000-8000-000000000001";
const demo = join(root, "viewer", "assets", "demo", "run_mug");
const SHOTS = process.env.SHOTS_DIR || null;
const THEME = process.env.WK_THEME === "light" ? "light" : null;  // WK_THEME=light: light-theme screenshots, named *_light
const results = [];
const check = (name, cond, detail) => { results.push({ name, pass: !!cond }); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" };

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
execFileSync(npm, ["run", "prepare-viewer"], { cwd: web, stdio: "ignore", shell: process.platform === "win32" });
execFileSync(process.execPath, [join(web, "node_modules", "vite", "bin", "vite.js"), "build", "--outDir", "dist-mock"], {
  cwd: web, stdio: "ignore", env: { ...process.env, VITE_SUPABASE_URL: MOCK, VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_mock" },
});
const srv = spawn(process.execPath, [join(web, "node_modules", "vite", "bin", "vite.js"), "preview", "--outDir", "dist-mock", "--host", "127.0.0.1", "--port", "4174", "--strictPort"], { cwd: web, stdio: "ignore" });
const BASE = "http://127.0.0.1:4174";

const projects = [
  { id: "p-done", user_id: UID, name: "my mug", mode: "object", status: "done", video_path: `${UID}/p-done/video.mp4`, result_path: `${UID}/p-done`, report: { kind: "object", files: ["model.glb", "report.json", "model_print.stl", "parts/parts.json"] }, error: null, created_at: "2026-11-02T10:00:00Z" },
  { id: "p-proc", user_id: UID, name: "desk with coin", mode: "map", status: "processing", video_path: `${UID}/p-proc/video.mp4`, result_path: null, report: null, error: null, created_at: "2026-11-02T09:00:00Z" },
  { id: "p-up", user_id: UID, name: "second mug", mode: "object", status: "uploaded", video_path: `${UID}/p-up/video.mp4`, result_path: null, report: null, error: null, created_at: "2026-11-02T08:00:00Z" },
  { id: "p-fail", user_id: UID, name: "dark room", mode: "object", status: "failed", video_path: `${UID}/p-fail/video.mp4`, result_path: null, report: null, error: "The pipeline stopped: SAM 3.1 could not find the mug in any frame.", created_at: "2026-11-01T08:00:00Z" },
];

async function mockSupabase(page, state) {
  await page.route(`${MOCK}/**`, async (route) => {
    const req = route.request(), url = new URL(req.url()), m = req.method(), p = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, headers: { ...cors, "content-type": "application/json" }, body: JSON.stringify(body) });
    if (m === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    if (p === "/auth/v1/token") {
      const body = JSON.parse(req.postData() || "{}");
      if (body.password === "wrongpw") return json({ code: 400, error_code: "invalid_credentials", msg: "Invalid login credentials" }, 400);
      return json({ access_token: "tok", token_type: "bearer", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, refresh_token: "r", user: { id: UID, email: body.email, aud: "authenticated", role: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-01T00:00:00Z" } });
    }
    if (p === "/auth/v1/user") return json({ id: UID, email: "test@worldkit.test", aud: "authenticated" });
    if (p === "/auth/v1/logout") return route.fulfill({ status: 204, headers: cors });
    if (p === "/rest/v1/projects") {
      if (m === "GET") {
        if (state.delayMs) await sleep(state.delayMs);
        if (state.listError) return json({ message: "boom" }, 500);
        const id = url.searchParams.get("id");
        const rows = state.projects.filter((r) => !id || id === `eq.${r.id}`);
        return json(id ? rows[0] || null : rows, 200);
      }
      if (m === "POST") { state.inserted = JSON.parse(req.postData()); return route.fulfill({ status: 201, headers: cors, body: "" }); }
      if (m === "DELETE") { state.deleted = url.searchParams.get("id"); return route.fulfill({ status: 204, headers: cors }); }
    }
    if (p.startsWith("/storage/v1/object/videos/") && m === "POST") { state.uploaded = { path: p.split("/videos/")[1], auth: req.headers()["authorization"], size: (req.postDataBuffer() || Buffer.alloc(0)).length }; return json({ Key: "videos/x" }); }
    if (p === "/storage/v1/object/sign/results" && m === "POST") {
      const body = JSON.parse(req.postData());
      return json(body.paths.map((path) => ({ error: null, path, signedURL: `/object/sign/results/${path}?token=t` })));
    }
    if (p.startsWith("/storage/v1/object/sign/results/")) {
      const rel = p.split(`/${UID}/p-done/`)[1];
      const f = join(demo, rel);
      return existsSync(f) ? route.fulfill({ status: 200, headers: cors, body: readFileSync(f) }) : route.fulfill({ status: 404, headers: cors, body: "" });
    }
    if (p.startsWith("/storage/v1/object/") && m === "DELETE") return json([]);
    return json({}, 200);
  });
  // the realtime websocket is not mocked: the page must fall back to polling
}

(async () => {
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE)).ok) break; } catch (e) { /* starting */ } await sleep(300); }
  const browser = await chromium.launch();
  const errors = [];
  try {
    for (const [tag, vp] of [["390", { width: 390, height: 844 }], ["1440", { width: 1440, height: 900 }]]) {
      const ctx = await browser.newContext({ viewport: vp, acceptDownloads: true });
      if (THEME) await ctx.addInitScript((t) => { try { localStorage.setItem("wk-theme", t); } catch (e) { /* ignore */ } }, THEME);
      const page = await ctx.newPage();
      page.on("pageerror", (e) => errors.push(`${tag}: ${e}`));
      page.on("console", (m) => { if (m.type() === "error" && !/WebSocket|realtime|net::ERR|Failed to load resource/i.test(m.text())) errors.push(`${tag}: ${m.text()}`); });
      const state = { projects: JSON.parse(JSON.stringify(projects)) };
      await mockSupabase(page, state);
      const shot = async (name) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await sleep(450); await page.screenshot({ path: join(SHOTS, `${name}_${tag}${THEME ? "_" + THEME : ""}.png`) }); } };  // 450 ms: entry and reveal motion settles

      // home
      await page.goto(BASE + "/", { waitUntil: "networkidle" });
      check(`[${tag}] home shows the wordmark, tagline, sign in and the demo viewer link`, (await page.locator("main").innerText()).includes("Film it. Print it.") && await page.locator('a[href="#/login"]').count() >= 1 && await page.locator('a[href="/viewer/index.html?demo=1"]').count() === 1);
      check(`[${tag}] home says the processing happens on the laptop`, (await page.locator("main").innerText()).includes("processed on your own laptop"));
      await shot("home");

      // login: validation, wrong password, success
      await page.goto(BASE + "/#/projects");
      await page.waitForURL(/#\/login/);
      await page.fill("#email", "nope"); await page.fill("#password", "123456"); await page.click("#submit");
      check(`[${tag}] login rejects a bad email with a sentence`, (await page.locator(".msg.err").innerText()).includes("valid email"));
      await page.fill("#email", "test@worldkit.test"); await page.fill("#password", "wrongpw"); await page.click("#submit");
      await page.waitForFunction(() => (document.querySelector(".msg.err") || { innerText: "" }).innerText.includes("Invalid"));
      check(`[${tag}] login shows the server's reason for a wrong password`, (await page.locator(".msg.err").innerText()).includes("Invalid login credentials"));
      await shot("login");
      await page.fill("#email", "test@worldkit.test"); await page.fill("#password", "secret1"); await page.click("#submit");
      await page.waitForURL(/#\/projects/);
      await page.waitForSelector(".card");

      // projects list: every status
      const text = await page.locator("main").innerText();
      check(`[${tag}] the projects list shows all four statuses`, ["uploaded", "processing", "done", "failed"].every((s) => text.includes(s)) && (await page.locator(".card").count()) === 4);
      check(`[${tag}] a failed project shows its plain-sentence error`, text.includes("SAM 3.1 could not find the mug in any frame"));
      check(`[${tag}] only the done project has Open; uploaded says what it waits for`, (await page.locator('a.btn[href^="#/project/"]').count()) === 1 && text.includes("python -m worldkit.worker"));
      await shot("projects");

      // delete
      page.once("dialog", (d) => d.accept());
      await page.locator('[data-del="p-fail"]').click();
      await page.waitForFunction(() => document.querySelectorAll(".card").length === 3);
      check(`[${tag}] delete removes the row`, state.deleted === "eq.p-fail");

      // polling picks up a status change (realtime is not mocked)
      state.projects.find((p) => p.id === "p-up").status = "processing";
      await page.waitForFunction(() => [...document.querySelectorAll(".chip.processing")].length === 2, null, { timeout: 15000 });
      check(`[${tag}] a status change shows up without reloading (polling fallback)`, true);

      // new project: validation, upload with progress, the row is created AFTER the upload
      await page.click('a[href="#/new"]');
      await page.fill("#name", "");
      await page.click("#go");
      check(`[${tag}] new project asks for a name first`, (await page.locator(".msg.err").innerText()).includes("name"));
      await page.fill("#name", "test mug");
      await page.click("#m-map"); await page.click("#m-object");
      await page.click("#go");
      check(`[${tag}] and for a video`, (await page.locator(".msg.err").innerText()).includes("video"));
      await page.setInputFiles("#f-choose", { name: "clip.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(300_000, 1) });
      check(`[${tag}] the chosen video is listed with its size`, (await page.locator("#picked").innerText()).includes("clip.mp4"));
      await shot("new");
      await page.click("#go");
      await page.waitForURL(/#\/projects/, { timeout: 15000 });
      check(`[${tag}] the upload goes to videos/<user id>/<project id>/ with the user's token`, state.uploaded && state.uploaded.path.startsWith(`${UID}/`) && state.uploaded.path.endsWith("/video.mp4") && state.uploaded.auth === "Bearer tok" && state.uploaded.size === 300000, JSON.stringify(state.uploaded));
      check(`[${tag}] the project row is created as uploaded, in the same folder`, state.inserted && state.inserted.status === "uploaded" && state.inserted.mode === "object" && state.inserted.name === "test mug" && state.inserted.video_path === state.uploaded.path && state.inserted.id === state.uploaded.path.split("/")[1]);
      await page.click('a[href="#/new"]');
      await page.setInputFiles("#f-choose", { name: "big.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(51_000_000, 1) });
      check(`[${tag}] a video over 50 MB is refused before uploading, with the limit stated`, (await page.locator(".msg.err").innerText()).includes("limit is 50 MB"));

      // empty and error states
      state.projects = [];
      await page.goto(BASE + "/#/projects"); await page.reload();
      await page.waitForSelector(".empty");
      check(`[${tag}] the empty state offers a new project`, (await page.locator(".empty").innerText()).includes("No projects yet"));
      await shot("projects_empty");
      // the other states for the screenshots: skeleton (slow list), disabled (nothing is done yet, every Open is disabled)
      if (SHOTS) {
        state.projects = JSON.parse(JSON.stringify(projects)).filter((p) => p.status === "processing" || p.status === "uploaded");
        await page.reload(); await page.waitForSelector(".card"); await shot("projects_disabled");
        state.projects = JSON.parse(JSON.stringify(projects)); state.delayMs = 4000;
        await page.reload(); await sleep(700); await shot("projects_skeleton");
        await page.waitForSelector(".card", { timeout: 15000 }); state.delayMs = 0;
        await page.goto(BASE + "/#/help"); await page.waitForSelector("h1"); await shot("help");
        await page.click("#user-btn"); await sleep(250); await shot("user_menu"); await page.keyboard.press("Escape");
        state.projects = []; await page.goto(BASE + "/#/projects"); await page.reload(); await page.waitForSelector(".empty");
      }
      state.listError = true;
      await page.reload(); await page.waitForSelector(".msg.err");
      check(`[${tag}] a load error is a sentence with a retry button`, (await page.locator(".msg.err").innerText()).includes("Could not load your projects") && await page.locator("#retry").count() === 1);
      await shot("projects_error");
      state.listError = false; state.projects = JSON.parse(JSON.stringify(projects));

      // open the done project in the viewer through signed URLs
      await page.goto(BASE + "/#/project/p-done"); await page.reload();
      const frame = page.frameLocator("#viewer-frame");
      await frame.locator("#obj-name").filter({ hasText: "my mug" }).waitFor({ timeout: 40000 });
      const dims = await frame.locator("#obj-dims").innerText();
      check(`[${tag}] a done project opens in the viewer with its own name and millimetre sizes`, /mm/.test(dims), dims);
      const fr = page.frames().find((f) => f.url().includes("/viewer/"));
      check(`[${tag}] the viewer got the model, the STL download and the SAM part through signed URLs`, await fr.evaluate(() => !document.getElementById("btn-print-obj").disabled && window.__partsUi().enabled));
      await sleep(800);
      await shot("viewer");
      await frame.locator("#btn-back").click();
      await page.waitForURL(/#\/projects/);
      check(`[${tag}] the viewer's Projects button goes back to the list`, true);
      await ctx.close();
    }

    // PWA: manifest, icons, service worker, installability (Chrome's own installability errors)
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await mockSupabase(page, { projects: [] });
    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller || navigator.serviceWorker.ready.then(() => true), null, { timeout: 15000 });
    await page.reload({ waitUntil: "networkidle" });
    const man = await (await fetch(BASE + "/manifest.webmanifest")).json();
    check("manifest: standalone, start_url, 192 and 512 PNG icons", man.display === "standalone" && man.start_url === "/" && man.icons.some((i) => i.sizes === "192x192" && i.type === "image/png") && man.icons.some((i) => i.sizes === "512x512" && i.type === "image/png"));
    const iconsOk = await Promise.all(man.icons.map(async (i) => (await fetch(BASE + i.src)).headers.get("content-type") === "image/png"));
    check("manifest icons are served as PNG", iconsOk.every(Boolean));
    check("the service worker is registered and controls the page", await page.evaluate(async () => { const r = await navigator.serviceWorker.getRegistration(); return !!r && !!navigator.serviceWorker.controller; }));
    const cache = await page.evaluate(async () => { const keys = await caches.keys(); const out = []; for (const k of keys) for (const r of await (await caches.open(k)).keys()) out.push(new URL(r.url).pathname); return out; });
    check("the cache holds the app shell only (no viewer files, no videos, no Supabase)", cache.length > 0 && cache.every((p) => !p.startsWith("/viewer/") && !p.includes("storage")), cache.join(","));
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Page.enable");
    const inst = await cdp.send("Page.getInstallabilityErrors");
    check("Chrome reports no installability errors", inst.installabilityErrors.length === 0, JSON.stringify(inst.installabilityErrors));
    await ctx.close();
    check("no page or console errors in any run", errors.length === 0, errors.slice(0, 3).join(" | "));
  } catch (e) {
    check("test ran to the end", false, String(e).slice(0, 400));
  } finally {
    await browser.close();
    srv.kill();
  }
  const failed = results.filter((r) => !r.pass).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
