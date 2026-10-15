// Dexter PWA service worker.
//
// Just enough to make the app installable to a phone's home screen and open it
// full-screen, with the app *shell* cached so a flaky connection still loads the
// UI. Live data (the API, SSE, audio) is never cached - those always hit the
// network, so the phone never shows stale state.

const CACHE = 'dexter-shell-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }
  // Only ever cache same-origin GETs for the shell. Everything dynamic is left
  // to the browser's default (network) handling.
  if (
    req.method !== 'GET' ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/health')
  ) {
    return;
  }

  // Network-first: fresh when online, cached shell when offline.
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('index.html'))),
  );
});
