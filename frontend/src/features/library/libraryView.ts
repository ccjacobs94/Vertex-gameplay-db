import type { GameResponse, LibraryStatsResponse } from "../../shared/api-types.ts";
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
  resetReconcileDismissals,
  updateGame,
} from "../../shared/api.ts";
import { clearEl, el, openModalDialog, showBanner } from "../../shared/dom.ts";

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

export async function renderLibraryView(container: HTMLElement): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const statsBox = el("div", { class: "stats-grid" });
  const listBox = el("div", { class: "games-grid" });

  const searchIn = el("input", { type: "search", placeholder: "Search games...", "aria-label": "Search games" }) as HTMLInputElement;
  const platformIn = el("input", { type: "text", placeholder: "Platform filter", "aria-label": "Platform filter" }) as HTMLInputElement;
  const statusSel = el("select", { "aria-label": "Status filter" }, [
    el("option", { value: "" }, ["All statuses"]),
    ...STATUSES.map((s) => el("option", { value: s }, [s])),
  ]) as HTMLSelectElement;

  let taxonomy: { genres: Array<string>; tags: Array<string> } = { genres: [], tags: [] };

  async function refresh(): Promise<void> {
    try {
      const [stats, games] = await Promise.all([
        getStats(),
        listGames(searchIn.value.trim(), platformIn.value.trim(), statusSel.value),
      ]);
      drawStats(statsBox, stats);
      drawGames(listBox, games);
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading library", "error");
    }
  }

  function drawStats(target: HTMLElement, s: LibraryStatsResponse): void {
    clearEl(target);
    const items = [
      ["Unique Titles", String(s.unique_titles)],
      ["Playing", String(s.playing)],
      ["Completed", String(s.completed)],
      ["Hours", s.total_playtime_hours.toFixed(1)],
    ];
    for (const [label, val] of items) {
      target.appendChild(el("div", { class: "stat-card" }, [
        el("span", { class: "stat-val" }, [val]),
        el("span", { class: "stat-lbl" }, [label]),
      ]));
    }
  }

  function drawGames(target: HTMLElement, games: Array<GameResponse>): void {
    clearEl(target);
    if (!games.length) {
      target.appendChild(el("p", { class: "muted" }, ["No games match your filters yet. Add a manual game or connect Steam in Settings."]));
      return;
    }
    for (const game of games) {
      const card = el("article", { class: "game-card" }, [
        el("div", { class: "game-card-head" }, [
          el("h3", {}, [game.title]),
          game.favorite ? el("span", { class: "badge badge-fav" }, ["Fav"]) : null,
        ]),
        el("p", { class: "muted" }, [`${game.platform} • ${game.completion_status}${game.status_locked ? " [Locked]" : ""}`]),
        el("p", { class: "muted" }, [`${game.ownership_status}${game.is_game_pass ? " • Game Pass" : ""} • ${game.playtime_hours}h`]),
        game.genres.length ? el("p", { class: "tags-line" }, [game.genres.join(", ")]) : null,
        game.tags.length ? el("p", { class: "muted" }, [`Tags: ${game.tags.join(", ")}`]) : null,
        game.notes ? el("p", { class: "notes-preview" }, [game.notes]) : null,
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-secondary", onClick: () => openEditDialog(game) }, ["Edit"]),
          el("button", { type: "button", class: "btn btn-secondary", onClick: () => openAchievementsModal(game) }, ["Achievements"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await deleteGame(game.id);
                await refresh();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Could not delete game", "error");
              }
            },
          }, ["Delete"]),
        ]),
      ]);
      target.appendChild(card);
    }
  }

  function openAddDialog(): void {
    openModalDialog("Add Manual Game", (dialog, body) => {
      const modalBanner = el("div");
      const titleIn = el("input", { type: "text", required: true, autofocus: true }) as HTMLInputElement;
      const platIn = el("input", { type: "text", required: true, value: "Nintendo Switch" }) as HTMLInputElement;
      const statusIn = el("select", {}, STATUSES.map((s) => el("option", { value: s }, [s]))) as HTMLSelectElement;
      const ownIn = el("select", {}, OWNERSHIPS.map((o) => el("option", { value: o }, [o]))) as HTMLSelectElement;
      const hoursIn = el("input", { type: "number", step: "0.1", min: "0", value: "0" }) as HTMLInputElement;
      const gpIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      const notesIn = el("textarea", { rows: "3" }) as HTMLTextAreaElement;

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            await addGame({
              title: titleIn.value,
              platform: platIn.value,
              completion_status: statusIn.value,
              ownership_status: ownIn.value,
              playtime_hours: Number(hoursIn.value || 0),
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
        el("label", {}, ["Title", titleIn]),
        el("label", {}, ["Platform", platIn]),
        el("label", {}, ["Completion Status", statusIn]),
        el("label", {}, ["Ownership", ownIn]),
        el("label", {}, ["Playtime (hours)", hoursIn]),
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
    openModalDialog(`Edit: ${game.title}`, (dialog, body) => {
      const modalBanner = el("div");
      const statusIn = el("select", { autofocus: true }, buildSelectOptions(STATUSES, game.completion_status)) as HTMLSelectElement;
      const lockStatusIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      lockStatusIn.checked = game.status_locked;

      const ownIn = el("select", {}, buildSelectOptions(OWNERSHIPS, game.ownership_status)) as HTMLSelectElement;
      const gpIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      gpIn.checked = game.is_game_pass;

      const favIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      favIn.checked = game.favorite;

      const hoursIn = el("input", { type: "number", step: "0.1", min: "0", value: String(game.playtime_hours) }) as HTMLInputElement;
      const ratingIn = el("input", { type: "number", min: "1", max: "10", value: game.user_rating ? String(game.user_rating) : "" }) as HTMLInputElement;
      const genresIn = el("input", { type: "text", value: game.genres.join(", "), placeholder: taxonomy.genres.slice(0, 5).join(", ") }) as HTMLInputElement;
      const tagsIn = el("input", { type: "text", value: game.tags.join(", "), placeholder: taxonomy.tags.slice(0, 5).join(", ") }) as HTMLInputElement;
      const notesIn = el("textarea", { rows: "3" }) as HTMLTextAreaElement;
      notesIn.value = game.notes;

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
              playtime_hours: Number(hoursIn.value || 0),
              favorite: favIn.checked,
              user_rating: rating,
              clear_rating: rating === null,
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
        el("label", {}, ["Completion Status", statusIn]),
        el("label", { class: "inline-check" }, [lockStatusIn, " Lock status against sync"]),
        el("label", {}, ["Ownership", ownIn]),
        el("label", { class: "inline-check" }, [gpIn, " Game Pass available"]),
        el("label", {}, ["Playtime (hours)", hoursIn]),
        el("label", { class: "inline-check" }, [favIn, " Favorite"]),
        el("label", {}, ["Rating (1-10)", ratingIn]),
        el("label", {}, ["Genres (comma-separated controlled taxonomy)", genresIn]),
        el("label", {}, ["Tags (comma-separated controlled taxonomy)", tagsIn]),
        el("label", {}, ["Notes", notesIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save Changes"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
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
      const detail = await getGameAchievements(game.id);
      openModalDialog(`Achievements: ${game.title}`, (dialog, body) => {
        const platBox = el("div", { class: "stack" });
        for (const ps of detail.per_platform_summary) {
          platBox.appendChild(el("p", { class: "muted" }, [`${ps.platform}: ${ps.unlocked}/${ps.total} (${ps.percentage}%)`]));
        }
        const achList = el("ul", { class: "bullet-list" });
        for (const a of detail.achievements.slice(0, 60)) {
          const plats = a.unlocked_platforms.length ? ` [${a.unlocked_platforms.join(", ")}]` : "";
          achList.appendChild(el("li", {}, [`${a.unlocked ? "[x]" : "[ ]"} ${a.name}${plats}${a.description ? ` — ${a.description}` : ""}`]));
        }
        body.appendChild(el("div", { class: "stack" }, [
          el("p", {}, [`Unified Total: ${detail.unified_summary.unlocked}/${detail.unified_summary.total} (${detail.unified_summary.percentage}%) • Cross-platform overlap: ${detail.unified_summary.overlap_count}`]),
          platBox,
          detail.achievements.length ? achList : el("p", { class: "muted" }, ["No detailed achievement records cached for this game."]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close() }, ["Close"]),
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
            candBox.appendChild(el("div", { class: "prompt-card row-between" }, [
              el("div", { class: "stack" }, [
                el("strong", {}, [c.canonical_title]),
                el("span", { class: "muted" }, [plats]),
              ]),
              el("div", { class: "row-actions" }, [
                el("button", {
                  type: "button",
                  class: "btn btn-primary",
                  onClick: async () => {
                    await confirmReconcile(ids);
                    await loadCandidates();
                    await refresh();
                  },
                }, ["Merge"]),
                el("button", {
                  type: "button",
                  class: "btn btn-ghost",
                  onClick: async () => {
                    await dismissReconcile(ids);
                    await loadCandidates();
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
              const res = await resetReconcileDismissals();
              showBanner(modalBanner, `Restored ${res.cleared} dismissed candidate pair(s)`, "info");
              await loadCandidates();
            },
          }, ["Reset Dismissals"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close() }, ["Close"]),
        ]),
        candBox,
      ]));
      void loadCandidates();
    });
  }

  const toolbar = el("div", { class: "toolbar" }, [
    searchIn,
    platformIn,
    statusSel,
    el("button", { type: "button", class: "btn btn-secondary", onClick: () => refresh() }, ["Filter"]),
    el("button", { type: "button", class: "btn btn-primary", onClick: openAddDialog }, ["+ Add Game"]),
    el("button", { type: "button", class: "btn btn-secondary", onClick: openReconcileDialog }, ["Reconcile"]),
    el("button", {
      type: "button",
      class: "btn btn-secondary",
      onClick: async () => {
        try {
          const rep = await enrichLibrary({ limit: 500, force: false });
          showBanner(bannerBox, rep.message || `Enriched ${rep.games_enriched} game(s)`, "info");
          await refresh();
        } catch (err) {
          showBanner(bannerBox, err instanceof Error ? err.message : "Metadata enrichment failed", "error");
        }
      },
    }, ["Enrich Metadata"]),
    el("button", { type: "button", class: "btn btn-ghost", onClick: openPortabilityDialog }, ["Import / Export"]),
  ]);

  container.appendChild(el("section", { class: "stack" }, [el("h1", {}, ["Library"]), bannerBox, statsBox, toolbar, listBox]));
  try {
    taxonomy = await getTaxonomy();
  } catch {
    taxonomy = { genres: [], tags: [] };
  }
  await refresh();
}
