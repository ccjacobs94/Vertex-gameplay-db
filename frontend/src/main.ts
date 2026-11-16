import type { UserAccountResponse } from "./shared/api-types.ts";
import { enqueueAllSyncs, getAccount, logout } from "./shared/api.ts";
import { clearEl, el } from "./shared/dom.ts";
import { LUCIDE_ICONS, iconSpan } from "./shared/media.ts";
import { renderAuthView } from "./features/auth/authView.ts";
import { renderLibraryView } from "./features/library/libraryView.ts";
import { renderActivityView } from "./features/activity/activityView.ts";
import { renderInsightsView } from "./features/insights/insightsView.ts";
import { renderWishlistView } from "./features/wishlist/wishlistView.ts";
import { renderSettingsView } from "./features/settings/settingsView.ts";
import { renderAdminView } from "./features/admin/adminView.ts";

let currentUser: UserAccountResponse | null = null;
const INITIAL_HASH = window.location.hash.replace(/^#/, "").trim();
const ALLOWED_VIEWS = new Set(["library", "activity", "insights", "wishlist", "settings", "admin"]);
let currentView: string = ALLOWED_VIEWS.has(INITIAL_HASH) ? INITIAL_HASH : "library";

async function clearClientCaches(): Promise<void> {
  if ("serviceWorker" in navigator && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({ type: "VERTEX_LOGOUT" });
  }
  if ("caches" in window) {
    const names = await caches.keys();
    await Promise.all(names.map((name) => caches.delete(name)));
  }
}

async function renderApp(): Promise<void> {
  const root = document.getElementById("app");
  if (!root) return;
  clearEl(root);

  if (!currentUser) {
    await renderAuthView(root, async (account: UserAccountResponse) => {
      currentUser = account;
      const hashView = window.location.hash.replace(/^#/, "").trim();
      currentView = ALLOWED_VIEWS.has(hashView) ? hashView : "library";
      await renderApp();
    });
    return;
  }

  const userSnapshot = currentUser;
  const navItems: Array<[string, string, string]> = [
    ["library", "Library", LUCIDE_ICONS.library],
    ["activity", "Activity", LUCIDE_ICONS.activity],
    ["insights", "Insights", LUCIDE_ICONS.insights],
    ["wishlist", "Wishlist", LUCIDE_ICONS.wishlist],
    ["settings", "Settings", LUCIDE_ICONS.settings],
  ];
  if (userSnapshot.role === "admin") {
    navItems.push(["admin", "Administration", LUCIDE_ICONS.admin]);
  }

  const mainHost = el("main", { class: "app-main" });
  const syncStatusBanner = el("div");
  const syncBtn = el("button", {
    type: "button",
    class: "btn btn-secondary",
    title: "Sync configured platform libraries",
    onClick: async () => {
      syncBtn.setAttribute("disabled", "true");
      try {
        const res = await enqueueAllSyncs();
        const count = Array.isArray(res.jobs) ? res.jobs.length : 0;
        syncStatusBanner.textContent = "";
        const b = el("div", { class: "banner banner-success", role: "status" }, [
          count > 0 ? `Queued ${count} background platform sync job(s).` : (res.message || "Sync check queued."),
        ]);
        syncStatusBanner.appendChild(b);
      } catch (err) {
        syncStatusBanner.textContent = "";
        syncStatusBanner.appendChild(el("div", { class: "banner banner-error", role: "status" }, [
          err instanceof Error ? err.message : "Failed to queue library sync",
        ]));
      } finally {
        syncBtn.removeAttribute("disabled");
      }
    },
  }, [iconSpan(LUCIDE_ICONS.sync), "Sync"]);

  const logoImg = el("img", {
    src: "/assets/vertex_logo.png",
    alt: "Vertex",
    class: "brand-logo",
    width: "38",
    height: "38",
  });

  const navBar = el("header", { class: "topbar" }, [
    el("div", { class: "topbar-inner" }, [
      el("div", { class: "brand-group" }, [
        logoImg,
        el("div", { class: "brand-text" }, [
          el("h1", { class: "brand-title" }, ["Vertex"]),
          el("p", { class: "subtitle" }, [`Self-Hosted Game Library • @${userSnapshot.username}`]),
        ]),
      ]),
      el("nav", { class: "nav-tabs", "aria-label": "Main navigation" }, [
        ...navItems.map(([key, label, iconSvg]) => el("button", {
          type: "button",
          class: currentView === key ? "btn btn-primary" : "btn btn-ghost",
          ...(currentView === key ? { "aria-current": "page" } : {}),
          onClick: async () => {
            currentView = key;
            window.location.hash = key === "library" ? "" : `#${key}`;
            await renderApp();
          },
        }, [iconSpan(iconSvg), label])),
        syncBtn,
        el("button", {
          type: "button",
          class: "btn btn-ghost",
          onClick: async () => {
            try {
              await logout();
            } finally {
              currentUser = null;
              currentView = "library";
              clearEl(mainHost);
              await clearClientCaches();
              await renderApp();
            }
          },
        }, ["Sign Out"]),
      ]),
    ]),
  ]);

  root.appendChild(navBar);
  mainHost.appendChild(syncStatusBanner);
  const viewContainer = el("div");
  mainHost.appendChild(viewContainer);
  root.appendChild(mainHost);

  if (currentView === "activity") {
    await renderActivityView(viewContainer);
  } else if (currentView === "insights") {
    await renderInsightsView(viewContainer);
  } else if (currentView === "wishlist") {
    await renderWishlistView(viewContainer);
  } else if (currentView === "settings") {
    await renderSettingsView(viewContainer, userSnapshot, (updated) => {
      currentUser = updated;
    });
  } else if (currentView === "admin" && userSnapshot.role === "admin") {
    await renderAdminView(viewContainer, userSnapshot);
  } else {
    await renderLibraryView(viewContainer);
  }
}

async function boot(): Promise<void> {
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  try {
    currentUser = await getAccount();
  } catch {
    currentUser = null;
  }
  await renderApp();
}

boot();
