// App-shell cache. Network-first so a new deploy is never masked by a stale cache; the cache is only the
// offline / slow-link fallback. API, WebSocket and audio signalling are never cached.
const CACHE = "rr-shell-v56";
const SHELL = [
  "/", "/style.css", "/manifest.webmanifest", "/icons/icon-192.png",
  "/src/main.js", "/src/api.js", "/src/audio.js", "/src/components/freedv.js", "/src/worklets/capture.js", "/src/util.js", "/src/prefs.js", "/src/tuner.js", "/src/pages/login.js", "/src/pages/radio.js", "/src/pages/admin.js",
  "/src/components/freq-display.js", "/src/components/vfo-b.js", "/src/components/memories.js", "/src/components/vfo-panels.js", "/src/components/tuning-strip.js", "/src/meter-cal.js", "/src/components/meters.js",
  "/src/components/controls.js", "/src/components/ptt.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => Promise.allSettled(SHELL.map((u) => c.add(u)))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname === "/ws") return;
  e.respondWith(
    fetch(r).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(r, copy)); }
      return res;
    }).catch(() => caches.match(r).then((m) => m || caches.match("/"))),
  );
});
