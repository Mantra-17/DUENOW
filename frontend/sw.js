/* ClassFlow Service Worker — Cache-first for assets, Network-first for API */

const CACHE_NAME    = 'classflow-v1';
const OFFLINE_URL   = '/';

/* All static assets to pre-cache on install */
const PRECACHE_URLS = [
    '/',
    '/css/variables.css',
    '/css/base.css',
    '/css/nav.css',
    '/css/layout.css',
    '/css/cards.css',
    '/css/subjects.css',
    '/css/modal.css',
    '/css/profile.css',
    '/css/notifications.css',
    '/css/utilities.css',
    '/css/animations.css',
    '/js/config.js',
    '/js/state.js',
    '/js/api.js',
    '/js/theme.js',
    '/js/avatar.js',
    '/js/render.js',
    '/js/notifications.js',
    '/js/profile.js',
    '/js/app.js',
    '/icons/icon-192.png',
    '/icons/icon-512.png',
    '/manifest.json',
];

/* ════════════════════════════════════════════
   INSTALL — pre-cache all static assets
════════════════════════════════════════════ */
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE_URLS))
    );
    self.skipWaiting();   // activate immediately, don't wait for old SW to die
});

/* ════════════════════════════════════════════
   ACTIVATE — delete old caches
════════════════════════════════════════════ */
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            )
        )
    );
    self.clients.claim();   // take control of all open tabs immediately
});

/* ════════════════════════════════════════════
   FETCH — routing strategy
════════════════════════════════════════════ */
self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip non-GET and cross-origin requests
    if (request.method !== 'GET' || url.origin !== location.origin) return;

    // API routes → Network-first (always try live data)
    if (url.pathname.startsWith('/tasks') ||
        url.pathname.startsWith('/stats') ||
        url.pathname.startsWith('/subjects') ||
        url.pathname.startsWith('/notifications') ||
        url.pathname.startsWith('/health')) {
        event.respondWith(networkFirst(request));
        return;
    }

    // Static assets → Cache-first (fast, offline-safe)
    event.respondWith(cacheFirst(request));
});

/* ────────────────────────────────────────────
   STRATEGIES
──────────────────────────────────────────── */

/** Cache-first: serve from cache, fall back to network, cache the result */
async function cacheFirst(request) {
    const cached = await caches.match(request);
    if (cached) return cached;

    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch {
        // Offline fallback — serve the cached shell
        const fallback = await caches.match(OFFLINE_URL);
        return fallback || new Response('Offline', { status: 503 });
    }
}

/** Network-first: try network, fall back to cache if offline */
async function networkFirst(request) {
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch {
        const cached = await caches.match(request);
        if (cached) return cached;
        return new Response(
            JSON.stringify({ error: 'You are offline. Showing cached data.' }),
            { status: 503, headers: { 'Content-Type': 'application/json' } }
        );
    }
}

/* ════════════════════════════════════════════
   PUSH NOTIFICATIONS (Phase 2 — ready to wire up)
════════════════════════════════════════════ */
self.addEventListener('push', event => {
    if (!event.data) return;
    const data = event.data.json();
    event.waitUntil(
        self.registration.showNotification(data.title || 'ClassFlow', {
            body:    data.body   || 'You have a new update.',
            icon:    '/icons/icon-192.png',
            badge:   '/icons/icon-96.png',
            tag:     data.tag   || 'classflow-notif',
            data:    { url: data.url || '/' },
            actions: [
                { action: 'view',    title: 'View'    },
                { action: 'dismiss', title: 'Dismiss' },
            ],
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    if (event.action === 'dismiss') return;
    const url = event.notification.data?.url || '/';
    event.waitUntil(clients.openWindow(url));
});
