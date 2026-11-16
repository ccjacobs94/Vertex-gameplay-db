/* Static-only Service Worker: NEVER caches private /api/* routes and wipes caches on logout. */

const STATIC_CACHE = "vertex-v2-static-3";
const STATIC_ASSETS = [
  "/",
  "/manifest.webmanifest",
  "/assets/styles/main.css",
  "/assets/main.js",
  "/assets/shared/api.js",
  "/assets/shared/dom.js",
  "/assets/features/auth/authView.js",
  "/assets/features/library/libraryView.js",
  "/assets/features/activity/activityView.js",
  "/assets/features/insights/insightsView.js",
  "/assets/features/wishlist/wishlistView.js",
  "/assets/features/settings/settingsView.js",
  "/assets/features/admin/adminView.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== STATIC_CACHE || k.startsWith("vertex-pwa-"))
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "VERTEX_LOGOUT") {
    event.waitUntil(
      caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
    );
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  if (req.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  // Strictly NEVER cache API or health endpoints.
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz" || url.pathname === "/readyz") {
    event.respondWith(fetch(req, { cache: "no-store" }));
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req);
    })
  );
});
