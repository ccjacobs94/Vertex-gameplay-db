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
  "FPS",
  "Hack and Slash",
  "Horror",
  "Immersive Sim",
  "Interactive Drama",
  "Metroidvania",
  "Platformer",
  "Puzzle",
  "Puzzle Platformer",
  "Racing",
  "Roguelike",
  "RPG",
  "Sandbox",
  "Shooter",
  "Simulation",
  "Soulslike",
  "Sports",
  "Strategy",
  "Survival",
  "Tactical",
  "Tactical RPG",
  "Third-Person Shooter",
  "Turn-Based Strategy",
  "Visual Novel",
];

const COMMON_TAGS = [
  "Casual",
  "Co-op",
  "Comedy",
  "Crafting",
  "Dark",
  "Detective",
  "Exploration",
  "Fantasy",
  "Free To Play",
  "Horror",
  "Indie",
  "Multiplayer",
  "Open World",
  "Post-Apocalyptic",
  "Psychological",
  "Puzzle",
  "Real-Time",
  "Rhythm",
  "Sci-Fi",
  "Space",
  "Stealth",
  "Story-Rich",
  "Superhero",
  "Supernatural",
  "Survival Horror",
  "Tower Defense",
  "Turn-Based",
];

const state = {
  games: [],
  allGenres: [...COMMON_GENRES],
  allTags: [...COMMON_TAGS],
  platform: "",
  status: "",
  genre: "",
  tag: "",
  search: "",
  sortBy: "playtime_desc",
  favoriteOnly: false,
  viewMode: localStorage.getItem("vertex_view_mode") || "grid",
  activeTab: localStorage.getItem("vertex_active_tab") || "library",
  habitsData: null,
  triageQueue: [],
  triageIndex: 0,
  triageScope: "unlocked",
  reconcileQueue: [],
  reconcileIndex: 0,
  reconcileScope: "all",
  reconcileCheckedIds: new Set(),
  manualSelectedGames: [],
  reconcileCandidatesCount: 0,
  playtimePrompts: [],
  currentGameAchievements: null,
  currentAchFilter: "all",
  currentAchPlatform: "all",
  currentAchSearch: "",
};

