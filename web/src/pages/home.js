import { esc } from "../lib.js";

// Home (Figma "Desktop · Home · Default"): headline and actions on the left, a live read-only preview of the demo mug on the right.
// The preview is the viewer itself (?demo=1&preview=1: canvas only, orbit only); the size under it is reported by that viewer,
// so it is the demo run's real measured-from-coin size, never a number typed into this page.
export async function renderHome(root, ctx) {
  root.innerHTML = `
    <section class="home">
      <div class="home-l">
        <h1>Film it. Print it.</h1>
        <p class="lead">Turn a phone video into a printable STL, scaled from a coin.<br />Edit the model with a plain-text request.</p>
        <div class="cta">
          ${ctx.user ? '<a class="btn primary big" href="#/projects">Your projects</a>' : '<a class="btn primary big" href="#/login">Sign in</a>'}
          <a class="btn big" href="/viewer/index.html?demo=1">Open demo viewer</a>
          <a class="btn big" href="https://www.loom.com/share/55fd3cf835994a6a99e1fee8fc2b907d" target="_blank" rel="noopener">Watch demo video (3 min)</a>
        </div>
        <p class="hint">Object or desk <span aria-hidden="true">·</span> STL in millimetres</p>
      </div>
      <figure class="demo">
        <div class="demo-h"><span>Demo <span class="sep">/</span> mug</span><span class="chip" id="demo-ver">v0</span></div>
        <div class="card demo-vp">
          <iframe id="demo-frame" title="Demo mug, 3D preview (drag to orbit)" src="/viewer/index.html?demo=1&amp;preview=1" loading="lazy"></iframe>
          <span class="vp-tl">Solid</span><span class="vp-br mono">drag to orbit</span>
        </div>
        <figcaption><span class="mono" id="demo-size">loading size</span><span>Object</span></figcaption>
      </figure>
    </section>
    <section class="steps" aria-label="How it works">
      <div class="panel"><h3>1. Film</h3><p class="hint">Walk slowly around the object for about 20 seconds. Put a coin on the same surface for real scale.</p></div>
      <div class="panel"><h3>2. Upload</h3><p class="hint">Choose Object or Map, name it, send the video from your phone.</p></div>
      <div class="panel"><h3>3. Open</h3><p class="hint">Videos are processed on your own laptop. When it is done, open the model, read its sizes and download the STL.</p></div>
    </section>`;
  const onMessage = (e) => {
    if (e.origin !== location.origin || !e.data || e.data.type !== "wk-preview") return;
    const mm = e.data.mm;
    root.querySelector("#demo-size").textContent = mm ? `${mm.map((v) => v.toFixed(1)).join(" × ")} mm` : "size not calibrated";
    root.querySelector("#demo-size").title = e.data.scale || "";
    if (e.data.name) root.querySelector(".demo-h > span").innerHTML = `Demo <span class="sep">/</span> ${esc(e.data.name)}`;
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}
