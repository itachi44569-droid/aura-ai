const CACHE = 'nova-v1';
const PRECACHE = ['/', '/static/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Network first, fallback to cache for navigation requests
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);

  // Don't cache API calls or WebSocket upgrades
  if (url.pathname.startsWith('/ws') || url.pathname.startsWith('/chat') ||
      url.pathname.startsWith('/auth') || url.pathname.startsWith('/admin') ||
      url.pathname.startsWith('/voice') || url.pathname.startsWith('/ingest')) {
    return;
  }

  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
