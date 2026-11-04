import { supabase } from "../supabase.js";
import { esc, resultPaths, collectSignedUrls, videoLinkFrom } from "../lib.js";

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

  const [view, dl, vres] = await Promise.all([
    supabase.storage.from("results").createSignedUrls(paths, 3600),
    supabase.storage.from("results").createSignedUrls(paths.filter((x) => x.endsWith(".stl")), 3600, { download: true }),
    // the uploaded video for the Source tab; a failure is shown there, the model still opens
    p.video_path ? supabase.storage.from("videos").createSignedUrl(p.video_path, 3600).catch((e) => ({ error: { message: String((e && e.message) || e) } })) : null,
  ]);
  const video = videoLinkFrom(vres, p.video_path);
  const prefix = `${p.result_path}/`;
  const { files, downloads, missing } = collectSignedUrls(view, dl, prefix);
  // a file without a signed URL is an error the user sees, not a silent gap in the viewer
  if (missing.length) return fail(`Could not open ${missing.length} result file(s): ${missing.join(", ")}. Try again; if it keeps failing, the files may have been removed.`);

  app.innerHTML = '<iframe id="viewer-frame" title="WorldKit viewer" src="/viewer/index.html?embed=1"></iframe>';
  const frame = app.querySelector("#viewer-frame");
  const onMessage = (e) => {
    if (e.origin !== location.origin) return;
    if (e.data && e.data.type === "wk-ready") frame.contentWindow.postMessage({ type: "wk-project", project: { id: p.id, name: p.name, mode: p.mode }, files, downloads, video }, location.origin);
    if (e.data && e.data.type === "wk-back") ctx.navigate("#/projects");
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}
