"use strict";

const STATUS_OPTIONS = [
  "Playing",
  "Plan to Play",
  "Not Played",
  "Beaten",
  "Completed",
  "Dropped",
];

const COMMON_GENRES = [
  "Action",
  "Action RPG",
  "Action-Adventure",
  "Adventure",
  "Card & Board",
  "Casual",
  "Fighting",
  "FPS",
  "Free To Play",
  "Hack and Slash",
  "Horror",
  "Immersive Sim",
  "Indie",
  "Interactive Drama",
  "JRPG",
  "Metroidvania",
  "MMORPG",
  "Open World",
  "Party",
  "Platformer",
  "Puzzle",
  "Puzzle Platformer",
  "Racing",
  "Rhythm",
  "Roguelike",
  "RPG",
  "Sandbox",
  "Shooter",
  "Simulation",
  "Soulslike",
  "Sports",
  "Stealth",
  "Strategy",
  "Survival",
  "Tactical",
  "Tactical RPG",
  "Third-Person Shooter",
  "Tower Defense",
  "Turn-Based Strategy",
  "Visual Novel",
];

const state = {
  games: [],
  allGenres: [...COMMON_GENRES],
  platform: "",
  status: "",
  genre: "",
  search: "",
  sortBy: "playtime_desc",
  favoriteOnly: false,
  viewMode: localStorage.getItem("vertex_view_mode") || "grid",
  triageQueue: [],
  triageIndex: 0,
  triageScope: "unlocked",
  reconcileQueue: [],
  reconcileIndex: 0,
  reconcileScope: "all",
  reconcileCheckedIds: new Set(),
  manualSelectedGames: [],
  reconcileCandidatesCount: 0,
};

function updateTriageButtonBadge() {
  const untriagedCount = state.games.filter((g) => !g.status_locked).length;
  const btn = document.getElementById("btn-open-triage");
  if (btn) {
    btn.innerHTML = "";
    btn.appendChild(document.createTextNode("⚡ Quick Triage "));
    if (untriagedCount > 0) {
      const badge = document.createElement("span");
      badge.className = "triage-badge-pill";
      badge.textContent = untriagedCount;
      btn.appendChild(badge);
    }
  }
  const mBadge = document.getElementById("mnav-triage-badge");
  if (mBadge) {
    mBadge.textContent = untriagedCount;
    mBadge.hidden = untriagedCount <= 0;
  }
}

function updateReconcileButtonBadge(count) {
  if (typeof count === "number") {
    state.reconcileCandidatesCount = count;
  }
  const btn = document.getElementById("btn-open-reconcile");
  if (btn) {
    btn.innerHTML = "";
    btn.appendChild(document.createTextNode("🔗 Reconcile "));
    if (state.reconcileCandidatesCount > 0) {
      const badge = document.createElement("span");
      badge.className = "triage-badge-pill";
      badge.textContent = state.reconcileCandidatesCount;
      btn.appendChild(badge);
    }
  }
  const mBadge = document.getElementById("mnav-reconcile-badge");
  if (mBadge) {
    mBadge.textContent = state.reconcileCandidatesCount;
    mBadge.hidden = state.reconcileCandidatesCount <= 0;
  }
}

function updateActiveFiltersUI() {
  const count =
    (state.genre ? 1 : 0) +
    (state.status ? 1 : 0) +
    (state.favoriteOnly ? 1 : 0) +
    (state.sortBy !== "playtime_desc" ? 1 : 0);

  const badge = document.getElementById("active-filter-badge");
  if (badge) {
    badge.textContent = count;
    badge.hidden = count === 0;
  }

  const resetBtn = document.getElementById("btn-reset-filters");
  if (resetBtn) {
    resetBtn.hidden =
      !state.genre &&
      !state.status &&
      !state.favoriteOnly &&
      !state.platform &&
      !state.search &&
      state.sortBy === "playtime_desc";
  }

  for (const card of document.querySelectorAll(".metric-card.metric-interactive")) {
    const targetStatus = card.dataset.filterStatus || "";
    card.classList.toggle("active", state.status === targetStatus);
  }
}

function applyViewMode(mode) {
  state.viewMode = mode === "list" ? "list" : "grid";
  localStorage.setItem("vertex_view_mode", state.viewMode);
  const grid = document.getElementById("games-grid");
  if (grid) {
    grid.classList.toggle("list-view", state.viewMode === "list");
  }
  const gridBtn = document.getElementById("btn-view-grid");
  const listBtn = document.getElementById("btn-view-list");
  if (gridBtn && listBtn) {
    const isList = state.viewMode === "list";
    gridBtn.classList.toggle("active", !isList);
    gridBtn.setAttribute("aria-pressed", String(!isList));
    listBtn.classList.toggle("active", isList);
    listBtn.setAttribute("aria-pressed", String(isList));
  }
}

