// Real-browser test of the Source tab: the original video next to the model built from it.
//   node viewer/tests/source_browser_test.js     (with python viewer/server.py running on 5050, as for the other suites)
// Checks the tab order, that ?demo=1 opens on Source, the video element (source file served, muted, inline, poster, no sound on its own,
// paused when the tab is hidden, no autoplay under reduced motion), the frame strip against source.json (thumbnails, SAM 3.1 outlines,
// seek on click), the missing-mask fallback, the scene run's verify image and label, and the error message for a failed signed video URL.
// Writes docs/ui/source_mug_{1440,390}.png and docs/ui/source_desk_{1440,390}.png (dark theme).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:5050/";
const ROOT = path.join(__dirname, "..", "..");
const MUG = JSON.parse(fs.readFileSync(path.join(ROOT, "viewer", "assets", "demo", "run_mug", "source", "source.json"), "utf8"));
const DESK = JSON.parse(fs.readFileSync(path.join(ROOT, "viewer", "assets", "demo", "run_desk", "source", "source.json"), "utf8"));
const SHOTS = path.join(ROOT, "docs", "ui");
const results = [];
const check = (name, cond, detail) => { results.push(!!cond); console.log((cond ? "PASS" : "FAIL") + " - " + name + (detail ? " :: " + detail : "")); };
const polys = (doc) => doc.frames.reduce((n, f) => n + Object.values(f.outlines).reduce((a, p) => a + p.length, 0), 0);

async function openDemo(browser, vp, opts = {}) {
  const page = await browser.newPage({ viewport: vp, colorScheme: "dark", ...opts });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(BASE + "?demo=1", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__source && window.__source().frames > 0, null, { timeout: 60000 });
  return { page, errors };
}

