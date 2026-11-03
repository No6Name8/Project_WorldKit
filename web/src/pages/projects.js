import { supabase } from "../supabase.js";
import { esc, statusInfo, formatDate, applyChange, resultPaths } from "../lib.js";
import { reveal, reducedMotion } from "../motion.js";

export async function renderProjects(root, ctx) {
  let list = null, error = "", live = false, timer = null, channel = null, filter = "all";
  let seen = null;           // ids already shown: later arrivals slide in, the first batch is revealed on scroll
  const lastStatus = {};     // status chips cross-fade when the status changes live
  root.innerHTML = `<div class="page-h"><div><h1>Projects</h1><p class="sub">Personal workspace</p></div><a class="btn primary big" href="#/new">New project</a></div>
    <div class="ptabs"><div class="tabs" role="tablist" aria-label="Filter projects">
      <button type="button" role="tab" data-f="all" aria-selected="true">All projects</button><button type="button" role="tab" data-f="object" aria-selected="false">Object</button><button type="button" role="tab" data-f="map" aria-selected="false">Map</button>
    </div><span class="live" id="live"><i></i><span>connecting</span></span></div>
    <div id="list"></div>`;
  const listEl = root.querySelector("#list");
  root.querySelectorAll("[data-f]").forEach((b) => { b.onclick = () => { filter = b.dataset.f; root.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-selected", String(x === b))); draw(); }; });

  const ICON = {
    done: '<path d="M12 2.5 20.5 7v10L12 21.5 3.5 17V7z"/><path d="M3.5 7 12 11.5 20.5 7M12 11.5v10"/>',
    processing: '<path d="M12 3a9 9 0 1 0 9 9"/>',
    uploaded: '<rect x="3" y="6.5" width="13" height="11" rx="1"/><path d="m16 10.5 5-3v9l-5-3"/>',
    failed: '<path d="M12 3.5 21.5 20h-19z"/><path d="M12 10v4.5M12 17v.5"/>',
  };
  const TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6"/></svg>';
  const card = (p) => {
    const s = statusInfo(p.status);
    const note = p.status === "failed" ? `<div class="note err">${esc(p.error || "The worker could not finish this project.")}</div>`
      : p.status === "done" ? "" : `<div class="note">${esc(s.hint)}</div>`;
    return `<article class="card" data-id="${esc(p.id)}">
      <div class="thumb s-${esc(p.status)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true">${ICON[p.status] || ICON.uploaded}</svg></div>
      <div class="pc-row"><span class="name">${esc(p.name)}</span><button type="button" class="icon-btn" data-del="${esc(p.id)}" aria-label="Delete ${esc(p.name)}" title="Delete">${TRASH}</button></div>
      <div class="pc-row"><span class="kind">${p.mode === "map" ? "Map" : "Object"}</span><span class="chip ${esc(p.status)}">${esc(s.label)}</span></div>
      <div class="pc-row"><span class="mono date">${esc(formatDate(p.created_at))}</span>
        ${p.status === "done" ? `<a class="btn link" href="#/project/${esc(p.id)}">Open model</a>` : `<span class="hint">${p.status === "failed" ? "Not built" : "Not ready"}</span>`}</div>
      ${note}</article>`;
  };

  const draw = () => {
    root.querySelector("#live").className = `live ${live ? "on" : ""}`;
    root.querySelector("#live span").textContent = live ? "live" : "checking every 8 s";
    if (error) { listEl.innerHTML = `<div class="msg err" role="alert">${esc(error)} <button type="button" id="retry">Try again</button></div>`; listEl.querySelector("#retry").onclick = load; return; }
    if (list === null) { listEl.innerHTML = '<div class="grid" aria-busy="true"><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>'; return; }
    if (!list.length) {
      listEl.innerHTML = `<div class="empty"><p><b>No projects yet.</b></p><p>Film an object, upload it, and it shows up here.</p><p><a class="btn primary" href="#/new">New project</a></p></div>`;
      return;
    }
    const shown = list.filter((p) => filter === "all" || p.mode === filter);
    listEl.innerHTML = shown.length ? `<div class="grid">${shown.map(card).join("")}</div>` : `<div class="empty"><p>No ${filter === "map" ? "Map" : "Object"} projects.</p></div>`;
    listEl.querySelectorAll("[data-del]").forEach((b) => { b.onclick = () => del(b.dataset.del); });
    const first = seen === null;
    if (first) seen = new Set();
    const fresh = [];
    for (const el of listEl.querySelectorAll(".grid > .card")) {
      const id = el.dataset.id, p = list.find((x) => x.id === id);
      if (first) fresh.push(el); else if (!seen.has(id)) el.classList.add("arrive");
      if (p && lastStatus[id] && lastStatus[id] !== p.status) el.querySelector(".chip").classList.add("chg");
      if (p) lastStatus[id] = p.status;
      seen.add(id);
    }
    if (first) reveal(fresh);
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
    const el = listEl.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
    if (el) el.classList.add("leaving");  // fades and shrinks while the files are removed
    const files = resultPaths(p);
    if (p.video_path) await supabase.storage.from("videos").remove([p.video_path]);
    if (files.length) await supabase.storage.from("results").remove(files);
    const { error: e } = await supabase.from("projects").delete().eq("id", id);
    if (e) { error = `Could not delete: ${e.message}`; draw(); return; }
    if (el && !reducedMotion()) await new Promise((r) => setTimeout(r, 220));
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