function makeFallbackCover(title, platform) {
  const safeTitle = (title || "Game").replace(/[<>&"]/g, "");
  const safePlat = (platform || "").replace(/[<>&"]/g, "");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
    <rect width="300" height="450" fill="#131b2e"/>
    <rect x="16" y="16" width="268" height="418" rx="8" fill="none" stroke="#26334d" stroke-width="2"/>
    <text x="150" y="200" fill="#94a3b8" font-family="sans-serif" font-size="14" text-anchor="middle">${safePlat}</text>
    <text x="150" y="235" fill="#f1f5f9" font-family="sans-serif" font-weight="bold" font-size="18" text-anchor="middle">${safeTitle.slice(0, 24)}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function attachCoverAspectObserver(wrapper, img, fallbackSrc, targetSrc) {
  if (!wrapper || !img) return;

  let attemptedFallbackHeader = false;

  const handleRatio = () => {
    if (img.naturalWidth && img.naturalHeight) {
      const ratio = img.naturalWidth / img.naturalHeight;
      // 2:3 aspect ratio is ~0.667. If > 0.72 (e.g. 1.0 square or wide landscape),
      // standard object-fit: cover would crop the left and right sides.
      // Switch to ambient blurred contain mode so the entire logo and artwork remain visible.
      if (ratio > 0.72) {
        wrapper.classList.add("cover-ambient");
        wrapper.style.setProperty("--cover-bg", `url("${img.currentSrc || img.src}")`);
      } else {
        wrapper.classList.remove("cover-ambient");
        wrapper.style.removeProperty("--cover-bg");
      }
    }
  };

  img.addEventListener("load", handleRatio);

  img.addEventListener("error", () => {
    // If a Steam library_600x900.jpg 404s, attempt Steam header.jpg before falling back to SVG
    if (!attemptedFallbackHeader && img.src && img.src.includes("steamstatic.com") && img.src.includes("library_600x900.jpg")) {
      attemptedFallbackHeader = true;
      img.src = img.src.replace("library_600x900.jpg", "header.jpg");
      return;
    }
    wrapper.classList.remove("cover-ambient");
    wrapper.style.removeProperty("--cover-bg");
    img.src = fallbackSrc;
  });

  if (targetSrc) {
    img.src = targetSrc;
  }

  if (img.complete && img.naturalWidth) {
    handleRatio();
  }
}

async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) return;
    const stats = await res.json();

    document.getElementById("stat-total-games").textContent = stats.total_games || 0;
    document.getElementById("stat-total-hours").textContent = `${stats.total_playtime_hours || 0} hrs`;
    document.getElementById("stat-playing").textContent = stats.playing_count || 0;
    document.getElementById("stat-backlog").textContent = stats.backlog_count || 0;
    document.getElementById("stat-completed").textContent = stats.completed_count || 0;
    updateReconcileButtonBadge(stats.reconcile_candidates_count || 0);

    const genreSelect = document.getElementById("genre-filter");
    if (Array.isArray(stats.all_genres)) {
      const merged = new Set([...COMMON_GENRES, ...stats.all_genres]);
      state.allGenres = Array.from(merged).sort((a, b) => a.localeCompare(b));
      if (genreSelect) {
        const prevGenre = state.genre || genreSelect.value || "";
        genreSelect.innerHTML = "";
        const allOpt = document.createElement("option");
        allOpt.value = "";
        allOpt.textContent = "All Genres";
        genreSelect.appendChild(allOpt);
        for (const gName of stats.all_genres) {
          const opt = document.createElement("option");
          opt.value = gName;
          opt.textContent = gName;
          if (gName === prevGenre) opt.selected = true;
          genreSelect.appendChild(opt);
        }
      }
    }

    const srcEl = document.getElementById("source-indicators");
    srcEl.innerHTML = "";
    const cfg = stats.configured_sources || {};
    const labels = [
      { key: "steam", name: "Steam Auto-Sync" },
      { key: "xbox", name: "Xbox Auto-Sync" },
      { key: "psn", name: "PSN Auto-Sync" },
    ];
    for (const item of labels) {
      const span = document.createElement("span");
      span.className = `source-tag ${cfg[item.key] ? "active" : ""}`;
      span.textContent = `${item.name}: ${cfg[item.key] ? "Active" : "Not Set"}`;
      srcEl.appendChild(span);
    }
  } catch (err) {
    console.error("Failed to fetch stats:", err);
  }
}

async function fetchGames() {
  const params = new URLSearchParams();
  if (state.search) params.set("search", state.search);
  if (state.platform) params.set("platform", state.platform);
  if (state.status) params.set("status", state.status);
  if (state.genre) params.set("genre", state.genre);
  if (state.favoriteOnly) params.set("favorite_only", "true");
  params.set("sort_by", state.sortBy);

  try {
    const res = await fetch(`/api/games?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    state.games = data.games || [];
    updateTriageButtonBadge();
    renderGames();
  } catch (err) {
    console.error("Failed to load games:", err);
  }
}

async function quickPatchGame(gameId, updates) {
  try {
    const res = await fetch(`/api/games/${gameId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const updated = data.game;
    if (updated) {
      const idx = state.games.findIndex((g) => g.id === gameId);
      if (idx !== -1) state.games[idx] = updated;
      const tIdx = state.triageQueue.findIndex((g) => g.id === gameId);
      if (tIdx !== -1) state.triageQueue[tIdx] = updated;
      updateTriageButtonBadge();
      fetchStats();
    }
    return updated;
  } catch (err) {
    console.error("Quick update failed:", err);
    return null;
  }
}

function renderGames() {
  const grid = document.getElementById("games-grid");
  const countEl = document.getElementById("results-count");
  countEl.textContent = state.games.length;
  grid.innerHTML = "";
  grid.classList.toggle("list-view", state.viewMode === "list");
  updateActiveFiltersUI();

  if (state.games.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p><strong>No games match your current filter.</strong></p>
      <p>Use <strong>+ Add Game</strong> to add Switch/console games, or configure your Steam, Xbox, and PSN keys in <code>.env</code> and click <strong>Sync Libraries</strong>.</p>
    `;
    grid.appendChild(empty);
    return;
  }

  for (let i = 0; i < state.games.length; i++) {
    const game = state.games[i];
    const card = document.createElement("article");
    card.className = i >= 12 ? "game-card deferred-card" : "game-card";
    card.tabIndex = 0;

    const fallbackSrc = makeFallbackCover(game.title, game.platform);
    const imgSrc = game.cover_url || fallbackSrc;

    const wrapper = document.createElement("div");
    wrapper.className = "cover-wrapper";

    const img = document.createElement("img");
    img.alt = `${game.title} cover art`;
    img.width = 300;
    img.height = 450;
    img.loading = i < 6 ? "eager" : "lazy";
    attachCoverAspectObserver(wrapper, img, fallbackSrc, imgSrc);
    wrapper.appendChild(img);

    const platBadge = document.createElement("span");
    platBadge.className = "platform-badge";
    platBadge.textContent = game.platform;
    wrapper.appendChild(platBadge);

    if (game.favorite) {
      const fav = document.createElement("span");
      fav.className = "fav-star";
      fav.textContent = "★";
      fav.setAttribute("aria-label", "Favorite");
      wrapper.appendChild(fav);
    }

    if (game.user_rating) {
      const ratingBadge = document.createElement("span");
      ratingBadge.className = "rating-badge";
      ratingBadge.textContent = `★ ${game.user_rating}/10`;
      ratingBadge.setAttribute("aria-label", `User Rating: ${game.user_rating} out of 10`);
      wrapper.appendChild(ratingBadge);
    }

    const body = document.createElement("div");
    body.className = "card-body";

    const h3 = document.createElement("h3");
    h3.className = "card-title";
    h3.textContent = game.title;
    body.appendChild(h3);

    const subrow = document.createElement("div");
    subrow.className = "card-list-subrow";

    const listPlat = document.createElement("span");
    listPlat.className = "card-list-plat";
    listPlat.textContent = game.platform;
    subrow.appendChild(listPlat);

    const genres = document.createElement("div");
    genres.className = "card-genres";
    const genreText = (game.genres || []).join(", ");
    genres.textContent = genreText || (game.release_year ? `Released ${game.release_year}` : game.source);
    subrow.appendChild(genres);

    body.appendChild(subrow);

    const footer = document.createElement("div");
    footer.className = "card-footer";

    const hours = document.createElement("span");
    if (game.is_reconciled && Array.isArray(game.linked_games) && game.linked_games.length > 1) {
      hours.className = "reconciled-card-indicator";
      hours.textContent = `🔗 ⏱ ${game.playtime_hours || 0}h`;
      const breakdown = game.linked_games
        .map((lg) => `${lg.platform}: ${lg.playtime_hours || 0}h`)
        .join(" + ");
      hours.title = `Consolidated across ${game.linked_games.length} copies: ${breakdown}`;
    } else {
      hours.textContent = `⏱ ${game.playtime_hours || 0}h`;
    }
    footer.appendChild(hours);

    const statusSelect = document.createElement("select");
    statusSelect.className = "card-quick-status";
    const currentStatus = game.completion_status || "Not Played";
    statusSelect.dataset.status = currentStatus;
    statusSelect.setAttribute("aria-label", `Completion status for ${game.title}`);
    for (const optVal of STATUS_OPTIONS) {
      const opt = document.createElement("option");
      opt.value = optVal;
      opt.textContent = optVal;
      if (optVal === currentStatus) opt.selected = true;
      statusSelect.appendChild(opt);
    }
    statusSelect.addEventListener("click", (e) => e.stopPropagation());
    statusSelect.addEventListener("keydown", (e) => e.stopPropagation());
    statusSelect.addEventListener("change", async (e) => {
      e.stopPropagation();
      const newStatus = e.target.value;
      statusSelect.dataset.status = newStatus;
      game.completion_status = newStatus;
      game.status_locked = true;
      await quickPatchGame(game.id, { completion_status: newStatus });
      if (state.status && state.status.toLowerCase() !== newStatus.toLowerCase()) {
        fetchGames();
      }
    });
    footer.appendChild(statusSelect);

    card.appendChild(wrapper);
    card.appendChild(body);
    card.appendChild(footer);

    card.addEventListener("click", () => openEditModal(game));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openEditModal(game);
      }
    });

    grid.appendChild(card);
  }
}

function renderEditGenreChips() {
  const chipsEl = document.getElementById("edit-genre-chips");
  const inputEl = document.getElementById("edit-genres");
  const pickerEl = document.getElementById("edit-genre-picker");
  if (!chipsEl || !inputEl) return;
  chipsEl.innerHTML = "";
  const list = inputEl.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  for (const gName of list) {
    const chip = document.createElement("span");
    chip.className = "edit-genre-chip";
    chip.textContent = gName;

    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.setAttribute("aria-label", `Remove ${gName}`);
    rmBtn.addEventListener("click", () => {
      const updated = list.filter((item) => item !== gName);
      inputEl.value = updated.join(", ");
      renderEditGenreChips();
    });
    chip.appendChild(rmBtn);
    chipsEl.appendChild(chip);
  }

  if (pickerEl) {
    const existingLower = new Set(list.map((g) => g.toLowerCase()));
    pickerEl.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "+ Add genre...";
    pickerEl.appendChild(placeholder);

    for (const gOption of state.allGenres) {
      if (existingLower.has(gOption.toLowerCase())) continue;
      const opt = document.createElement("option");
      opt.value = gOption;
      opt.textContent = gOption;
      pickerEl.appendChild(opt);
    }
    pickerEl.value = "";
  }
}

function renderEditRatingPills(selectedVal) {
  const container = document.getElementById("edit-rating-pills");
  const inputEl = document.getElementById("edit-rating");
  const clearBtn = document.getElementById("btn-clear-rating");
  if (!container || !inputEl) return;
  container.innerHTML = "";
  const numVal = selectedVal ? parseInt(selectedVal, 10) : null;
  inputEl.value = numVal || "";
  if (clearBtn) {
    clearBtn.hidden = !numVal;
  }

  for (let i = 1; i <= 10; i++) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `rating-pill ${numVal === i ? "active" : ""}`;
    btn.textContent = String(i);
    btn.setAttribute("aria-pressed", String(numVal === i));
    btn.addEventListener("click", () => {
      const nextVal = numVal === i ? null : i;
      renderEditRatingPills(nextVal);
    });
    container.appendChild(btn);
  }
}

function syncEditFavButton(isFav) {
  const favCheck = document.getElementById("edit-favorite");
  const favBtn = document.getElementById("btn-edit-fav");
  if (favCheck) favCheck.checked = Boolean(isFav);
  if (favBtn) {
    favBtn.setAttribute("aria-pressed", String(Boolean(isFav)));
    favBtn.textContent = isFav ? "★ Favorited" : "☆ Favorite";
  }
}

function renderEditLinkedCopies(game) {
  const listEl = document.getElementById("edit-linked-list");
  const summaryEl = document.getElementById("edit-linked-summary");
  const hoursInput = document.getElementById("edit-hours");
  const hoursDisplay = document.getElementById("edit-hours-display");
  const hoursEditWrap = document.getElementById("edit-hours-edit-wrap");
  const toggleHoursBtn = document.getElementById("btn-toggle-edit-hours");
  const hoursHint = document.getElementById("edit-hours-hint");
  const searchInput = document.getElementById("edit-link-search");
  const resultsEl = document.getElementById("edit-link-results");

  if (!listEl) return;
  listEl.innerHTML = "";
  searchInput.value = "";
  resultsEl.hidden = true;
  resultsEl.innerHTML = "";

  const linked = Array.isArray(game.linked_games) && game.linked_games.length > 0
    ? game.linked_games
    : [game];

  const totalHours = game.playtime_hours || 0;
  if (hoursDisplay) {
    hoursDisplay.textContent = `⏱ ${totalHours} hrs`;
    hoursDisplay.hidden = false;
  }
  if (hoursEditWrap) {
    hoursEditWrap.hidden = true;
  }
  if (hoursInput) {
    hoursInput.readOnly = true;
    hoursInput.dataset.unlocked = "false";
  }

  if (linked.length > 1) {
    summaryEl.textContent = `${linked.length} copies • ${totalHours} hrs total`;
    if (toggleHoursBtn) toggleHoursBtn.hidden = true;
    hoursHint.hidden = false;
  } else {
    summaryEl.textContent = `1 copy (${game.platform})`;
    if (toggleHoursBtn) {
      toggleHoursBtn.hidden = false;
      toggleHoursBtn.textContent = "Edit";
    }
    hoursHint.hidden = true;
  }

  for (const copy of linked) {
    const li = document.createElement("li");
    li.className = "linked-copy-row";

    const left = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = copy.title;
    const platSpan = document.createElement("span");
    platSpan.className = "reconcile-copy-plat";
    platSpan.style.marginInlineStart = "0.45rem";
    platSpan.textContent = copy.platform;
    left.appendChild(strong);
    left.appendChild(platSpan);

    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "0.6rem";

    const hrs = document.createElement("span");
    hrs.className = "reconcile-copy-hours";
    hrs.textContent = `⏱ ${copy.playtime_hours || 0}h`;
    right.appendChild(hrs);

    if (linked.length > 1) {
      const unlinkBtn = document.createElement("button");
      unlinkBtn.type = "button";
      unlinkBtn.className = "btn btn-secondary btn-xs";
      unlinkBtn.textContent = "Unlink";
      unlinkBtn.addEventListener("click", async () => {
        const res = await fetch(`/api/reconcile/unlink/${copy.id}`, { method: "POST" });
        if (res.ok) {
          await fetchStats();
          await fetchGames();
          const refreshed = state.games.find((g) => g.id === game.id) || (await res.json()).game;
          if (refreshed) openEditModal(refreshed);
        }
      });
      right.appendChild(unlinkBtn);
    }

    li.appendChild(left);
    li.appendChild(right);
    listEl.appendChild(li);
  }
}

function openEditModal(game) {
  const dialog = document.getElementById("edit-game-dialog");
  document.getElementById("edit-modal-title").textContent = game.title;

  const badgesEl = document.getElementById("edit-header-badges");
  if (badgesEl) {
    badgesEl.innerHTML = "";
    const platBadge = document.createElement("span");
    platBadge.className = "edit-header-badge plat";
    platBadge.textContent = game.platform;
    badgesEl.appendChild(platBadge);

    if (game.last_played) {
      const lpDate = new Date(game.last_played);
      if (!Number.isNaN(lpDate.getTime())) {
        const lpBadge = document.createElement("span");
        lpBadge.className = "edit-header-badge";
        lpBadge.textContent = `Last played ${lpDate.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
        badgesEl.appendChild(lpBadge);
      }
    }
  }

  const fallbackSrc = makeFallbackCover(game.title, game.platform);
  const coverPreview = document.getElementById("edit-cover-preview");
  const coverWrap = coverPreview ? coverPreview.closest(".cover-wrapper") : null;
  if (coverPreview && coverWrap) {
    attachCoverAspectObserver(coverWrap, coverPreview, fallbackSrc, game.cover_url || fallbackSrc);
  }

  const achTile = document.getElementById("edit-ach-tile");
  const achDisplay = document.getElementById("edit-ach-display");
  if (achTile && achDisplay) {
    const unl = game.achievements_unlocked || 0;
    const tot = game.achievements_total || 0;
    if (tot > 0) {
      const pct = Math.round((unl / tot) * 100);
      achDisplay.textContent = `🏆 ${unl} / ${tot} (${pct}%)`;
      achTile.hidden = false;
    } else {
      achTile.hidden = true;
    }
  }

  const metaTile = document.getElementById("edit-meta-tile");
  const metaDisplay = document.getElementById("edit-meta-display");
  if (metaTile && metaDisplay) {
    const parts = [];
    if (game.release_year) parts.push(String(game.release_year));
    if (game.critic_score) parts.push(`Metacritic ${game.critic_score}`);
    if (parts.length > 0) {
      metaDisplay.textContent = parts.join(" • ");
      metaTile.hidden = false;
    } else {
      metaTile.hidden = true;
    }
  }

  document.getElementById("edit-id").value = game.id;
  document.getElementById("edit-status").value = game.completion_status || "Not Played";
  document.getElementById("edit-hours").value = game.playtime_hours ?? 0;
  renderEditRatingPills(game.user_rating);
  document.getElementById("edit-genres").value = (game.genres || []).join(", ");
  renderEditGenreChips();
  document.getElementById("edit-cover").value = game.cover_url || "";
  document.getElementById("edit-notes").value = game.notes || "";
  syncEditFavButton(game.favorite);
  renderEditLinkedCopies(game);
  if (!dialog.open) {
    dialog.showModal();
  }
  dialog.scrollTop = 0;
}

let reconcileToastTimer = null;

function showReconcileToast(msg) {
  const el = document.getElementById("reconcile-toast");
  if (!el) return;
  el.textContent = msg;
  clearTimeout(reconcileToastTimer);
  reconcileToastTimer = setTimeout(() => {
    el.textContent = "";
  }, 2400);
}

async function openReconcileModal() {
  const dialog = document.getElementById("reconcile-dialog");
  const scopeSelect = document.getElementById("reconcile-scope");
  state.reconcileScope = scopeSelect.value || "all";

  if (!dialog.open) {
    dialog.showModal();
  }
  dialog.scrollTop = 0;
  await reloadReconcileQueue(state.reconcileScope);
}

async function reloadReconcileQueue(scope) {
  state.reconcileScope = scope;
  const stageEl = document.getElementById("reconcile-stage");
  const manualStageEl = document.getElementById("reconcile-manual-stage");
  const progressBar = document.getElementById("reconcile-progress-bar");
  const prevBtn = document.getElementById("btn-reconcile-prev");
  const skipBtn = document.getElementById("btn-reconcile-skip");
  const counterEl = document.getElementById("reconcile-counter");

  if (scope === "manual") {
    stageEl.hidden = true;
    manualStageEl.hidden = false;
    progressBar.hidden = true;
    prevBtn.hidden = true;
    skipBtn.hidden = true;
    counterEl.textContent = "Manual";
    await renderManualReconcileStage();
    return;
  }

  stageEl.hidden = false;
  manualStageEl.hidden = true;
  progressBar.hidden = false;
  prevBtn.hidden = false;
  skipBtn.hidden = false;

  const crossOnly = scope === "cross_platform" ? "true" : "false";
  try {
    const res = await fetch(`/api/reconcile/candidates?cross_platform_only=${crossOnly}`);
    if (res.ok) {
      const data = await res.json();
      state.reconcileQueue = data.candidates || [];
      if (scope === "all") {
        updateReconcileButtonBadge(state.reconcileQueue.length);
      }
    }
  } catch (err) {
    console.error("Failed to load reconcile candidates:", err);
  }

  state.reconcileIndex = 0;
  initReconcileGroupSelection();
  renderReconcileCard();
}

function initReconcileGroupSelection() {
  const group = state.reconcileQueue[state.reconcileIndex];
  state.reconcileCheckedIds = new Set();
  if (!group || !Array.isArray(group.games)) return;
  for (const g of group.games) {
    state.reconcileCheckedIds.add(g.id);
  }
  const titleInput = document.getElementById("reconcile-unified-title");
  const statusSelect = document.getElementById("reconcile-unified-status");
  if (titleInput) titleInput.value = group.suggested_title || "";
  if (statusSelect) statusSelect.value = group.suggested_status || "Not Played";
}

function updateReconcilePreviewMetrics() {
  const group = state.reconcileQueue[state.reconcileIndex];
  if (!group) return;
  const selectedGames = (group.games || []).filter((g) => state.reconcileCheckedIds.has(g.id));
  const totalHours = Math.round(
    selectedGames.reduce((acc, g) => acc + (parseFloat(g.playtime_hours) || 0), 0) * 10
  ) / 10;

  const breakdownParts = selectedGames.map((g) => `${g.playtime_hours || 0}h`);
  const hoursText =
    selectedGames.length > 1
      ? `${breakdownParts.join(" + ")} = ${totalHours} hrs`
      : `${totalHours} hrs`;

  document.getElementById("reconcile-total-hours").textContent = hoursText;

  const plats = [];
  for (const g of selectedGames) {
    if (!plats.includes(g.platform)) plats.push(g.platform);
  }
  document.getElementById("reconcile-unified-plat").textContent =
    plats.join(" + ") || "Select at least 2 copies";

  document.getElementById("reconcile-selected-count").textContent =
    `${selectedGames.length} of ${group.games.length} selected`;

  const confirmBtn = document.getElementById("btn-reconcile-confirm");
  if (confirmBtn) {
    confirmBtn.disabled = selectedGames.length < 2;
  }
}

function renderReconcileCard() {
  const queue = state.reconcileQueue;
  const idx = state.reconcileIndex;
  const total = queue.length;

  const counterEl = document.getElementById("reconcile-counter");
  const progressFill = document.getElementById("reconcile-progress-fill");
  const cardEl = document.getElementById("reconcile-card");
  const emptyEl = document.getElementById("reconcile-empty");
  const prevBtn = document.getElementById("btn-reconcile-prev");
  const skipBtn = document.getElementById("btn-reconcile-skip");

  if (total === 0 || idx >= total) {
    counterEl.textContent = `${total} / ${total}`;
    progressFill.style.inlineSize = "100%";
    cardEl.hidden = true;
    emptyEl.hidden = false;
    prevBtn.disabled = total === 0 || idx <= 0;
    skipBtn.disabled = true;
    return;
  }

  cardEl.hidden = false;
  emptyEl.hidden = true;
  prevBtn.disabled = idx <= 0;
  skipBtn.disabled = false;

  counterEl.textContent = `${idx + 1} / ${total}`;
  const pct = Math.round((idx / total) * 100);
  progressFill.style.inlineSize = `${pct}%`;

  const group = queue[idx];
  const gridEl = document.getElementById("reconcile-candidates-grid");
  gridEl.innerHTML = "";

  for (const g of group.games) {
    const isChecked = state.reconcileCheckedIds.has(g.id);
    const copyCard = document.createElement("div");
    copyCard.className = `reconcile-copy-card ${isChecked ? "selected" : ""}`;

    const fallbackSrc = makeFallbackCover(g.title, g.platform);
    const thumb = document.createElement("img");
    thumb.className = "reconcile-copy-thumb";
    thumb.src = g.cover_url || fallbackSrc;
    thumb.alt = "";
    thumb.width = 64;
    thumb.height = 96;
    thumb.onerror = () => {
      thumb.src = fallbackSrc;
    };
    copyCard.appendChild(thumb);

    const info = document.createElement("div");
    info.className = "reconcile-copy-info";

    const topRow = document.createElement("div");
    topRow.className = "reconcile-copy-top";

    const titleEl = document.createElement("h4");
    titleEl.className = "reconcile-copy-title";
    titleEl.textContent = g.title;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "reconcile-copy-check";
    checkbox.checked = isChecked;
    checkbox.setAttribute("aria-label", `Include ${g.title} on ${g.platform}`);

    topRow.appendChild(titleEl);
    topRow.appendChild(checkbox);
    info.appendChild(topRow);

    const metaRow = document.createElement("div");
    metaRow.className = "reconcile-copy-meta";

    const platBadge = document.createElement("span");
    platBadge.className = "reconcile-copy-plat";
    platBadge.textContent = g.platform;
    metaRow.appendChild(platBadge);

    const statusBadge = document.createElement("span");
    statusBadge.className = "status-pill";
    const st = g.completion_status || "Not Played";
    statusBadge.dataset.status = st;
    statusBadge.textContent = st;
    metaRow.appendChild(statusBadge);

    info.appendChild(metaRow);

    const statsRow = document.createElement("div");
    statsRow.className = "reconcile-copy-meta";

    const hrsEl = document.createElement("span");
    hrsEl.className = "reconcile-copy-hours";
    hrsEl.textContent = `⏱ ${g.playtime_hours || 0} hrs`;
    statsRow.appendChild(hrsEl);

    if ((g.achievements_total || 0) > 0 || (g.achievements_unlocked || 0) > 0) {
      const achEl = document.createElement("span");
      achEl.textContent = `🏆 ${g.achievements_unlocked || 0}/${g.achievements_total || 0}`;
      statsRow.appendChild(achEl);
    }

    if (g.last_played) {
      const lpEl = document.createElement("span");
      lpEl.textContent = `Played ${formatLastPlayed(g.last_played)}`;
      statsRow.appendChild(lpEl);
    }

    info.appendChild(statsRow);
    copyCard.appendChild(info);

    const toggleSelect = () => {
      if (state.reconcileCheckedIds.has(g.id)) {
        state.reconcileCheckedIds.delete(g.id);
      } else {
        state.reconcileCheckedIds.add(g.id);
      }
      renderReconcileCard();
    };

    copyCard.addEventListener("click", (e) => {
      if (e.target === checkbox) return;
      toggleSelect();
    });
    checkbox.addEventListener("change", toggleSelect);

    gridEl.appendChild(copyCard);
  }

  updateReconcilePreviewMetrics();
}

async function confirmCurrentReconcileGroup() {
  const queue = state.reconcileQueue;
  if (state.reconcileIndex >= queue.length) return;
  const group = queue[state.reconcileIndex];
  const selectedIds = (group.games || [])
    .filter((g) => state.reconcileCheckedIds.has(g.id))
    .map((g) => g.id);

  if (selectedIds.length < 2) {
    showReconcileToast("Select at least 2 copies to consolidate.");
    return;
  }

  const customTitle = document.getElementById("reconcile-unified-title").value.trim();
  const unifiedStatus = document.getElementById("reconcile-unified-status").value;

  const res = await fetch("/api/reconcile/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      game_ids: selectedIds,
      primary_id: selectedIds[0],
      custom_title: customTitle || group.suggested_title,
      completion_status: unifiedStatus,
    }),
  });

  if (res.ok) {
    const data = await res.json();
    const merged = data.game;
    if (data.stats) {
      updateReconcileButtonBadge(data.stats.reconcile_candidates_count || 0);
    }
    showReconcileToast(
      `Consolidated "${merged.title}" (${merged.playtime_hours} hrs across ${merged.linked_count} copies)`
    );
    runTriageTransition(() => {
      state.reconcileQueue.splice(state.reconcileIndex, 1);
      initReconcileGroupSelection();
      renderReconcileCard();
    }, "forward");
  }
}

