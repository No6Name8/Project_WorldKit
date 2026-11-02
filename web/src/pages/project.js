import { supabase } from "../supabase.js";
import { esc, resultPaths } from "../lib.js";

/** Full-screen viewer for one finished project: the viewer (an iframe of /viewer/) gets signed URLs for the project's private result files. */
export async function renderProject(app, id, ctx) {
  app.innerHTML = '<main id="page" aria-busy="true"><div class="cards"><div class="skel"></div><div class="skel"></div></div></main>';
  const fail = (msg) => { app.innerHTML = `<main id="page"><div class="msg err" role="alert">${esc(msg)}</div><p style="margin-top:var(--space-4)"><a class="btn" href="#/projects">Back to projects</a></p></main>`; };

  const { data: p, error } = await supabase.from("projects").select("*").eq("id", id).maybeSingle();
  if (error) return fail(`Could not load the project: ${error.message}`);
  if (!p) return fail("That project does not exist (it may have been deleted).");
  if (p.status !== "done") return fail(`This project is ${p.status}. ${p.status === "failed" ? (p.error || "") : "Open it again once it is done."}`);
  const paths = resultPaths(p);
  if (!paths.length) return fail("The project is marked done but has no result files.");

  const [view, dl] = await Promise.all([
    supabase.storage.from("results").createSignedUrls(paths, 3600),
    supabase.storage.from("results").createSignedUrls(paths.filter((x) => x.endsWith(".stl")), 3600, { download: true }),
  ]);
  if (view.error) return fail(`Could not open the result files: ${view.error.message}`);
  const prefix = `${p.result_path}/`;
  const files = {}, downloads = {};
  for (const r of view.data) if (r.signedUrl) files[r.path.slice(prefix.length)] = r.signedUrl;
  for (const r of (dl.data || [])) if (r.signedUrl) downloads[r.path.slice(prefix.length)] = r.signedUrl;

  app.innerHTML = '<iframe id="viewer-frame" title="WorldKit viewer" src="/viewer/index.html?embed=1"></iframe>';
  const frame = app.querySelector("#viewer-frame");
  const onMessage = (e) => {
    if (e.origin !== location.origin) return;
    if (e.data && e.data.type === "wk-ready") frame.contentWindow.postMessage({ type: "wk-project", project: { id: p.id, name: p.name, mode: p.mode }, files, downloads }, location.origin);
    if (e.data && e.data.type === "wk-back") ctx.navigate("#/projects");
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}
