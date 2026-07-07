/* Service worker for 完形填空 · Chinese Cloze.
 *
 * Strategy:
 *   - Static shell (html, React, fonts, icons): cache-first (fast, offline-ready).
 *   - Narration under data/audio/: cache-first, populated at runtime on first play
 *     (85 MB of mp3s — too large to precache, so it's cached lazily and then works
 *      offline). See audioCacheFirst for the Range-request handling.
 *   - Other documents + JSON data under data/: network-first, falling back to cache
 *     (so re-syncing stories or rebundling shows up immediately when online,
 *      and everything still works offline).
 *
 * Bump CACHE when the shell changes so old caches are purged on activate.
 */
const CACHE = 'chinese-cloze-v13';

const SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './vendor/react.production.min.js',
  './vendor/react-dom.production.min.js',
  './fonts/fonts.css',
  './fonts/fraunces-normal-latin.woff2',
  './fonts/fraunces-italic-latin.woff2',
  './fonts/fraunces-normal-latin-ext.woff2',
  './fonts/fraunces-italic-latin-ext.woff2',
  './fonts/fraunces-normal-vietnamese.woff2',
  './fonts/fraunces-italic-vietnamese.woff2',
  './icons/favicon.svg',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon-180.png',
  './data/stories/index.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // addAll is atomic; if one shell URL 404s the whole install fails, so keep the list honest.
    await cache.addAll(SHELL);
    // Precache every story in the index — adding a story needs only a re-sync, not an SW edit.
    try {
      const res = await fetch('./data/stories/index.json', { cache: 'no-cache' });
      if (res.ok) {
        const index = await res.json();
        const urls = (Array.isArray(index) ? index : [])
          .map((e) => './data/stories/' + (typeof e === 'string' ? e : e.id) + '.json');
        await Promise.all(urls.map((u) => cache.add(u).catch(() => {})));
      }
    } catch (e) { /* offline install: stories fill in via runtime caching later */ }
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

function isDocOrData(url, req) {
  return req.mode === 'navigate'
    || url.pathname.endsWith('/')
    || url.pathname.endsWith('/index.html')
    || url.pathname.includes('/data/');
}

async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res && res.ok && res.type === 'basic') cache.put(req, res.clone());
    return res;
  } catch (e) {
    const cached = await cache.match(req);
    if (cached) return cached;
    if (req.mode === 'navigate') {
      const shell = (await cache.match('./index.html')) || (await cache.match('./'));
      if (shell) return shell;
    }
    throw e;
  }
}

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res && res.ok && res.type === 'basic') {
    const cache = await caches.open(CACHE);
    cache.put(req, res.clone());
  }
  return res;
}

// Narration mp3s: cache-first, but <audio> issues ranged GETs whose 206 responses
// can't be stored (cache.put rejects 206). So on a miss we fetch a FULL response by
// URL (no Range header) to cache a plain 200, and return that — the audio element
// accepts a full body for a ranged request. On later hits, cache.match(url) ignores
// Range and serves the cached 200, so playback works offline.
async function audioCacheFirst(req) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(req.url);
  if (cached) return cached;
  const full = await fetch(req.url);
  if (full && full.ok && full.type === 'basic') cache.put(req.url, full.clone());
  return full;
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // never touch cross-origin
  if (url.pathname.includes('/data/audio/')) { event.respondWith(audioCacheFirst(req)); return; }
  event.respondWith(isDocOrData(url, req) ? networkFirst(req) : cacheFirst(req));
});
