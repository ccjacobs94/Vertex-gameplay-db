import type {
  WishlistDealEvaluationResponse,
  WishlistItemResponse,
} from "../../shared/api-types.ts";
import {
  createWishlistItem,
  deleteWishlistItem,
  getVapidPublicKey,
  getWishlistDeals,
  getWishlistHistory,
  listWishlist,
  purchaseWishlistItem,
  recordWishlistPrice,
  searchCatalog,
  searchWishlistCandidates,
  subscribePush,
  syncWishlistPrices,
  testPushNotification,
  updateWishlistItem,
  updateWishlistPlatforms,
} from "../../shared/api.ts";
import { clearEl, el, openModalDialog, showBanner } from "../../shared/dom.ts";
import { createCoverElement, createPlatformBrandIcons } from "../../shared/media.ts";

const PLATFORM_FAMILIES = ["PC", "Xbox", "PlayStation", "Nintendo Switch"];

export async function renderWishlistView(container: HTMLElement): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const dealsBox = el("div", { class: "stack" });
  const itemsBox = el("div", { class: "wishlist-grid" });

  const statusSel = el("select", { "aria-label": "Wishlist status" }, [
    el("option", { value: "active", selected: "selected" }, ["Active"]),
    el("option", { value: "purchased" }, ["Purchased"]),
    el("option", { value: "archived" }, ["Archived"]),
  ]) as HTMLSelectElement;
  const smartOnlyIn = el("input", { type: "checkbox" }) as HTMLInputElement;
  const aboveTargetIn = el("input", { type: "checkbox" }) as HTMLInputElement;

  function drawDeals(evalRes: WishlistDealEvaluationResponse): void {
    clearEl(dealsBox);
    dealsBox.appendChild(el("p", { class: "muted" }, [
      `Matching deals: ${evalRes.total_deals} | Smart upgrades: ${evalRes.smart_upgrades_count} | All-time lows: ${evalRes.all_time_lows_count}`,
    ]));
    if (!evalRes.deals.length) {
      dealsBox.appendChild(el("p", { class: "muted" }, ["No active deals match your current target thresholds."]));
      return;
    }
    const grid = el("div", { class: "wishlist-grid" });
    for (let i = 0; i < evalRes.deals.length; i += 1) {
      const d = evalRes.deals[i];
      const coverThumb = createCoverElement(d.game_title, d.platform_family, d.cover_url, i, [], "cover-thumb-md");
      grid.appendChild(el("article", { class: "wishlist-card" }, [
        coverThumb,
        el("div", { class: "wishlist-card-body" }, [
          el("div", { class: "row-between" }, [
            el("strong", {}, [d.game_title]),
            d.is_all_time_low ? el("span", { class: "badge badge-fav" }, ["ATL"]) : null,
          ]),
          el("p", { style: "margin:0" }, [d.deal_headline]),
          el("div", { class: "row-actions" }, [
            createPlatformBrandIcons(d.platform_family),
            el("span", { class: "muted" }, [`${d.storefront} • ${d.edition_name}${d.is_smart_upgrade ? " (Smart Upgrade)" : ""}`]),
          ]),
          el("span", { class: "muted" }, [`$${d.current_price_usd.toFixed(2)} (target $${d.target_price_usd.toFixed(2)}) • -${d.discount_pct}%`]),
        ]),
      ]));
    }
    dealsBox.appendChild(grid);
  }

  function openAddModal(): void {
    openModalDialog("Add Wishlist Item", (dialog, body) => {
      const modalBanner = el("div");
      const titleIn = el("input", { type: "text", required: true, autofocus: true }) as HTMLInputElement;
      let selectedCoverUrl: string | null = null;
      let selectedDeveloper = "";
      let selectedPublisher = "";
      titleIn.addEventListener("input", () => {
        selectedCoverUrl = null;
        selectedDeveloper = "";
        selectedPublisher = "";
      });
      const platSel = el("select", {}, PLATFORM_FAMILIES.map((p) => el("option", { value: p }, [p]))) as HTMLSelectElement;
      const targetIn = el("input", { type: "number", step: "0.01", min: "0", value: "25.00" }) as HTMLInputElement;
      const yearIn = el("input", { type: "number", placeholder: "Optional year" }) as HTMLInputElement;
      const genresIn = el("input", { type: "text", placeholder: "Action, RPG" }) as HTMLInputElement;
      const smartIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      smartIn.checked = true;
      const forceIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      const notesIn = el("textarea", { rows: "2" }) as HTMLTextAreaElement;
      const searchOut = el("div", { class: "catalog-pick-list" });

      const lookupBtn = el("button", {
        type: "button",
        class: "btn btn-secondary",
        onClick: async () => {
          const q = titleIn.value.trim();
          if (!q) {
            showBanner(modalBanner, "Enter a title first to search catalog & storefronts", "error");
            return;
          }
          clearEl(searchOut);
          try {
            const resp = await searchWishlistCandidates(q, 5);
            const cands = resp.candidates || [];
            if (!cands.length) {
              const hits = await searchCatalog(q, 5);
              if (!hits.length) {
                searchOut.appendChild(el("span", { class: "muted" }, ["No catalog or storefront matches found."]));
                return;
              }
              for (const h of hits) {
                searchOut.appendChild(el("button", {
                  type: "button",
                  class: "catalog-pick-item",
                  onClick: () => {
                    titleIn.value = h.title;
                    selectedCoverUrl = h.cover_url || null;
                    selectedDeveloper = "";
                    selectedPublisher = "";
                    if (h.release_year) yearIn.value = String(h.release_year);
                    if (h.genres.length) genresIn.value = h.genres.join(", ");
                  },
                }, [
                  createCoverElement(h.title, "Catalog", h.cover_url, 10, [], "catalog-pick-thumb"),
                  el("div", { class: "stack", style: "gap:0.1rem" }, [
                    el("strong", {}, [h.title]),
                    el("span", { class: "muted" }, [h.cached ? "Catalog (cached)" : "Steam Store"]),
                  ]),
                ]));
              }
              return;
            }
            for (const c of cands) {
              const priceLabel = c.current_price_usd !== null && c.current_price_usd !== undefined
                ? ` · $${Number(c.current_price_usd).toFixed(2)}`
                : "";
              const conflictLabel = c.ownership_conflict && c.ownership_conflict.already_in_library
                ? " [Owned/GP]"
                : "";
              searchOut.appendChild(el("button", {
                type: "button",
                class: "catalog-pick-item",
                onClick: () => {
                  titleIn.value = c.title;
                  selectedCoverUrl = c.cover_url || null;
                  selectedDeveloper = c.developer || "";
                  selectedPublisher = c.publisher || "";
                  if (c.release_year) yearIn.value = String(c.release_year);
                  if (c.genres && c.genres.length) genresIn.value = c.genres.join(", ");
                  if (c.ownership_conflict && c.ownership_conflict.conflict_warning) {
                    showBanner(modalBanner, c.ownership_conflict.conflict_warning, "info");
                  }
                },
              }, [
                createCoverElement(c.title, c.storefront, c.cover_url, 10, [], "catalog-pick-thumb"),
                el("div", { class: "stack", style: "gap:0.1rem" }, [
                  el("strong", {}, [c.title]),
                  el("span", { class: "muted" }, [`${c.storefront}${priceLabel}${conflictLabel}`]),
                ]),
              ]));
            }
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Candidate lookup failed", "error");
          }
        },
      }, ["Search Storefronts & Catalog"]);

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            const genres = genresIn.value.split(",").map((s) => s.trim()).filter(Boolean);
            await createWishlistItem({
              title: titleIn.value.trim(),
              platform_targets: [{ platform: platSel.value, target_price_usd: Number(targetIn.value || 25) }],
              release_year: yearIn.value.trim() ? Number(yearIn.value.trim()) : null,
              developer: selectedDeveloper,
              publisher: selectedPublisher,
              cover_url: selectedCoverUrl,
              genres,
              allow_smart_upgrade: smartIn.checked,
              force_track_owned: forceIn.checked,
              notes: notesIn.value,
            });
            dialog.close("saved");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Could not add wishlist item", "error");
          }
        },
      }, [
        modalBanner,
        el("label", {}, ["Title", titleIn]),
        el("div", { class: "row-actions" }, [lookupBtn]),
        searchOut,
        el("label", {}, ["Primary Platform Target", platSel]),
        el("label", {}, ["Target Price ($ USD)", targetIn]),
        el("label", {}, ["Release Year", yearIn]),
        el("label", {}, ["Genres (comma-separated)", genresIn]),
        el("label", { class: "row-check" }, [smartIn, "Allow smart edition upgrade alerts"]),
        el("label", { class: "row-check" }, [forceIn, "Track even if owned on platform (don't auto-mute)"]),
        el("label", {}, ["Notes", notesIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Add to Wishlist"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
    });
  }

  function openPriceModal(item: WishlistItemResponse): void {
    openModalDialog(`Record Price: ${item.canonical_game.title}`, (dialog, body) => {
      const modalBanner = el("div");
      const platSel = el("select", {}, PLATFORM_FAMILIES.map((p) => el("option", { value: p }, [p]))) as HTMLSelectElement;
      const storeIn = el("input", { type: "text", required: true, value: "steam" }) as HTMLInputElement;
      const edIn = el("input", { type: "text", required: true, value: "Standard Edition" }) as HTMLInputElement;
      const priceIn = el("input", { type: "number", step: "0.01", min: "0", required: true, value: "19.99" }) as HTMLInputElement;
      const regIn = el("input", { type: "number", step: "0.01", min: "0", placeholder: "Optional regular USD" }) as HTMLInputElement;
      const urlIn = el("input", { type: "url", placeholder: "Optional store URL" }) as HTMLInputElement;

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            const res = await recordWishlistPrice(item.id, {
              platform: platSel.value,
              storefront: storeIn.value.trim(),
              edition_name: edIn.value.trim(),
              price_usd: Number(priceIn.value || 0),
              regular_price_usd: regIn.value.trim() ? Number(regIn.value.trim()) : null,
              store_url: urlIn.value.trim(),
            });
            dialog.close("saved");
            showBanner(bannerBox, `Recorded $${res.price_usd.toFixed(2)} for ${item.canonical_game.title}${res.is_all_time_low ? " (NEW ALL-TIME LOW)" : ""}`, "info");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Failed recording price", "error");
          }
        },
      }, [
        modalBanner,
        el("label", {}, ["Platform", platSel]),
        el("label", {}, ["Storefront", storeIn]),
        el("label", {}, ["Edition Name", edIn]),
        el("label", {}, ["Price ($ USD)", priceIn]),
        el("label", {}, ["Regular Price ($ USD)", regIn]),
        el("label", {}, ["Store URL", urlIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save Price"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
    });
  }

  function openTargetsModal(item: WishlistItemResponse): void {
    openModalDialog(`Targets & Settings: ${item.canonical_game.title}`, (dialog, body) => {
      const modalBanner = el("div");
      const defaultPlat = item.targets.length ? item.targets[0].platform_family : PLATFORM_FAMILIES[0];
      const platSel = el("select", {}, PLATFORM_FAMILIES.map((p) => {
        const opt = el("option", { value: p }, [p]) as HTMLOptionElement;
        if (p === defaultPlat) opt.selected = true;
        return opt;
      })) as HTMLSelectElement;
      const findTarget = (plat: string) => item.targets.find((t) => t.platform_family === plat);
      const initialTarget = findTarget(platSel.value);
      const targetIn = el("input", {
        type: "number",
        step: "0.01",
        min: "0",
        value: initialTarget ? initialTarget.target_price_usd.toFixed(2) : "25.00",
      }) as HTMLInputElement;
      const smartIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      smartIn.checked = item.allow_smart_upgrade;
      const forceIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      forceIn.checked = Boolean(initialTarget && !initialTarget.muted_owned_conflict && item.conflict.conflicted_platforms.includes(platSel.value));
      const notesIn = el("textarea", { rows: "2" }, [item.notes || ""]) as HTMLTextAreaElement;

      platSel.addEventListener("change", () => {
        const match = findTarget(platSel.value);
        if (match) {
          targetIn.value = match.target_price_usd.toFixed(2);
          forceIn.checked = !match.muted_owned_conflict && item.conflict.conflicted_platforms.includes(platSel.value);
        } else {
          targetIn.value = "25.00";
          forceIn.checked = false;
        }
      });

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            await updateWishlistItem(item.id, { notes: notesIn.value, allow_smart_upgrade: smartIn.checked });
            await updateWishlistPlatforms(item.id, {
              platform_targets: [{ platform: platSel.value, target_price_usd: Number(targetIn.value || 25) }],
              allow_smart_upgrade: smartIn.checked,
              force_track_owned: forceIn.checked,
            });
            dialog.close("saved");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Failed updating wishlist target", "error");
          }
        },
      }, [
        modalBanner,
        el("label", {}, ["Platform Target", platSel]),
        el("label", {}, ["Target Price ($ USD)", targetIn]),
        el("label", { class: "row-check" }, [smartIn, "Allow smart edition upgrades"]),
        el("label", { class: "row-check" }, [forceIn, "Unmute / force track if owned"]),
        el("label", {}, ["Notes", notesIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              showBanner(modalBanner, "");
              try {
                await updateWishlistPlatforms(item.id, { remove_platforms: [platSel.value] });
                dialog.close("removed");
                await refresh();
              } catch (err) {
                showBanner(modalBanner, err instanceof Error ? err.message : "Failed removing platform target", "error");
              }
            },
          }, ["Remove Target"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
    });
  }

  function openPurchaseModal(item: WishlistItemResponse): void {
    openModalDialog(`Mark Purchased: ${item.canonical_game.title}`, (dialog, body) => {
      const modalBanner = el("div");
      const platIn = el("input", { type: "text", required: true, value: "PC (Steam)" }) as HTMLInputElement;
      const edIn = el("input", { type: "text", value: "Standard Edition" }) as HTMLInputElement;
      const priceIn = el("input", { type: "number", step: "0.01", min: "0", placeholder: "Price paid (optional)" }) as HTMLInputElement;
      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            const res = await purchaseWishlistItem(item.id, {
              platform: platIn.value.trim(),
              edition_name: edIn.value.trim() || null,
              price_paid_usd: priceIn.value.trim() ? Number(priceIn.value.trim()) : null,
            });
            dialog.close("purchased");
            showBanner(bannerBox, res.already_purchased ? `Already linked to library game #${res.library_game_id}` : `Added to library as game #${res.library_game_id}`, "info");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Could not complete purchase", "error");
          }
        },
      }, [
        modalBanner,
        el("label", {}, ["Platform", platIn]),
        el("label", {}, ["Edition Purchased", edIn]),
        el("label", {}, ["Price Paid ($ USD)", priceIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Convert to Library"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
    });
  }

  async function openHistoryModal(item: WishlistItemResponse): Promise<void> {
    try {
      const hist = await getWishlistHistory(item.id, 50);
      openModalDialog(`Price History: ${item.canonical_game.title}`, (dialog, body) => {
        const rowsBox = el("div", { class: "stack" });
        const entries = Object.entries(hist.price_history_by_sku || {});
        if (!entries.length) {
          rowsBox.appendChild(el("p", { class: "muted" }, ["No SKUs or price observations recorded yet."]));
        }
        for (const [skuId, list] of entries) {
          const sku = hist.item.skus.find((s) => String(s.id) === skuId);
          rowsBox.appendChild(el("h3", {}, [sku ? `${sku.platform_family} / ${sku.storefront}` : `SKU #${skuId}`]));
          if (!list.length) {
            rowsBox.appendChild(el("p", { class: "muted" }, ["No history entries."]));
          } else {
            const ul = el("ul", { class: "bullet-list" });
            for (const p of list) {
              ul.appendChild(el("li", {}, [`${p.recorded_at.slice(0, 10)}: $${p.price_usd.toFixed(2)} (-${p.discount_pct}%)${p.is_all_time_low ? " [ATL]" : ""}`]));
            }
            rowsBox.appendChild(ul);
          }
        }
        body.appendChild(el("div", { class: "stack" }, [
          rowsBox,
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close() }, ["Close"]),
        ]));
      });
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading price history", "error");
    }
  }

  function drawItems(items: Array<WishlistItemResponse>): void {
    clearEl(itemsBox);
    if (!items.length) {
      itemsBox.appendChild(el("p", { class: "muted" }, ["No wishlist items match this filter."]));
      return;
    }
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      const targetsLine = item.targets.map((t) => `${t.platform_family}: $${t.target_price_usd.toFixed(2)}${t.muted_owned_conflict ? " [Muted Owned]" : ""}`).join(" | ");
      const skusLine = item.skus.map((s) => `${s.storefront}: ${s.current_price_usd != null ? `$${s.current_price_usd.toFixed(2)}` : "-"} (ATL ${s.historical_low_usd != null ? `$${s.historical_low_usd.toFixed(2)}` : "-"})`).join(" | ");
      const primaryPlat = item.targets.length ? item.targets[0].platform_family : "PC";
      const coverThumb = createCoverElement(item.canonical_game.title, primaryPlat, item.canonical_game.cover_url, i, [], "cover-thumb-md");
      itemsBox.appendChild(el("article", { class: "wishlist-card" }, [
        coverThumb,
        el("div", { class: "wishlist-card-body" }, [
          el("div", { class: "row-between" }, [
            el("h3", { style: "margin:0;font-size:1rem" }, [item.canonical_game.title]),
            el("span", { class: "badge" }, [item.status]),
          ]),
          item.conflict.conflict_warning ? el("span", { class: "error-text" }, [item.conflict.conflict_warning]) : null,
          targetsLine ? el("span", { class: "muted" }, [`Targets: ${targetsLine}`]) : null,
          skusLine ? el("span", { class: "muted" }, [`Prices: ${skusLine}`]) : null,
          item.notes ? el("span", { class: "muted" }, [item.notes]) : null,
          el("div", { class: "row-actions", style: "margin-top:0.3rem" }, [
            el("button", { type: "button", class: "btn btn-secondary", onClick: () => openPriceModal(item) }, ["+ Price"]),
            el("button", { type: "button", class: "btn btn-secondary", onClick: () => openHistoryModal(item) }, ["History"]),
            el("button", { type: "button", class: "btn btn-secondary", onClick: () => openTargetsModal(item) }, ["Targets"]),
            item.status === "active"
              ? el("button", { type: "button", class: "btn btn-primary", onClick: () => openPurchaseModal(item) }, ["Mark Purchased"])
              : null,
            el("button", {
              type: "button",
              class: "btn btn-ghost",
              onClick: async () => {
                try {
                  await deleteWishlistItem(item.id);
                  await refresh();
                } catch (err) {
                  showBanner(bannerBox, err instanceof Error ? err.message : "Could not delete item", "error");
                }
              },
            }, ["Delete"]),
          ]),
        ]),
      ]));
    }
  }

  async function refresh(): Promise<void> {
    try {
      const [items, deals] = await Promise.all([
        listWishlist(statusSel.value),
        getWishlistDeals(smartOnlyIn.checked, aboveTargetIn.checked),
      ]);
      drawDeals(deals);
      drawItems(items);
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading wishlist", "error");
    }
  }

  function urlBase64ToBytes(b64: string): Uint8Array {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const norm = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(norm);
    const arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) arr[i] = raw.charCodeAt(i);
    return arr;
  }

  const syncPricesBtn = el("button", {
    type: "button",
    class: "btn btn-secondary",
    onClick: async () => {
      syncPricesBtn.setAttribute("disabled", "true");
      try {
        const job = await syncWishlistPrices({ force: true });
        showBanner(bannerBox, `Wishlist price sync queued (job ${job.id}). In-app deal alerts refresh shortly.`, "success");
        await refresh();
      } catch (err) {
        showBanner(bannerBox, err instanceof Error ? err.message : "Failed queuing price sync", "error");
      } finally {
        syncPricesBtn.removeAttribute("disabled");
      }
    },
  }, ["Sync Prices"]);

  const enablePushBtn = el("button", {
    type: "button",
    class: "btn btn-ghost",
    style: "display:none",
    onClick: async () => {
      try {
        if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
          showBanner(bannerBox, "Browser PushManager not supported on this client.", "error");
          return;
        }
        const keyResp = await getVapidPublicKey();
        const reg = await navigator.serviceWorker.ready;
        const perm = await Notification.requestPermission();
        if (perm !== "granted") {
          showBanner(bannerBox, "Notification permission not granted.", "error");
          return;
        }
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToBytes(keyResp.public_key),
        });
        const jsonSub = sub.toJSON();
        await subscribePush({
          endpoint: jsonSub.endpoint || sub.endpoint,
          keys: {
            p256dh: (jsonSub.keys && jsonSub.keys.p256dh) || "",
            auth: (jsonSub.keys && jsonSub.keys.auth) || "",
          },
        });
        const testRes = await testPushNotification();
        showBanner(bannerBox, `Web Push enabled (test sent: ${testRes.sent}).`, "success");
      } catch (err) {
        showBanner(bannerBox, err instanceof Error ? err.message : "Could not enable Web Push", "error");
      }
    },
  }, ["Enable Deal Push"]);

  if ("serviceWorker" in navigator && "PushManager" in window) {
    getVapidPublicKey()
      .then((res) => {
        if (res.enabled && res.public_key) enablePushBtn.style.display = "";
      })
      .catch(() => {
        // Optional Web Push is disabled by default; keep button hidden cleanly.
      });
  }

  const toolbar = el("div", { class: "toolbar" }, [
    statusSel,
    el("label", { class: "row-check" }, [smartOnlyIn, "Smart upgrades only"]),
    el("label", { class: "row-check" }, [aboveTargetIn, "Include above target"]),
    syncPricesBtn,
    enablePushBtn,
    el("button", { type: "button", class: "btn btn-secondary", onClick: () => refresh() }, ["Refresh"]),
    el("button", { type: "button", class: "btn btn-primary", onClick: openAddModal }, ["+ Add Wishlist Item"]),
  ]);

  container.appendChild(el("section", { class: "stack" }, [
    el("h1", {}, ["Wishlist & Deals"]),
    bannerBox,
    toolbar,
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Deal Alerts & Smart Upgrades"]),
      dealsBox,
    ]),
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Tracked Games"]),
      itemsBox,
    ]),
  ]));

  statusSel.addEventListener("change", () => {
    void refresh();
  });
  smartOnlyIn.addEventListener("change", () => {
    void refresh();
  });
  aboveTargetIn.addEventListener("change", () => {
    void refresh();
  });

  await refresh();
}
