// Pure helpers (no DOM, no network) so they can be unit-tested with node --test.

export const MAX_VIDEO_MB = 50; // the free Supabase plan refuses a single object over 50 MB

export const STATUS = {
  uploaded: { label: "uploaded", hint: "Waiting for your laptop worker. Run: python -m worldkit.worker" },
  processing: { label: "processing", hint: "Your laptop is working on it." },
  done: { label: "done", hint: "Ready to open in the viewer." },
  failed: { label: "failed", hint: "The worker could not finish this project." },
};

export function statusInfo(status) {
  return STATUS[status] || { label: String(status || "unknown"), hint: "" };
}

/** "#/project/abc" -> { name: "project", id: "abc" }; unknown hashes fall back to home. */
export function parseRoute(hash) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  const [name = "home", id] = parts;
  if (name === "project" && id) return { name: "project", id: decodeURIComponent(id) };
  if (["home", "login", "new", "projects", "help"].includes(name)) return { name };
  return { name: "home" };
}

export function extensionOf(fileName) {
  const m = /\.([a-z0-9]{2,5})$/i.exec(fileName || "");
  return m ? m[1].toLowerCase() : "mp4";
}

/** Storage path rule: the FIRST folder must be the user's id (the bucket policies check it). */
export function videoPath(userId, projectId, fileName) {
  if (!userId || !projectId) throw new Error("videoPath needs a user id and a project id");
  return `${userId}/${projectId}/video.${extensionOf(fileName)}`;
}

export function resultPrefix(userId, projectId) {
  return `${userId}/${projectId}`;
}

export function validateVideo(file, maxMb = MAX_VIDEO_MB) {
  if (!file) return "Choose or record a video first.";
  if (file.type && !file.type.startsWith("video/")) return "That file is not a video.";
  if (file.size === 0) return "That video is empty.";
  if (file.size > maxMb * 1_000_000) {
    return `This video is ${(file.size / 1e6).toFixed(0)} MB. The limit is ${maxMb} MB. Record about 20 seconds, slowly around the object.`;
  }
  return null;
}

export function validateProjectForm({ name, mode }) {
  if (!String(name || "").trim()) return "Give the project a name.";
  if (String(name).trim().length > 120) return "The name is too long (120 characters at most).";
  if (!["object", "map"].includes(mode)) return "Pick Object or Map.";
  return null;
}

export function formatDate(iso, now = new Date()) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? `today ${pad(d.getHours())}:${pad(d.getMinutes())}` : day;
}

export function formatMB(bytes) {
  return `${(bytes / 1e6).toFixed(1)} MB`;
}

/** Apply a Realtime payload ({eventType, new, old}) to the list of projects, newest first. */
export function applyChange(list, payload) {
  const t = payload.eventType;
  if (t === "DELETE") return list.filter((p) => p.id !== (payload.old && payload.old.id));
  const row = payload.new;
  if (!row) return list;
  const rest = list.filter((p) => p.id !== row.id);
  return [row, ...rest].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

export function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Files of a finished project that the viewer needs, as storage paths under the result prefix. */
export function resultPaths(project) {
  const files = (project.report && project.report.files) || [];
  return files.map((f) => `${project.result_path}/${f}`);
}

/** Signed URLs for a project's result files (view and download). Returns {files, downloads, missing}: missing lists every requested file
 *  that got no URL (a whole-call error or a per-file error), so the page can say so instead of opening a viewer with gaps. */
export function collectSignedUrls(view, dl, prefix) {
  const files = {}, downloads = {}, missing = [];
  const rel = (p) => (p || "").slice(prefix.length);
  if (view && view.error) return { files, downloads, missing: [`all files (${view.error.message})`] };
  for (const r of (view && view.data) || []) {
    if (r.signedUrl) files[rel(r.path)] = r.signedUrl; else missing.push(`${rel(r.path)}${r.error ? ` (${r.error})` : ""}`);
  }
  if (dl && dl.error) missing.push(`STL download links (${dl.error.message})`);
  for (const r of (dl && dl.data) || []) {
    if (r.signedUrl) downloads[rel(r.path)] = r.signedUrl; else missing.push(`${rel(r.path)} download${r.error ? ` (${r.error})` : ""}`);
  }
  return { files, downloads, missing };
}

/** The uploaded video for the viewer's Source tab, from a createSignedUrl result: {url, name} or {error, name}. A failure is shown to the user. */
export function videoLinkFrom(res, path) {
  const name = path ? `your upload (${String(path).split("/").pop()})` : "";
  if (!path) return { error: "this project has no stored video", name };
  if (!res || res.error || !res.data || !res.data.signedUrl) return { error: res && res.error ? res.error.message || String(res.error) : "no signed link was returned", name };
  return { url: res.data.signedUrl, name };
}
