// WorldKit service worker: caches the APP SHELL only (HTML, built JS/CSS, icons). Never videos, results, the viewer's model files or any Supabase call.
const CACHE = "worldkit-shell-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/logo.svg"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

function cacheable(url, req) {
  if (req.method !== "GET" || req.headers.has("range")) return false;
  if (url.origin !== self.location.origin) return false;          // Supabase, signed URLs and any other origin: network only
  if (url.pathname.startsWith("/viewer/")) return false;           // the viewer and its model files are big: network (browser HTTP cache) only
  return url.pathname === "/" || url.pathname === "/index.html" || url.pathname === "/manifest.webmanifest" || url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/");
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (!cacheable(url, e.request)) return;
  e.respondWith(
    caches.open(CACHE).then(async (c) => {
      const hit = await c.match(e.request);
      const net = fetch(e.request).then((r) => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
      return hit || net;  // shell opens offline; new versions are fetched in the background
    })
  );
});
