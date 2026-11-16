"""Read-only library and session analytics service."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from vertex.activity.service import _parse_iso, _platform_family, _resolve_zoneinfo
from vertex.analytics.models import (
    AnalyticsSummary,
    BucketAnalyticsItem,
    CircadianRhythmAnalytics,
    GenreAnalyticsItem,
    LibraryAnalytics,
    PlatformAnalyticsItem,
    SessionAnalytics,
    SessionWindowStats,
    StatusAnalyticsItem,
    TagAnalyticsItem,
)
from vertex.analytics.repository import fetch_library_snapshot_rows
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.models import GameRecord, LibraryStats
from vertex.library.repository import row_to_game

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _format_hour_12(hour_idx: int) -> str:
    ampm = "AM" if hour_idx < 12 else "PM"
    h12 = 12 if hour_idx % 12 == 0 else hour_idx % 12
    return f"{h12} {ampm}"


def _resolve_base_tz_name(stored_tz: str, client_timezone: str | None) -> str:
    cleaned_stored = (stored_tz or "").strip()
    if cleaned_stored and cleaned_stored.lower() != "auto":
        return cleaned_stored
    if client_timezone:
        cand = client_timezone.strip()
        if cand and cand.lower() != "auto":
            try:
                ZoneInfo(cand)
                return cand
            except Exception:
                pass
    return "UTC"


def compute_summary_stats(games: list[GameRecord]) -> LibraryStats:
    platforms: dict[str, int] = {}
    total_minutes = 0
    for g in games:
        platforms[g.platform] = platforms.get(g.platform, 0) + 1
        total_minutes += g.playtime_minutes

    groups: dict[str, list[GameRecord]] = {}
    for g in games:
        key = f"canon:{g.canonical_id}" if g.canonical_id is not None else f"id:{g.id}"
        groups.setdefault(key, []).append(g)

    playing = 0
    completed = 0
    not_played = 0
    favorites = 0
    for grp in groups.values():
        statuses = {g.completion_status for g in grp}
        if "Playing" in statuses:
            playing += 1
        elif statuses & {"Beaten", "Completed"}:
            completed += 1
        elif statuses & {"Not Played", "Unplayed", "Plan to Play"}:
            not_played += 1
        if any(g.favorite for g in grp):
            favorites += 1

    return LibraryStats(
        total_entries=len(games),
        unique_titles=len(groups),
        playing=playing,
        completed=completed,
        not_played=not_played,
        favorites=favorites,
        total_playtime_hours=round(total_minutes / 60.0, 2),
        platforms=dict(sorted(platforms.items())),
    )


class AnalyticsService:
    """Computes strictly read-only summary, habits, and session analytics from authoritative tables."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    def _get_timezone_settings(
        self,
        scope: UserScope,
        client_timezone: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        with self.db_manager.system_connection() as conn:
            row = conn.execute(
                "SELECT timezone, timezone_eras_json FROM users WHERE id = ?",
                (scope.user_id,),
            ).fetchone()
        if row is None:
            return _resolve_base_tz_name("auto", client_timezone), []
        eras = json.loads(row["timezone_eras_json"] or "[]")
        return _resolve_base_tz_name(row["timezone"] or "auto", client_timezone), eras

    def get_summary_stats(self, scope: UserScope) -> LibraryStats:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute("SELECT * FROM games ORDER BY id ASC").fetchall()
        return compute_summary_stats([row_to_game(r) for r in rows])

    def get_library_analytics(
        self,
        scope: UserScope,
        *,
        search: str = "",
        platform: str = "",
        status: str = "",
        genre: str = "",
        tag: str = "",
        ownership: str = "",
        is_game_pass: bool | None = None,
        is_play_anywhere: bool | None = None,
        favorite_only: bool = False,
        client_timezone: str | None = None,
    ) -> LibraryAnalytics:
        scope.require_active()
        base_tz_name, eras = self._get_timezone_settings(scope, client_timezone)

        with self.db_manager.library_connection(scope) as conn:
            snap = fetch_library_snapshot_rows(conn)

        all_games = [row_to_game(r) for r in snap["games"]]

        # Filter games without altering stored records.
        filtered: list[GameRecord] = []
        s_q = search.strip().lower()
        p_q = platform.strip().lower()
        st_q = status.strip()
        g_q = genre.strip().lower()
        t_q = tag.strip().lower()
        own_q = ownership.strip().lower()

        for g in all_games:
            if s_q and s_q not in g.title.lower():
                continue
            if p_q and p_q not in g.platform.lower() and _platform_family(g.platform).lower() != p_q:
                continue
            if st_q and g.completion_status != st_q:
                continue
            if g_q and not any(x.lower() == g_q for x in g.genres):
                continue
            if t_q and not any(x.lower() == t_q for x in g.tags):
                continue
            if own_q and g.ownership_status.lower() != own_q:
                continue
            if is_game_pass is not None and g.is_game_pass != is_game_pass:
                continue
            if is_play_anywhere is not None and g.is_play_anywhere != is_play_anywhere:
                continue
            if favorite_only and not g.favorite:
                continue
            filtered.append(g)

        # Group reconciled copies for unique-title summaries while keeping copy-level platform metrics.
        groups_map: dict[str, list[GameRecord]] = {}
        for g in filtered:
            key = f"canon:{g.canonical_id}" if g.canonical_id is not None else f"id:{g.id}"
            groups_map.setdefault(key, []).append(g)

        def _group_status(grp: list[GameRecord]) -> str:
            sts = [x.completion_status for x in grp]
            for priority in ("Completed", "Beaten", "Playing", "Plan to Play", "Dropped", "Not Played", "Unplayed"):
                if priority in sts:
                    return priority
            return sts[0]

        consolidated: list[dict[str, Any]] = []
        for grp in groups_map.values():
            primary = max(grp, key=lambda x: (x.playtime_minutes, int(x.favorite), -x.id))
            total_mins = sum(x.playtime_minutes for x in grp)
            hours = round(total_mins / 60.0, 2)
            merged_genres: list[str] = []
            merged_tags: list[str] = []
            for m in grp:
                for x in m.genres:
                    if x not in merged_genres:
                        merged_genres.append(x)
                for x in m.tags:
                    if x not in merged_tags:
                        merged_tags.append(x)
            ratings = [x.user_rating for x in grp if x.user_rating is not None]
            critics = [x.critic_score for x in grp if x.critic_score is not None]
            years = [x.release_year for x in grp if x.release_year is not None]
            consolidated.append(
                {
                    "id": primary.id,
                    "title": primary.title,
                    "platform": " + ".join(dict.fromkeys(m.platform for m in grp)),
                    "hours": hours,
                    "status": _group_status(grp),
                    "ownership_status": primary.ownership_status,
                    "is_game_pass": any(m.is_game_pass for m in grp),
                    "favorite": any(m.favorite for m in grp),
                    "genres": merged_genres,
                    "tags": merged_tags,
                    "user_rating": max(ratings) if ratings else None,
                    "critic_score": max(critics) if critics else None,
                    "release_year": min(years) if years else None,
                    "cover_url": next((m.cover_url for m in grp if m.cover_url), None),
                    "ach_unlocked": sum(m.achievements_unlocked for m in grp),
                    "ach_total": sum(m.achievements_total for m in grp),
                    "copies": grp,
                }
            )

        unique_titles = len(consolidated)
        total_entries = len(filtered)
        total_hours = round(sum(c["hours"] for c in consolidated), 1)
        played = [c for c in consolidated if c["hours"] > 0]
        beaten = [c for c in consolidated if c["status"] in ("Beaten", "Completed")]
        dropped = [c for c in consolidated if c["status"] == "Dropped"]
        playing = [c for c in consolidated if c["status"] == "Playing"]
        shelf = [c for c in consolidated if c["status"] in ("Not Played", "Unplayed", "Plan to Play")]
        favs = [c for c in consolidated if c["favorite"]]

        played_cnt = len(played)
        beaten_cnt = len(beaten)
        dropped_cnt = len(dropped)
        completion_rate = round((beaten_cnt / max(played_cnt, 1)) * 100, 1) if played_cnt else 0.0
        drop_rate = round((dropped_cnt / max(played_cnt, 1)) * 100, 1) if played_cnt else 0.0
        avg_hours = round(total_hours / max(unique_titles, 1), 1) if unique_titles else 0.0
        avg_played_hours = round(total_hours / max(played_cnt, 1), 1) if played_cnt else 0.0
        avg_beaten_hours = round(sum(c["hours"] for c in beaten) / max(beaten_cnt, 1), 1) if beaten_cnt else 0.0
        avg_dropped_hours = round(sum(c["hours"] for c in dropped) / max(dropped_cnt, 1), 1) if dropped_cnt else 0.0

        # Platform breakdown
        plat_acc: dict[str, dict[str, Any]] = {}
        for c in consolidated:
            seen_fams: set[str] = set()
            for copy in c["copies"]:
                fam = _platform_family(copy.platform)
                st = plat_acc.setdefault(
                    fam,
                    {"hours": 0.0, "count": 0, "beaten": 0, "top_title": None, "top_hours": -1.0},
                )
                c_hrs = copy.playtime_minutes / 60.0
                st["hours"] = float(st["hours"]) + c_hrs
                if fam not in seen_fams:
                    seen_fams.add(fam)
                    st["count"] += 1
                    if c["status"] in ("Beaten", "Completed"):
                        st["beaten"] += 1
                if c_hrs > st["top_hours"]:
                    st["top_hours"] = c_hrs
                    st["top_title"] = c["title"]

        platforms_out: list[PlatformAnalyticsItem] = []
        for fam, st in sorted(plat_acc.items(), key=lambda kv: kv[1]["hours"], reverse=True):
            fam_hours = round(float(st["hours"]), 1)
            platforms_out.append(
                PlatformAnalyticsItem(
                    name=fam,
                    count=st["count"],
                    hours=fam_hours,
                    beaten_count=st["beaten"],
                    playtime_pct=round((fam_hours / max(total_hours, 0.1)) * 100, 1) if total_hours else 0.0,
                    library_pct=round((st["count"] / max(unique_titles, 1)) * 100, 1) if unique_titles else 0.0,
                    completion_rate=round((st["beaten"] / max(st["count"], 1)) * 100, 1),
                    top_game_title=st["top_title"],
                    top_game_hours=max(0.0, round(st["top_hours"], 1)),
                )
            )

        # Genre breakdown
        genre_acc: dict[str, dict[str, int | float]] = {}
        for c in consolidated:
            g_hrs = c["hours"]
            st_name = c["status"]
            for gen in c["genres"]:
                gd = genre_acc.setdefault(
                    gen,
                    {"hours": 0.0, "count": 0, "beaten": 0, "playing": 0, "backlog": 0, "dropped": 0},
                )
                gd["hours"] = float(gd["hours"]) + g_hrs
                gd["count"] = int(gd["count"]) + 1
                if st_name in ("Beaten", "Completed"):
                    gd["beaten"] = int(gd["beaten"]) + 1
                elif st_name == "Playing":
                    gd["playing"] = int(gd["playing"]) + 1
                elif st_name == "Dropped":
                    gd["dropped"] = int(gd["dropped"]) + 1
                else:
                    gd["backlog"] = int(gd["backlog"]) + 1

        genres_out = [
            GenreAnalyticsItem(
                genre=gen,
                count=int(gd["count"]),
                hours=round(float(gd["hours"]), 1),
                beaten_count=int(gd["beaten"]),
                playing_count=int(gd["playing"]),
                backlog_count=int(gd["backlog"]),
                dropped_count=int(gd["dropped"]),
                playtime_pct=round((round(float(gd["hours"]), 1) / max(total_hours, 0.1)) * 100, 1)
                if total_hours
                else 0.0,
                completion_rate=round((int(gd["beaten"]) / max(int(gd["count"]), 1)) * 100, 1),
            )
            for gen, gd in sorted(genre_acc.items(), key=lambda kv: kv[1]["hours"], reverse=True)
        ]

        # Tag / mechanic DNA breakdown
        tag_acc: dict[str, dict[str, int | float]] = {}
        for c in consolidated:
            for t in c["tags"]:
                td = tag_acc.setdefault(t, {"count": 0, "hours": 0.0, "beaten": 0, "dropped": 0})
                td["count"] = int(td["count"]) + 1
                td["hours"] = float(td["hours"]) + c["hours"]
                if c["status"] in ("Beaten", "Completed"):
                    td["beaten"] = int(td["beaten"]) + 1
                elif c["status"] == "Dropped":
                    td["dropped"] = int(td["dropped"]) + 1

        tags_out = [
            TagAnalyticsItem(
                tag=t,
                count=int(td["count"]),
                hours=round(float(td["hours"]), 1),
                dropped_count=int(td["dropped"]),
                drop_rate=min(100.0, round((int(td["dropped"]) / max(int(td["count"]), 1)) * 100, 1)),
                completion_rate=min(100.0, round((int(td["beaten"]) / max(int(td["count"]), 1)) * 100, 1)),
            )
            for t, td in sorted(tag_acc.items(), key=lambda kv: (kv[1]["count"], kv[1]["hours"]), reverse=True)
        ]

        # Status breakdown
        statuses_out: dict[str, StatusAnalyticsItem] = {}
        for st_name in ("Completed", "Beaten", "Playing", "Plan to Play", "Not Played", "Unplayed", "Dropped"):
            matching = [c for c in consolidated if c["status"] == st_name]
            st_hours = round(sum(c["hours"] for c in matching), 1)
            statuses_out[st_name] = StatusAnalyticsItem(
                name=st_name,
                count=len(matching),
                count_pct=round((len(matching) / max(unique_titles, 1)) * 100, 1) if unique_titles else 0.0,
                hours=st_hours,
                hours_pct=round((st_hours / max(total_hours, 0.1)) * 100, 1) if total_hours else 0.0,
            )

        # Commitment tiers
        tier_defs = [
            ("bite", "Bite-Sized (< 5 hrs)", 0.0, 5.0),
            ("medium", "Medium (5-20 hrs)", 5.0, 20.0),
            ("deep", "Deep Dives (20-50 hrs)", 20.0, 50.0),
            ("marathon", "Marathons (50+ hrs)", 50.0, 10_000_000.0),
        ]
        tier_acc = {k: {"label": lbl, "count": 0, "hours": 0.0, "min": lo, "max": hi} for k, lbl, lo, hi in tier_defs}
        for c in consolidated:
            h = c["hours"]
            for k, td in tier_acc.items():
                if td["min"] <= h < td["max"]:
                    td["count"] += 1
                    td["hours"] = round(td["hours"] + h, 1)
                    break
        tiers_out = [
            BucketAnalyticsItem(
                key=k,
                label=str(td["label"]),
                count=int(td["count"]),
                count_pct=round((int(td["count"]) / max(unique_titles, 1)) * 100, 1) if unique_titles else 0.0,
                hours=float(td["hours"]),
                hours_pct=round((float(td["hours"]) / max(total_hours, 0.1)) * 100, 1) if total_hours else 0.0,
            )
            for k, td in tier_acc.items()
        ]

        # Eras
        era_acc: dict[str, dict[str, Any]] = {
            "modern": {"label": "Modern (2020+)", "count": 0, "hours": 0.0},
            "golden": {"label": "2010s (2010-2019)", "count": 0, "hours": 0.0},
            "classics": {"label": "2000s (2000-2009)", "count": 0, "hours": 0.0},
            "retro": {"label": "Retro (Pre-2000)", "count": 0, "hours": 0.0},
            "unknown": {"label": "Unknown Era", "count": 0, "hours": 0.0},
        }
        for c in consolidated:
            yr = c["release_year"]
            if not yr:
                ek = "unknown"
            elif yr >= 2020:
                ek = "modern"
            elif yr >= 2010:
                ek = "golden"
            elif yr >= 2000:
                ek = "classics"
            else:
                ek = "retro"
            era_acc[ek]["count"] += 1
            era_acc[ek]["hours"] = round(era_acc[ek]["hours"] + c["hours"], 1)
        eras_out = [
            BucketAnalyticsItem(
                key=k,
                label=ed["label"],
                count=ed["count"],
                count_pct=round((ed["count"] / max(unique_titles, 1)) * 100, 1) if unique_titles else 0.0,
                hours=ed["hours"],
                hours_pct=round((ed["hours"] / max(total_hours, 0.1)) * 100, 1) if total_hours else 0.0,
            )
            for k, ed in era_acc.items()
        ]

        # Ratings & gems
        user_ratings = [c["user_rating"] for c in consolidated if c["user_rating"] is not None]
        critic_scores = [c["critic_score"] for c in consolidated if c["critic_score"] is not None]
        avg_user_rating = round(sum(user_ratings) / len(user_ratings), 1) if user_ratings else None
        avg_critic_score = round(sum(critic_scores) / len(critic_scores), 1) if critic_scores else None

        backlog_gems = sorted(
            [
                {
                    "id": c["id"],
                    "title": c["title"],
                    "critic_score": c["critic_score"],
                    "platform": c["platform"],
                    "completion_status": c["status"],
                    "release_year": c["release_year"],
                }
                for c in consolidated
                if c["status"] in ("Plan to Play", "Not Played", "Unplayed") and c["critic_score"] is not None
            ],
            key=lambda x: int(x["critic_score"]),
            reverse=True,
        )[:5]

        hall_of_fame = [
            {
                "id": c["id"],
                "title": c["title"],
                "platform": c["platform"],
                "playtime_hours": c["hours"],
                "completion_status": c["status"],
                "user_rating": c["user_rating"],
            }
            for c in sorted(consolidated, key=lambda x: x["hours"], reverse=True)[:6]
        ]

        # Achievements & trophy pyramid
        filtered_ids = {g.id for g in filtered}
        ach_unlocked_sum = sum(c["ach_unlocked"] for c in consolidated)
        ach_total_sum = sum(c["ach_total"] for c in consolidated)
        perfect_count = sum(
            1 for c in consolidated if c["ach_total"] > 0 and c["ach_unlocked"] >= c["ach_total"]
        )
        ach_rate = round((ach_unlocked_sum / max(ach_total_sum, 1)) * 100, 1) if ach_total_sum > 0 else 0.0

        pyramid = {
            "ultra_rare": {"total": 0, "unlocked": 0, "pct": 0.0},
            "rare": {"total": 0, "unlocked": 0, "pct": 0.0},
            "common": {"total": 0, "unlocked": 0, "pct": 0.0},
        }
        ach_by_game: dict[int, tuple[int, int]] = {}
        event_timestamps: list[str] = []

        for ar in snap["achievements"]:
            gid = int(ar["game_id"])
            if gid not in filtered_ids:
                continue
            cur_u, cur_t = ach_by_game.get(gid, (0, 0))
            ach_by_game[gid] = (cur_u + (1 if ar["unlocked"] else 0), cur_t + 1)
            rarity = ar["rarity_pct"]
            if rarity is not None:
                rf = float(rarity)
                bucket = "ultra_rare" if rf <= 5.0 else ("rare" if rf <= 25.0 else "common")
                pyramid[bucket]["total"] += 1
                if ar["unlocked"]:
                    pyramid[bucket]["unlocked"] += 1
            if ar["unlocked"] and ar["unlock_time"]:
                event_timestamps.append(str(ar["unlock_time"]))

        for b in pyramid.values():
            b["pct"] = round((b["unlocked"] / max(b["total"], 1)) * 100, 1) if b["total"] > 0 else 0.0

        almost_perfect: list[dict[str, Any]] = []
        for g in filtered:
            u_cnt, t_cnt = ach_by_game.get(g.id, (g.achievements_unlocked, g.achievements_total))
            if t_cnt > 0 and 0 < u_cnt < t_cnt:
                pct = round((u_cnt / t_cnt) * 100, 1)
                if pct >= 70.0:
                    almost_perfect.append(
                        {
                            "id": g.id,
                            "title": g.title,
                            "platform": g.platform,
                            "unlocked": u_cnt,
                            "total": t_cnt,
                            "remaining": t_cnt - u_cnt,
                            "pct": pct,
                        }
                    )
        almost_perfect.sort(key=lambda x: x["pct"], reverse=True)

        # Circadian rhythm from sessions, achievements, and prompts in resolved user timezone/eras
        for sr in snap["sessions"]:
            if int(sr["game_id"]) in filtered_ids:
                event_timestamps.append(str(sr["started_at"]))
        for pr in snap["prompts"]:
            if int(pr["game_id"]) in filtered_ids and pr["detected_at"]:
                event_timestamps.append(str(pr["detected_at"]))

        circadian_grid = [[0 for _ in range(24)] for _ in range(7)]
        time_buckets = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
        total_events = 0
        for raw_ts in event_timestamps:
            try:
                dt_utc = _parse_iso(raw_ts)
            except Exception:
                continue
            tz = _resolve_zoneinfo(base_tz_name, eras, dt_utc)
            local_dt = dt_utc.astimezone(tz)
            wd = local_dt.weekday()
            hr = local_dt.hour
            circadian_grid[wd][hr] += 1
            total_events += 1
            if hr < 6:
                time_buckets["night"] += 1
            elif hr < 12:
                time_buckets["morning"] += 1
            elif hr < 18:
                time_buckets["afternoon"] += 1
            else:
                time_buckets["evening"] += 1

        day_totals = [sum(row) for row in circadian_grid]
        hour_totals = [sum(circadian_grid[d][h] for d in range(7)) for h in range(24)]
        peak_day_idx = max(range(7), key=lambda i: day_totals[i]) if total_events else 5
        peak_hour_idx = max(range(24), key=lambda i: hour_totals[i]) if total_events else 20
        weekend_events = day_totals[4] + day_totals[5] + day_totals[6]
        weekend_pct = round((weekend_events / max(total_events, 1)) * 100) if total_events else 0
        if not total_events:
            archetype = "No Rhythm Data Yet"
        elif weekend_pct >= 55:
            archetype = f"Weekend Night Owl ({weekend_pct}% Fri-Sun)"
        elif peak_hour_idx >= 20 or peak_hour_idx <= 2:
            archetype = f"Midnight Grinder (Peak {_format_hour_12(peak_hour_idx)})"
        elif 6 <= peak_hour_idx < 12:
            archetype = f"Morning Explorer (Peak {_format_hour_12(peak_hour_idx)})"
        else:
            archetype = f"Evening Player (Peak {_format_hour_12(peak_hour_idx)})"

        # Subscription ROI
        owned_entries = [g for g in filtered if g.ownership_status == "Owned"]
        sub_entries = [g for g in filtered if g.ownership_status == "Subscription" or g.is_game_pass]
        sub_tourists = [g for g in sub_entries if g.playtime_hours < 1.0]
        subscription_roi = {
            "owned": {
                "count": len(owned_entries),
                "hours": round(sum(g.playtime_hours for g in owned_entries), 1),
            },
            "subscription": {
                "count": len(sub_entries),
                "hours": round(sum(g.playtime_hours for g in sub_entries), 1),
                "tourist_count": len(sub_tourists),
                "tourist_rate": round((len(sub_tourists) / max(len(sub_entries), 1)) * 100, 1)
                if sub_entries
                else 0.0,
            },
        }

        # Cross-platform double-dipping
        double_dipped = [
            c
            for c in consolidated
            if len({_platform_family(m.platform) for m in c["copies"]}) > 1
        ]
        cross_platform = {
            "double_dipped_count": len(double_dipped),
            "double_dipped_hours": round(sum(c["hours"] for c in double_dipped), 1),
            "top_titles": [
                {"title": c["title"], "platforms": c["platform"], "hours": c["hours"]}
                for c in sorted(double_dipped, key=lambda x: x["hours"], reverse=True)[:5]
            ],
        }

        return LibraryAnalytics(
            summary=AnalyticsSummary(
                total_entries=total_entries,
                unique_titles=unique_titles,
                total_hours=total_hours,
                played_games=played_cnt,
                beaten_count=beaten_cnt,
                dropped_count=dropped_cnt,
                playing_count=len(playing),
                shelf_of_shame_count=len(shelf),
                favorites_count=len(favs),
                completion_rate=completion_rate,
                drop_rate=drop_rate,
                avg_hours=avg_hours,
                avg_played_hours=avg_played_hours,
                avg_beaten_hours=avg_beaten_hours,
                avg_dropped_hours=avg_dropped_hours,
                dominant_system=platforms_out[0].name if platforms_out else "None",
                dominant_genre=genres_out[0].genre if genres_out else "None",
                achievements_unlocked=ach_unlocked_sum,
                achievements_total=ach_total_sum,
                achievement_rate=ach_rate,
                perfect_games_count=perfect_count,
            ),
            platforms=platforms_out,
            genres=genres_out,
            tags=tags_out,
            statuses=statuses_out,
            commitment_tiers=tiers_out,
            eras=eras_out,
            avg_user_rating=avg_user_rating,
            avg_critic_score=avg_critic_score,
            backlog_gems=backlog_gems,
            hall_of_fame=hall_of_fame,
            circadian_rhythm=CircadianRhythmAnalytics(
                grid=circadian_grid,
                time_buckets=time_buckets,
                total_events=total_events,
                peak_day=_WEEKDAY_NAMES[peak_day_idx],
                peak_hour=_format_hour_12(peak_hour_idx),
                archetype=archetype,
                eras_count=len(eras),
            ),
            subscription_roi=subscription_roi,
            cross_platform=cross_platform,
            trophy_pyramid=pyramid,
            almost_perfect=almost_perfect[:10],
        )

    def get_session_analytics(
        self,
        scope: UserScope,
        *,
        days: int = 365,
        game_id: int | None = None,
        title: str | None = None,
        platform: str | None = None,
        client_timezone: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SessionAnalytics:
        scope.require_active()
        base_tz_name, eras = self._get_timezone_settings(scope, client_timezone)
        now_utc = datetime.now(timezone.utc)
        now_tz = _resolve_zoneinfo(base_tz_name, eras, now_utc)
        today_date = now_utc.astimezone(now_tz).date()
        yesterday_date = today_date - timedelta(days=1)

        with self.db_manager.library_connection(scope) as conn:
            snap = fetch_library_snapshot_rows(conn)

        games_by_id = {int(g["id"]): g for g in snap["games"]}
        target_game_id = game_id
        if target_game_id is None and title and title.strip():
            q = title.strip().lower()
            matched_title_gid: int | None = None
            for gid, grow in games_by_id.items():
                if grow["title"].strip().lower() == q:
                    matched_title_gid = gid
                    break
            target_game_id = matched_title_gid if matched_title_gid is not None else -1

        plat_filter = (platform or "").strip().lower()
        target_fam = _platform_family(platform) if plat_filter else None
        cutoff_utc = now_utc - timedelta(days=max(1, days)) if days > 0 else None
        cutoff_90d = now_utc - timedelta(days=90)

        def _matches_plat(raw_plat: str) -> bool:
            if not plat_filter:
                return True
            low = raw_plat.lower()
            return plat_filter in low or _platform_family(raw_plat).lower() == plat_filter

        sessions_filtered: list[dict[str, Any]] = []
        count_90d = 0
        for s in snap["sessions"]:
            gid = int(s["game_id"])
            if target_game_id is not None and gid != target_game_id:
                continue
            raw_plat = str(s["platform"] or "")
            if not _matches_plat(raw_plat):
                continue
            try:
                end_dt = _parse_iso(s["ended_at"])
            except Exception:
                continue
            if end_dt >= cutoff_90d:
                count_90d += 1
            if cutoff_utc is not None and end_dt < cutoff_utc:
                continue
            g = games_by_id.get(gid)
            dur = int(s["duration_minutes"] or 0)
            active_m = float(s["active_minutes"]) if s["active_minutes"] is not None else float(dur)
            idle_m = float(s["idle_minutes"]) if s["idle_minutes"] is not None else 0.0
            sessions_filtered.append(
                {
                    "id": int(s["id"]),
                    "game_id": gid,
                    "title": g["title"] if g else f"Game #{gid}",
                    "platform": s["platform"],
                    "started_at": s["started_at"],
                    "ended_at": s["ended_at"],
                    "duration_minutes": dur,
                    "active_minutes": active_m,
                    "idle_minutes": idle_m,
                    "source": s["source"],
                    "precision_level": s["precision_level"],
                    "notes": s["notes"],
                    "achievements_unlocked": int(s["achievements_unlocked"] or 0),
                    "is_locked": bool(s["is_locked"]),
                }
            )

        # Ledger projection filtered cleanly
        ledger_rows = []
        for lr in snap["ledger"]:
            gid = int(lr["game_id"])
            if target_game_id is not None and gid != target_game_id:
                continue
            if plat_filter:
                fam_val = str(lr["platform_family"] or "")
                g_row = games_by_id.get(gid)
                g_plat = str(g_row["platform"] or "") if g_row else ""
                if target_fam and fam_val.lower() != target_fam.lower():
                    continue
                if target_fam and plat_filter != target_fam.lower() and not _matches_plat(g_plat):
                    continue
            ledger_rows.append(lr)

        # 1. Streaks from daily_playtime_ledger (dates >= 15 mins)
        mins_by_date: dict[str, float] = {}
        by_date_items: dict[str, list[dict[str, Any]]] = {}
        for lr in ledger_rows:
            d_str = lr["date"]
            m_val = float(lr["minutes_played"] or 0.0)
            mins_by_date[d_str] = mins_by_date.get(d_str, 0.0) + m_val
            gid = int(lr["game_id"])
            g = games_by_id.get(gid)
            by_date_items.setdefault(d_str, []).append(
                {
                    "game_id": gid,
                    "title": g["title"] if g else f"Game #{gid}",
                    "platform_family": lr["platform_family"],
                    "minutes_played": round(m_val, 1),
                    "sessions_count": int(lr["sessions_count"] or 0),
                    "achievements_unlocked": int(lr["achievements_unlocked"] or 0),
                }
            )

        active_dates = sorted(
            datetime.strptime(d, "%Y-%m-%d").date()
            for d, tot in mins_by_date.items()
            if tot >= 15.0
        )
        longest_streak = 0
        current_streak = 0
        if active_dates:
            run = 1
            longest_streak = 1
            for i in range(1, len(active_dates)):
                if active_dates[i] - active_dates[i - 1] == timedelta(days=1):
                    run += 1
                else:
                    run = 1
                longest_streak = max(longest_streak, run)
            if active_dates[-1] in (today_date, yesterday_date):
                c_run = 1
                for i in range(len(active_dates) - 1, 0, -1):
                    if active_dates[i] - active_dates[i - 1] == timedelta(days=1):
                        c_run += 1
                    else:
                        break
                current_streak = c_run

        # 2. Sweet spot & AFK/idle ratio
        durations = sorted(s["active_minutes"] for s in sessions_filtered if s["active_minutes"] > 0)
        if durations:
            mid = len(durations) // 2
            median_mins = (
                durations[mid]
                if len(durations) % 2 == 1
                else round((durations[mid - 1] + durations[mid]) / 2.0, 1)
            )
            avg_mins = round(sum(durations) / len(durations), 1)
        else:
            median_mins = 0.0
            avg_mins = 0.0

        sessions_per_week = round(count_90d / (90.0 / 7.0), 1)

        tot_active = round(sum(s["active_minutes"] for s in sessions_filtered), 1)
        tot_idle = round(sum(s["idle_minutes"] for s in sessions_filtered), 1)
        tot_wall = round(tot_active + tot_idle, 1)
        idle_ratio_pct = round((tot_idle / max(tot_wall, 0.1)) * 100, 1) if tot_wall > 0 else 0.0

        # 3. 30-day Focus Score & Gantt
        date_30d_str = (today_date - timedelta(days=29)).strftime("%Y-%m-%d")
        mins_30d_by_game: dict[int, float] = {}
        gantt_dates = [(today_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
        game_30d_days: dict[int, dict[str, float]] = {}
        for lr in ledger_rows:
            if lr["date"] >= date_30d_str:
                gid = int(lr["game_id"])
                m = float(lr["minutes_played"] or 0.0)
                mins_30d_by_game[gid] = mins_30d_by_game.get(gid, 0.0) + m
                prev_day = game_30d_days.setdefault(gid, {}).get(lr["date"], 0.0)
                game_30d_days[gid][lr["date"]] = round(prev_day + m, 1)

        total_30d_mins = sum(mins_30d_by_game.values())
        ranked_30d = sorted(mins_30d_by_game.items(), key=lambda kv: kv[1], reverse=True)
        if ranked_30d and total_30d_mins > 0:
            top_gid, top_mins = ranked_30d[0]
            top_title = games_by_id[top_gid]["title"] if top_gid in games_by_id else f"Game #{top_gid}"
            focus_score_pct = round((top_mins / total_30d_mins) * 100, 1)
            active_titles = len(ranked_30d)
            if focus_score_pct >= 65:
                focus_archetype = f"{int(round(focus_score_pct))}% Monogamous - Deep in {top_title}"
            elif focus_score_pct >= 35:
                focus_archetype = f"{int(round(focus_score_pct))}% Balanced - {active_titles} titles"
            else:
                focus_archetype = f"{int(round(focus_score_pct))}% Variety Rotation - {active_titles} titles"
        else:
            top_title = "None"
            focus_score_pct = 0.0
            active_titles = 0
            focus_archetype = "No Recent Activity"

        # 4. 365-Day Heatmap
        heatmap_365: list[dict[str, Any]] = []
        for i in range(364, -1, -1):
            d_str = (today_date - timedelta(days=i)).strftime("%Y-%m-%d")
            day_items = by_date_items.get(d_str, [])
            day_mins = round(sum(x["minutes_played"] for x in day_items), 1)
            day_sess = sum(x["sessions_count"] for x in day_items)
            day_achs = sum(x["achievements_unlocked"] for x in day_items)
            intensity = (
                0 if day_mins == 0 else (1 if day_mins < 60 else (2 if day_mins <= 180 else (3 if day_mins <= 300 else 4)))
            )
            heatmap_365.append(
                {
                    "date": d_str,
                    "minutes_played": day_mins,
                    "hours_played": round(day_mins / 60.0, 1),
                    "sessions_count": day_sess,
                    "achievements_unlocked": day_achs,
                    "intensity": intensity,
                    "games_played": [x["title"] for x in day_items],
                }
            )

        # 5. Circadian Clock
        c_hours = [0.0 for _ in range(24)]
        c_days = [0.0 for _ in range(7)]
        for s in sessions_filtered:
            start_utc = _parse_iso(s["started_at"])
            tz = _resolve_zoneinfo(base_tz_name, eras, start_utc)
            local_start = start_utc.astimezone(tz)
            c_hours[local_start.hour] += float(s["active_minutes"])
            c_days[local_start.weekday()] += float(s["active_minutes"])

        hours_int = [int(round(v)) for v in c_hours]
        days_int = [int(round(v)) for v in c_days]
        peak_h = max(range(24), key=lambda i: hours_int[i]) if any(hours_int) else 20
        fri_sun = days_int[4] + days_int[5] + days_int[6]
        wk_tot = max(sum(days_int), 1)
        wknd_pct = int(round((fri_sun / wk_tot) * 100))
        if not any(hours_int):
            circ_archetype = "No Session Clock Data"
        elif wknd_pct >= 55:
            circ_archetype = f"Weekend Night Owl - {wknd_pct}% Fri-Sun"
        elif peak_h >= 20 or peak_h <= 2:
            circ_archetype = f"Midnight Grinder - Peak {_format_hour_12(peak_h)}"
        elif 6 <= peak_h < 12:
            circ_archetype = f"Early Morning Explorer - Peak {_format_hour_12(peak_h)}"
        else:
            circ_archetype = f"Prime-Time Evening Player - Peak {_format_hour_12(peak_h)}"

        # 6. Snack vs Binge histogram
        b_quick = [s["active_minutes"] for s in sessions_filtered if s["active_minutes"] < 30.0]
        b_std = [s["active_minutes"] for s in sessions_filtered if 30.0 <= s["active_minutes"] < 90.0]
        b_deep = [s["active_minutes"] for s in sessions_filtered if 90.0 <= s["active_minutes"] < 180.0]
        b_binge = [s["active_minutes"] for s in sessions_filtered if s["active_minutes"] >= 180.0]
        tot_s = max(len(sessions_filtered), 1)
        longest = max(sessions_filtered, key=lambda x: x["active_minutes"], default=None)
        longest_marathon = None
        if longest and longest["active_minutes"] > 0:
            longest_marathon = {
                "session_id": longest["id"],
                "game_id": longest["game_id"],
                "title": longest["title"],
                "platform": longest["platform"],
                "minutes": round(longest["active_minutes"], 1),
                "hours": round(longest["active_minutes"] / 60.0, 1),
            }

        snack_vs_binge = {
            "quick_checkins": {
                "label": "Quick Check-ins (< 30m)",
                "count": len(b_quick),
                "pct": round((len(b_quick) / tot_s) * 100, 1) if sessions_filtered else 0.0,
                "hours": round(sum(b_quick) / 60.0, 1),
            },
            "standard_runs": {
                "label": "Standard Runs (30-90m)",
                "count": len(b_std),
                "pct": round((len(b_std) / tot_s) * 100, 1) if sessions_filtered else 0.0,
                "hours": round(sum(b_std) / 60.0, 1),
            },
            "deep_sessions": {
                "label": "Deep Sessions (1.5-3h)",
                "count": len(b_deep),
                "pct": round((len(b_deep) / tot_s) * 100, 1) if sessions_filtered else 0.0,
                "hours": round(sum(b_deep) / 60.0, 1),
            },
            "binge_marathons": {
                "label": "Binge Marathons (3h+)",
                "count": len(b_binge),
                "pct": round((len(b_binge) / tot_s) * 100, 1) if sessions_filtered else 0.0,
                "hours": round(sum(b_binge) / 60.0, 1),
            },
            "longest_marathon": longest_marathon,
        }

        # 7. Gantt rows
        gantt_rows = []
        for gid, tot_m in ranked_30d:
            g = games_by_id.get(gid)
            if not g:
                continue
            gantt_rows.append(
                {
                    "game_id": gid,
                    "title": g["title"],
                    "platform": g["platform"],
                    "completion_status": g["completion_status"],
                    "total_30d_minutes": round(tot_m, 1),
                    "total_30d_hours": round(tot_m / 60.0, 1),
                    "active_dates": game_30d_days.get(gid, {}),
                }
            )

        capped_limit = max(1, min(500, int(limit)))
        safe_offset = max(0, int(offset))
        paged_sessions = sessions_filtered[safe_offset : safe_offset + capped_limit]

        return SessionAnalytics(
            kpis={
                "streaks": {
                    "current_streak_days": current_streak,
                    "longest_streak_days": longest_streak,
                    "active_days_total": len(active_dates),
                    "active_today": today_date in active_dates,
                },
                "sweet_spot": {
                    "median_session_minutes": median_mins,
                    "avg_session_minutes": avg_mins,
                    "sessions_per_week": sessions_per_week,
                },
                "focus_30d": {
                    "focus_score_pct": focus_score_pct,
                    "top_game_title": top_title,
                    "active_titles_count": active_titles,
                    "archetype_label": focus_archetype,
                },
                "afk_menu_tax": {
                    "idle_ratio_pct": idle_ratio_pct,
                    "total_wall_clock_minutes": tot_wall,
                    "total_active_minutes": tot_active,
                    "total_idle_minutes": tot_idle,
                },
            },
            heatmap_365=heatmap_365,
            circadian_clock={
                "hours": hours_int,
                "days": days_int,
                "peak_hour": _format_hour_12(peak_h),
                "archetype": circ_archetype,
            },
            snack_vs_binge=snack_vs_binge,
            rotation_gantt_30d={"days_axis": gantt_dates, "rows": gantt_rows},
            recent_sessions=paged_sessions,
            total_sessions=len(sessions_filtered),
            limit=capped_limit,
            offset=safe_offset,
        )

    def get_session_stats(
        self,
        scope: UserScope,
        *,
        days: int = 30,
        client_timezone: str | None = None,
    ) -> SessionWindowStats:
        scope.require_active()
        base_tz_name, eras = self._get_timezone_settings(scope, client_timezone)
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(days=max(1, int(days))) if days > 0 else None

        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute("SELECT * FROM play_sessions ORDER BY ended_at ASC, id ASC").fetchall()

        total_sessions = 0
        total_minutes = 0
        daily_map: dict[str, int] = {}
        plat_map: dict[str, int] = {}

        for r in rows:
            try:
                end_dt = _parse_iso(r["ended_at"])
            except Exception:
                continue
            if cutoff is not None and end_dt < cutoff:
                continue
            dur = int(r["duration_minutes"] or 0)
            total_sessions += 1
            total_minutes += dur
            tz = _resolve_zoneinfo(base_tz_name, eras, end_dt)
            day_str = end_dt.astimezone(tz).date().isoformat()
            daily_map[day_str] = daily_map.get(day_str, 0) + dur
            plat = r["platform"] or "Unknown"
            plat_map[plat] = plat_map.get(plat, 0) + dur

        avg_dur = round(total_minutes / total_sessions, 1) if total_sessions > 0 else 0.0
        series = [{"date": d, "minutes": m} for d, m in sorted(daily_map.items())]
        return SessionWindowStats(
            total_sessions=total_sessions,
            total_minutes=total_minutes,
            average_duration_minutes=avg_dur,
            daily_playtime_series=series,
            platform_distribution=dict(sorted(plat_map.items())),
        )
