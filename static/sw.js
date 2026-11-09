"use strict";

const CACHE_NAME = "vertex-pwa-v56";
const STATIC_ASSETS = [
  "/manifest.webmanifest",
  "/static/vertex_logo.svg",
  "/static/vertex_logo.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/steam.svg",
  "/static/icons/xbox.svg",
  "/static/icons/playstation.svg",
  "/static/icons/switch.svg",
  "/static/icons/gamepass.svg",
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => {})
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.map((key) => {
            if (key !== CACHE_NAME) {
              return caches.delete(key);
            }
            return null;
          })
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache login/logout or OAuth endpoints
  if (
    url.pathname.startsWith("/login") ||
    url.pathname.startsWith("/logout") ||
    url.pathname.startsWith("/oauth/")
  ) {
    return;
  }

  // Network-first with cache fallback for GET API calls (/api/games, /api/stats, /api/wishlist)
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Network-first with cache fallback for app shell & static assets so updates appear immediately
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response && response.ok && !response.redirected) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
  );
});

// RFC 8030 Web Push Notification Listener
self.addEventListener("push", (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch {
      data = { title: "Vertex Deal Alert", body: event.data.text() };
    }
  }
  const title = data.title || "Vertex Deal Alert";
  const options = {
    body: data.body || "A game on your wishlist has dropped in price!",
    icon: data.icon || "/static/icons/icon-192.png",
    badge: data.badge || "/static/icons/icon-192.png",
    tag: data.tag || "vertex-deal-alert",
    data: data.data || { url: "/#tab-wishlist" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Notification click: Focus existing window or open wishlist view
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/#tab-wishlist";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ("focus" in client) {
          if ("navigate" in client) {
            client.navigate(targetUrl);
          }
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
