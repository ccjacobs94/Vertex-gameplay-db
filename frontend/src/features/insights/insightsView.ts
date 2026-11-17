import type { GameResponse, LibraryAnalyticsResponse, SessionAnalyticsResponse } from "../../shared/api-types.ts";
import { getLibraryAnalytics, getSessionAnalytics, listGames } from "../../shared/api.ts";
import { clearEl, el, showBanner } from "../../shared/dom.ts";
import { createCoverElement, createPlatformBrandIcons } from "../../shared/media.ts";

function kpiCard(label: string, val: string): HTMLElement {
  return el("div", { class: "stat-card" }, [
    el("span", { class: "stat-val" }, [val]),
    el("span", { class: "stat-lbl" }, [label]),
  ]);
}

function asRec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function meterBar(label: string, rightText: string, pct: number): HTMLElement {
  const safePct = Math.min(100, Math.max(pct > 0 ? 3 : 0, pct));
  return el("div", { class: "meter-row" }, [
    el("div", { class: "row-between" }, [
      el("span", {}, [label]),
      el("span", { class: "muted" }, [rightText]),
    ]),
    el("div", { class: "meter-track" }, [
      el("div", { class: "meter-fill", style: `width:${safePct}%` }),
    ]),
  ]);
}

export async function renderInsightsView(container: HTMLElement): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const kpiBox = el("div", { class: "stats-grid" });
  const breakdownBox = el("div", { class: "stack" });
  const habitsBox = el("div", { class: "stack" });
  const coverByTitle = new Map<string, string>();

  const platIn = el("input", { type: "text", placeholder: "Filter platform (optional)", "aria-label": "Platform filter" }) as HTMLInputElement;
  const daysSel = el("select", { "aria-label": "Session window" }, [
    el("option", { value: "30" }, ["Last 30 days"]),
    el("option", { value: "90" }, ["Last 90 days"]),
    el("option", { value: "365", selected: "selected" }, ["Last 365 days"]),
  ]) as HTMLSelectElement;

  function drawAll(lib: LibraryAnalyticsResponse, sess: SessionAnalyticsResponse): void {
    clearEl(kpiBox);
    clearEl(breakdownBox);
    clearEl(habitsBox);

    const s = lib.summary;
    const kpis = asRec(sess.kpis);
    const streaks = asRec(kpis.streaks);
    const sweet = asRec(kpis.sweet_spot);
    const focus = asRec(kpis.focus_30d);
    const afk = asRec(kpis.afk_menu_tax);

    kpiBox.appendChild(kpiCard("Unique Titles", String(s.unique_titles)));
    kpiBox.appendChild(kpiCard("Total Hours", s.total_hours.toFixed(1)));
    kpiBox.appendChild(kpiCard("Completion Rate", `${s.completion_rate.toFixed(1)}%`));
    kpiBox.appendChild(kpiCard("Achievements", `${s.achievement_rate.toFixed(1)}%`));
    kpiBox.appendChild(kpiCard("Current Streak", `${Number(streaks.current_streak_days || 0)}d`));
    kpiBox.appendChild(kpiCard("Longest Streak", `${Number(streaks.longest_streak_days || 0)}d`));
    kpiBox.appendChild(kpiCard("Median Session", `${Number(sweet.median_session_minutes || 0)}m`));
    kpiBox.appendChild(kpiCard("30d Focus Score", `${Number(focus.focus_score_pct || 0)}%`));

    // Platform table with brand icons
    const platBody = el("tbody");
    for (const p of lib.platforms) {
      platBody.appendChild(el("tr", {}, [
        el("td", {}, [
          el("span", { class: "card-plat-indicator" }, [
            createPlatformBrandIcons(p.name),
            p.name,
          ]),
        ]),
        el("td", {}, [String(p.count)]),
        el("td", {}, [p.hours.toFixed(1)]),
        el("td", {}, [`${p.playtime_pct.toFixed(1)}%`]),
        el("td", {}, [`${p.completion_rate.toFixed(1)}%`]),
        el("td", {}, [p.top_game_title || "-"]),
      ]));
    }
    breakdownBox.appendChild(el("div", { class: "panel stack" }, [
      el("h2", { style: "margin:0" }, ["Platform Breakdown"]),
      lib.platforms.length
        ? el("div", { class: "table-scroll" }, [
          el("table", { class: "data-table" }, [
            el("caption", { class: "sr-only" }, ["Library Platform Metrics"]),
            el("thead", {}, [
              el("tr", {}, [
                el("th", { scope: "col" }, ["Platform"]),
                el("th", { scope: "col" }, ["Games"]),
                el("th", { scope: "col" }, ["Hours"]),
                el("th", { scope: "col" }, ["Playtime %"]),
                el("th", { scope: "col" }, ["Completion %"]),
                el("th", { scope: "col" }, ["Top Title"]),
              ]),
            ]),
            platBody,
          ]),
        ])
        : el("p", { class: "muted" }, ["No platform metrics recorded yet."]),
    ]));

    // Top genres & tags with visual meter bars
    const genreMeters = el("div", { class: "meter-list" });
    const maxGenreHours = Math.max(1, ...lib.genres.slice(0, 8).map((g) => g.hours));
    for (const g of lib.genres.slice(0, 8)) {
      genreMeters.appendChild(meterBar(
        g.genre,
        `${g.count} titles • ${g.hours.toFixed(1)}h (${g.completion_rate.toFixed(0)}% beaten)`,
        (g.hours / maxGenreHours) * 100
      ));
    }
    const tagMeters = el("div", { class: "meter-list" });
    const maxTagHours = Math.max(1, ...lib.tags.slice(0, 8).map((t) => t.hours));
    for (const t of lib.tags.slice(0, 8)) {
      tagMeters.appendChild(meterBar(
        t.tag,
        `${t.count} titles • ${t.hours.toFixed(1)}h`,
        (t.hours / maxTagHours) * 100
      ));
    }
    breakdownBox.appendChild(el("div", { class: "two-col" }, [
      el("div", { class: "panel stack" }, [
        el("h2", { style: "margin:0" }, ["Top Genres"]),
        lib.genres.length ? genreMeters : el("p", { class: "muted" }, ["No genres assigned yet."]),
      ]),
      el("div", { class: "panel stack" }, [
        el("h2", { style: "margin:0" }, ["Top Tags"]),
        lib.tags.length ? tagMeters : el("p", { class: "muted" }, ["No tags assigned yet."]),
      ]),
    ]));

    // Snack vs Binge & Circadian Rhythm
    const svb = asRec(sess.snack_vs_binge);
    const svbKeys = ["quick_checkins", "standard_runs", "deep_sessions", "binge_marathons"];
    const svbMeters = el("div", { class: "meter-list" });
    for (const key of svbKeys) {
      const bucket = asRec(svb[key]);
      if (bucket.label) {
        svbMeters.appendChild(meterBar(
          String(bucket.label),
          `${Number(bucket.count || 0)} sessions (${Number(bucket.hours || 0)}h, ${Number(bucket.pct || 0)}%)`,
          Number(bucket.pct || 0)
        ));
      }
    }
    const longest = asRec(svb.longest_marathon);
    const clock = asRec(sess.circadian_clock);
    const cr = lib.circadian_rhythm;
    const subRoi = asRec(lib.subscription_roi);
    const subInfo = asRec(subRoi.subscription);
    const xp = asRec(lib.cross_platform);

    habitsBox.appendChild(el("div", { class: "two-col" }, [
      el("div", { class: "panel stack" }, [
        el("h2", { style: "margin:0" }, ["Session Cadence & Snack vs. Binge"]),
        el("p", { class: "muted", style: "margin:0" }, [`Active sessions in window: ${sess.total_sessions} | Idle ratio: ${Number(afk.idle_ratio_pct || 0)}% | Focus: ${String(focus.archetype_label || "N/A")}`]),
        svbMeters,
        longest.title ? el("p", { style: "margin:0" }, [`Longest marathon: ${String(longest.title)} (${Number(longest.hours || 0)}h)`]) : null,
      ]),
      el("div", { class: "panel stack" }, [
        el("h2", { style: "margin:0" }, ["Circadian Rhythm & Cross-Platform ROI"]),
        el("p", { style: "margin:0" }, [`Library rhythm: ${cr.archetype} (Peak ${cr.peak_day} @ ${cr.peak_hour}, ${cr.eras_count} tz eras)`]),
        el("p", { class: "muted", style: "margin:0" }, [`Session clock: ${String(clock.archetype || "No clock data")} (Peak ${String(clock.peak_hour || "-")})`]),
        el("p", { style: "margin:0" }, [`Double-dipped titles: ${Number(xp.double_dipped_count || 0)} (${Number(xp.double_dipped_hours || 0)}h)`]),
        el("p", { style: "margin:0" }, [`Subscription titles: ${Number(subInfo.count || 0)} (${Number(subInfo.hours || 0)}h, tourist rate ${Number(subInfo.tourist_rate || 0)}%)`]),
      ]),
    ]));

    // Hall of Fame & Backlog Gems poster grids
    const hofGrid = el("div", { class: "hof-grid" });
    const hofItems = lib.hall_of_fame.slice(0, 6);
    for (let i = 0; i < hofItems.length; i += 1) {
      const item = hofItems[i];
      const title = String(item.title || "");
      const plat = String(item.platform || "");
      const hofHours = Number(item.playtime_hours ?? item.hours ?? 0);
      const cov = (typeof item.cover_url === "string" && item.cover_url) ? item.cover_url : coverByTitle.get(title.toLowerCase());
      const poster = createCoverElement(title, plat, cov, i + 4);
      if (item.user_rating) {
        poster.appendChild(el("span", { class: "rating-badge" }, [`★ ${String(item.user_rating)}/10`]));
      }
      hofGrid.appendChild(el("article", { class: "hof-card" }, [
        poster,
        el("div", { class: "hof-card-body" }, [
          el("strong", { class: "card-title", title }, [title]),
          el("span", { class: "muted" }, [`${plat} • ${hofHours}h`]),
        ]),
      ]));
    }

    const gemsGrid = el("div", { class: "hof-grid" });
    const gemItems = lib.backlog_gems.slice(0, 6);
    for (let i = 0; i < gemItems.length; i += 1) {
      const item = gemItems[i];
      const title = String(item.title || "");
      const plat = String(item.platform || "Backlog");
      const cov = (typeof item.cover_url === "string" && item.cover_url) ? item.cover_url : coverByTitle.get(title.toLowerCase());
      const poster = createCoverElement(title, plat, cov, i + 10);
      if (item.critic_score) {
        poster.appendChild(el("span", { class: "rating-badge" }, [`Meta ${String(item.critic_score)}`]));
      }
      gemsGrid.appendChild(el("article", { class: "hof-card" }, [
        poster,
        el("div", { class: "hof-card-body" }, [
          el("strong", { class: "card-title", title }, [title]),
          el("span", { class: "muted" }, [item.critic_score ? `Critic ${String(item.critic_score)}` : plat]),
        ]),
      ]));
    }

    habitsBox.appendChild(el("div", { class: "two-col" }, [
      el("div", { class: "panel stack" }, [
        el("h2", { style: "margin:0" }, ["Hall of Fame"]),
        lib.hall_of_fame.length ? hofGrid : el("p", { class: "muted" }, ["No favorites or top-rated titles yet."]),
      ]),
      el("div", { class: "panel stack" }, [
        el("h2", { style: "margin:0" }, ["Backlog Gems"]),
        lib.backlog_gems.length ? gemsGrid : el("p", { class: "muted" }, ["No unplayed critic gems flagged yet."]),
      ]),
    ]));
  }

  async function refresh(): Promise<void> {
    try {
      const p = platIn.value.trim();
      const days = Number(daysSel.value || 365);
      const [lib, sess, games] = await Promise.all([
        getLibraryAnalytics("", p, ""),
        getSessionAnalytics(days, 50, 0, p),
        listGames("", "", "").catch(() => [] as Array<GameResponse>),
      ]);
      coverByTitle.clear();
      for (const g of games) {
        if (g.cover_url && !coverByTitle.has(g.title.toLowerCase())) {
          coverByTitle.set(g.title.toLowerCase(), g.cover_url);
        }
      }
      drawAll(lib, sess);
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading insights", "error");
    }
  }

  const controls = el("div", { class: "toolbar" }, [
    platIn,
    daysSel,
    el("button", { type: "button", class: "btn btn-secondary", onClick: () => refresh() }, ["Apply"]),
  ]);

  container.appendChild(el("section", { class: "stack" }, [
    el("h1", { class: "catalog-title" }, ["Insights & Analytics"]),
    bannerBox,
    controls,
    kpiBox,
    breakdownBox,
    habitsBox,
  ]));

  await refresh();
}