async function dismissCurrentReconcileGroup() {
  const queue = state.reconcileQueue;
  if (state.reconcileIndex >= queue.length) return;
  const group = queue[state.reconcileIndex];
  const allIds = (group.games || []).map((g) => g.id);

  const res = await fetch("/api/reconcile/dismiss", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_ids: allIds }),
  });

  if (res.ok) {
    showReconcileToast(`Dismissed "${group.suggested_title}"`);
    updateReconcileButtonBadge(Math.max(0, state.reconcileCandidatesCount - 1));
    runTriageTransition(() => {
      state.reconcileQueue.splice(state.reconcileIndex, 1);
      initReconcileGroupSelection();
      renderReconcileCard();
    }, "forward");
  }
}

function stepReconcile(delta) {
  const nextIdx = state.reconcileIndex + delta;
  if (nextIdx < 0 || nextIdx > state.reconcileQueue.length) return;
  const direction = delta >= 0 ? "forward" : "backward";
  runTriageTransition(() => {
    state.reconcileIndex = nextIdx;
    initReconcileGroupSelection();
    renderReconcileCard();
  }, direction);
}

async function renderManualReconcileStage() {
  await fetchGames();
  renderManualSelectedBar();
  renderManualSearchResults(document.getElementById("manual-reconcile-search").value.trim());
  renderReconciledGroupsList();
}

