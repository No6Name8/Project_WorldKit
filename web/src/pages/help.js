export async function renderHelp(root) {
  root.innerHTML = `
    <div class="page-h"><h1>Help</h1></div>
    <div class="cards">
      <section class="panel"><h3 class="sub">Filming</h3><p class="hint">Walk slowly around the object for about 20 seconds, keep it in frame, and put a coin on the same surface so WorldKit can set the real size. Use Map for a desk with several objects.</p></section>
      <section class="panel"><h3 class="sub">Processing</h3><p class="hint">The video is processed on your own laptop, not on this site. Run <span class="mono">python -m worldkit.worker</span> there. Until then the project shows as uploaded.</p></section>
      <section class="panel"><h3 class="sub">Sizes</h3><p class="hint">Sizes are in millimetres. The model is generated, not measured, and the scale is an estimate unless you set the real height yourself.</p></section>
      <p><a class="btn" href="/viewer/index.html?demo=1">Open demo viewer</a> <a class="btn" href="https://www.loom.com/share/55fd3cf835994a6a99e1fee8fc2b907d" target="_blank" rel="noopener">Watch demo video (3 min)</a></p>
    </div>`;
}
