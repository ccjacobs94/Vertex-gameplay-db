import type {
  CatalogSearchResultResponse,
  GameAchievementsResponse,
  GameResponse,
  LibraryStatsResponse,
  PlaytimePromptResponse,
} from "../../shared/api-types.ts";
import {
  addGame,
  confirmReconcile,
  deleteGame,
  dismissReconcile,
  enrichLibrary,
  exportLibraryJson,
  getGameAchievements,
  getReconcileCandidates,
  getStats,
  getTaxonomy,
  importLegacySqlite,
  importLibraryJson,
  listGames,
  listPlaytimePrompts,
  resetReconcileDismissals,
  resolvePlaytimePrompt,
  searchCatalog,
  syncGameAchievements,
  updateGame,
} from "../../shared/api.ts";
import { clearEl, el, openModalDialog, showBanner } from "../../shared/dom.ts";
import {
  BRAND_ICONS,
  LUCIDE_ICONS,
  createCoverElement,
  createPlatformBrandIcons,
  formatCardPlatformLabel,
  getPlatformBrands,
  iconSpan,
  makeFallbackAchIcon,
} from "../../shared/media.ts";

const STATUSES = ["Not Played", "Plan to Play", "Playing", "Beaten", "Completed", "Dropped"];
const OWNERSHIPS = ["Owned", "Subscription", "Borrowed", "None"];

function buildSelectOptions(items: Array<string>, currentVal: string): Array<HTMLElement> {
  const opts = items.includes(currentVal) ? items : [currentVal, ...items];
  return opts.map((item) => {
    const opt = el("option", { value: item }, [item]) as HTMLOptionElement;
    if (item === currentVal) opt.selected = true;
    return opt;
  });
}

function matchesPlatformFamily(game: GameResponse, family: string): boolean {
  if (!family) return true;
  const brands = getPlatformBrands(game.platform || game.source);
  if (brands.includes(family)) return true;
  const low = `${game.platform} ${game.source}`.toLowerCase();
  return low.includes(family);
}

function sortGames(games: Array<GameResponse>, sortBy: string): Array<GameResponse> {
  const copy = [...games];
  if (sortBy === "title_asc") {
    copy.sort((a, b) => a.title.localeCompare(b.title));
  } else if (sortBy === "rating_desc") {
    copy.sort((a, b) => (b.user_rating || b.critic_score || 0) - (a.user_rating || a.critic_score || 0) || b.playtime_hours - a.playtime_hours);
  } else if (sortBy === "year_desc") {
    copy.sort((a, b) => (b.release_year || 0) - (a.release_year || 0) || a.title.localeCompare(b.title));
  } else if (sortBy === "recent_desc") {
    copy.sort((a, b) => String(b.last_played || b.created_at).localeCompare(String(a.last_played || a.created_at)));
  } else {
    copy.sort((a, b) => b.playtime_hours - a.playtime_hours || a.title.localeCompare(b.title));
  }
  return copy;
}