function renderManualSelectedBar() {
  const bar = document.getElementById("manual-reconcile-selected");
  const selected = state.manualSelectedGames;
  if (selected.length === 0) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }

  bar.hidden = false;
  bar.innerHTML = "";

  const chipsWrap = document.createElement("div");
  chipsWrap.className = "manual-selected-chips";

  let totalHrs = 0;
  for (const g of selected) {
    totalHrs += parseFloat(g.playtime_hours) || 0;
    const chip = document.createElement("span");
    chip.className = "manual-chip";
    chip.textContent = `${g.title} (${g.platform} • ${g.playtime_hours || 0}h)`;
    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.setAttribute("aria-label", `Remove ${g.title}`);
    rmBtn.addEventListener("click", () => {
      state.manualSelectedGames = state.manualSelectedGames.filter((x) => x.id !== g.id);
      renderManualSelectedBar();
      renderManualSearchResults(document.getElementById("manual-reconcile-search").value.trim());
    });
    chip.appendChild(rmBtn);
    chipsWrap.appendChild(chip);
  }

  const rightWrap = document.createElement("div");
  rightWrap.style.display = "flex";
  rightWrap.style.alignItems = "center";
  rightWrap.style.gap = "0.65rem";

  const hrsLabel = document.createElement("strong");
  hrsLabel.className = "reconcile-hours-highlight";
  hrsLabel.textContent = `Total: ${Math.round(totalHrs * 10) / 10} hrs`;
  rightWrap.appendChild(hrsLabel);

  const linkBtn = document.createElement("button");
  linkBtn.type = "button";
  linkBtn.className = "btn btn-primary btn-xs";
  linkBtn.disabled = selected.length < 2;
  linkBtn.textContent = selected.length >= 2 ? `✓ Link ${selected.length} Games` : "Select 1 more game...";
  linkBtn.addEventListener("click", async () => {
    const ids = selected.map((x) => x.id);
    const res = await fetch("/api/reconcile/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_ids: ids, primary_id: ids[0] }),
    });
    if (res.ok) {
      const data = await res.json();
      state.manualSelectedGames = [];
      showReconcileToast(`Linked "${data.game.title}" (${data.game.playtime_hours} hrs)`);
      await fetchStats();
      await renderManualReconcileStage();
    }
  });
  rightWrap.appendChild(linkBtn);

  bar.appendChild(chipsWrap);
  bar.appendChild(rightWrap);
}

