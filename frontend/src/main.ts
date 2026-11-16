import type { UserAccountResponse } from "./shared/api-types.ts";
import { getAccount, logout } from "./shared/api.ts";
import { clearEl, el } from "./shared/dom.ts";
import { renderAuthView } from "./features/auth/authView.ts";
import { renderLibraryView } from "./features/library/libraryView.ts";
import { renderActivityView } from "./features/activity/activityView.ts";
import { renderInsightsView } from "./features/insights/insightsView.ts";
import { renderWishlistView } from "./features/wishlist/wishlistView.ts";
import { renderSettingsView } from "./features/settings/settingsView.ts";
import { renderAdminView } from "./features/admin/adminView.ts";

let currentUser: UserAccountResponse | null = null;
let currentView: string = "library";

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
      currentView = "library";
      await renderApp();
    });
    return;
  }

  const userSnapshot = currentUser;
  const navItems: Array<[string, string]> = [
    ["library", "Library"],
    ["activity", "Activity"],
    ["insights", "Insights"],
    ["wishlist", "Wishlist"],
    ["settings", "Settings"],
  ];
  if (userSnapshot.role === "admin") {
    navItems.push(["admin", "Administration"]);
  }

  const mainHost = el("main", { class: "app-main" });
  const navBar = el("header", { class: "topbar" }, [
    el("div", { class: "brand" }, [el("strong", {}, ["Vertex"]), el("span", { class: "muted" }, [` @${userSnapshot.username}`])]),
    el("nav", { class: "nav-tabs", "aria-label": "Main navigation" }, [
      ...navItems.map(([key, label]) => el("button", {
        type: "button",
        class: currentView === key ? "btn btn-primary" : "btn btn-ghost",
        ...(currentView === key ? { "aria-current": "page" } : {}),
        onClick: async () => {
          currentView = key;
          await renderApp();
        },
      }, [label])),
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
  ]);

  root.appendChild(navBar);
  root.appendChild(mainHost);

  if (currentView === "activity") {
    await renderActivityView(mainHost);
  } else if (currentView === "insights") {
    await renderInsightsView(mainHost);
  } else if (currentView === "wishlist") {
    await renderWishlistView(mainHost);
  } else if (currentView === "settings") {
    await renderSettingsView(mainHost, userSnapshot);
  } else if (currentView === "admin" && userSnapshot.role === "admin") {
    await renderAdminView(mainHost, userSnapshot);
  } else {
    await renderLibraryView(mainHost);
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
