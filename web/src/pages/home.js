export async function renderHome(root, ctx) {
  root.innerHTML = `
    <section class="hero">
      <h1>WorldKit</h1>
      <p class="tag">Film it. Print it.</p>
      <p class="lead">Film an object, or a desk with a coin next to it. WorldKit builds a 3D model with real sizes in millimetres that you can inspect, edit and print.</p>
      <p class="lead">Videos are processed on your own laptop. This site uploads the video, shows the status and opens the result.</p>
      <div class="cta">
        ${ctx.user ? '<a class="btn primary big" href="#/projects">Your projects</a><a class="btn big" href="#/new">New project</a>' : '<a class="btn primary big" href="#/login">Sign in</a>'}
        <a class="btn big" href="/viewer/index.html?demo=1">Open demo viewer</a>
      </div>
    </section>
    <section class="facts" aria-label="How it works">
      <div class="panel"><h3>1. Film</h3><p class="hint">Walk slowly around the object for about 20 seconds. Put a coin on the same surface for real scale.</p></div>
      <div class="panel"><h3>2. Upload</h3><p class="hint">Choose Object or Map, name it, send the video from your phone.</p></div>
      <div class="panel"><h3>3. Open</h3><p class="hint">When your laptop worker is done, open the model, read its sizes and download the STL.</p></div>
    </section>`;
}