async function renderManualSearchResults(query) {
  const listEl = document.getElementById("manual-reconcile-results");
  listEl.innerHTML = "";
  const q = (query || "").toLowerCase().trim();

  let pool = state.games;
  if (q) {
    try {
      const res = await fetch(`/api/games?search=${encodeURIComponent(q)}&limit=40`);
      if (res.ok) {
        const data = await res.json();
        pool = data.games || [];
      }
    } catch (err) {
      console.error("Manual search error:", err);
    }
  } else {
    pool = state.games.slice(0, 25);
  }

  for (const g of pool) {
    const alreadySelected = state.manualSelectedGames.some((x) => x.id === g.id);
    const li = document.createElement("li");
    li.className = "manual-result-row";

    const left = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = g.title;
    const plat = document.createElement("span");
    plat.className = "reconcile-copy-plat";
    plat.style.marginInlineStart = "0.45rem";
    plat.textContent = g.platform;
    left.appendChild(strong);
    left.appendChild(plat);

    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "0.6rem";

    const hrs = document.createElement("span");
    hrs.textContent = `⏱ ${g.playtime_hours || 0}h`;
    right.appendChild(hrs);

    const selectBtn = document.createElement("button");
    selectBtn.type = "button";
    selectBtn.className = `btn ${alreadySelected ? "btn-secondary" : "btn-primary"} btn-xs`;
    selectBtn.textContent = alreadySelected ? "Selected ✓" : "+ Select";
    selectBtn.addEventListener("click", () => {
      if (alreadySelected) {
        state.manualSelectedGames = state.manualSelectedGames.filter((x) => x.id !== g.id);
      } else {
        state.manualSelectedGames.push(g);
      }
      renderManualSelectedBar();
      renderManualSearchResults(query);
    });
    right.appendChild(selectBtn);

    li.appendChild(left);
    li.appendChild(right);
    listEl.appendChild(li);
  }
}

function renderReconciledGroupsList() {
  const container = document.getElementById("reconciled-groups-list");
  const countBadge = document.getElementById("reconciled-groups-count");
  container.innerHTML = "";

  const reconciled = state.games.filter((g) => g.is_reconciled && (g.linked_count || 1) > 1);
  countBadge.textContent = reconciled.length;

  if (reconciled.length === 0) {
    const empty = document.createElement("div");
    empty.className = "subtitle";
    empty.textContent = "No games have been linked yet. Use Suggested Matches or Manual Link above.";
    container.appendChild(empty);
    return;
  }

  for (const g of reconciled) {
    const card = document.createElement("div");
    card.className = "reconciled-group-card";

    const top = document.createElement("div");
    top.className = "reconciled-group-top";

    const titleWrap = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = g.title;
    const badge = document.createElement("span");
    badge.className = "linked-summary-badge";
    badge.style.marginInlineStart = "0.5rem";
    badge.textContent = `${g.linked_count} copies • ${g.playtime_hours || 0} hrs total`;
    titleWrap.appendChild(strong);
    titleWrap.appendChild(badge);
    top.appendChild(titleWrap);
    card.appendChild(top);

    const copiesList = document.createElement("ul");
    copiesList.className = "linked-copies-list";
    for (const lg of g.linked_games || []) {
      const row = document.createElement("li");
      row.className = "linked-copy-row";

      const lText = document.createElement("span");
      lText.textContent = `${lg.title} (${lg.platform}) — ⏱ ${lg.playtime_hours || 0}h`;

      const unlinkBtn = document.createElement("button");
      unlinkBtn.type = "button";
      unlinkBtn.className = "btn btn-secondary btn-xs";
      unlinkBtn.textContent = "Unlink";
      unlinkBtn.addEventListener("click", async () => {
        const res = await fetch(`/api/reconcile/unlink/${lg.id}`, { method: "POST" });
        if (res.ok) {
          showReconcileToast(`Unlinked ${lg.title} (${lg.platform})`);
          await fetchStats();
          await renderManualReconcileStage();
        }
      });

      row.appendChild(lText);
      row.appendChild(unlinkBtn);
      copiesList.appendChild(row);
    }
    card.appendChild(copiesList);
    container.appendChild(card);
  }
}

let triageToastTimer = null;

function showTriageToast(msg) {
  const el = document.getElementById("triage-toast");
  if (!el) return;
  el.textContent = msg;
  clearTimeout(triageToastTimer);
  triageToastTimer = setTimeout(() => {
    el.textContent = "";
  }, 2200);
}

function runTriageTransition(updateDOM, direction = "forward") {
  if (!document.startViewTransition) {
    updateDOM();
    return;
  }
  try {
    document.startViewTransition({
      update: updateDOM,
      types: [direction],
    });
  } catch {
    document.startViewTransition(updateDOM);
  }
}

function formatLastPlayed(isoStr) {
  if (!isoStr) return "Never";
  try {
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return "Never";
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "Never";
  }
}

function buildTriageQueue(sourceGames, scope) {
  if (scope === "unlocked") {
    return sourceGames.filter((g) => !g.status_locked);
  }
  if (scope === "not_played") {
    return sourceGames.filter((g) => (g.completion_status || "Not Played") === "Not Played");
  }
  return [...sourceGames];
}

async function openTriageModal() {
  const dialog = document.getElementById("triage-dialog");
  const scopeSelect = document.getElementById("triage-scope");
  state.triageScope = scopeSelect.value || "unlocked";

  if (!dialog.open) {
    dialog.showModal();
  }
  dialog.scrollTop = 0;
  await reloadTriageQueue(state.triageScope);
}

async function reloadTriageQueue(scope) {
  state.triageScope = scope;
  let pool = state.games;

  if (scope !== "current_filter") {
    try {
      const res = await fetch("/api/games?sort_by=playtime_desc&limit=2000");
      if (res.ok) {
        const data = await res.json();
        pool = data.games || state.games;
      }
    } catch (err) {
      console.error("Failed to fetch full library for triage:", err);
    }
  }

  let queue = buildTriageQueue(pool, scope);
  if (scope === "unlocked" && queue.length === 0 && pool.length > 0) {
    // Fallback to all games in current view if everything is already locked
    document.getElementById("triage-scope").value = "current_filter";
    state.triageScope = "current_filter";
    queue = [...state.games];
  }

  state.triageQueue = queue;
  state.triageIndex = 0;
  renderTriageCard();
}

function renderTriageCard() {
  const queue = state.triageQueue;
  const idx = state.triageIndex;
  const total = queue.length;

  const counterEl = document.getElementById("triage-counter");
  const progressFill = document.getElementById("triage-progress-fill");
  const cardEl = document.getElementById("triage-card");
  const emptyEl = document.getElementById("triage-empty");
  const prevBtn = document.getElementById("btn-triage-prev");
  const skipBtn = document.getElementById("btn-triage-skip");

  if (total === 0 || idx >= total) {
    counterEl.textContent = `${total} / ${total}`;
    progressFill.style.inlineSize = "100%";
    cardEl.hidden = true;
    emptyEl.hidden = false;
    prevBtn.disabled = total === 0 || idx <= 0;
    skipBtn.disabled = true;
    return;
  }

  cardEl.hidden = false;
  emptyEl.hidden = true;
  prevBtn.disabled = idx <= 0;
  skipBtn.disabled = false;

  counterEl.textContent = `${idx + 1} / ${total}`;
  const pct = Math.round((idx / total) * 100);
  progressFill.style.inlineSize = `${pct}%`;

  const game = queue[idx];
  const fallbackSrc = makeFallbackCover(game.title, game.platform);
  const imgEl = document.getElementById("triage-img");
  const triageWrap = imgEl ? imgEl.closest(".cover-wrapper") : null;
  if (imgEl && triageWrap) {
    attachCoverAspectObserver(triageWrap, imgEl, fallbackSrc, game.cover_url || fallbackSrc);
  }
  imgEl.alt = `${game.title} cover art`;

  // Preload next game's cover art for instant transition
  if (idx + 1 < total && queue[idx + 1].cover_url) {
    const preload = new Image();
    preload.src = queue[idx + 1].cover_url;
  }

  document.getElementById("triage-plat").textContent = game.platform;
  document.getElementById("triage-title").textContent = game.title;

  const metaParts = [game.source];
  if (game.release_year) metaParts.push(`Released ${game.release_year}`);
  if (game.genres && game.genres.length > 0) metaParts.push(game.genres.join(", "));
  if (game.status_locked) metaParts.push("🔒 Status Locked");
  document.getElementById("triage-submeta").textContent = metaParts.join(" • ");

  document.getElementById("triage-playtime").textContent = `${game.playtime_hours || 0} hrs`;
  const achTotal = game.achievements_total || 0;
  const achUnl = game.achievements_unlocked || 0;
  document.getElementById("triage-achievements").textContent =
    achTotal > 0 ? `${achUnl} / ${achTotal}` : achUnl > 0 ? `${achUnl} unlocked` : "—";
  document.getElementById("triage-last-played").textContent = formatLastPlayed(game.last_played);

  const curStatus = game.completion_status || "Not Played";
  const statusEl = document.getElementById("triage-current-status");
  statusEl.textContent = curStatus;
  statusEl.dataset.status = curStatus;

  const favBtn = document.getElementById("btn-triage-fav");
  favBtn.setAttribute("aria-pressed", String(Boolean(game.favorite)));

  for (const btn of document.querySelectorAll(".triage-action-btn")) {
    btn.classList.toggle("active-status", btn.dataset.status === curStatus);
  }

  const ratingContainer = document.getElementById("triage-rating-pills");
  ratingContainer.innerHTML = "";
  for (let r = 1; r <= 10; r++) {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = `rating-pill ${game.user_rating === r ? "active" : ""}`;
    pill.textContent = String(r);
    pill.title = `Rate ${r}/10`;
    pill.addEventListener("click", async () => {
      const newRating = game.user_rating === r ? null : r;
      game.user_rating = newRating;
      renderTriageCard();
      await quickPatchGame(game.id, { user_rating: newRating });
      showTriageToast(newRating ? `Rated ${game.title}: ${newRating}/10` : "Rating cleared");
    });
    ratingContainer.appendChild(pill);
  }
}