(async () => {
  const browser = await chromium.launch();
  try {
    // 1) hosted demo, mug
    const { page, errors } = await openDemo(browser, { width: 1440, height: 900 });
    const tabs = await page.$$eval("#proj-head .tabs [role=tab]", (b) => b.map((x) => x.textContent.trim()));
    check("tabs read Source | Viewer | AI Edit | Accuracy", tabs.join("|") === "Source|Viewer|AI Edit|Accuracy", tabs.join(" | "));
    let s = await page.evaluate(() => window.__source());
    check("?demo=1 opens on the Source tab", s.tab === "source" && (await page.getAttribute("#tab-source", "aria-selected")) === "true");
    const vattr = await page.$eval("#src-video-el", (v) => ({ controls: v.controls, inline: v.playsInline, muted: v.muted, defaultMuted: v.defaultMuted, autoplayAttr: v.hasAttribute("autoplay") }));
    check("the video element points at the run's copy of the original video, with a poster", s.src === `assets/demo/run_mug/source/${MUG.video}` && s.poster === "assets/demo/run_mug/source/poster.jpg", `${s.src} | ${s.poster}`);
    check("the video has controls, plays inline, is muted by default and has no autoplay attribute", vattr.controls && vattr.inline && vattr.muted && vattr.defaultMuted && !vattr.autoplayAttr, JSON.stringify(vattr));
    const head = await page.evaluate(async (u) => { const r = await fetch(u, { headers: { Range: "bytes=0-1023" } }); return { status: r.status, type: r.headers.get("content-type") }; }, s.src);
    check("the video file is served as video/mp4 and answers range requests (seeking)", (head.status === 206 || head.status === 200) && /video\/mp4/.test(head.type), JSON.stringify(head));
    await page.waitForFunction(() => document.getElementById("src-video-el").readyState >= 1, null, { timeout: 20000 });
    const dur = await page.$eval("#src-video-el", (v) => v.duration);
    check("the browser reads the video's real length (matches source.json)", Math.abs(dur - MUG.duration_s) < 0.3, `${dur.toFixed(2)} s vs ${MUG.duration_s}`);
    check("title shows the file name, length and number of frames used", s.file === MUG.file_name && s.length === `${MUG.duration_s.toFixed(1)} s` && s.nframes === String(MUG.frames_used), `${s.file} | ${s.length} | ${s.nframes}`);
    check("the model card carries the one-line caption", s.caption === `Built from this video: ${MUG.built_with}` && (await page.isVisible("#vp-src-cap")) && (await page.isVisible("#canvas-wrap canvas")), s.caption);
    check("under the strip: the scale source and the object name", s.scale === `Scale source: ${MUG.scale_source}` && s.object === `Object: ${MUG.object}`, `${s.scale} | ${s.object}`);
    check("the strip shows every frame in source.json (6 to 10)", s.frames === MUG.frames.length && s.frames >= 6 && s.frames <= 10, String(s.frames));
    const imgsOk = await page.$$eval("#src-frames img", (im) => Promise.all(im.map((i) => (i.complete ? Promise.resolve() : new Promise((r) => { i.onload = i.onerror = r; })))).then(() => im.every((i) => i.naturalWidth === 360)));
    check("every thumbnail loads at 360 px wide", imgsOk);
    check("each SAM 3.1 outline from the cached masks is drawn as a thin accent outline", s.outlined === MUG.frames.filter((f) => Object.keys(f.outlines).length).length && s.outlines === polys(MUG), `${s.outlined} frames, ${s.outlines} polygons`);
    const stroke = await page.$eval("#src-frames .src-outline", (p) => { const c = getComputedStyle(p); return { stroke: c.stroke, w: c.strokeWidth, fill: c.fill }; });
    const accent = await page.evaluate(() => { const d = document.createElement("i"); d.style.color = "var(--accent)"; document.body.appendChild(d); const c = getComputedStyle(d).color; d.remove(); return c; });
    check("the outline uses the accent token, no fill", stroke.stroke === accent && stroke.fill === "none" && parseFloat(stroke.w) <= 2, JSON.stringify(stroke));

    // seek on click
    const pick = MUG.frames[3];
    await page.click(`#src-frames .src-frame:nth-child(4)`);
    await page.waitForFunction((t) => Math.abs(document.getElementById("src-video-el").currentTime - t) < 0.3, pick.time_s, { timeout: 10000 }).catch(() => {});
    s = await page.evaluate(() => window.__source());
    check("clicking a frame seeks the video to that frame's time and pauses it", Math.abs(s.time - pick.time_s) < 0.3 && s.paused && (await page.getAttribute("#src-frames .src-frame:nth-child(4)", "aria-pressed")) === "true", `${s.time.toFixed(2)} s vs ${pick.time_s} s`);
    await page.screenshot({ path: path.join(SHOTS, "source_mug_1440.png") });

    // muted loop only on the Source tab; hidden tab pauses it
    await page.evaluate(() => { const v = document.getElementById("src-video-el"); v.dataset.userPaused = "0"; });
    await page.click("#tab-viewer"); await page.click("#tab-source");
    await page.waitForFunction(() => !document.getElementById("src-video-el").paused, null, { timeout: 8000 }).catch(() => {});
    s = await page.evaluate(() => window.__source());
    check("on the Source tab the video plays muted (looping), never with sound", !s.paused && s.muted, JSON.stringify({ paused: s.paused, muted: s.muted }));
    await page.click("#tab-viewer");
    s = await page.evaluate(() => window.__source());
    check("switching to Viewer pauses the video", s.paused && s.tab === "viewer");
    await page.click("#tab-source");

    // missing-mask fallback: the same frames, one without its cached mask
    const doc2 = JSON.parse(JSON.stringify(MUG));
    doc2.frames[0].outlines = {}; doc2.frames[0].missing_masks = [MUG.object];
    await page.evaluate((d) => window.__setSourceDoc("assets/demo/run_mug/", d), doc2);
    s = await page.evaluate(() => window.__source());
    const first = await page.$eval("#src-frames .src-frame", (b) => ({ cls: b.className, svg: !!b.querySelector("svg"), img: b.querySelector("img").naturalWidth || 1, title: b.title }));
    check("a frame without a cached mask shows its thumbnail without an outline, and says so", s.noMask === 1 && !first.svg && /no-mask/.test(first.cls) && /no cached SAM 3.1 mask/.test(first.title) && s.outlined === MUG.frames.length - 1, first.title);
    await page.evaluate((d) => window.__setSourceDoc("assets/demo/run_mug/", d), MUG);

    // 2) coin desk scene
    await page.click("#mode-map");
    await page.evaluate(() => window.__loadDemoRun("map"));
    await page.waitForFunction(() => window.__source().verify, null, { timeout: 60000 });
    await page.click("#tab-source").catch(() => {});
    s = await page.evaluate(() => window.__source());
    check("the coin desk run shows its own video and frames", s.src === `assets/demo/run_desk/source/${DESK.video}` && s.frames === DESK.frames.length && s.outlines === polys(DESK), `${s.src} | ${s.frames} frames | ${s.outlines} polygons`);
    check("the scene shows the real | reconstructed verify image with its label", s.verify === "Held-out frame: real vs reconstruction, mean IoU 0.643" && (await page.$eval("#src-verify-img", (i) => i.naturalWidth)) > 0, s.verify);
    check("the scene caption names the models that ran", s.caption === `Built from this video: ${DESK.built_with}`, s.caption);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(SHOTS, "source_desk_1440.png") });
    check("no page or console errors (1440)", errors.length === 0, errors.slice(0, 3).join(" | "));
    await page.close();

    // 3) phone width screenshots
    for (const kind of ["mug", "desk"]) {
      const { page: p, errors: e2 } = await openDemo(browser, { width: 390, height: 844 });
      if (kind === "desk") { await p.click("#mode-map"); await p.evaluate(() => window.__loadDemoRun("map")); await p.waitForFunction(() => window.__source().verify, null, { timeout: 60000 }); }
      await p.waitForTimeout(1500);
      const ov = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(`[390] ${kind}: the Source tab fits the phone width (no sideways scroll)`, ov <= 1, `overflow ${ov}px`);
      await p.screenshot({ path: path.join(SHOTS, `source_${kind}_390.png`), fullPage: true });
      check(`[390] ${kind}: no page or console errors`, e2.length === 0, e2.slice(0, 3).join(" | "));
      await p.close();
    }

    // 4) reduced motion: no autoplay at all
    const { page: rm } = await openDemo(browser, { width: 1440, height: 900 }, { reducedMotion: "reduce" });
    await rm.waitForTimeout(1500);
    check("under reduced motion the video does not start by itself", (await rm.evaluate(() => window.__source())).paused);
    await rm.close();

    // 5) without ?demo the viewer still opens on Viewer, and the Source tab is there
    const plain = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await plain.goto(BASE, { waitUntil: "domcontentloaded" });
    await plain.waitForFunction(() => window.__source, null, { timeout: 30000 });
    check("the plain viewer opens on Viewer, with the Source tab available", (await plain.evaluate(() => document.body.dataset.tab)) === "viewer" && (await plain.isVisible("#tab-source")));
    await plain.close();

    // 6) web project page (embed): a failed signed URL for the uploaded video is a clear message, the model still opens
    const emb = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await emb.goto(BASE + "?embed=1", { waitUntil: "domcontentloaded" });
    await emb.waitForFunction(() => window.__source, null, { timeout: 30000 });
    await emb.evaluate(() => window.postMessage({ type: "wk-project", project: { id: "t", name: "test mug", mode: "object" },
      files: { "model.glb": "assets/demo/run_mug/model.glb", "report.json": "assets/demo/run_mug/report.json" }, downloads: {},
      video: { error: "Object not found", name: "your upload (video.mp4)" } }, location.origin));
    await emb.waitForFunction(() => window.__source().msg, null, { timeout: 30000 }).catch(() => {});
    await emb.click("#tab-source");
    s = await emb.evaluate(() => window.__source());
    check("embed: a failed signed video URL shows 'Could not open the original video: ...' and hides the player", /^Could not open the original video: Object not found\./.test(s.msg) && s.hidden && (await emb.isVisible("#src-msg")), s.msg);
    check("embed: the model still loads next to the message", (await emb.evaluate(() => document.getElementById("obj-dims").textContent)).includes("mm"));
    await emb.evaluate(() => window.postMessage({ type: "wk-project", project: { id: "t", name: "test mug", mode: "object" },
      files: { "model.glb": "assets/demo/run_mug/model.glb", "report.json": "assets/demo/run_mug/report.json" }, downloads: {},
      video: { url: "assets/demo/run_mug/source/mug_on_table.mp4", name: "your upload (video.mp4)" } }, location.origin));
    await emb.waitForFunction(() => window.__source().src && !window.__source().msg, null, { timeout: 30000 }).catch(() => {});
    s = await emb.evaluate(() => window.__source());
    check("embed: a signed video URL plays in the Source tab, muted", s.src.endsWith("mug_on_table.mp4") && !s.hidden && s.muted && !s.msg, s.src);
    await emb.close();
  } catch (e) {
    check("test ran to the end", false, String(e).slice(0, 400));
  } finally {
    await browser.close();
  }
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})();