function makeFallbackAchIcon(name) {
  const char = (name || "A").trim().charAt(0).toUpperCase() || "🏆";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">
    <rect width="44" height="44" rx="6" fill="#1e293b"/>
    <text x="22" y="27" fill="#94a3b8" font-family="sans-serif" font-size="16" font-weight="bold" text-anchor="middle">${char}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

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
    (state.tag ? 1 : 0) +
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
      !state.tag &&
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

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function syncFilterInputs() {
  for (const p of document.querySelectorAll("#platform-pills .pill")) {
    p.classList.toggle("active", (p.dataset.platform || "") === state.platform);
  }
  const stSelect = document.getElementById("status-filter");
  if (stSelect) stSelect.value = state.status;
  const gnSelect = document.getElementById("genre-filter");
  if (gnSelect) gnSelect.value = state.genre;
  const tgSelect = document.getElementById("tag-filter");
  if (tgSelect) tgSelect.value = state.tag;
  const qInput = document.getElementById("search-input");
  if (qInput) qInput.value = state.search;
  const favBtn = document.getElementById("btn-fav-toggle");
  if (favBtn) favBtn.setAttribute("aria-pressed", String(state.favoriteOnly));
}

function refreshActiveView() {
  updateActiveFiltersUI();
  if (state.activeTab === "habits") {
    fetchHabitsAnalytics();
  } else {
    fetchGames();
  }
}

function switchMainTab(tabName, shouldScroll = false) {
  state.activeTab = tabName === "habits" ? "habits" : "library";
  localStorage.setItem("vertex_active_tab", state.activeTab);

  const tabLib = document.getElementById("tab-library");
  const tabHab = document.getElementById("tab-habits");
  const mnavLib = document.getElementById("mnav-library");
  const mnavHab = document.getElementById("mnav-habits");
  const libSec = document.getElementById("library-view-section");
  const habSec = document.getElementById("habits-view-section");
  const metricsBar = document.getElementById("metrics-bar");
  const promptsBanner = document.getElementById("playtime-prompts-banner");

  const isHabits = state.activeTab === "habits";

  if (tabLib) {
    tabLib.classList.toggle("active", !isHabits);
    tabLib.setAttribute("aria-selected", String(!isHabits));
  }
  if (tabHab) {
    tabHab.classList.toggle("active", isHabits);
    tabHab.setAttribute("aria-selected", String(isHabits));
  }
  if (mnavLib) {
    mnavLib.classList.toggle("active", !isHabits);
  }
  if (mnavHab) {
    mnavHab.classList.toggle("active", isHabits);
  }

  if (libSec) libSec.hidden = isHabits;
  if (metricsBar) metricsBar.hidden = isHabits;
  if (promptsBanner) promptsBanner.hidden = isHabits || (state.playtimePrompts || []).length === 0;
  if (habSec) habSec.hidden = !isHabits;

  if (isHabits) {
    fetchHabitsAnalytics();
  } else {
    fetchGames();
  }

  if (shouldScroll) {
    document.getElementById("controls-bar")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function fetchHabitsAnalytics() {
  const params = new URLSearchParams();
  if (state.search) params.set("search", state.search);
  if (state.platform) params.set("platform", state.platform);
  if (state.status) params.set("status", state.status);
  if (state.genre) params.set("genre", state.genre);
  if (state.tag) params.set("tag", state.tag);
  if (state.favoriteOnly) params.set("favorite_only", "true");

  try {
    const res = await fetch(`/api/analytics?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    state.habitsData = data;
    renderHabitsDashboard(data);
  } catch (err) {
    console.error("Failed to load habits analytics:", err);
  }
}

const STATUS_COLORS = {
  Completed: "#10b981",
  Beaten: "#34d399",
  Playing: "#3b82f6",
  "Plan to Play": "#f59e0b",
  "Not Played": "#64748b",
  Dropped: "#ef4444",
};

async function openGameById(gameId) {
  let game = state.games.find((g) => g.id === gameId);
  if (!game) {
    try {
      const res = await fetch(`/api/games?search=${encodeURIComponent(gameId)}`);
      if (res.ok) {
        const d = await res.json();
        game = (d.games || []).find((g) => g.id === gameId);
      }
    } catch (e) {
      console.error("Failed to fetch game by ID:", e);
    }
  }
  if (game) {
    openEditModal(game);
  }
}

function renderHabitsDashboard(data) {
  if (!data || !data.summary) return;
  const s = data.summary;

  // 1. KPI Pulse Strip
  const elHours = document.getElementById("hkpi-hours");
  if (elHours) elHours.textContent = `${s.total_hours.toLocaleString()} hrs`;
  const elDays = document.getElementById("hkpi-days");
  if (elDays) elDays.textContent = `~${s.gaming_days} days of gameplay`;

  const elSystem = document.getElementById("hkpi-system");
  if (elSystem) elSystem.textContent = s.dominant_system || "—";
  const elSystemDetail = document.getElementById("hkpi-system-detail");
  if (elSystemDetail) {
    const topPlat = data.platforms[0];
    elSystemDetail.textContent = topPlat
      ? `${topPlat.hours.toLocaleString()} hrs (${topPlat.playtime_pct}%)`
      : "Most played platform";
  }

  const elGenre = document.getElementById("hkpi-genre");
  if (elGenre) elGenre.textContent = s.dominant_genre || "—";
  const elGenreDetail = document.getElementById("hkpi-genre-detail");
  if (elGenreDetail) {
    const topGen = data.genres[0];
    elGenreDetail.textContent = topGen
      ? `${topGen.hours.toLocaleString()} hrs (${topGen.count} titles)`
      : "Favorite game category";
  }

  const elCompletion = document.getElementById("hkpi-completion");
  if (elCompletion) elCompletion.textContent = `${s.completion_rate}%`;
  const elCompletionDetail = document.getElementById("hkpi-completion-detail");
  if (elCompletionDetail) {
    elCompletionDetail.textContent = `${s.beaten_count} finished · ${s.played_games} started`;
  }

  const elBailout = document.getElementById("hkpi-bailout");
  if (elBailout) elBailout.textContent = `${s.avg_dropped_hours}h vs ${s.avg_beaten_hours}h`;
  const elBailoutDetail = document.getElementById("hkpi-bailout-detail");
  if (elBailoutDetail) {
    elBailoutDetail.textContent = `Drop avg (${s.avg_dropped_hours}h) vs finish avg (${s.avg_beaten_hours}h)`;
  }

  const elShame = document.getElementById("hkpi-shame");
  if (elShame) elShame.textContent = `${s.shelf_of_shame_count} games`;
  const elShameDetail = document.getElementById("hkpi-shame-detail");
  if (elShameDetail) {
    const pct = Math.round((s.shelf_of_shame_count / Math.max(s.total_games, 1)) * 100);
    elShameDetail.textContent = `${pct}% of filtered backlog`;
  }

  // 2. Active Filter Summary
  const subTitle = document.getElementById("habits-subtitle");
  if (subTitle) {
    subTitle.textContent = `Analyzing ${s.total_games} games · ${s.total_hours.toLocaleString()} total playtime hours`;
  }
  const summaryChips = document.getElementById("habits-active-filters-summary");
  if (summaryChips) {
    summaryChips.innerHTML = "";
    const chips = [];
    if (state.platform) chips.push({ label: `Platform: ${state.platform}`, key: "platform" });
    if (state.genre) chips.push({ label: `Genre: ${state.genre}`, key: "genre" });
    if (state.tag) chips.push({ label: `Tag: ${state.tag}`, key: "tag" });
    if (state.status) chips.push({ label: `Status: ${state.status}`, key: "status" });
    if (state.favoriteOnly) chips.push({ label: "★ Favorites", key: "favoriteOnly" });
    if (state.search) chips.push({ label: `Search: "${state.search}"`, key: "search" });

    for (const chip of chips) {
      const span = document.createElement("span");
      span.className = "habits-filter-chip";
      span.innerHTML = `<span>${escapeHtml(chip.label)}</span><button type="button" class="habits-filter-chip-clear" aria-label="Remove filter">✕</button>`;
      span.querySelector("button").addEventListener("click", () => {
        if (chip.key === "platform") state.platform = "";
        else if (chip.key === "genre") state.genre = "";
        else if (chip.key === "tag") state.tag = "";
        else if (chip.key === "status") state.status = "";
        else if (chip.key === "favoriteOnly") state.favoriteOnly = false;
        else if (chip.key === "search") state.search = "";
        syncFilterInputs();
        refreshActiveView();
      });
      summaryChips.appendChild(span);
    }
  }

  // 3. Systems Comparison List
  const systemsContainer = document.getElementById("systems-comparison-list");
  if (systemsContainer) {
    systemsContainer.innerHTML = "";
    if (data.platforms.length === 0) {
      systemsContainer.innerHTML = '<p class="empty-state">No platform data found for current filters.</p>';
    } else {
      for (const plat of data.platforms) {
        let pillClass = "system-pill-other";
        const pLower = plat.name.toLowerCase();
        if (pLower.includes("steam")) pillClass = "system-pill-steam";
        else if (pLower.includes("xbox")) pillClass = "system-pill-xbox";
        else if (pLower.includes("playstation")) pillClass = "system-pill-playstation";
        else if (pLower.includes("switch")) pillClass = "system-pill-switch";

        const card = document.createElement("div");
        card.className = `system-row-card ${state.platform === plat.name ? "active-filter" : ""}`;
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("title", `Click to filter by ${plat.name}`);

        const topGameSnippet = plat.top_game
          ? `
            <div class="system-top-game-snippet">
              ${plat.top_game.cover_url ? `<img src="${escapeHtml(plat.top_game.cover_url)}" alt="" class="system-top-game-img" loading="lazy">` : ""}
              <span>#1 Most Played: <strong>${escapeHtml(plat.top_game.title)}</strong> (${plat.top_game.hours}h)</span>
            </div>
          `
          : "";

        card.innerHTML = `
          <div class="system-row-header">
            <div class="system-title-group">
              <span class="system-pill-badge ${pillClass}">${escapeHtml(plat.name)}</span>
              <span class="system-hours-text">${plat.hours.toLocaleString()} hrs</span>
            </div>
            <span class="badge-subtle">${plat.completion_rate}% Finished</span>
          </div>
          <div class="dual-meters-wrap">
            <div class="meter-labels-row">
              <span>Playtime: <strong>${plat.playtime_pct}%</strong> (${plat.hours.toLocaleString()}h)</span>
              <span>Library: <strong>${plat.library_pct}%</strong> (${plat.count} games)</span>
            </div>
            <div class="meter-track" title="Playtime Share: ${plat.playtime_pct}%">
              <div class="meter-fill meter-fill-${plat.name.toLowerCase().replace(/[^a-z]/g, "")}" style="inline-size: ${Math.min(100, Math.max(2, plat.playtime_pct))}%"></div>
            </div>
            <div class="meter-track" style="block-size: 6px;" title="Ownership Share: ${plat.library_pct}%">
              <div class="meter-fill meter-fill-ownership" style="inline-size: ${Math.min(100, Math.max(2, plat.library_pct))}%"></div>
            </div>
          </div>
          ${topGameSnippet}
        `;

        const togglePlatform = () => {
          state.platform = state.platform === plat.name ? "" : plat.name;
          syncFilterInputs();
          refreshActiveView();
        };
        card.addEventListener("click", togglePlatform);
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            togglePlatform();
          }
        });

        systemsContainer.appendChild(card);
      }
    }
  }

  // 4. Status Donut Chart & Legend
  const donutEl = document.getElementById("status-donut-chart");
  const legendEl = document.getElementById("status-legend-list");
  if (donutEl && legendEl) {
    legendEl.innerHTML = "";
    const statuses = data.statuses;
    const statusOrder = ["Completed", "Beaten", "Playing", "Plan to Play", "Not Played", "Dropped"];
    let currentDeg = 0;
    const segments = [];

    for (const key of statusOrder) {
      const st = statuses[key] || { name: key, count: 0, count_pct: 0, hours: 0, hours_pct: 0 };
      const color = STATUS_COLORS[key] || "#94a3b8";
      const deg = Math.round((st.count_pct / 100) * 360);
      if (deg > 0) {
        segments.push(`${color} ${currentDeg}deg ${currentDeg + deg}deg`);
        currentDeg += deg;
      }

      const item = document.createElement("div");
      item.className = "status-legend-item";
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      item.setAttribute("title", `Click to filter by ${key}`);
      item.innerHTML = `
        <div class="status-legend-left">
          <span class="status-color-dot" style="background-color: ${color}"></span>
          <span class="status-legend-name">${escapeHtml(key)}</span>
        </div>
        <div class="status-legend-right">
          <span class="status-legend-count">${st.count}</span>
          <span>(${st.count_pct}%) &bull; ${st.hours}h</span>
        </div>
      `;

      const toggleStatus = () => {
        state.status = state.status === key ? "" : key;
        syncFilterInputs();
        refreshActiveView();
      };
      item.addEventListener("click", toggleStatus);
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleStatus();
        }
      });

      legendEl.appendChild(item);
    }

    if (segments.length > 0 && currentDeg < 360) {
      segments.push(`${STATUS_COLORS["Not Played"]} ${currentDeg}deg 360deg`);
    }
    donutEl.style.background = segments.length > 0
      ? `conic-gradient(${segments.join(", ")})`
      : "rgba(255, 255, 255, 0.1)";

    const centerRate = document.getElementById("donut-center-rate");
    if (centerRate) centerRate.textContent = `${s.completion_rate}%`;
  }

  // 5. Genre Taste Profile Bars
  const genresContainer = document.getElementById("genres-bars-container");
  if (genresContainer) {
    genresContainer.innerHTML = "";
    if (data.genres.length === 0) {
      genresContainer.innerHTML = '<p class="empty-state">No genre data found for current selection.</p>';
    } else {
      const maxHours = Math.max(...data.genres.map((g) => g.hours), 1);
      for (const g of data.genres) {
        const bar = document.createElement("div");
        bar.className = "genre-bar-item";
        bar.setAttribute("role", "button");
        bar.setAttribute("tabindex", "0");
        bar.setAttribute("title", `Click to filter by ${g.genre}`);
        const fillPct = Math.min(100, Math.max(3, (g.hours / maxHours) * 100));

        bar.innerHTML = `
          <div class="genre-bar-header">
            <span class="genre-bar-title">${escapeHtml(g.genre)}</span>
            <span class="genre-bar-metrics">${g.hours.toLocaleString()} hrs (${g.playtime_pct}%) &bull; ${g.count} titles</span>
          </div>
          <div class="genre-meter-track" title="${g.hours} hrs">
            <div class="genre-meter-fill" style="inline-size: ${fillPct}%"></div>
          </div>
          <div class="genre-mini-badges">
            <span>✓ ${g.beaten_count} beaten</span>
            <span>▶ ${g.playing_count} playing</span>
            <span>⏳ ${g.backlog_count} backlog</span>
            <span>✕ ${g.dropped_count} dropped</span>
            <span style="margin-inline-start: auto; font-weight: 700; color: #34d399;">${g.completion_rate}% finished</span>
          </div>
        `;

        const toggleGenre = () => {
          state.genre = state.genre === g.genre ? "" : g.genre;
          syncFilterInputs();
          refreshActiveView();
        };
        bar.addEventListener("click", toggleGenre);
        bar.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleGenre();
          }
        });

        genresContainer.appendChild(bar);
      }
    }
  }

  // 6. Commitment Tiers
  const commitmentsContainer = document.getElementById("commitments-list");
  if (commitmentsContainer) {
    commitmentsContainer.innerHTML = "";
    for (const t of data.commitments) {
      const item = document.createElement("div");
      item.className = "tier-item-card";
      item.innerHTML = `
        <div class="tier-item-header">
          <span class="tier-item-label">${escapeHtml(t.label)}</span>
          <span class="tier-item-stats">${t.count} games (${t.count_pct}%) &bull; ${t.hours.toLocaleString()}h (${t.hours_pct}%)</span>
        </div>
        <div class="meter-track" title="${t.hours} hrs">
          <div class="meter-fill" style="inline-size: ${Math.min(100, Math.max(2, t.hours_pct))}%; background: linear-gradient(90deg, #3b82f6, #06b6d4);"></div>
        </div>
      `;
      commitmentsContainer.appendChild(item);
    }
  }

  // 7. Gaming Eras & Decades
  const erasContainer = document.getElementById("eras-list");
  if (erasContainer) {
    erasContainer.innerHTML = "";
    for (const e of data.eras) {
      const item = document.createElement("div");
      item.className = "tier-item-card";
      item.innerHTML = `
        <div class="tier-item-header">
          <span class="tier-item-label">${escapeHtml(e.label)}</span>
          <span class="tier-item-stats">${e.count} games (${e.count_pct}%) &bull; ${e.hours.toLocaleString()}h (${e.hours_pct}%)</span>
        </div>
        <div class="meter-track" title="${e.hours} hrs">
          <div class="meter-fill" style="inline-size: ${Math.min(100, Math.max(2, e.hours_pct))}%; background: linear-gradient(90deg, #f59e0b, #ef4444);"></div>
        </div>
      `;
      erasContainer.appendChild(item);
    }
  }

  // 8. Hall of Fame (Top Played)
  const hofContainer = document.getElementById("hof-games-row");
  if (hofContainer) {
    hofContainer.innerHTML = "";
    if (data.hall_of_fame.length === 0) {
      hofContainer.innerHTML = '<p class="empty-state">No games found.</p>';
    } else {
      data.hall_of_fame.forEach((g, idx) => {
        const card = document.createElement("div");
        card.className = "hof-game-card";
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("title", `View ${g.title}`);

        const fallback = makeFallbackCover(g.title, g.platform);
        const coverSrc = g.cover_url || fallback;

        card.innerHTML = `
          <div class="hof-poster-wrap">
            <span class="hof-rank-badge">👑 #${idx + 1}</span>
            <img src="${escapeHtml(coverSrc)}" alt="${escapeHtml(g.title)}" class="hof-poster-img" loading="lazy" onerror="this.src='${fallback}'">
          </div>
          <div class="hof-card-body">
            <div class="hof-card-title">${escapeHtml(g.title)}</div>
            <div class="hof-card-meta">
              <span class="system-pill-badge system-pill-other" style="font-size: 0.68rem; padding: 0.1rem 0.4rem;">${escapeHtml(g.platform)}</span>
              <span class="hof-hours-tag">${g.playtime_hours} hrs</span>
            </div>
          </div>
        `;
        card.addEventListener("click", () => openGameById(g.id));
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openGameById(g.id);
          }
        });
        hofContainer.appendChild(card);
      });
    }
  }

  // 9. Backlog Gems (Top Critic Rated Unplayed)
  const gemsContainer = document.getElementById("gems-games-row");
  if (gemsContainer) {
    gemsContainer.innerHTML = "";
    const gems = data.ratings.backlog_gems || [];
    if (gems.length === 0) {
      gemsContainer.innerHTML = '<p class="empty-state">No unplayed critic gems found in this filter.</p>';
    } else {
      gems.forEach((g) => {
        const card = document.createElement("div");
        card.className = "hof-game-card";
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("title", `View ${g.title}`);

        const fallback = makeFallbackCover(g.title, g.platform);
        const coverSrc = g.cover_url || fallback;

        card.innerHTML = `
          <div class="hof-poster-wrap">
            <span class="hof-critic-badge">⭐ ${g.critic_score}</span>
            <img src="${escapeHtml(coverSrc)}" alt="${escapeHtml(g.title)}" class="hof-poster-img" loading="lazy" onerror="this.src='${fallback}'">
          </div>
          <div class="hof-card-body">
            <div class="hof-card-title">${escapeHtml(g.title)}</div>
            <div class="hof-card-meta">
              <span class="system-pill-badge system-pill-other" style="font-size: 0.68rem; padding: 0.1rem 0.4rem;">${escapeHtml(g.platform)}</span>
              <span style="color: var(--accent-amber); font-weight: 700;">${escapeHtml(g.completion_status)}</span>
            </div>
          </div>
        `;
        card.addEventListener("click", () => openGameById(g.id));
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openGameById(g.id);
          }
        });
        gemsContainer.appendChild(card);
      });
    }
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

async function resolvePlaytimePrompt(promptId, action) {
  try {
    const res = await fetch(`/api/playtime-prompts/${promptId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    if (!res.ok) return;
    await fetchStats();
    refreshActiveView();
  } catch (err) {
    console.error("Failed to resolve playtime prompt:", err);
  }
}

function renderPlaytimePrompts(prompts) {
  state.playtimePrompts = Array.isArray(prompts) ? prompts : [];
  const bannerEl = document.getElementById("playtime-prompts-banner");
  if (!bannerEl) return;

  if (state.playtimePrompts.length === 0 || state.activeTab === "habits") {
    bannerEl.hidden = true;
    bannerEl.innerHTML = "";
    return;
  }

  bannerEl.hidden = false;
  bannerEl.innerHTML = "";

  for (const p of state.playtimePrompts) {
    const card = document.createElement("article");
    card.className = "playtime-prompt-card";

    const fallbackSrc = makeFallbackCover(p.title, p.platform);
    const coverSrc = p.cover_url || fallbackSrc;

    const left = document.createElement("div");
    left.className = "playtime-prompt-left";

    const thumb = document.createElement("img");
    thumb.className = "playtime-prompt-thumb";
    thumb.src = coverSrc;
    thumb.alt = "";
    thumb.width = 44;
    thumb.height = 66;
    thumb.loading = "lazy";
    thumb.onerror = () => {
      thumb.src = fallbackSrc;
    };
    left.appendChild(thumb);

    const info = document.createElement("div");
    info.className = "playtime-prompt-info";

    const isReplay = p.current_status === "Beaten" || p.current_status === "Completed";
    const kickerText = isReplay ? "🔄 Replay Activity Detected" : "🎮 Recent Playtime Detected";

    info.innerHTML = `
      <div class="playtime-prompt-kicker">
        <span>${escapeHtml(kickerText)}</span>
        <span class="playtime-prompt-delta">+${p.delta_hours} hrs</span>
        <span class="reconcile-copy-plat">${escapeHtml(p.platform)}</span>
      </div>
      <div class="playtime-prompt-text">
        Mark <strong>${escapeHtml(p.title)}</strong> as <strong>Playing</strong>?
      </div>
      <div class="playtime-prompt-sub">
        Playtime increased from ${p.previous_hours}h to ${p.new_hours}h &bull; Current status: <strong>${escapeHtml(p.current_status)}</strong>
      </div>
    `;
    left.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "playtime-prompt-actions";

    const acceptBtn = document.createElement("button");
    acceptBtn.type = "button";
    acceptBtn.className = "btn btn-primary btn-xs";
    acceptBtn.textContent = "▶ Mark as Playing";
    acceptBtn.addEventListener("click", () => resolvePlaytimePrompt(p.prompt_id, "accept"));

    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "btn btn-secondary btn-xs";
    dismissBtn.textContent = `Dismiss for now (Keep ${p.current_status})`;
    dismissBtn.addEventListener("click", () => resolvePlaytimePrompt(p.prompt_id, "dismiss"));

    const muteBtn = document.createElement("button");
    muteBtn.type = "button";
    muteBtn.className = "btn-link-subtle";
    muteBtn.textContent = "Don't ask again";
    muteBtn.title = "Never prompt on playtime increases for this game";
    muteBtn.addEventListener("click", () => resolvePlaytimePrompt(p.prompt_id, "mute"));

    actions.appendChild(acceptBtn);
    actions.appendChild(dismissBtn);
    actions.appendChild(muteBtn);

    card.appendChild(left);
    card.appendChild(actions);
    bannerEl.appendChild(card);
  }
}

async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    if (res.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
    if (!res.ok) return;
    const stats = await res.json();

    const logoutBtn = document.getElementById("btn-logout");
    if (logoutBtn) {
      logoutBtn.hidden = !stats.auth_enabled;
    }

    document.getElementById("stat-total-games").textContent = stats.total_games || 0;
    document.getElementById("stat-total-hours").textContent = `${stats.total_playtime_hours || 0} hrs`;
    document.getElementById("stat-playing").textContent = stats.playing_count || 0;
    document.getElementById("stat-backlog").textContent = stats.backlog_count || 0;
    document.getElementById("stat-completed").textContent = stats.completed_count || 0;
    updateReconcileButtonBadge(stats.reconcile_candidates_count || 0);
    renderPlaytimePrompts(stats.playtime_prompts || []);

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

    const tagSelect = document.getElementById("tag-filter");
    if (tagSelect && Array.isArray(stats.all_tags)) {
      const mergedTags = new Set([...COMMON_TAGS, ...stats.all_tags]);
      state.allTags = Array.from(mergedTags).sort((a, b) => a.localeCompare(b));
      const prevTag = state.tag || tagSelect.value || "";
      tagSelect.innerHTML = "";
      const allOpt = document.createElement("option");
      allOpt.value = "";
      allOpt.textContent = "All Tags";
      tagSelect.appendChild(allOpt);
      const tagList = stats.all_tags.length ? stats.all_tags : state.allTags;
      for (const tName of tagList) {
        const opt = document.createElement("option");
        opt.value = tName;
        opt.textContent = tName;
        if (tName === prevTag) opt.selected = true;
        tagSelect.appendChild(opt);
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
  if (state.tag) params.set("tag", state.tag);
  if (state.favoriteOnly) params.set("favorite_only", "true");
  params.set("sort_by", state.sortBy);

  try {
    const res = await fetch(`/api/games?${params.toString()}`);
    if (res.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
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

    const achTot = game.achievements_total || 0;
    const achUnl = game.achievements_unlocked || 0;
    if (achTot > 0 || achUnl > 0) {
      const achBadge = document.createElement("span");
      achBadge.className = "card-ach-badge";
      if (achTot > 0 && achUnl >= achTot) {
        achBadge.classList.add("ach-complete");
      }
      achBadge.textContent = achTot > 0 ? `🏆 ${achUnl}/${achTot}` : `🏆 ${achUnl}`;
      if (game.is_reconciled && Array.isArray(game.linked_games) && game.linked_games.length > 1) {
        const platDetails = game.linked_games
          .map((lg) => `${lg.platform}: ${lg.achievements_unlocked || 0}/${lg.achievements_total || 0}`)
          .join(" • ");
        achBadge.title = `Unique non-overlapping: ${achUnl}/${achTot} (${platDetails})`;
      } else {
        achBadge.title = `Achievements: ${achUnl}/${achTot}`;
      }
      wrapper.appendChild(achBadge);
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

    if (achTot > 0 || achUnl > 0) {
      const listAch = document.createElement("span");
      listAch.className = "card-list-ach";
      if (achTot > 0 && achUnl >= achTot) listAch.classList.add("ach-complete");
      listAch.textContent = achTot > 0 ? `🏆 ${achUnl}/${achTot}` : `🏆 ${achUnl}`;
      subrow.appendChild(listAch);
    }

    const genres = document.createElement("div");
    genres.className = "card-genres";
    const genreText = (game.genres || []).join(", ");
    const tagText = (game.tags || []).slice(0, 3).map((t) => `#${t}`).join(" ");
    if (genreText && tagText) {
      genres.textContent = `${genreText} • ${tagText}`;
    } else if (genreText) {
      genres.textContent = genreText;
    } else if (tagText) {
      genres.textContent = tagText;
    } else {
      genres.textContent = game.release_year ? `Released ${game.release_year}` : game.source;
    }
    genres.title = [
      ...(game.genres || []),
      ...(game.tags || []).map((t) => `#${t}`),
    ].join(", ");
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

function renderEditTagChips() {
  const chipsEl = document.getElementById("edit-tag-chips");
  const inputEl = document.getElementById("edit-tags");
  const pickerEl = document.getElementById("edit-tag-picker");
  if (!chipsEl || !inputEl) return;
  chipsEl.innerHTML = "";
  const list = inputEl.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  for (const tName of list) {
    const chip = document.createElement("span");
    chip.className = "edit-genre-chip";
    chip.textContent = tName;

    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.setAttribute("aria-label", `Remove ${tName}`);
    rmBtn.addEventListener("click", () => {
      const updated = list.filter((item) => item.toLowerCase() !== tName.toLowerCase());
      inputEl.value = updated.join(", ");
      renderEditTagChips();
    });
    chip.appendChild(rmBtn);
    chipsEl.appendChild(chip);
  }

  if (pickerEl) {
    const existingLower = new Set(list.map((t) => t.toLowerCase()));
    pickerEl.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "+ Add tag...";
    pickerEl.appendChild(placeholder);

    for (const tOption of state.allTags || COMMON_TAGS) {
      if (existingLower.has(tOption.toLowerCase())) continue;
      const opt = document.createElement("option");
      opt.value = tOption;
      opt.textContent = tOption;
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

    const copyTot = copy.achievements_total || 0;
    const copyUnl = copy.achievements_unlocked || 0;
    if (copyTot > 0 || copyUnl > 0) {
      const copyAch = document.createElement("span");
      copyAch.className = "linked-copy-ach-badge";
      if (copyTot > 0 && copyUnl >= copyTot) {
        copyAch.classList.add("ach-complete");
      }
      copyAch.textContent = copyTot > 0 ? `🏆 ${copyUnl}/${copyTot}` : `🏆 ${copyUnl}`;
      copyAch.title = `${copy.platform} achievements: ${copyUnl}/${copyTot}`;
      right.appendChild(copyAch);
    }

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

function shortPlatformLabel(platform) {
  const p = (platform || "").trim();
  const mapping = {
    "PC (Steam)": "Steam",
    "PC (Xbox)": "PC (Xbox)",
    "Xbox / PC": "Xbox/PC",
    "Xbox": "Xbox",
    "PlayStation 4": "PS4",
    "PlayStation 5": "PS5",
    "Nintendo Switch": "Switch",
    "PC (Other)": "PC",
  };
  return mapping[p] || p;
}

function renderAchievementsList() {
  const payload = state.currentGameAchievements || {};
  const listEl = document.getElementById("edit-achievements-list");
  const countBadge = document.getElementById("ach-explorer-count-badge");
  const overlapBadge = document.getElementById("ach-explorer-overlap-badge");
  const emptyEl = document.getElementById("ach-empty-state");
  const platPillsWrap = document.getElementById("ach-platform-filter-pills");

  if (!listEl) return;
  listEl.innerHTML = "";

  const unified = payload.unified_summary || { unlocked: 0, total: 0, percentage: 0, overlap_count: 0 };
  const perPlatform = payload.per_platform_summary || [];
  const achievements = payload.achievements || [];

  if (countBadge) {
    countBadge.textContent = `${unified.unlocked} / ${unified.total} (${unified.percentage}%)`;
  }

  if (overlapBadge) {
    if (unified.overlap_count > 0) {
      overlapBadge.hidden = false;
      overlapBadge.textContent = `★ ${unified.overlap_count} overlapping on multiple platforms`;
    } else {
      overlapBadge.hidden = true;
    }
  }

  const allCountEl = document.getElementById("ach-pill-all-count");
  const unlCountEl = document.getElementById("ach-pill-unl-count");
  const lckCountEl = document.getElementById("ach-pill-lck-count");
  if (allCountEl) allCountEl.textContent = achievements.length;
  if (unlCountEl) unlCountEl.textContent = achievements.filter((a) => a.unlocked).length;
  if (lckCountEl) lckCountEl.textContent = achievements.filter((a) => !a.unlocked).length;

  if (platPillsWrap) {
    if (perPlatform.length > 1) {
      platPillsWrap.hidden = false;
      platPillsWrap.innerHTML = "";

      const allPlatBtn = document.createElement("button");
      allPlatBtn.type = "button";
      allPlatBtn.className = `ach-pill ${state.currentAchPlatform === "all" ? "active" : ""}`;
      allPlatBtn.textContent = "All Platforms";
      allPlatBtn.addEventListener("click", () => {
        state.currentAchPlatform = "all";
        renderAchievementsList();
      });
      platPillsWrap.appendChild(allPlatBtn);

      for (const p of perPlatform) {
        const pBtn = document.createElement("button");
        pBtn.type = "button";
        pBtn.className = `ach-pill ${state.currentAchPlatform === p.platform ? "active" : ""}`;
        pBtn.textContent = `${p.short_platform} (${p.unlocked}/${p.total})`;
        pBtn.addEventListener("click", () => {
          state.currentAchPlatform = p.platform;
          renderAchievementsList();
        });
        platPillsWrap.appendChild(pBtn);
      }
    } else {
      platPillsWrap.hidden = true;
      platPillsWrap.innerHTML = "";
    }
  }

  const filterStatus = state.currentAchFilter || "all";
  const filterPlat = state.currentAchPlatform || "all";
  const searchQ = (state.currentAchSearch || "").toLowerCase().trim();

  const filtered = achievements.filter((a) => {
    if (filterStatus === "unlocked" && !a.unlocked) return false;
    if (filterStatus === "locked" && a.unlocked) return false;

    if (filterPlat !== "all") {
      const matchPlat = (a.platforms || []).some(
        (p) => p.platform === filterPlat || p.short_platform === filterPlat
      );
      if (!matchPlat) return false;
    }

    if (searchQ) {
      const nameMatch = (a.name || "").toLowerCase().includes(searchQ);
      const descMatch = (a.description || "").toLowerCase().includes(searchQ);
      if (!nameMatch && !descMatch) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    if (emptyEl) {
      emptyEl.hidden = false;
      if (achievements.length === 0) {
        emptyEl.textContent = "No achievements synced yet. Click '↻ Sync Achievements' above to check platform APIs.";
      } else {
        emptyEl.textContent = "No achievements match your filter.";
      }
    }
    return;
  }

  if (emptyEl) emptyEl.hidden = true;

  for (const a of filtered) {
    const li = document.createElement("li");
    li.className = `ach-card-item ${a.unlocked ? "unlocked" : "locked"}`;

    const iconWrap = document.createElement("div");
    iconWrap.className = "ach-card-icon";

    const iconImg = document.createElement("img");
    iconImg.className = "ach-icon-img";
    const fallbackSrc = makeFallbackAchIcon(a.name);
    const targetSrc = a.unlocked
      ? a.icon_url || a.icon_gray_url || fallbackSrc
      : a.icon_gray_url || a.icon_url || fallbackSrc;
    iconImg.src = targetSrc;
    iconImg.alt = "";
    iconImg.width = 48;
    iconImg.height = 48;
    iconImg.loading = "lazy";
    iconImg.onerror = () => {
      iconImg.src = fallbackSrc;
    };
    iconWrap.appendChild(iconImg);
    li.appendChild(iconWrap);

    const body = document.createElement("div");
    body.className = "ach-card-body";

    const topRow = document.createElement("div");
    topRow.className = "ach-card-top";

    const nameEl = document.createElement("strong");
    nameEl.className = "ach-card-name";
    nameEl.textContent = a.name;
    topRow.appendChild(nameEl);

    const metaTags = document.createElement("div");
    metaTags.className = "ach-meta-tags";

    if (a.reward_type && a.reward_value) {
      const rewTag = document.createElement("span");
      const rewClass = (a.reward_type || "").toLowerCase();
      rewTag.className = `ach-reward-tag ${rewClass}`;
      rewTag.textContent = a.reward_type === "Gamerscore" ? `${a.reward_value}G` : `🏆 ${a.reward_type}`;
      metaTags.appendChild(rewTag);
    }
    if (a.rarity_pct != null) {
      const rarTag = document.createElement("span");
      rarTag.className = "ach-rarity-tag";
      rarTag.textContent = `${Math.round(a.rarity_pct * 10) / 10}%`;
      rarTag.title = `Global unlock rate: ${a.rarity_pct}%`;
      metaTags.appendChild(rarTag);
    }
    topRow.appendChild(metaTags);
    body.appendChild(topRow);

    const descEl = document.createElement("p");
    descEl.className = "ach-card-desc";
    const descText = a.description || (a.is_hidden && !a.unlocked ? "Hidden secret achievement" : "No description provided");
    descEl.textContent = descText;
    body.appendChild(descEl);

    if (Array.isArray(a.platforms) && a.platforms.length > 0) {
      const platRow = document.createElement("div");
      platRow.className = "ach-platform-badges";
      for (const p of a.platforms) {
        const pBadge = document.createElement("span");
        pBadge.className = `ach-plat-badge ${p.unlocked ? "unlocked" : "locked"}`;
        const check = p.unlocked ? "✓" : "🔒";
        pBadge.textContent = `${check} ${p.short_platform}`;
        if (p.unlocked && p.unlock_time) {
          const uDate = new Date(p.unlock_time);
          const dateStr = !Number.isNaN(uDate.getTime())
            ? uDate.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
            : p.unlock_time;
          pBadge.title = `Unlocked on ${p.platform}: ${dateStr}`;
        } else {
          pBadge.title = p.unlocked ? `Unlocked on ${p.platform}` : `Locked on ${p.platform}`;
        }
        platRow.appendChild(pBadge);
      }
      body.appendChild(platRow);
    }

    li.appendChild(body);
    listEl.appendChild(li);
  }
}

async function loadAndRenderGameAchievements(gameId, forceSync = false) {
  const section = document.getElementById("edit-achievements-section");
  const listEl = document.getElementById("edit-achievements-list");
  const countBadge = document.getElementById("ach-explorer-count-badge");
  const overlapBadge = document.getElementById("ach-explorer-overlap-badge");
  const loadingEl = document.getElementById("ach-loading-indicator");
  const emptyEl = document.getElementById("ach-empty-state");
  const platPillsWrap = document.getElementById("ach-platform-filter-pills");
  const refreshBtn = document.getElementById("btn-refresh-game-ach");

  if (!section || !listEl) return;

  state.currentAchFilter = "all";
  state.currentAchPlatform = "all";
  state.currentAchSearch = "";
  const searchInput = document.getElementById("ach-search-input");
  if (searchInput) searchInput.value = "";

  for (const pill of document.querySelectorAll(".ach-pill[data-ach-filter]")) {
    pill.classList.toggle("active", pill.dataset.achFilter === "all");
  }

  listEl.innerHTML = "";
  if (loadingEl) loadingEl.hidden = false;
  if (emptyEl) emptyEl.hidden = true;
  if (refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Syncing...";
  }

  try {
    const url = forceSync
      ? `/api/games/${gameId}/achievements/sync`
      : `/api/games/${gameId}/achievements?auto_sync=true`;
    const res = await fetch(url, { method: forceSync ? "POST" : "GET" });
    if (!res.ok) {
      if (emptyEl) {
        emptyEl.textContent = "Failed to load achievements.";
        emptyEl.hidden = false;
      }
      return;
    }
    const data = await res.json();
    const payload = forceSync ? (data.achievements || {}) : data;

    if (payload && payload.unified_summary) {
      const u = payload.unified_summary.unlocked || 0;
      const t = payload.unified_summary.total || 0;
      const pct = payload.unified_summary.percentage != null
        ? payload.unified_summary.percentage
        : (t > 0 ? Math.round((u / t) * 100) : 0);

      const achDisplay = document.getElementById("edit-ach-display");
      if (achDisplay) {
        if (t > 0) {
          achDisplay.textContent = `🏆 ${u} / ${t} (${pct}%)`;
        } else if (u > 0) {
          achDisplay.textContent = `🏆 ${u} unlocked`;
        } else {
          achDisplay.textContent = "—";
        }
      }

      const editAchUnlInput = document.getElementById("edit-ach-unlocked");
      const editAchTotInput = document.getElementById("edit-ach-total");
      const editAchWrap = document.getElementById("edit-ach-edit-wrap");
      if (editAchUnlInput && editAchWrap && editAchWrap.hidden) {
        editAchUnlInput.value = u;
      }
      if (editAchTotInput && editAchWrap && editAchWrap.hidden) {
        editAchTotInput.value = t;
      }

      const achPills = document.getElementById("edit-ach-breakdown-pills");
      if (achPills && Array.isArray(payload.per_platform_summary) && payload.per_platform_summary.length > 1) {
        achPills.innerHTML = "";
        for (const p of payload.per_platform_summary) {
          const pill = document.createElement("span");
          pill.className = "edit-ach-pill";
          pill.textContent = `${p.short_platform}: ${p.unlocked}/${p.total}`;
          achPills.appendChild(pill);
        }
      }

      const gIdx = state.games.findIndex((g) => g.id === gameId);
      if (gIdx !== -1) {
        const prevUnl = state.games[gIdx].achievements_unlocked;
        const prevTot = state.games[gIdx].achievements_total;
        if (prevUnl !== u || prevTot !== t) {
          state.games[gIdx].achievements_unlocked = u;
          state.games[gIdx].achievements_total = t;
          renderGames();
        }
      }
    }

    state.currentGameAchievements = payload;
    renderAchievementsList();
  } catch (err) {
    console.error("Achievements load error:", err);
    if (emptyEl) {
      emptyEl.textContent = "Could not load achievements.";
      emptyEl.hidden = false;
    }
  } finally {
    if (loadingEl) loadingEl.hidden = true;
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "↻ Sync Achievements";
    }
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
  const achPills = document.getElementById("edit-ach-breakdown-pills");
  if (achTile && achDisplay) {
    const unl = game.achievements_unlocked || 0;
    const tot = game.achievements_total || 0;
    if (tot > 0) {
      const pct = Math.round((unl / tot) * 100);
      achDisplay.textContent = `🏆 ${unl} / ${tot} (${pct}%)`;
      achTile.hidden = false;
    } else if (unl > 0) {
      achDisplay.textContent = `🏆 ${unl} unlocked`;
      achTile.hidden = false;
    } else {
      achDisplay.textContent = "—";
      achTile.hidden = false;
    }

    if (achPills) {
      achPills.innerHTML = "";
      if (game.is_reconciled && Array.isArray(game.linked_games) && game.linked_games.length > 1) {
        for (const lg of game.linked_games) {
          const pill = document.createElement("span");
          pill.className = "edit-ach-pill";
          pill.textContent = `${shortPlatformLabel(lg.platform)}: ${lg.achievements_unlocked || 0}/${lg.achievements_total || 0}`;
          achPills.appendChild(pill);
        }
      }
    }
  }

  const editAchUnlInput = document.getElementById("edit-ach-unlocked");
  const editAchTotInput = document.getElementById("edit-ach-total");
  const editAchWrap = document.getElementById("edit-ach-edit-wrap");
  const toggleAchBtn = document.getElementById("btn-toggle-edit-ach");
  if (editAchUnlInput) editAchUnlInput.value = game.achievements_unlocked ?? 0;
  if (editAchTotInput) editAchTotInput.value = game.achievements_total ?? 0;
  if (editAchWrap) editAchWrap.hidden = true;
  if (achDisplay) achDisplay.hidden = false;
  if (toggleAchBtn) toggleAchBtn.textContent = "Edit";

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
  const editTagsEl = document.getElementById("edit-tags");
  if (editTagsEl) {
    editTagsEl.value = (game.tags || []).join(", ");
    renderEditTagChips();
  }
  document.getElementById("edit-cover").value = game.cover_url || "";
  document.getElementById("edit-notes").value = game.notes || "";
  syncEditFavButton(game.favorite);
  renderEditLinkedCopies(game);
  loadAndRenderGameAchievements(game.id, false);
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

function enableDragToScroll(el) {
  if (!el) return;
  let isDown = false;
  let startX = 0;
  let scrollLeft = 0;
  let isDragging = false;

  el.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    isDown = true;
    isDragging = false;
    startX = e.pageX - el.offsetLeft;
    scrollLeft = el.scrollLeft;
  });

  window.addEventListener("mouseup", () => {
    isDown = false;
    if (isDragging) {
      setTimeout(() => {
        isDragging = false;
      }, 50);
    }
  });

  el.addEventListener("mousemove", (e) => {
    if (!isDown) return;
    const x = e.pageX - el.offsetLeft;
    const walk = x - startX;
    if (Math.abs(walk) > 4) {
      isDragging = true;
      el.scrollLeft = scrollLeft - walk;
    }
  });

  el.addEventListener(
    "click",
    (e) => {
      if (isDragging) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    true
  );
}

document.addEventListener("DOMContentLoaded", () => {
  applyViewMode(state.viewMode);
  fetchStats();
  switchMainTab(state.activeTab, false);

  // Main View Switcher
  const tabLibraryBtn = document.getElementById("tab-library");
  const tabHabitsBtn = document.getElementById("tab-habits");
  const mnavLibraryBtn = document.getElementById("mnav-library");
  const mnavHabitsBtn = document.getElementById("mnav-habits");

  if (tabLibraryBtn) tabLibraryBtn.addEventListener("click", () => switchMainTab("library"));
  if (tabHabitsBtn) tabHabitsBtn.addEventListener("click", () => switchMainTab("habits"));
  if (mnavLibraryBtn) mnavLibraryBtn.addEventListener("click", () => switchMainTab("library"));
  if (mnavHabitsBtn) mnavHabitsBtn.addEventListener("click", () => switchMainTab("habits"));

  // Enable drag-to-scroll on mobile-scrollable strips
  enableDragToScroll(document.getElementById("platform-pills"));
  enableDragToScroll(document.getElementById("metrics-bar"));

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
  const installTitle = document.getElementById("pwa-install-title");
  const installSubtext = document.getElementById("pwa-install-subtext");
  const isStandalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  const installDismissed = localStorage.getItem("vertex_pwa_dismissed") === "1";

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (!isStandalone && !installDismissed && installBanner) {
      if (installTitle) installTitle.textContent = "Install Vertex App";
      if (installSubtext) {
        installSubtext.textContent = "Add to your home screen for quick full-screen library access.";
      }
      if (installBtn) installBtn.hidden = false;
      installBanner.hidden = false;
    }
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    if (installBanner) installBanner.hidden = true;
  });

  // Detect iOS Safari (non-standalone) or Android plain-HTTP (insecure context) to show helpful hint
  const ua = window.navigator.userAgent || "";
  const isIOS = /iPhone|iPad|iPod/i.test(ua);
  const isAndroid = /Android/i.test(ua);
  if (isIOS && !isStandalone && !installDismissed && installBanner && installSubtext && installBtn) {
    installSubtext.textContent = "Tap Share ⎋ in Safari, then 'Add to Home Screen' for the full-screen app.";
    installBtn.hidden = true;
    installBanner.hidden = false;
  } else if (
    isAndroid &&
    !window.isSecureContext &&
    !isStandalone &&
    !installDismissed &&
    installBanner &&
    installSubtext &&
    installBtn
  ) {
    if (installTitle) installTitle.textContent = "HTTPS Required for Full App Install";
    installSubtext.textContent =
      "Open https://vertex.crjacobs.cc in Chrome to install Vertex as a standalone Android app.";
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
      refreshActiveView();
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
      state.tag = "";
      state.search = "";
      state.sortBy = "playtime_desc";
      state.favoriteOnly = false;

      document.getElementById("search-input").value = "";
      document.getElementById("status-filter").value = "";
      document.getElementById("genre-filter").value = "";
      const tf = document.getElementById("tag-filter");
      if (tf) tf.value = "";
      document.getElementById("sort-select").value = "playtime_desc";
      document.getElementById("btn-fav-toggle").setAttribute("aria-pressed", "false");
      for (const p of document.querySelectorAll("#platform-pills .pill")) {
        p.classList.toggle("active", p.dataset.platform === "");
      }
      document.querySelector('#platform-pills .pill[data-platform=""]')?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      refreshActiveView();
    });
  }

  // Search input
  let searchTimer = null;
  document.getElementById("search-input").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      refreshActiveView();
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
    btn.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    state.platform = btn.dataset.platform || "";
    refreshActiveView();
  });

  // Status, Genre & Sort selects
  const genreFilterEl = document.getElementById("genre-filter");
  if (genreFilterEl) {
    genreFilterEl.addEventListener("change", (e) => {
      state.genre = e.target.value;
      refreshActiveView();
    });
  }

  const tagFilterEl = document.getElementById("tag-filter");
  if (tagFilterEl) {
    tagFilterEl.addEventListener("change", (e) => {
      state.tag = e.target.value;
      refreshActiveView();
    });
  }

  document.getElementById("status-filter").addEventListener("change", (e) => {
    state.status = e.target.value;
    refreshActiveView();
  });

  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    refreshActiveView();
  });

  // Favorites toggle
  const favBtn = document.getElementById("btn-fav-toggle");
  favBtn.addEventListener("click", () => {
    state.favoriteOnly = !state.favoriteOnly;
    favBtn.setAttribute("aria-pressed", String(state.favoriteOnly));
    refreshActiveView();
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
    const tagsRaw = document.getElementById("add-tags")?.value || "";
    const tags = tagsRaw
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
      tags,
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

  const editTagPicker = document.getElementById("edit-tag-picker");
  if (editTagPicker) {
    editTagPicker.addEventListener("change", (e) => {
      const chosen = e.target.value;
      if (!chosen) return;
      const inputEl = document.getElementById("edit-tags");
      const current = inputEl.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (!current.some((t) => t.toLowerCase() === chosen.toLowerCase())) {
        current.push(chosen);
        inputEl.value = current.join(", ");
      }
      renderEditTagChips();
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

  const toggleAchBtn = document.getElementById("btn-toggle-edit-ach");
  if (toggleAchBtn) {
    toggleAchBtn.addEventListener("click", () => {
      const achWrap = document.getElementById("edit-ach-edit-wrap");
      const achDisplay = document.getElementById("edit-ach-display");
      const isEditing = achWrap && !achWrap.hidden;
      if (!isEditing) {
        if (achWrap) achWrap.hidden = false;
        if (achDisplay) achDisplay.hidden = true;
        toggleAchBtn.textContent = "Cancel";
        const unlInput = document.getElementById("edit-ach-unlocked");
        if (unlInput) {
          unlInput.focus();
          unlInput.select();
        }
      } else {
        if (achWrap) achWrap.hidden = true;
        if (achDisplay) achDisplay.hidden = false;
        toggleAchBtn.textContent = "Edit";
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
    const tagsRaw = document.getElementById("edit-tags")?.value || "";
    const tags = tagsRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const ratingVal = document.getElementById("edit-rating").value;
    const hoursInput = document.getElementById("edit-hours");

    const payload = {
      completion_status: document.getElementById("edit-status").value,
      user_rating: ratingVal ? parseInt(ratingVal, 10) : null,
      genres,
      tags,
      cover_url: document.getElementById("edit-cover").value.trim() || null,
      notes: document.getElementById("edit-notes").value.trim(),
      favorite: document.getElementById("edit-favorite").checked,
    };

    if (hoursInput && hoursInput.dataset.unlocked === "true") {
      payload.playtime_hours = parseFloat(hoursInput.value || "0");
    }

    const achWrap = document.getElementById("edit-ach-edit-wrap");
    const achUnlInput = document.getElementById("edit-ach-unlocked");
    const achTotInput = document.getElementById("edit-ach-total");
    if (achWrap && !achWrap.hidden && achUnlInput && achTotInput) {
      const u = parseInt(achUnlInput.value, 10);
      const t = parseInt(achTotInput.value, 10);
      if (!Number.isNaN(u)) payload.achievements_unlocked = Math.max(0, u);
      if (!Number.isNaN(t)) payload.achievements_total = Math.max(0, t);
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

  // Achievements Explorer Listeners inside Edit Game Modal
  const refreshGameAchBtn = document.getElementById("btn-refresh-game-ach");
  if (refreshGameAchBtn) {
    refreshGameAchBtn.addEventListener("click", () => {
      const idVal = parseInt(document.getElementById("edit-id").value, 10);
      if (idVal) {
        loadAndRenderGameAchievements(idVal, true);
      }
    });
  }

  document.querySelectorAll(".ach-pill[data-ach-filter]").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".ach-pill[data-ach-filter]").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      state.currentAchFilter = pill.dataset.achFilter;
      renderAchievementsList();
    });
  });

  const achSearchInput = document.getElementById("ach-search-input");
  let achSearchTimer = null;
  if (achSearchInput) {
    achSearchInput.addEventListener("input", (e) => {
      clearTimeout(achSearchTimer);
      achSearchTimer = setTimeout(() => {
        state.currentAchSearch = (e.target.value || "").trim().toLowerCase();
        renderAchievementsList();
      }, 150);
    });
  }

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