async function applyTriageStatus(newStatus) {
  const queue = state.triageQueue;
  if (state.triageIndex >= queue.length) return;
  const game = queue[state.triageIndex];

  game.completion_status = newStatus;
  game.status_locked = true;
  showTriageToast(`Saved: ${game.title} → ${newStatus}`);

  // Advance immediately (< 5s rapid flow) while persisting in background
  const patchPromise = quickPatchGame(game.id, { completion_status: newStatus });

  runTriageTransition(() => {
    state.triageIndex += 1;
    renderTriageCard();
  }, "forward");

  await patchPromise;
}

async function toggleTriageFavorite() {
  const queue = state.triageQueue;
  if (state.triageIndex >= queue.length) return;
  const game = queue[state.triageIndex];
  const nextFav = !game.favorite;
  game.favorite = nextFav;
  renderTriageCard();
  await quickPatchGame(game.id, { favorite: nextFav });
  showTriageToast(nextFav ? `★ Favorited ${game.title}` : `Removed favorite`);
}

function stepTriage(delta) {
  const nextIdx = state.triageIndex + delta;
  if (nextIdx < 0 || nextIdx > state.triageQueue.length) return;
  const direction = delta >= 0 ? "forward" : "backward";
  runTriageTransition(() => {
    state.triageIndex = nextIdx;
    renderTriageCard();
  }, direction);
}

