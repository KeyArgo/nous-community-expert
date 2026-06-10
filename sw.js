const CACHE = 'talio-v2';
// Precache only the small static assets and metadata. Data shards
// (search-data-N.json, search-index-N.json) are picked up at runtime by the
// generic fetch handler below; precaching them here would couple the cache
// version to a specific shard count, which changes as data grows.
const ASSETS = [
  '/', '/index.html', '/metadata.json',
  '/logo.svg', '/favicon.svg', '/og-image.svg',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(cached => {
      const fetchPromise = fetch(e.request).then(response => {
        if (response.ok && e.request.method === 'GET') {
          const clone = response.clone();
          caches.open(CACHE).then(cache => cache.put(e.request, clone));
        }
        return response;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
