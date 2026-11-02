import { supabase } from "../supabase.js";
import { uploadWithProgress } from "../upload.js";
import { esc, validateVideo, validateProjectForm, videoPath, formatMB, MAX_VIDEO_MB } from "../lib.js";

export async function renderNew(root, ctx) {
  const state = { mode: "object", name: "", file: null, busy: false };
  root.innerHTML = `
    <div class="page-h"><h1>New project</h1></div>
    <form class="stack" id="new-form" novalidate>
      <div class="field" style="gap:var(--space-2)"><span>What is in the video?</span>
        <div class="seg" role="group" aria-label="Mode">
          <button type="button" id="m-object" aria-pressed="true">Object</button>
          <button type="button" id="m-map" aria-pressed="false">Map</button>
        </div>
        <span class="hint" id="mode-hint"></span>
      </div>
      <label class="field">Name<input id="name" type="text" maxlength="120" placeholder="e.g. my mug" autocomplete="off" /></label>
      <div class="field" style="gap:var(--space-2)"><span>Video</span>
        <div class="pickrow">
          <button type="button" class="big" id="record">Record video</button>
          <button type="button" class="big" id="choose">Choose file</button>
        </div>
        <input id="f-record" type="file" accept="video/*" capture="environment" />
        <input id="f-choose" type="file" accept="video/*" />
        <span class="picked" id="picked">No video yet.</span>
        <span class="hint">Up to ${MAX_VIDEO_MB} MB. About 20 seconds, slowly around the object, coin on the same surface.</span>
      </div>
      <div id="status" aria-live="polite"></div>
      <div class="progress" id="bar" hidden role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div></div></div>
      <div class="pickrow"><button class="primary big" id="go" type="submit">Upload</button><a class="btn big" href="#/projects">Cancel</a></div>
    </form>`;
  const $ = (s) => root.querySelector(s);
  const hint = { object: "One object, filmed all the way around. Result: one model and its STL.", map: "A desk or table with several objects and a coin. Result: a scene with one STL per object and a diorama." };
  const show = (msg, kind = "") => { $("#status").innerHTML = msg ? `<div class="msg ${kind}" role="${kind === "err" ? "alert" : "status"}">${esc(msg)}</div>` : ""; };
  const setMode = (m) => {
    state.mode = m;
    $("#m-object").setAttribute("aria-pressed", String(m === "object"));
    $("#m-map").setAttribute("aria-pressed", String(m === "map"));
    $("#mode-hint").textContent = hint[m];
  };
  setMode("object");
  $("#m-object").onclick = () => setMode("object");
  $("#m-map").onclick = () => setMode("map");
  $("#record").onclick = () => $("#f-record").click();
  $("#choose").onclick = () => $("#f-choose").click();
  const picked = (e) => {
    state.file = e.target.files[0] || null;
    const err = state.file ? validateVideo(state.file) : null;
    $("#picked").textContent = state.file ? `${state.file.name || "video"}, ${formatMB(state.file.size)}` : "No video yet.";
    show(err || "", err ? "err" : "");
  };
  $("#f-record").onchange = picked;
  $("#f-choose").onchange = picked;
  $("#name").oninput = (e) => { state.name = e.target.value; };

  $("#new-form").onsubmit = async (e) => {
    e.preventDefault();
    if (state.busy) return;
    const bad = validateProjectForm({ name: state.name, mode: state.mode }) || validateVideo(state.file);
    if (bad) return show(bad, "err");
    state.busy = true;
    $("#go").disabled = true;
    $("#bar").hidden = false;
    show("Uploading. Keep this page open.");
    const id = crypto.randomUUID();
    const path = videoPath(ctx.user.id, id, state.file.name);
    try {
      const { data } = await supabase.auth.getSession();
      await uploadWithProgress("videos", path, state.file, data.session.access_token, (p) => {
        const pct = Math.round(p * 100);
        $("#bar > div").style.width = `${pct}%`;
        $("#bar").setAttribute("aria-valuenow", String(pct));
        $("#status").innerHTML = `<div class="msg" role="status">Uploading <span class="mono">${pct}%</span></div>`;
      });
      // the row is created only after the upload finished, so the worker never sees a project without its video
      const { error } = await supabase.from("projects").insert({ id, name: state.name.trim(), mode: state.mode, status: "uploaded", video_path: path });
      if (error) {
        await supabase.storage.from("videos").remove([path]);
        throw new Error(`The video uploaded but the project could not be saved: ${error.message}`);
      }
      ctx.navigate("#/projects");
    } catch (err) {
      state.busy = false;
      $("#go").disabled = false;
      $("#bar").hidden = true;
      show(err.message || String(err), "err");
    }
  };
}
