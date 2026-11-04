import test from "node:test";
import assert from "node:assert/strict";
import { parseRoute, videoPath, validateVideo, validateProjectForm, statusInfo, formatDate, applyChange, esc, resultPaths, extensionOf, MAX_VIDEO_MB } from "../src/lib.js";

test("routes", () => {
  assert.deepEqual(parseRoute(""), { name: "home" });
  assert.deepEqual(parseRoute("#/projects"), { name: "projects" });
  assert.deepEqual(parseRoute("#/project/abc-1"), { name: "project", id: "abc-1" });
  assert.deepEqual(parseRoute("#/project"), { name: "home" });
  assert.deepEqual(parseRoute("#/nope"), { name: "home" });
});

test("video path puts the user id first, as the storage policies require", () => {
  assert.equal(videoPath("u1", "p1", "Clip.MOV"), "u1/p1/video.mov");
  assert.equal(videoPath("u1", "p1", "noext"), "u1/p1/video.mp4");
  assert.equal(extensionOf("a.b.webm"), "webm");
  assert.throws(() => videoPath("", "p1", "a.mp4"));
});

test("video validation", () => {
  assert.match(validateVideo(null), /Choose or record/);
  assert.match(validateVideo({ type: "image/png", size: 5 }), /not a video/);
  assert.match(validateVideo({ type: "video/mp4", size: 0 }), /empty/);
  assert.match(validateVideo({ type: "video/mp4", size: (MAX_VIDEO_MB + 1) * 1_000_000 }), /limit is 50 MB/);
  assert.equal(validateVideo({ type: "video/mp4", size: 20_000_000 }), null);
  assert.equal(validateVideo({ type: "", size: 20_000_000 }), null); // some phones give no type
});

test("project form validation", () => {
  assert.match(validateProjectForm({ name: "  ", mode: "object" }), /name/);
  assert.match(validateProjectForm({ name: "x".repeat(121), mode: "object" }), /too long/);
  assert.match(validateProjectForm({ name: "mug", mode: "room" }), /Object or Map/);
  assert.equal(validateProjectForm({ name: "mug", mode: "map" }), null);
});

test("status chips and dates", () => {
  assert.equal(statusInfo("processing").label, "processing");
  assert.match(statusInfo("uploaded").hint, /worldkit\.worker/);
  assert.equal(statusInfo("weird").label, "weird");
  const now = new Date(2026, 10, 2, 15, 0);
  assert.equal(formatDate(new Date(2026, 10, 2, 9, 5).toISOString(), now), "today 09:05");
  assert.equal(formatDate(new Date(2026, 9, 30, 9, 5).toISOString(), now), "2026-10-30");
  assert.equal(formatDate("nope"), "");
});

test("realtime changes update the list, newest first", () => {
  let list = [{ id: "a", created_at: "2026-11-01", status: "uploaded" }];
  list = applyChange(list, { eventType: "INSERT", new: { id: "b", created_at: "2026-11-02", status: "uploaded" } });
  assert.deepEqual(list.map((p) => p.id), ["b", "a"]);
  list = applyChange(list, { eventType: "UPDATE", new: { id: "a", created_at: "2026-11-01", status: "done" } });
  assert.equal(list.find((p) => p.id === "a").status, "done");
  assert.equal(list.length, 2);
  list = applyChange(list, { eventType: "DELETE", old: { id: "b" } });
  assert.deepEqual(list.map((p) => p.id), ["a"]);
});

test("escaping and result paths", () => {
  assert.equal(esc(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
  assert.deepEqual(resultPaths({ result_path: "u/p", report: { files: ["model.glb", "parts/parts.json"] } }), ["u/p/model.glb", "u/p/parts/parts.json"]);
  assert.deepEqual(resultPaths({ result_path: "u/p", report: null }), []);
});

test("help is a route", async () => {
  const { parseRoute } = await import("../src/lib.js");
  assert.deepEqual(parseRoute("#/help"), { name: "help" });
});

test("collectSignedUrls reports every file without a signed URL instead of skipping it", async () => {
  const { collectSignedUrls } = await import("../src/lib.js");
  const pre = "u/p/";
  const ok = collectSignedUrls({ data: [{ path: "u/p/model.glb", signedUrl: "https://a" }] }, { data: [{ path: "u/p/model_print.stl", signedUrl: "https://b" }] }, pre);
  assert.deepEqual(ok.missing, []);
  assert.equal(ok.files["model.glb"], "https://a");
  assert.equal(ok.downloads["model_print.stl"], "https://b");
  const bad = collectSignedUrls({ data: [{ path: "u/p/model.glb", signedUrl: null, error: "Object not found" }] }, { error: { message: "timeout" } }, pre);
  assert.equal(bad.missing.length, 2);
  assert.match(bad.missing[0], /model\.glb \(Object not found\)/);
  assert.match(bad.missing[1], /timeout/);
  assert.match(collectSignedUrls({ error: { message: "403" } }, null, pre).missing[0], /all files \(403\)/);
});