export async function renderLibraryView(container: HTMLElement): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const promptsHost = el("div", { class: "stack" });
  const statsBox = el("div", { class: "stats-grid" });
  const listBox = el("div", { class: "games-grid" });

  let allGames: Array<GameResponse> = [];
  let cachedStats: LibraryStatsResponse | null = null;
  let pendingReconcileCount = 0;
  let taxonomy: { genres: Array<string>; tags: Array<string> } = { genres: [], tags: [] };

  let activePlatformFamily = "";
  let activeStatus = "";
  let activeOwnership = "";
  let activeGenre = "";
  let activeTag = "";
  let activeSearch = "";
  let favOnly = false;
  let gpOnly = false;
  let xpaOnly = false;
  let sortBy = "playtime_desc";
  let viewMode = localStorage.getItem("vertex_view_mode") === "list" ? "list" : "grid";

  const searchIn = el("input", { type: "search", placeholder: "Search titles, genres, notes...", "aria-label": "Search games" }) as HTMLInputElement;
  const statusSel = el("select", { "aria-label": "Status filter" }, [
    el("option", { value: "" }, ["All statuses"]),
    ...STATUSES.map((s) => el("option", { value: s }, [s])),
  ]) as HTMLSelectElement;
  const ownSel = el("select", { "aria-label": "Ownership filter" }, [
    el("option", { value: "" }, ["All ownership"]),
    ...OWNERSHIPS.map((o) => el("option", { value: o }, [o])),
  ]) as HTMLSelectElement;
  const genreSel = el("select", { "aria-label": "Genre filter" }, [
    el("option", { value: "" }, ["All genres"]),
  ]) as HTMLSelectElement;
  const tagSel = el("select", { "aria-label": "Tag filter" }, [
    el("option", { value: "" }, ["All tags"]),
  ]) as HTMLSelectElement;
  const sortSel = el("select", { "aria-label": "Sort games" }, [
    el("option", { value: "playtime_desc", selected: "selected" }, ["Most Played"]),
    el("option", { value: "title_asc" }, ["Title A-Z"]),
    el("option", { value: "rating_desc" }, ["Highest Rated"]),
    el("option", { value: "year_desc" }, ["Newest Release"]),
    el("option", { value: "recent_desc" }, ["Recently Played / Added"]),
  ]) as HTMLSelectElement;

  const platformPillHost = el("div", { class: "platform-pills" });
  const resultsCountEl = el("span", { class: "catalog-subtitle" }, ["Loading library..."]);
  const reconcileBtn = el("button", { type: "button", class: "btn btn-reconcile", onClick: () => openReconcileDialog() }, [
    iconSpan(LUCIDE_ICONS.reconcile),
    "Reconcile",
  ]);
  const triageBtn = el("button", { type: "button", class: "btn btn-triage", onClick: () => openTriageDialog() }, [
    iconSpan(LUCIDE_ICONS.triage),
    "Quick Triage",
  ]);

  function drawPlatformPills(): void {
    clearEl(platformPillHost);
    const defs: Array<[string, string, string]> = [
      ["", "All Platforms", ""],
      ["steam", "Steam", BRAND_ICONS.steam],
      ["xbox", "Xbox", BRAND_ICONS.xbox],
      ["playstation", "PlayStation", BRAND_ICONS.playstation],
      ["switch", "Switch", BRAND_ICONS.switch],
      ["gog", "GOG", BRAND_ICONS.gog],
    ];
    for (const [key, label, iconSvg] of defs) {
      const btn = el("button", {
        type: "button",
        class: activePlatformFamily === key ? "pill active" : "pill",
        onClick: () => {
          activePlatformFamily = key;
          drawPlatformPills();
          applyFiltersAndRender();
        },
      }, [iconSvg ? iconSpan(iconSvg) : null, label]);
      platformPillHost.appendChild(btn);
    }
  }

  function populateDynamicFilterDropdowns(): void {
    const genreSet = new Set<string>(taxonomy.genres);
    const tagSet = new Set<string>(taxonomy.tags);
    for (const g of allGames) {
      for (const gn of g.genres) genreSet.add(gn);
      for (const tg of g.tags) tagSet.add(tg);
    }
    const prevG = activeGenre;
    clearEl(genreSel);
    genreSel.appendChild(el("option", { value: "" }, ["All genres"]));
    for (const g of Array.from(genreSet).sort()) {
      const opt = el("option", { value: g }, [g]) as HTMLOptionElement;
      if (g === prevG) opt.selected = true;
      genreSel.appendChild(opt);
    }
    const prevT = activeTag;
    clearEl(tagSel);
    tagSel.appendChild(el("option", { value: "" }, ["All tags"]));
    for (const t of Array.from(tagSet).sort()) {
      const opt = el("option", { value: t }, [t]) as HTMLOptionElement;
      if (t === prevT) opt.selected = true;
      tagSel.appendChild(opt);
    }
  }

  function drawStats(): void {
    clearEl(statsBox);
    if (!cachedStats) return;
    const planCount = allGames.filter((g) => g.completion_status === "Plan to Play").length;
    const cards: Array<[string, string, string]> = [
      ["Unique Titles", String(cachedStats.unique_titles), ""],
      ["Playing", String(cachedStats.playing), "Playing"],
      ["Plan to Play", String(planCount), "Plan to Play"],
      ["Completed / Beaten", String(cachedStats.completed), "Completed"],
      ["Favorites", String(cachedStats.favorites), "__fav__"],
      ["Hours Logged", `${cachedStats.total_playtime_hours.toFixed(1)}h`, "__hours__"],
    ];
    for (const [label, val, targetFilter] of cards) {
      if (targetFilter === "__hours__") {
        statsBox.appendChild(el("div", { class: "stat-card" }, [
          el("span", { class: "stat-val" }, [val]),
          el("span", { class: "stat-lbl" }, [label]),
        ]));
        continue;
      }
      const isActive = targetFilter === "__fav__"
        ? favOnly
        : (targetFilter !== "" && activeStatus === targetFilter);
      statsBox.appendChild(el("button", {
        type: "button",
        class: isActive ? "stat-card active" : "stat-card",
        onClick: () => {
          if (targetFilter === "__fav__") {
            favOnly = !favOnly;
          } else {
            activeStatus = activeStatus === targetFilter ? "" : targetFilter;
            statusSel.value = activeStatus;
          }
          drawStats();
          applyFiltersAndRender();
        },
      }, [
        el("span", { class: "stat-val" }, [val]),
        el("span", { class: "stat-lbl" }, [label]),
      ]));
    }
  }

  function drawPrompts(prompts: Array<PlaytimePromptResponse>): void {
    clearEl(promptsHost);
    for (const p of prompts) {
      const coverThumb = createCoverElement(p.title, p.platform, p.cover_url, 10, [], "cover-thumb-sm");
      if (p.prompt_type === "congrats") {
        const verb = p.current_status === "Completed" ? "completing" : "beating";
        const achText = p.achievements_total > 0
          ? ` • ${p.achievements_unlocked}/${p.achievements_total} achievements`
          : (p.achievements_unlocked > 0 ? ` • ${p.achievements_unlocked} achievements` : "");
        const sessText = p.sessions_count > 0
          ? ` • ${p.sessions_count} session${p.sessions_count === 1 ? "" : "s"}`
          : "";
        const ratingText = p.user_rating ? ` • ★ ${p.user_rating}/10` : "";
        const gameObj = allGames.find((g) => g.id === p.game_id);
        promptsHost.appendChild(el("article", { class: "playtime-prompt-card playtime-prompt-congrats" }, [
          el("div", { class: "playtime-prompt-left" }, [
            coverThumb,
            el("div", { class: "stack", style: "gap:0.2rem" }, [
              el("div", { class: "row-actions" }, [
                el("strong", {}, [`Congratulations on ${verb} ${p.title}!`]),
                p.delta_hours > 0 ? el("span", { class: "playtime-prompt-delta" }, [`+${p.delta_hours}h final`]) : null,
                el("span", { class: "muted" }, [formatCardPlatformLabel(p.platform)]),
              ]),
              el("span", { class: "muted" }, [`${p.total_playtime_hours}h total${sessText}${achText}${ratingText}`]),
            ]),
          ]),
          el("div", { class: "row-actions" }, [
            gameObj ? el("button", {
              type: "button",
              class: "btn btn-primary",
              onClick: () => openEditDialog(gameObj),
            }, ["★ Rate & Review"]) : null,
            el("button", {
              type: "button",
              class: "btn btn-secondary",
              onClick: async () => {
                await resolvePlaytimePrompt(p.id, "dismiss");
                await refresh();
              },
            }, ["Dismiss"]),
          ]),
        ]));
        continue;
      }
      promptsHost.appendChild(el("article", { class: "playtime-prompt-card" }, [
        el("div", { class: "playtime-prompt-left" }, [
          coverThumb,
          el("div", { class: "stack", style: "gap:0.2rem" }, [
            el("div", { class: "row-actions" }, [
              el("strong", {}, [p.title]),
              el("span", { class: "playtime-prompt-delta" }, [`+${p.delta_hours}h`]),
              el("span", { class: "muted" }, [formatCardPlatformLabel(p.platform)]),
            ]),
            el("span", { class: "muted" }, [`Playtime increased ${p.previous_hours}h -> ${p.new_hours}h while marked "${p.current_status}"`]),
          ]),
        ]),
        el("div", { class: "row-actions" }, [
          el("button", {
            type: "button",
            class: "btn btn-primary",
            onClick: async () => {
              await resolvePlaytimePrompt(p.id, "accept");
              await refresh();
            },
          }, ["Mark Playing"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              await resolvePlaytimePrompt(p.id, "dismiss");
              await refresh();
            },
          }, [`Keep ${p.current_status}`]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              await resolvePlaytimePrompt(p.id, "mute");
              await refresh();
            },
          }, ["Mute"]),
        ]),
      ]));
    }
  }

  function buildCoverLookupMap(): Map<number, Array<string>> {
    const byCanonical = new Map<number, Array<string>>();
    for (const g of allGames) {
      if (g.canonical_id && g.cover_url) {
        const list = byCanonical.get(g.canonical_id) || [];
        if (!list.includes(g.cover_url)) list.push(g.cover_url);
        byCanonical.set(g.canonical_id, list);
      }
    }
    return byCanonical;
  }

  function applyFiltersAndRender(): void {
    const q = activeSearch.toLowerCase();
    const filtered = allGames.filter((g) => {
      if (q) {
        const hay = `${g.title} ${g.platform} ${g.genres.join(" ")} ${g.tags.join(" ")} ${g.notes}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (!matchesPlatformFamily(g, activePlatformFamily)) return false;
      if (activeStatus) {
        if (activeStatus === "Completed") {
          if (g.completion_status !== "Completed" && g.completion_status !== "Beaten") return false;
        } else if (g.completion_status !== activeStatus) {
          return false;
        }
      }
      if (activeOwnership && g.ownership_status !== activeOwnership) return false;
      if (activeGenre && !g.genres.includes(activeGenre)) return false;
      if (activeTag && !g.tags.includes(activeTag)) return false;
      if (favOnly && !g.favorite) return false;
      if (gpOnly && !g.is_game_pass) return false;
      if (xpaOnly && !g.is_play_anywhere) return false;
      return true;
    });

    const sorted = sortGames(filtered, sortBy);
    resultsCountEl.textContent = `${sorted.length} of ${allGames.length} games`;
    drawGames(sorted);
  }

  function drawGames(games: Array<GameResponse>): void {
    clearEl(listBox);
    listBox.classList.toggle("list-view", viewMode === "list");
    if (!games.length) {
      listBox.appendChild(el("div", { class: "panel" }, [
        el("p", { class: "muted" }, ["No games match your filters yet. Try clearing filters, adding a manual game, or syncing a platform in Settings."]),
      ]));
      return;
    }

    const altCoverMap = buildCoverLookupMap();
    const frag = document.createDocumentFragment();

    for (let i = 0; i < games.length; i += 1) {
      const game = games[i];
      const altCovers = game.canonical_id ? (altCoverMap.get(game.canonical_id) || []).filter((u) => u !== game.cover_url) : [];
      const coverEl = createCoverElement(game.title, game.platform, game.cover_url, i, altCovers);

      if (game.favorite) {
        const favBadge = el("span", { class: "fav-star", title: "Favorited" }, [iconSpan(LUCIDE_ICONS.starFilled)]);
        coverEl.appendChild(favBadge);
      }

      const ratingVal = game.user_rating || (game.critic_score ? Math.round(game.critic_score / 10) : null);
      if (ratingVal) {
        const prefix = game.user_rating ? "★ " : "Meta ";
        const text = game.user_rating ? `${prefix}${game.user_rating}/10` : `${prefix}${game.critic_score}`;
        coverEl.appendChild(el("span", { class: "rating-badge" }, [text]));
      }

      const platRow = el("span", { class: "card-plat-indicator" }, [
        createPlatformBrandIcons(game.platform),
        el("span", {}, [formatCardPlatformLabel(game.platform)]),
      ]);

      const metaChildren: Array<HTMLElement | null> = [platRow];
      if (game.is_game_pass) {
        metaChildren.push(el("span", { class: "card-gp-indicator" }, ["Game Pass"]));
      } else if (game.ownership_status && game.ownership_status !== "Owned") {
        metaChildren.push(el("span", { class: "card-ownership-indicator" }, [game.ownership_status]));
      }
      if (game.is_play_anywhere) {
        metaChildren.push(el("span", { class: "card-xpa-indicator" }, ["XPA"]));
      }

      const quickStatus = el("select", {
        class: "card-quick-status",
        "data-status": game.completion_status,
        "aria-label": `Completion status for ${game.title}`,
      }, buildSelectOptions(STATUSES, game.completion_status)) as HTMLSelectElement;

      quickStatus.addEventListener("click", (ev) => ev.stopPropagation());
      quickStatus.addEventListener("keydown", (ev) => ev.stopPropagation());
      quickStatus.addEventListener("change", async (ev) => {
        ev.stopPropagation();
        const nextStatus = quickStatus.value;
        quickStatus.setAttribute("data-status", nextStatus);
        try {
          const updated = await updateGame(game.id, { completion_status: nextStatus, status_locked: true });
          game.completion_status = updated.completion_status;
          game.status_locked = updated.status_locked;
          cachedStats = await getStats();
          drawStats();
          if (activeStatus) applyFiltersAndRender();
        } catch (err) {
          showBanner(bannerBox, err instanceof Error ? err.message : "Could not update status", "error");
        }
      });

      const rightMetrics = el("div", { class: "card-metrics-right" }, [
        game.achievements_total > 0
          ? el("span", { class: "card-time-indicator", title: "Achievements unlocked" }, [
              iconSpan(LUCIDE_ICONS.trophy),
              `${game.achievements_unlocked}/${game.achievements_total}`,
            ])
          : null,
        game.playtime_hours > 0
          ? el("span", { class: "card-time-indicator", title: "Hours played" }, [
              iconSpan(LUCIDE_ICONS.clock),
              `${game.playtime_hours}h`,
            ])
          : null,
      ]);

      const cardBody = el("div", { class: "card-body" }, [
        el("h3", { class: "card-title", title: game.title }, [game.title]),
        el("div", { class: "card-meta-row" }, metaChildren),
        game.genres.length ? el("div", { class: "card-genres" }, [game.genres.slice(0, 3).join(" • ")]) : null,
        el("div", { class: "card-status-row" }, [quickStatus, rightMetrics]),
      ]);

      const card = el("article", { class: "game-card", tabindex: "0" }, [coverEl, cardBody]);
      card.addEventListener("click", () => openEditDialog(game));
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openEditDialog(game);
        }
      });

      frag.appendChild(card);
    }

    listBox.appendChild(frag);
  }

  function renderCatalogPicker(query: string, pickHost: HTMLElement, onSelect: (hit: CatalogSearchResultResponse) => void): void {
    clearEl(pickHost);
    if (!query.trim()) return;
    pickHost.appendChild(el("p", { class: "muted" }, ["Searching external catalog & Steam Store..."]));
    void searchCatalog(query.trim(), 6).then((hits) => {
      clearEl(pickHost);
      if (!hits.length) {
        pickHost.appendChild(el("p", { class: "muted" }, ["No catalog matches found."]));
        return;
      }
      for (const h of hits) {
        const thumb = createCoverElement(h.title, h.source || "Catalog", h.cover_url, 10, [], "catalog-pick-thumb");
        const meta = [h.release_year ? String(h.release_year) : "", h.genres.slice(0, 3).join(", "), h.critic_score ? `Meta ${h.critic_score}` : ""].filter(Boolean).join(" • ");
        const item = el("button", {
          type: "button",
          class: "catalog-pick-item",
          onClick: () => onSelect(h),
        }, [
          thumb,
          el("div", { class: "stack", style: "gap:0.12rem;min-width:0" }, [
            el("strong", {}, [h.title]),
            el("span", { class: "muted" }, [meta || h.source]),
          ]),
        ]);
        pickHost.appendChild(item);
      }
    }).catch(() => {
      clearEl(pickHost);
      pickHost.appendChild(el("p", { class: "muted" }, ["Catalog lookup unavailable offline."]));
    });
  }

  function openAddDialog(): void {
    openModalDialog("Add Manual Game", (dialog, body) => {
      const modalBanner = el("div");
      const titleIn = el("input", { type: "text", required: true, autofocus: true, placeholder: "e.g. Metroid Prime Remastered" }) as HTMLInputElement;
      const platIn = el("input", { type: "text", required: true, value: "Nintendo Switch" }) as HTMLInputElement;
      const statusIn = el("select", {}, STATUSES.map((s) => el("option", { value: s }, [s]))) as HTMLSelectElement;
      const ownIn = el("select", {}, OWNERSHIPS.map((o) => el("option", { value: o }, [o]))) as HTMLSelectElement;
      const hoursIn = el("input", { type: "number", step: "0.1", min: "0", value: "0" }) as HTMLInputElement;
      const coverIn = el("input", { type: "url", placeholder: "https://... cover art URL" }) as HTMLInputElement;
      const genresIn = el("input", { type: "text", placeholder: "Action, Adventure" }) as HTMLInputElement;
      const yearIn = el("input", { type: "number", placeholder: "Release year" }) as HTMLInputElement;
      const gpIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      const notesIn = el("textarea", { rows: "2" }) as HTMLTextAreaElement;
      const lookupResults = el("div", { class: "catalog-pick-list" });
      const lookupBtn = el("button", {
        type: "button",
        class: "btn btn-secondary",
        onClick: () => {
          renderCatalogPicker(titleIn.value, lookupResults, (hit) => {
            titleIn.value = hit.title;
            if (hit.cover_url) coverIn.value = hit.cover_url;
            if (hit.release_year) yearIn.value = String(hit.release_year);
            if (hit.genres.length) genresIn.value = hit.genres.join(", ");
            clearEl(lookupResults);
          });
        },
      }, [iconSpan(LUCIDE_ICONS.search), "Find Cover & Metadata"]);

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            const genres = genresIn.value.split(",").map((x) => x.trim()).filter(Boolean);
            await addGame({
              title: titleIn.value,
              platform: platIn.value,
              completion_status: statusIn.value,
              ownership_status: ownIn.value,
              playtime_hours: Number(hoursIn.value || 0),
              cover_url: coverIn.value.trim() || null,
              release_year: yearIn.value.trim() ? Number(yearIn.value.trim()) : null,
              genres,
              is_game_pass: gpIn.checked,
              notes: notesIn.value,
            });
            dialog.close("saved");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Could not add game", "error");
          }
        },
      }, [
        modalBanner,
        el("div", { class: "row-actions" }, [el("div", { style: "flex:1" }, [el("label", {}, ["Title", titleIn])]), lookupBtn]),
        lookupResults,
        el("div", { class: "two-col" }, [
          el("label", {}, ["Platform", platIn]),
          el("label", {}, ["Completion Status", statusIn]),
          el("label", {}, ["Ownership", ownIn]),
          el("label", {}, ["Playtime (hours)", hoursIn]),
        ]),
        el("div", { class: "two-col" }, [
          el("label", {}, ["Genres", genresIn]),
          el("label", {}, ["Release Year", yearIn]),
        ]),
        el("label", {}, ["Cover Art URL", coverIn]),
        el("label", { class: "inline-check" }, [gpIn, " Game Pass available"]),
        el("label", {}, ["Notes", notesIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save Game"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
    });
  }

  function openEditDialog(game: GameResponse): void {
    openModalDialog(`${game.title}`, (dialog, body) => {
      const modalBanner = el("div");
      const previewHost = el("div");
      const coverIn = el("input", { type: "url", value: game.cover_url || "", placeholder: "https://... poster art URL" }) as HTMLInputElement;
      const catalogHost = el("div", { class: "catalog-pick-list" });

      function redrawPreview(urlText: string): void {
        clearEl(previewHost);
        previewHost.appendChild(createCoverElement(game.title, game.platform, urlText, 0));
      }
      redrawPreview(game.cover_url || "");
      coverIn.addEventListener("input", () => redrawPreview(coverIn.value));

      const statusIn = el("select", { autofocus: true }, buildSelectOptions(STATUSES, game.completion_status)) as HTMLSelectElement;
      const lockStatusIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      lockStatusIn.checked = game.status_locked;

      const ownIn = el("select", {}, buildSelectOptions(OWNERSHIPS, game.ownership_status)) as HTMLSelectElement;
      const gpIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      gpIn.checked = game.is_game_pass;
      const xpaIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      xpaIn.checked = game.is_play_anywhere;

      const favIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      favIn.checked = game.favorite;

      const hoursIn = el("input", { type: "number", step: "0.1", min: "0", value: String(game.playtime_hours) }) as HTMLInputElement;
      const ratingIn = el("input", { type: "number", min: "1", max: "10", value: game.user_rating ? String(game.user_rating) : "" }) as HTMLInputElement;
      const yearIn = el("input", { type: "number", value: game.release_year ? String(game.release_year) : "" }) as HTMLInputElement;
      const genresIn = el("input", { type: "text", value: game.genres.join(", "), placeholder: taxonomy.genres.slice(0, 5).join(", ") }) as HTMLInputElement;
      const tagsIn = el("input", { type: "text", value: game.tags.join(", "), placeholder: taxonomy.tags.slice(0, 5).join(", ") }) as HTMLInputElement;
      const notesIn = el("textarea", { rows: "3" }) as HTMLTextAreaElement;
      notesIn.value = game.notes;

      const genreChips = el("div", { class: "chip-row" });
      function drawGenreChips(): void {
        clearEl(genreChips);
        const list = genresIn.value.split(",").map((x) => x.trim()).filter(Boolean);
        for (const item of list) {
          genreChips.appendChild(el("span", { class: "tag-chip" }, [
            item,
            el("button", {
              type: "button",
              "aria-label": `Remove ${item}`,
              onClick: () => {
                genresIn.value = list.filter((x) => x !== item).join(", ");
                drawGenreChips();
              },
            }, ["×"]),
          ]));
        }
      }
      drawGenreChips();
      genresIn.addEventListener("input", () => drawGenreChips());

      const findCoverBtn = el("button", {
        type: "button",
        class: "btn btn-secondary",
        style: "width:100%",
        onClick: () => {
          renderCatalogPicker(game.title, catalogHost, (hit) => {
            if (hit.cover_url) {
              coverIn.value = hit.cover_url;
              redrawPreview(hit.cover_url);
            }
            if (!yearIn.value && hit.release_year) yearIn.value = String(hit.release_year);
            if (!genresIn.value && hit.genres.length) {
              genresIn.value = hit.genres.join(", ");
              drawGenreChips();
            }
            clearEl(catalogHost);
          });
        },
      }, [iconSpan(LUCIDE_ICONS.search), "Find Cover Art"]);

      const achBtn = el("button", {
        type: "button",
        class: "btn btn-secondary",
        style: "width:100%",
        onClick: () => {
          dialog.close("ach");
          void openAchievementsModal(game);
        },
      }, [iconSpan(LUCIDE_ICONS.trophy), `Achievements (${game.achievements_unlocked}/${game.achievements_total})`]);

      const leftCol = el("div", { class: "stack" }, [
        previewHost,
        el("div", { class: "row-actions" }, [
          createPlatformBrandIcons(game.platform),
          el("span", { class: "muted" }, [game.platform]),
        ]),
        game.critic_score ? el("span", { class: "badge" }, [`Metacritic ${game.critic_score}`]) : null,
        el("label", {}, ["Cover URL", coverIn]),
        findCoverBtn,
        catalogHost,
        achBtn,
      ]);

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            const genres = genresIn.value.split(",").map((x) => x.trim()).filter(Boolean);
            const tags = tagsIn.value.split(",").map((x) => x.trim()).filter(Boolean);
            const rating = ratingIn.value.trim() ? Number(ratingIn.value) : null;
            await updateGame(game.id, {
              completion_status: statusIn.value,
              status_locked: lockStatusIn.checked,
              ownership_status: ownIn.value,
              is_game_pass: gpIn.checked,
              is_play_anywhere: xpaIn.checked,
              playtime_hours: Number(hoursIn.value || 0),
              favorite: favIn.checked,
              user_rating: rating,
              clear_rating: rating === null,
              release_year: yearIn.value.trim() ? Number(yearIn.value.trim()) : null,
              cover_url: coverIn.value.trim() || null,
              genres,
              tags,
              replace_labels: true,
              notes: notesIn.value,
            });
            dialog.close("saved");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Could not update game", "error");
          }
        },
      }, [
        modalBanner,
        el("div", { class: "two-col" }, [
          el("label", {}, ["Completion Status", statusIn]),
          el("label", {}, ["Ownership", ownIn]),
          el("label", {}, ["Playtime (hours)", hoursIn]),
          el("label", {}, ["My Rating (1-10)", ratingIn]),
          el("label", {}, ["Release Year", yearIn]),
        ]),
        el("div", { class: "row-actions" }, [
          el("label", { class: "inline-check" }, [lockStatusIn, "Lock status against sync"]),
          el("label", { class: "inline-check" }, [favIn, "Favorite"]),
          el("label", { class: "inline-check" }, [gpIn, "Game Pass"]),
          el("label", { class: "inline-check" }, [xpaIn, "Play Anywhere"]),
        ]),
        genreChips,
        el("label", {}, ["Genres (comma-separated)", genresIn]),
        el("label", {}, ["Tags (comma-separated)", tagsIn]),
        el("label", {}, ["Notes", notesIn]),
        el("div", { class: "row-between" }, [
          el("button", {
            type: "button",
            class: "btn btn-danger",
            onClick: async () => {
              try {
                await deleteGame(game.id);
                dialog.close("deleted");
                await refresh();
              } catch (err) {
                showBanner(modalBanner, err instanceof Error ? err.message : "Could not delete game", "error");
              }
            },
          }, [iconSpan(LUCIDE_ICONS.trash), "Delete"]),
          el("div", { class: "row-actions" }, [
            el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
            el("button", { type: "submit", class: "btn btn-primary" }, ["Save Changes"]),
          ]),
        ]),
      ]);

      body.appendChild(el("div", { class: "edit-modal-grid" }, [leftCol, form]));
    });
  }

  function openTriageDialog(): void {
    const queue = allGames.filter((g) => !g.status_locked);
    if (!queue.length) {
      showBanner(bannerBox, "All games already have locked statuses!", "info");
      return;
    }
    let idx = 0;
    openModalDialog("Quick Backlog Triage", (dialog, body) => {
      const host = el("div", { class: "stack" });
      body.appendChild(host);

      function renderCard(): void {
        clearEl(host);
        if (idx >= queue.length) {
          host.appendChild(el("p", {}, ["Triage complete! All unlocked games reviewed."]));
          host.appendChild(el("button", { type: "button", class: "btn btn-primary", onClick: () => dialog.close() }, ["Done"]));
          return;
        }
        const g = queue[idx];
        const cover = createCoverElement(g.title, g.platform, g.cover_url, 0);
        const statusBtns = STATUSES.map((st) => el("button", {
          type: "button",
          class: st === g.completion_status ? "btn btn-primary" : "btn btn-secondary",
          onClick: async () => {
            await updateGame(g.id, { completion_status: st, status_locked: true });
            idx += 1;
            renderCard();
            await refresh();
          },
        }, [st]));

        host.appendChild(el("span", { class: "muted" }, [`Title ${idx + 1} of ${queue.length}`]));
        host.appendChild(el("div", { class: "edit-modal-grid" }, [
          cover,
          el("div", { class: "stack" }, [
            el("h3", { style: "margin:0" }, [g.title]),
            el("p", { class: "muted" }, [`${g.platform} • ${g.playtime_hours}h played • Current: ${g.completion_status}`]),
            el("div", { class: "row-actions" }, statusBtns),
            el("div", { class: "row-actions" }, [
              el("button", { type: "button", class: "btn btn-ghost", onClick: () => { idx += 1; renderCard(); } }, ["Skip"]),
              el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close() }, ["Close"]),
            ]),
          ]),
        ]));
      }
      renderCard();
    });
  }

  function openPortabilityDialog(): void {
    openModalDialog("Export & Import Library", (_dialog, body) => {
      const modalBanner = el("div");
      const reportPre = el("pre", { class: "code-out" });
      const jsonFileIn = el("input", { type: "file", accept: ".json,application/json" }) as HTMLInputElement;
      const dbFileIn = el("input", { type: "file", accept: ".db,.sqlite,.sqlite3" }) as HTMLInputElement;
      const modeSel = el("select", {}, [
        el("option", { value: "merge" }, ["Merge (preserve edits)"]),
        el("option", { value: "replace" }, ["Replace library"]),
      ]) as HTMLSelectElement;

      const exportBtn = el("button", {
        type: "button",
        class: "btn btn-primary",
        onClick: async () => {
          showBanner(modalBanner, "");
          try {
            const data = await exportLibraryJson();
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "vertex-library.json";
            a.click();
            URL.revokeObjectURL(url);
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Export failed", "error");
          }
        },
      }, ["Download Versioned JSON Export"]);

      async function handleJsonImport(dryRun: boolean): Promise<void> {
        const f = jsonFileIn.files ? jsonFileIn.files[0] : null;
        if (!f) {
          showBanner(modalBanner, "Select a JSON file first", "error");
          return;
        }
        showBanner(modalBanner, "");
        try {
          const parsed = JSON.parse(await f.text()) as Record<string, unknown>;
          const rep = await importLibraryJson(parsed, modeSel.value, dryRun);
          reportPre.textContent = JSON.stringify(rep, null, 2);
          if (!dryRun) await refresh();
        } catch (err) {
          showBanner(modalBanner, err instanceof Error ? err.message : "JSON import failed", "error");
        }
      }

      async function handleSqliteImport(dryRun: boolean): Promise<void> {
        const f = dbFileIn.files ? dbFileIn.files[0] : null;
        if (!f) {
          showBanner(modalBanner, "Select a SQLite .db file first", "error");
          return;
        }
        showBanner(modalBanner, "");
        try {
          const rep = await importLegacySqlite(f, modeSel.value, dryRun);
          reportPre.textContent = JSON.stringify(rep, null, 2);
          if (!dryRun) await refresh();
        } catch (err) {
          showBanner(modalBanner, err instanceof Error ? err.message : "SQLite import failed", "error");
        }
      }

      body.appendChild(el("div", { class: "stack" }, [
        modalBanner,
        exportBtn,
        el("hr"),
        el("label", {}, ["Import Mode", modeSel]),
        el("label", {}, ["Versioned JSON file", jsonFileIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-secondary", onClick: () => handleJsonImport(true) }, ["Preview JSON"]),
          el("button", { type: "button", class: "btn btn-primary", onClick: () => handleJsonImport(false) }, ["Apply JSON"]),
        ]),
        el("hr"),
        el("label", {}, ["Legacy SQLite Snapshot (.db)", dbFileIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-secondary", onClick: () => handleSqliteImport(true) }, ["Preview SQLite"]),
          el("button", { type: "button", class: "btn btn-primary", onClick: () => handleSqliteImport(false) }, ["Import SQLite"]),
        ]),
        reportPre,
      ]));
    });
  }

  async function openAchievementsModal(game: GameResponse): Promise<void> {
    try {
      const initialDetail = await getGameAchievements(game.id);
      openModalDialog(`Achievements: ${game.title}`, (dialog, body) => {
        const modalBanner = el("div");
        const contentBox = el("div", { class: "stack" });

        function renderDetail(detail: GameAchievementsResponse): void {
          clearEl(contentBox);
          const pct = Math.min(100, Math.max(0, detail.unified_summary.percentage || 0));
          contentBox.appendChild(el("div", { class: "meter-row" }, [
            el("div", { class: "row-between" }, [
              el("strong", {}, [`Unified Total: ${detail.unified_summary.unlocked}/${detail.unified_summary.total} (${pct}%)`]),
              el("span", { class: "muted" }, [`Cross-platform overlap: ${detail.unified_summary.overlap_count}`]),
            ]),
            el("div", { class: "meter-track" }, [
              el("div", { class: "meter-fill", style: `width:${pct}%` }),
            ]),
          ]));

          const platBox = el("div", { class: "row-actions" });
          for (const ps of detail.per_platform_summary) {
            platBox.appendChild(el("span", { class: "badge" }, [`${ps.short_platform || ps.platform}: ${ps.unlocked}/${ps.total} (${ps.percentage}%)`]));
          }
          contentBox.appendChild(platBox);

          if (!detail.achievements.length) {
            contentBox.appendChild(el("p", { class: "muted" }, ["No detailed achievement records cached for this game."]));
            return;
          }
          const achStack = el("div", { class: "stack", style: "gap:0.45rem;max-height:50vh;overflow-y:auto" });
          for (const a of detail.achievements.slice(0, 100)) {
            const iconSrc = (a.unlocked ? a.icon_url : (a.icon_gray_url || a.icon_url)) || makeFallbackAchIcon(a.name);
            const iconEl = el("img", {
              src: iconSrc,
              alt: "",
              class: "ach-icon",
              width: "40",
              height: "40",
              loading: "lazy",
            }) as HTMLImageElement;
            iconEl.addEventListener("error", () => {
              const fb = makeFallbackAchIcon(a.name);
              if (iconEl.src !== fb) iconEl.src = fb;
            });
            const plats = a.unlocked_platforms.length ? ` • ${a.unlocked_platforms.join(", ")}` : "";
            achStack.appendChild(el("div", { class: a.unlocked ? "ach-row" : "ach-row locked" }, [
              iconEl,
              el("div", { class: "stack", style: "gap:0.1rem;flex:1" }, [
                el("strong", {}, [`${a.unlocked ? "✓ " : ""}${a.name}`]),
                a.description ? el("span", { class: "muted" }, [a.description]) : null,
              ]),
              el("span", { class: "muted" }, [`${a.rarity_pct !== null && a.rarity_pct !== undefined ? `${a.rarity_pct}%` : ""}${plats}`]),
            ]));
          }
          contentBox.appendChild(achStack);
        }

        renderDetail(initialDetail);
        body.appendChild(el("div", { class: "stack" }, [
          modalBanner,
          el("div", { class: "row-actions" }, [
            el("button", {
              type: "button",
              class: "btn btn-primary",
              onClick: async () => {
                showBanner(modalBanner, "");
                try {
                  const synced = await syncGameAchievements(game.id);
                  renderDetail(synced);
                  const msg = synced.synced_copies > 0
                    ? `Synced achievements from ${synced.synced_providers.join(", ")}`
                    : (synced.warnings.join("; ") || "No new achievements synced");
                  showBanner(modalBanner, msg, synced.synced_copies > 0 ? "info" : "error");
                  await refresh();
                } catch (err) {
                  showBanner(modalBanner, err instanceof Error ? err.message : "Failed syncing achievements", "error");
                }
              },
            }, ["Sync Achievements"]),
            el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close() }, ["Close"]),
          ]),
          contentBox,
        ]));
      });
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading achievements", "error");
    }
  }

  function openReconcileDialog(): void {
    openModalDialog("Reconcile Cross-Platform Titles", (dialog, body) => {
      const modalBanner = el("div");
      const candBox = el("div", { class: "stack" });

      async function loadCandidates(): Promise<void> {
        clearEl(candBox);
        try {
          const candidates = await getReconcileCandidates();
          if (!candidates.length) {
            candBox.appendChild(el("p", { class: "muted" }, ["No pending cross-platform merge candidates."]));
            return;
          }
          for (const c of candidates) {
            const ids = c.games.map((g) => g.id);
            const plats = c.games.map((g) => `${g.platform} (${g.playtime_hours}h)`).join(" + ");
            const firstCover = c.games.find((g) => Boolean(g.cover_url));
            candBox.appendChild(el("div", { class: "prompt-card row-between" }, [
              el("div", { class: "media-row-left" }, [
                createCoverElement(c.canonical_title, "Merged", firstCover ? firstCover.cover_url : null, 10, [], "cover-thumb-sm"),
                el("div", { class: "stack", style: "gap:0.15rem" }, [
                  el("strong", {}, [c.canonical_title]),
                  el("span", { class: "muted" }, [plats]),
                ]),
              ]),
              el("div", { class: "row-actions" }, [
                el("button", {
                  type: "button",
                  class: "btn btn-primary",
                  onClick: async () => {
                    try {
                      await confirmReconcile(ids);
                      await loadCandidates();
                      await refresh();
                    } catch (err) {
                      showBanner(modalBanner, err instanceof Error ? err.message : "Failed merging titles", "error");
                    }
                  },
                }, ["Merge"]),
                el("button", {
                  type: "button",
                  class: "btn btn-ghost",
                  onClick: async () => {
                    try {
                      await dismissReconcile(ids);
                      await loadCandidates();
                    } catch (err) {
                      showBanner(modalBanner, err instanceof Error ? err.message : "Failed dismissing candidate", "error");
                    }
                  },
                }, ["Dismiss"]),
              ]),
            ]));
          }
        } catch (err) {
          showBanner(modalBanner, err instanceof Error ? err.message : "Failed loading candidates", "error");
        }
      }

      body.appendChild(el("div", { class: "stack" }, [
        modalBanner,
        el("div", { class: "row-actions" }, [
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                const res = await resetReconcileDismissals();
                showBanner(modalBanner, `Restored ${res.cleared} dismissed candidate pair(s)`, "info");
                await loadCandidates();
              } catch (err) {
                showBanner(modalBanner, err instanceof Error ? err.message : "Failed resetting dismissals", "error");
              }
            },
          }, ["Reset Dismissals"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close() }, ["Close"]),
        ]),
        candBox,
      ]));
      void loadCandidates();
    });
  }

  async function refresh(): Promise<void> {
    try {
      const [stats, games, prompts, recCands] = await Promise.all([
        getStats(),
        listGames("", "", ""),
        listPlaytimePrompts().catch(() => [] as Array<PlaytimePromptResponse>),
        getReconcileCandidates().catch(() => []),
      ]);
      cachedStats = stats;
      allGames = games;
      pendingReconcileCount = recCands.length;
      const untriagedCount = allGames.filter((g) => !g.status_locked).length;
      clearEl(triageBtn);
      triageBtn.appendChild(iconSpan(LUCIDE_ICONS.triage));
      triageBtn.appendChild(document.createTextNode("Quick Triage"));
      if (untriagedCount > 0) triageBtn.appendChild(el("span", { class: "triage-badge-pill" }, [String(untriagedCount)]));

      clearEl(reconcileBtn);
      reconcileBtn.appendChild(iconSpan(LUCIDE_ICONS.reconcile));
      reconcileBtn.appendChild(document.createTextNode("Reconcile"));
      if (pendingReconcileCount > 0) reconcileBtn.appendChild(el("span", { class: "triage-badge-pill" }, [String(pendingReconcileCount)]));

      drawPrompts(prompts);
      populateDynamicFilterDropdowns();
      drawStats();
      applyFiltersAndRender();
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading library", "error");
    }
  }

  const favChip = el("button", {
    type: "button",
    class: "btn btn-chip",
    "aria-pressed": "false",
    onClick: () => {
      favOnly = !favOnly;
      favChip.setAttribute("aria-pressed", String(favOnly));
      drawStats();
      applyFiltersAndRender();
    },
  }, [iconSpan(LUCIDE_ICONS.star), "Favorites"]);

  const gpChip = el("button", {
    type: "button",
    class: "btn btn-chip btn-chip-gp",
    "aria-pressed": "false",
    onClick: () => {
      gpOnly = !gpOnly;
      gpChip.setAttribute("aria-pressed", String(gpOnly));
      applyFiltersAndRender();
    },
  }, [iconSpan(BRAND_ICONS.gamepass), "Game Pass"]);

  const xpaChip = el("button", {
    type: "button",
    class: "btn btn-chip btn-chip-xpa",
    "aria-pressed": "false",
    onClick: () => {
      xpaOnly = !xpaOnly;
      xpaChip.setAttribute("aria-pressed", String(xpaOnly));
      applyFiltersAndRender();
    },
  }, [iconSpan(BRAND_ICONS.playanywhere), "Play Anywhere"]);

  const gridViewBtn = el("button", {
    type: "button",
    class: viewMode === "grid" ? "btn active" : "btn btn-ghost",
    "aria-label": "Grid view",
  }, [iconSpan(LUCIDE_ICONS.grid)]);
  const listViewBtn = el("button", {
    type: "button",
    class: viewMode === "list" ? "btn active" : "btn btn-ghost",
    "aria-label": "List view",
  }, [iconSpan(LUCIDE_ICONS.list)]);

  gridViewBtn.addEventListener("click", () => {
    viewMode = "grid";
    localStorage.setItem("vertex_view_mode", "grid");
    gridViewBtn.className = "btn active";
    listViewBtn.className = "btn btn-ghost";
    listBox.classList.remove("list-view");
  });
  listViewBtn.addEventListener("click", () => {
    viewMode = "list";
    localStorage.setItem("vertex_view_mode", "list");
    listViewBtn.className = "btn active";
    gridViewBtn.className = "btn btn-ghost";
    listBox.classList.add("list-view");
  });

  searchIn.addEventListener("input", () => {
    activeSearch = searchIn.value.trim();
    applyFiltersAndRender();
  });
  statusSel.addEventListener("change", () => {
    activeStatus = statusSel.value;
    drawStats();
    applyFiltersAndRender();
  });
  ownSel.addEventListener("change", () => {
    activeOwnership = ownSel.value;
    applyFiltersAndRender();
  });
  genreSel.addEventListener("change", () => {
    activeGenre = genreSel.value;
    applyFiltersAndRender();
  });
  tagSel.addEventListener("change", () => {
    activeTag = tagSel.value;
    applyFiltersAndRender();
  });
  sortSel.addEventListener("change", () => {
    sortBy = sortSel.value;
    applyFiltersAndRender();
  });

  const headerBar = el("div", { class: "catalog-header" }, [
    el("div", { class: "catalog-header-text" }, [
      el("h2", { class: "catalog-title" }, ["Game Library"]),
      resultsCountEl,
    ]),
    el("div", { class: "row-actions" }, [
      triageBtn,
      reconcileBtn,
      el("button", {
        type: "button",
        class: "btn btn-secondary",
        onClick: async () => {
          try {
            const rep = await enrichLibrary({ limit: 500, force: false });
            showBanner(bannerBox, rep.message || `Enriched ${rep.games_enriched} game(s), filled ${rep.covers_filled} cover(s)`, "success");
            await refresh();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Metadata enrichment failed", "error");
          }
        },
      }, [iconSpan(LUCIDE_ICONS.sparkles), "Enrich Metadata"]),
      el("button", { type: "button", class: "btn btn-secondary", onClick: openPortabilityDialog }, [iconSpan(LUCIDE_ICONS.download), "Import / Export"]),
      el("button", { type: "button", class: "btn btn-primary", onClick: openAddDialog }, [iconSpan(LUCIDE_ICONS.plus), "Add Game"]),
    ]),
  ]);

  const controlsPanel = el("div", { class: "controls-panel" }, [
    el("div", { class: "row-between" }, [
      platformPillHost,
      el("div", { class: "view-toggle-group" }, [gridViewBtn, listViewBtn]),
    ]),
    el("div", { class: "toolbar" }, [
      searchIn,
      statusSel,
      ownSel,
      genreSel,
      tagSel,
      sortSel,
      favChip,
      gpChip,
      xpaChip,
    ]),
  ]);

  drawPlatformPills();
  container.appendChild(el("section", { class: "stack" }, [headerBar, bannerBox, promptsHost, statsBox, controlsPanel, listBox]));
  try {
    taxonomy = await getTaxonomy();
  } catch {
    taxonomy = { genres: [], tags: [] };
  }
  await refresh();
}