async function handleCatalogSearch() {
  const input = document.getElementById("catalog-search-input");
  const listEl = document.getElementById("catalog-results");
  const q = input.value.trim();
  if (q.length < 2) return;

  const btn = document.getElementById("btn-catalog-lookup");
  btn.disabled = true;
  btn.textContent = "Searching...";

  try {
    const res = await fetch(`/api/search-external?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    const results = data.results || [];
    listEl.innerHTML = "";

    if (results.length === 0) {
      listEl.hidden = false;
      const li = document.createElement("li");
      li.className = "catalog-item";
      li.textContent = "No catalog matches found — you can fill the fields manually below.";
      listEl.appendChild(li);
      return;
    }

    listEl.hidden = false;
    for (const item of results) {
      const li = document.createElement("li");
      li.className = "catalog-item";

      if (item.cover_url) {
        const img = document.createElement("img");
        img.src = item.cover_url;
        img.alt = "";
        img.width = 36;
        img.height = 48;
        img.loading = "lazy";
        li.appendChild(img);
      }

      const info = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = item.title;
      const meta = document.createElement("div");
      meta.style.fontSize = "0.78rem";
      meta.style.color = "var(--text-secondary)";
      meta.textContent = `${item.release_year ? item.release_year + " • " : ""}${item.provider}`;
      info.appendChild(strong);
      info.appendChild(meta);
      li.appendChild(info);

      li.addEventListener("click", () => {
        document.getElementById("add-title").value = item.title;
        if (item.cover_url) document.getElementById("add-cover").value = item.cover_url;
        if (item.genres && item.genres.length > 0) {
          document.getElementById("add-genres").value = item.genres.join(", ");
        }
        listEl.hidden = true;
      });

      listEl.appendChild(li);
    }
  } catch (err) {
    console.error("Catalog search error:", err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Search Catalog";
  }
}

function syncAddFavButton(isFav) {
  const favCheck = document.getElementById("add-favorite");
  const favBtn = document.getElementById("btn-add-fav");
  if (favCheck) favCheck.checked = Boolean(isFav);
  if (favBtn) {
    favBtn.setAttribute("aria-pressed", String(Boolean(isFav)));
    favBtn.textContent = isFav ? "★ Favorited" : "☆ Favorite";
  }
}

function attachHorizontalSwipe(el, onSwipeLeft, onSwipeRight) {
  if (!el) return;
  let startX = 0;
  let startY = 0;
  let tracking = false;

  el.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
    },
    { passive: true }
  );

  el.addEventListener(
    "touchend",
    (e) => {
      if (!tracking || e.changedTouches.length !== 1) return;
      tracking = false;
      const dx = e.changedTouches[0].clientX - startX;
      const dy = e.changedTouches[0].clientY - startY;
      if (Math.abs(dx) >= 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        if (dx < 0 && onSwipeLeft) onSwipeLeft();
        if (dx > 0 && onSwipeRight) onSwipeRight();
      }
    },
    { passive: true }
  );
}

document.addEventListener("DOMContentLoaded", () => {
  applyViewMode(state.viewMode);
  fetchStats();
  fetchGames();

  // Register Service Worker for PWA installability & offline resilience
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.warn("Service worker registration skipped:", err);
    });
  }

  // PWA Install Banner (Android beforeinstallprompt & iOS Safari hint)
  let deferredInstallPrompt = null;
  const installBanner = document.getElementById("pwa-install-banner");
  const installBtn = document.getElementById("btn-pwa-install");
  const dismissInstallBtn = document.getElementById("btn-pwa-dismiss");
  const installSubtext = document.getElementById("pwa-install-subtext");
  const isStandalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  const installDismissed = localStorage.getItem("vertex_pwa_dismissed") === "1";

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (!isStandalone && !installDismissed && installBanner) {
      installBanner.hidden = false;
    }
  });

  // Detect iOS Safari (non-standalone) to show helpful Add to Home Screen instructions
  const ua = window.navigator.userAgent || "";
  const isIOS = /iPhone|iPad|iPod/i.test(ua);
  if (isIOS && !isStandalone && !installDismissed && installBanner && installSubtext && installBtn) {
    installSubtext.textContent = "Tap Share ⎋ in Safari, then 'Add to Home Screen' for the full-screen app.";
    installBtn.hidden = true;
    installBanner.hidden = false;
  }

  if (installBtn) {
    installBtn.addEventListener("click", async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      if (installBanner) installBanner.hidden = true;
    });
  }

  if (dismissInstallBtn) {
    dismissInstallBtn.addEventListener("click", () => {
      localStorage.setItem("vertex_pwa_dismissed", "1");
      if (installBanner) installBanner.hidden = true;
    });
  }

  // Filter form prevent default submit
  document.getElementById("filter-form").addEventListener("submit", (e) => {
    e.preventDefault();
  });

  // View Mode Switch (Grid / List)
  const viewGridBtn = document.getElementById("btn-view-grid");
  const viewListBtn = document.getElementById("btn-view-list");
  if (viewGridBtn) {
    viewGridBtn.addEventListener("click", () => applyViewMode("grid"));
  }
  if (viewListBtn) {
    viewListBtn.addEventListener("click", () => applyViewMode("list"));
  }

  // Mobile Filter Drawer Toggle
  const toggleFiltersBtn = document.getElementById("btn-toggle-filters");
  const filterDrawer = document.getElementById("filter-selects-drawer");
  if (toggleFiltersBtn && filterDrawer) {
    toggleFiltersBtn.addEventListener("click", () => {
      const isOpen = filterDrawer.classList.toggle("is-open");
      toggleFiltersBtn.setAttribute("aria-expanded", String(isOpen));
    });
  }

  // Interactive Metric Pills (tap to filter status)
  for (const card of document.querySelectorAll(".metric-card.metric-interactive")) {
    const activateMetricFilter = () => {
      const targetStatus = card.dataset.filterStatus || "";
      state.status = state.status === targetStatus && targetStatus !== "" ? "" : targetStatus;
      const statusSelect = document.getElementById("status-filter");
      if (statusSelect) statusSelect.value = state.status;
      updateActiveFiltersUI();
      fetchGames();
    };
    card.addEventListener("click", activateMetricFilter);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activateMetricFilter();
      }
    });
  }

  // Reset Filters button
  const resetFiltersBtn = document.getElementById("btn-reset-filters");
  if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener("click", () => {
      state.platform = "";
      state.status = "";
      state.genre = "";
      state.search = "";
      state.sortBy = "playtime_desc";
      state.favoriteOnly = false;

      document.getElementById("search-input").value = "";
      document.getElementById("status-filter").value = "";
      document.getElementById("genre-filter").value = "";
      document.getElementById("sort-select").value = "playtime_desc";
      document.getElementById("btn-fav-toggle").setAttribute("aria-pressed", "false");
      for (const p of document.querySelectorAll("#platform-pills .pill")) {
        p.classList.toggle("active", p.dataset.platform === "");
      }
      updateActiveFiltersUI();
      fetchGames();
    });
  }

  // Search input
  let searchTimer = null;
  document.getElementById("search-input").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      updateActiveFiltersUI();
      fetchGames();
    }, 200);
  });

  // Platform pills
  document.getElementById("platform-pills").addEventListener("click", (e) => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    for (const p of document.querySelectorAll("#platform-pills .pill")) {
      p.classList.remove("active");
    }
    btn.classList.add("active");
    state.platform = btn.dataset.platform || "";
    updateActiveFiltersUI();
    fetchGames();
  });

  // Status, Genre & Sort selects
  const genreFilterEl = document.getElementById("genre-filter");
  if (genreFilterEl) {
    genreFilterEl.addEventListener("change", (e) => {
      state.genre = e.target.value;
      updateActiveFiltersUI();
      fetchGames();
    });
  }

  document.getElementById("status-filter").addEventListener("change", (e) => {
    state.status = e.target.value;
    updateActiveFiltersUI();
    fetchGames();
  });

  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    updateActiveFiltersUI();
    fetchGames();
  });

  // Favorites toggle
  const favBtn = document.getElementById("btn-fav-toggle");
  favBtn.addEventListener("click", () => {
    state.favoriteOnly = !state.favoriteOnly;
    favBtn.setAttribute("aria-pressed", String(state.favoriteOnly));
    updateActiveFiltersUI();
    fetchGames();
  });

  // Trigger Sync (Header + Mobile Bottom Nav)
  const syncBtn = document.getElementById("btn-sync");
  const mnavSyncBtn = document.getElementById("mnav-sync");
  const mnavSyncIcon = document.getElementById("mnav-sync-icon");
  const mnavSyncLabel = document.getElementById("mnav-sync-label");

  async function triggerLibrarySync() {
    if (syncBtn) {
      syncBtn.disabled = true;
      syncBtn.textContent = "Syncing...";
    }
    if (mnavSyncBtn) mnavSyncBtn.disabled = true;
    if (mnavSyncIcon) mnavSyncIcon.classList.add("spin-anim");
    if (mnavSyncLabel) mnavSyncLabel.textContent = "Syncing";
    try {
      await fetch("/api/sync", { method: "POST" });
      await fetchStats();
      await fetchGames();
    } finally {
      if (syncBtn) {
        syncBtn.disabled = false;
        syncBtn.textContent = "Sync Libraries";
      }
      if (mnavSyncBtn) mnavSyncBtn.disabled = false;
      if (mnavSyncIcon) mnavSyncIcon.classList.remove("spin-anim");
      if (mnavSyncLabel) mnavSyncLabel.textContent = "Sync";
    }
  }

  syncBtn.addEventListener("click", triggerLibrarySync);
  if (mnavSyncBtn) {
    mnavSyncBtn.addEventListener("click", triggerLibrarySync);
  }

  // Add Game Dialog
  const addDialog = document.getElementById("add-game-dialog");
  function openAddGameModal() {
    document.getElementById("add-game-form").reset();
    syncAddFavButton(false);
    document.getElementById("catalog-results").hidden = true;
    addDialog.showModal();
  }
  document.getElementById("btn-open-add").addEventListener("click", openAddGameModal);
  document.getElementById("btn-close-add").addEventListener("click", () => addDialog.close());
  document.getElementById("btn-cancel-add").addEventListener("click", () => addDialog.close());

  const addFavBtn = document.getElementById("btn-add-fav");
  if (addFavBtn) {
    addFavBtn.addEventListener("click", () => {
      const favCheck = document.getElementById("add-favorite");
      syncAddFavButton(!favCheck.checked);
    });
  }

  document.getElementById("btn-catalog-lookup").addEventListener("click", handleCatalogSearch);
  document.getElementById("catalog-search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleCatalogSearch();
    }
  });

  document.getElementById("add-game-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const genresRaw = document.getElementById("add-genres").value;
    const genres = genresRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const ratingVal = document.getElementById("add-rating").value;

    const payload = {
      title: document.getElementById("add-title").value.trim(),
      platform: document.getElementById("add-platform").value,
      completion_status: document.getElementById("add-status").value,
      playtime_hours: parseFloat(document.getElementById("add-hours").value || "0"),
      user_rating: ratingVal ? parseInt(ratingVal, 10) : null,
      cover_url: document.getElementById("add-cover").value.trim() || null,
      genres,
      notes: document.getElementById("add-notes").value.trim(),
      favorite: Boolean(document.getElementById("add-favorite")?.checked),
    };

    const res = await fetch("/api/games", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      addDialog.close();
      await fetchStats();
      await fetchGames();
    }
  });

  // Edit Game Dialog
  const editDialog = document.getElementById("edit-game-dialog");
  document.getElementById("btn-close-edit").addEventListener("click", () => editDialog.close());
  document.getElementById("btn-cancel-edit").addEventListener("click", () => editDialog.close());

  const editFavBtn = document.getElementById("btn-edit-fav");
  if (editFavBtn) {
    editFavBtn.addEventListener("click", () => {
      const favCheck = document.getElementById("edit-favorite");
      syncEditFavButton(!favCheck.checked);
    });
  }

  const clearRatingBtn = document.getElementById("btn-clear-rating");
  if (clearRatingBtn) {
    clearRatingBtn.addEventListener("click", () => {
      renderEditRatingPills(null);
    });
  }

  const editGenrePicker = document.getElementById("edit-genre-picker");
  if (editGenrePicker) {
    editGenrePicker.addEventListener("change", (e) => {
      const chosen = e.target.value;
      if (!chosen) return;
      const inputEl = document.getElementById("edit-genres");
      const current = inputEl.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (!current.some((g) => g.toLowerCase() === chosen.toLowerCase())) {
        current.push(chosen);
        inputEl.value = current.join(", ");
      }
      renderEditGenreChips();
    });
  }

  const editCoverInput = document.getElementById("edit-cover");
  if (editCoverInput) {
    editCoverInput.addEventListener("input", (e) => {
      const val = e.target.value.trim();
      const preview = document.getElementById("edit-cover-preview");
      if (preview && val) {
        preview.src = val;
      }
    });
  }

  const toggleHoursBtn = document.getElementById("btn-toggle-edit-hours");
  if (toggleHoursBtn) {
    toggleHoursBtn.addEventListener("click", () => {
      const hoursInput = document.getElementById("edit-hours");
      const hoursDisplay = document.getElementById("edit-hours-display");
      const hoursEditWrap = document.getElementById("edit-hours-edit-wrap");
      const isUnlocked = hoursInput.dataset.unlocked === "true";
      if (!isUnlocked) {
        hoursInput.dataset.unlocked = "true";
        hoursInput.readOnly = false;
        hoursDisplay.hidden = true;
        hoursEditWrap.hidden = false;
        toggleHoursBtn.textContent = "Lock";
        hoursInput.focus();
        hoursInput.select();
      } else {
        hoursInput.dataset.unlocked = "false";
        hoursInput.readOnly = true;
        hoursDisplay.textContent = `⏱ ${parseFloat(hoursInput.value || "0")} hrs`;
        hoursDisplay.hidden = false;
        hoursEditWrap.hidden = true;
        toggleHoursBtn.textContent = "Edit";
      }
    });
  }

  document.getElementById("edit-game-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const gameId = document.getElementById("edit-id").value;
    const genresRaw = document.getElementById("edit-genres").value;
    const genres = genresRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const ratingVal = document.getElementById("edit-rating").value;
    const hoursInput = document.getElementById("edit-hours");

    const payload = {
      completion_status: document.getElementById("edit-status").value,
      user_rating: ratingVal ? parseInt(ratingVal, 10) : null,
      genres,
      cover_url: document.getElementById("edit-cover").value.trim() || null,
      notes: document.getElementById("edit-notes").value.trim(),
      favorite: document.getElementById("edit-favorite").checked,
    };

    if (hoursInput && hoursInput.dataset.unlocked === "true") {
      payload.playtime_hours = parseFloat(hoursInput.value || "0");
    }

    const res = await fetch(`/api/games/${gameId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      editDialog.close();
      await fetchStats();
      await fetchGames();
    }
  });

  document.getElementById("btn-delete-game").addEventListener("click", async () => {
    const gameId = document.getElementById("edit-id").value;
    if (!confirm("Remove this game from your library?")) return;
    const res = await fetch(`/api/games/${gameId}`, { method: "DELETE" });
    if (res.ok) {
      editDialog.close();
      await fetchStats();
      await fetchGames();
    }
  });

  // Quick-link another game from inside the Edit Game modal
  let editLinkTimer = null;
  const editLinkInput = document.getElementById("edit-link-search");
  const editLinkResults = document.getElementById("edit-link-results");
  editLinkInput.addEventListener("input", (e) => {
    clearTimeout(editLinkTimer);
    const q = e.target.value.trim();
    if (q.length < 2) {
      editLinkResults.hidden = true;
      editLinkResults.innerHTML = "";
      return;
    }
    editLinkTimer = setTimeout(async () => {
      const currentId = parseInt(document.getElementById("edit-id").value, 10);
      const res = await fetch(`/api/games?search=${encodeURIComponent(q)}&limit=10`);
      if (!res.ok) return;
      const data = await res.json();
      const matches = (data.games || []).filter((g) => g.id !== currentId);
      editLinkResults.innerHTML = "";
      if (matches.length === 0) {
        editLinkResults.hidden = true;
        return;
      }
      editLinkResults.hidden = false;
      for (const m of matches) {
        const li = document.createElement("li");
        li.className = "catalog-item";
        li.style.justifyContent = "space-between";

        const info = document.createElement("span");
        info.textContent = `${m.title} (${m.platform} • ${m.playtime_hours || 0}h)`;

        const linkBtn = document.createElement("button");
        linkBtn.type = "button";
        linkBtn.className = "btn btn-primary btn-xs";
        linkBtn.textContent = "+ Link";
        linkBtn.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const confirmRes = await fetch("/api/reconcile/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              game_ids: [currentId, m.id],
              primary_id: currentId,
            }),
          });
          if (confirmRes.ok) {
            const cData = await confirmRes.json();
            await fetchStats();
            await fetchGames();
            if (cData.game) {
              openEditModal(cData.game);
            }
          }
        });

        li.appendChild(info);
        li.appendChild(linkBtn);
        editLinkResults.appendChild(li);
      }
    }, 180);
  });

  // Cross-Platform Reconcile Dialog & Hotkeys
  const reconcileDialog = document.getElementById("reconcile-dialog");
  document.getElementById("btn-open-reconcile").addEventListener("click", openReconcileModal);
  document.getElementById("btn-close-reconcile").addEventListener("click", () => {
    reconcileDialog.close();
  });
  reconcileDialog.addEventListener("close", () => {
    fetchGames();
    fetchStats();
  });

  document.getElementById("reconcile-scope").addEventListener("change", (e) => {
    reloadReconcileQueue(e.target.value);
  });

  document.getElementById("btn-reconcile-confirm").addEventListener("click", confirmCurrentReconcileGroup);
  document.getElementById("btn-reconcile-dismiss").addEventListener("click", dismissCurrentReconcileGroup);
  document.getElementById("btn-reconcile-prev").addEventListener("click", () => stepReconcile(-1));
  document.getElementById("btn-reconcile-skip").addEventListener("click", () => stepReconcile(1));

  document.getElementById("btn-reconcile-open-manual").addEventListener("click", () => {
    const scopeSelect = document.getElementById("reconcile-scope");
    scopeSelect.value = "manual";
    reloadReconcileQueue("manual");
  });

  document.getElementById("btn-reconcile-reset-dismissed").addEventListener("click", async () => {
    const res = await fetch("/api/reconcile/reset-dismissals", { method: "POST" });
    if (res.ok) {
      await fetchStats();
      await reloadReconcileQueue(state.reconcileScope);
      showReconcileToast("Restored dismissed suggestions");
    }
  });

  let manualReconcileTimer = null;
  document.getElementById("manual-reconcile-search").addEventListener("input", (e) => {
    clearTimeout(manualReconcileTimer);
    manualReconcileTimer = setTimeout(() => {
      renderManualSearchResults(e.target.value.trim());
    }, 180);
  });

  // Quick Triage Dialog & Hotkeys
  const triageDialog = document.getElementById("triage-dialog");
  document.getElementById("btn-open-triage").addEventListener("click", openTriageModal);
  document.getElementById("btn-close-triage").addEventListener("click", () => {
    triageDialog.close();
  });
  document.getElementById("btn-done-triage").addEventListener("click", () => {
    triageDialog.close();
  });
  triageDialog.addEventListener("close", () => {
    fetchGames();
    fetchStats();
  });

  document.getElementById("triage-scope").addEventListener("change", (e) => {
    reloadTriageQueue(e.target.value);
  });

  for (const btn of document.querySelectorAll(".triage-action-btn")) {
    btn.addEventListener("click", () => {
      applyTriageStatus(btn.dataset.status);
    });
  }

  document.getElementById("btn-triage-fav").addEventListener("click", toggleTriageFavorite);
  document.getElementById("btn-triage-prev").addEventListener("click", () => stepTriage(-1));
  document.getElementById("btn-triage-skip").addEventListener("click", () => stepTriage(1));

  // Touch swipe navigation on Triage & Reconcile cards (swipe left = next/skip, swipe right = previous)
  attachHorizontalSwipe(
    document.getElementById("triage-card"),
    () => stepTriage(1),
    () => stepTriage(-1)
  );
  attachHorizontalSwipe(
    document.getElementById("reconcile-card"),
    () => stepReconcile(1),
    () => stepReconcile(-1)
  );

  // Mobile Bottom Navigation Bar wiring
  const mnavLibrary = document.getElementById("mnav-library");
  const mnavTriage = document.getElementById("mnav-triage");
  const mnavAdd = document.getElementById("mnav-add");
  const mnavReconcile = document.getElementById("mnav-reconcile");

  function setMobileNavActive(activeId) {
    for (const item of document.querySelectorAll(".mobile-bottom-nav .mnav-item")) {
      item.classList.toggle("active", item.id === activeId);
    }
  }

  if (mnavLibrary) {
    mnavLibrary.addEventListener("click", () => {
      for (const d of document.querySelectorAll("dialog[open]")) {
        d.close();
      }
      setMobileNavActive("mnav-library");
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
  if (mnavTriage) {
    mnavTriage.addEventListener("click", () => {
      setMobileNavActive("mnav-triage");
      openTriageModal();
    });
  }
  if (mnavAdd) {
    mnavAdd.addEventListener("click", () => {
      openAddGameModal();
    });
  }
  if (mnavReconcile) {
    mnavReconcile.addEventListener("click", () => {
      setMobileNavActive("mnav-reconcile");
      openReconcileModal();
    });
  }

  triageDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));
  reconcileDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));
  addDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));

  // Floating Scroll-to-Top Button
  const scrollTopBtn = document.getElementById("btn-scroll-top");
  if (scrollTopBtn) {
    window.addEventListener(
      "scroll",
      () => {
        scrollTopBtn.hidden = window.scrollY < 520;
      },
      { passive: true }
    );
    scrollTopBtn.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  // Handle PWA Home Screen Shortcut URLs (?action=add | ?action=triage | ?action=reconcile)
  const urlParams = new URLSearchParams(window.location.search);
  const actionParam = urlParams.get("action");
  if (actionParam === "add") {
    openAddGameModal();
    window.history.replaceState({}, "", "/");
  } else if (actionParam === "triage") {
    openTriageModal();
    window.history.replaceState({}, "", "/");
  } else if (actionParam === "reconcile") {
    openReconcileModal();
    window.history.replaceState({}, "", "/");
  }

  // Global keyboard shortcuts while Quick Triage or Reconcile modal is open
  const keyToStatus = {
    "1": "Playing",
    "2": "Plan to Play",
    "3": "Beaten",
    "4": "Completed",
    "5": "Dropped",
    "6": "Not Played",
  };

  window.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (reconcileDialog.open && state.reconcileScope !== "manual") {
      if (e.key === "Enter" || e.key === "c" || e.key === "C") {
        e.preventDefault();
        confirmCurrentReconcileGroup();
      } else if (e.key === "x" || e.key === "X" || e.key === "d" || e.key === "D") {
        e.preventDefault();
        dismissCurrentReconcileGroup();
      } else if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        stepReconcile(1);
      } else if (e.key === "ArrowLeft" || e.key === "Backspace") {
        e.preventDefault();
        stepReconcile(-1);
      }
      return;
    }

    if (!triageDialog.open) return;

    if (keyToStatus[e.key]) {
      e.preventDefault();
      applyTriageStatus(keyToStatus[e.key]);
    } else if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      toggleTriageFavorite();
    } else if (e.key === "ArrowRight" || e.key === " ") {
      e.preventDefault();
      stepTriage(1);
    } else if (e.key === "ArrowLeft" || e.key === "Backspace") {
      e.preventDefault();
      stepTriage(-1);
    }
  });

  // Fallback for <dialog closedby="any"> light-dismiss in browsers without native closedBy support
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    const outside = (e, dialog) => {
      const r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
      let pressedOutside = false;
      dialog.addEventListener("pointerdown", (e) => {
        pressedOutside = outside(e, dialog);
      });
      dialog.addEventListener("pointerup", (e) => {
        if (pressedOutside && outside(e, dialog)) dialog.close();
      });
    }
  }
});
