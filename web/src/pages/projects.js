import { supabase } from "../supabase.js";
import { esc, statusInfo, formatDate, applyChange, resultPaths } from "../lib.js";

export async function renderProjects(root, ctx) {
  let list = null, error = "", live = false, timer = null, channel = null;
  root.innerHTML = `<div class="page-h"><h1>Projects</h1><span class="live" id="live"><i></i><span>connecting</span></span></div><div id="list"></div>`;
  const listEl = root.querySelector("#list");

  const card = (p) => {
    const s = statusInfo(p.status);
    const note = p.status === "failed" ? `<div class="note err">${esc(p.error || "The worker could not finish this project.")}</div>`
      : p.status === "done" ? "" : `<div class="note">${esc(s.hint)}</div>`;
    return `<article class="card" data-id="${esc(p.id)}">
      <div><div class="name">${esc(p.name)}</div>
        <div class="meta"><span class="chip ${esc(p.status)}">${esc(s.label)}</span><span class="chip">${esc(p.mode)}</span><span class="mono">${esc(formatDate(p.created_at))}</span></div></div>
      <div class="actions">
        ${p.status === "done" ? `<a class="btn primary" href="#/project/${esc(p.id)}">Open</a>` : ""}
        <button type="button" class="danger" data-del="${esc(p.id)}" aria-label="Delete ${esc(p.name)}">Delete</button>
      </div>${note}</article>`;
  };

  const draw = () => {
    root.querySelector("#live").className = `live ${live ? "on" : ""}`;
    root.querySelector("#live span").textContent = live ? "live" : "checking every 8 s";
    if (error) { listEl.innerHTML = `<div class="msg err" role="alert">${esc(error)} <button type="button" id="retry">Try again</button></div>`; listEl.querySelector("#retry").onclick = load; return; }
    if (list === null) { listEl.innerHTML = '<div class="cards" aria-busy="true"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>'; return; }
    if (!list.length) {
      listEl.innerHTML = `<div class="empty"><p><b>No projects yet.</b></p><p>Film an object, upload it, and it shows up here.</p><p><a class="btn primary" href="#/new">New project</a></p></div>`;
      return;
    }
    listEl.innerHTML = `<div class="cards">${list.map(card).join("")}</div>`;
    listEl.querySelectorAll("[data-del]").forEach((b) => { b.onclick = () => del(b.dataset.del); });
  };

  async function load() {
    error = "";
    const { data, error: e } = await supabase.from("projects").select("*").order("created_at", { ascending: false });
    if (e) error = `Could not load your projects: ${e.message}`; else list = data;
    draw();
  }

  async function del(id) {
    const p = list.find((x) => x.id === id);
    if (!p || !confirm(`Delete "${p.name}"? The video and results are removed too.`)) return;
    const files = resultPaths(p);
    if (p.video_path) await supabase.storage.from("videos").remove([p.video_path]);
    if (files.length) await supabase.storage.from("results").remove(files);
    const { error: e } = await supabase.from("projects").delete().eq("id", id);
    if (e) { error = `Could not delete: ${e.message}`; draw(); return; }
    list = list.filter((x) => x.id !== id);
    draw();
  }

  draw();
  await load();

  channel = supabase.channel(`projects-${ctx.user.id}`)
    .on("postgres_changes", { event: "*", schema: "public", table: "projects", filter: `user_id=eq.${ctx.user.id}` }, (payload) => {
      if (list) { list = applyChange(list, payload); draw(); }
    })
    .subscribe((status) => { live = status === "SUBSCRIBED"; draw(); });
  // if the live channel is down, poll while anything is still being worked on
  timer = setInterval(() => { if (!live || (list || []).some((p) => p.status === "uploaded" || p.status === "processing")) load(); }, 8000);

  return () => { clearInterval(timer); if (channel) supabase.removeChannel(channel); };
}
