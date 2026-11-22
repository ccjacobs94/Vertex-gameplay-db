"""Unit tests for Round 2 services: analytics, wishlist, Home Assistant, catalog, prompts, achievements."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.activity.service import ActivityService, _platform_family
from vertex.analytics.service import AnalyticsService
from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import NotFoundError, ValidationError
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.catalog_lookup import HttpCatalogLookupProvider
from vertex.integrations.homeassistant.service import HomeAssistantService, parse_ha_csv_to_spans
from vertex.library.service import LibraryService
from vertex.wishlist.service import WishlistService


class _FakeLookupProvider:
    def __init__(self) -> None:
        self.searches: list[str] = []
        self.lookups: list[str] = []

    async def search(self, query: str, *, limit: int = 10) -> list[dict]:
        self.searches.append(query)
        return [
            {
                "title": "Hollow Knight",
                "steam_appid": "367520",
                "genres": ["Metroidvania", "Action", "NotATaxonomyGenre"],
                "tags": ["Exploration", "Dark", "FakeTag"],
                "release_year": 2017,
                "critic_score": 90,
                "cover_url": "https://cdn.example.test/hk.jpg",
                "source": "fake_store",
            }
        ][:limit]

    async def lookup_game(self, *, title: str, external_id: str | None = None) -> dict | None:
        self.lookups.append(title)
        if title.lower() == "celeste":
            return {
                "title": "Celeste",
                "steam_appid": "504230",
                "genres": ["Platformer"],
                "tags": ["Indie", "Story-Rich"],
                "release_year": 2018,
                "critic_score": 92,
                "cover_url": "https://cdn.example.test/celeste.jpg",
                "source": "fake_store",
            }
        return None


class TestRound2Services(unittest.TestCase):
    def _setup(self, tmp_path: Path, *, outbound: bool = False):
        cfg = Settings(
            data_dir=tmp_path,
            migrations_dir=MIGRATIONS_DIR,
            frontend_dist_dir=tmp_path / "dist",
            secret_key="round2-services-test-secret",
            worker_enabled=False,
            outbound_sync_enabled=outbound,
        )
        mgr = DatabaseManager(cfg)
        mgr.ensure_system_ready()
        ident = IdentityService(mgr, SecretBox(cfg.secret_key), JobQueue(mgr))
        lib = LibraryService(mgr)
        act = ActivityService(mgr)
        ana = AnalyticsService(mgr)
        wish = WishlistService(mgr, lib)
        ha = HomeAssistantService(mgr, lib, act)
        alice, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
        return alice.to_scope(), mgr, ident, lib, act, ana, wish, ha

    def test_analytics_empty_and_populated_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, mgr, ident, lib, act, ana, _, _ = self._setup(Path(tmp))

            # Empty DB baseline
            empty_lib = ana.get_library_analytics(scope)
            self.assertEqual(empty_lib.summary.total_entries, 0)
            self.assertEqual(empty_lib.summary.unique_titles, 0)
            self.assertEqual(empty_lib.summary.completion_rate, 0.0)
            empty_sess = ana.get_session_analytics(scope)
            self.assertEqual(empty_sess.total_sessions, 0)
            self.assertEqual(empty_sess.kpis["streaks"]["current_streak_days"], 0)
            self.assertEqual(empty_sess.kpis["afk_menu_tax"]["total_active_minutes"], 0.0)
            empty_win = ana.get_session_stats(scope, days=30)
            self.assertEqual(empty_win.total_sessions, 0)

            # Populate reconciled multi-platform title + standalone backlog title
            g_pc = lib.add_manual_game(
                scope,
                title="Hades",
                platform="PC (Steam)",
                completion_status="Beaten",
                playtime_hours=40.0,
                genres=["Roguelike", "Action"],
                tags=["Story-Rich"],
                release_year=2020,
                user_rating=9,
                critic_score=93,
                favorite=True,
                ownership_status="Owned",
            )
            g_switch = lib.add_manual_game(
                scope,
                title="Hades",
                platform="Nintendo Switch",
                completion_status="Playing",
                playtime_hours=12.0,
                genres=["Roguelike"],
                release_year=2020,
                ownership_status="Owned",
            )
            lib.merge_games(scope, [g_pc.id, g_switch.id])

            g_back = lib.add_manual_game(
                scope,
                title="Disco Elysium",
                platform="PC (Steam)",
                completion_status="Plan to Play",
                playtime_hours=0.0,
                genres=["RPG"],
                tags=["Story-Rich"],
                release_year=2019,
                critic_score=91,
                ownership_status="Owned",
            )

            # Record achievements on PC game
            with mgr.library_connection(scope, immediate=True) as conn:
                conn.execute(
                    "UPDATE games SET achievements_unlocked = 25, achievements_total = 49 WHERE id = ?",
                    (g_pc.id,),
                )

            # Set timezone & log sessions across 2 consecutive days
            ident.update_timezone(scope, timezone_name="America/Chicago")
            now = datetime.now(timezone.utc)
            day1_start = (now - timedelta(days=1, hours=2)).isoformat()
            day1_end = (now - timedelta(days=1, hours=1)).isoformat()
            day2_start = (now - timedelta(hours=3)).isoformat()
            day2_end = (now - timedelta(hours=1, minutes=30)).isoformat()

            act.record_interval_observation(
                scope,
                game_id=g_pc.id,
                source="manual",
                platform="PC (Steam)",
                started_at=day1_start,
                ended_at=day1_end,
                duration_minutes=60,
                idempotency_key="ana:1",
            )
            act.record_interval_observation(
                scope,
                game_id=g_switch.id,
                source="manual",
                platform="Nintendo Switch",
                started_at=day2_start,
                ended_at=day2_end,
                duration_minutes=90,
                idempotency_key="ana:2",
            )

            # Snapshot before analytics to verify read-only invariant
            before_games = [(g.id, g.completion_status, g.playtime_minutes) for g in lib.list_games(scope)]
            before_sessions = len(act.list_sessions(scope, limit=50))

            report = ana.get_library_analytics(scope)
            self.assertEqual(report.summary.total_entries, 3)
            self.assertEqual(report.summary.unique_titles, 2)
            self.assertEqual(report.summary.total_hours, 52.0)
            self.assertEqual(report.summary.beaten_count, 1)
            self.assertEqual(report.summary.shelf_of_shame_count, 1)
            self.assertEqual(report.cross_platform["double_dipped_count"], 1)
            self.assertGreater(report.summary.achievement_rate, 0.0)
            self.assertTrue(any(g.title == "Disco Elysium" for g in [] ) or any(x["title"] == "Disco Elysium" for x in report.backlog_gems))

            sess_rep = ana.get_session_analytics(scope, days=30, limit=1, offset=0)
            self.assertEqual(sess_rep.total_sessions, 2)
            self.assertEqual(len(sess_rep.recent_sessions), 1)
            self.assertEqual(sess_rep.kpis["streaks"]["current_streak_days"], 2)
            self.assertGreaterEqual(sess_rep.kpis["afk_menu_tax"]["total_active_minutes"], 150.0)
            self.assertIn("standard_runs", sess_rep.snack_vs_binge)
            self.assertEqual(sess_rep.snack_vs_binge["standard_runs"]["count"], 1)
            self.assertEqual(sess_rep.snack_vs_binge["deep_sessions"]["count"], 1)
            self.assertIn("hours", sess_rep.circadian_clock)

            win_stats = ana.get_session_stats(scope, days=30)
            self.assertEqual(win_stats.total_sessions, 2)
            self.assertEqual(win_stats.total_minutes, 150)

            after_games = [(g.id, g.completion_status, g.playtime_minutes) for g in lib.list_games(scope)]
            after_sessions = len(act.list_sessions(scope, limit=50))
            self.assertEqual(before_games, after_games)
            self.assertEqual(before_sessions, after_sessions)

    def test_wishlist_lifecycle_conflicts_smart_upgrades_and_idempotent_purchase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, _, _, lib, _, _, wish, _ = self._setup(Path(tmp))

            # Validation errors
            with self.assertRaises(ValidationError):
                wish.add_item(scope, title="   ")
            with self.assertRaises(ValidationError):
                wish.add_item(scope, title="Bad Target", platform_targets=[{"platform_family": "PC", "target_price_usd": -5}])

            # Already own Elden Ring on PC (Steam) -> PC target should be auto-muted unless forced
            lib.add_manual_game(
                scope,
                title="Elden Ring",
                platform="PC (Steam)",
                ownership_status="Owned",
                completion_status="Beaten",
            )
            item = wish.add_item(
                scope,
                title="Elden Ring",
                platform_targets=[
                    {"platform_family": "PC", "target_price_usd": 30.0},
                    {"platform_family": "Xbox", "target_price_usd": 25.0},
                ],
                genres=["Action", "RPG", "BogusGenre"],
            )
            self.assertTrue(item.conflict.already_in_library)
            pc_target = next(t for t in item.targets if t.platform_family == "PC")
            xbox_target = next(t for t in item.targets if t.platform_family == "Xbox")
            self.assertTrue(pc_target.muted_owned_conflict)
            self.assertFalse(xbox_target.muted_owned_conflict)
            self.assertEqual(item.canonical_game.genres, ("Action", "RPG"))

            # Record base edition price above target and Deluxe Edition smart upgrade below target on Xbox
            with self.assertRaises(ValidationError):
                wish.record_manual_price(scope, item.id, price_usd=-1.0)

            p_base = wish.record_manual_price(
                scope,
                item.id,
                price_usd=49.99,
                regular_price_usd=59.99,
                platform="Xbox Series X",
                storefront="xbox",
                edition_name="Standard Edition",
            )
            self.assertTrue(p_base.is_all_time_low)

            p_deluxe = wish.record_manual_price(
                scope,
                item.id,
                price_usd=22.50,
                regular_price_usd=79.99,
                platform="Xbox",
                storefront="xbox",
                edition_name="Deluxe Edition",
            )
            self.assertTrue(p_deluxe.is_all_time_low)
            self.assertEqual(p_deluxe.historical_low_usd, 22.50)

            # Same-day identical price shouldn't duplicate history; lower price updates ATL and appends
            wish.record_manual_price(
                scope,
                item.id,
                price_usd=22.50,
                regular_price_usd=79.99,
                platform="Xbox",
                storefront="xbox",
                edition_name="Deluxe Edition",
            )
            hist_1 = wish.get_price_history(scope, item.id)
            self.assertEqual(len(hist_1.price_history_by_sku[p_deluxe.sku_id]), 1)

            wish.record_manual_price(
                scope,
                item.id,
                price_usd=19.99,
                regular_price_usd=79.99,
                platform="Xbox",
                storefront="xbox",
                edition_name="Deluxe Edition",
            )
            hist_2 = wish.get_price_history(scope, item.id)
            self.assertEqual(len(hist_2.price_history_by_sku[p_deluxe.sku_id]), 2)

            # Evaluate deals: PC target is muted so only Xbox Deluxe smart upgrade surfaces
            deals = wish.evaluate_deals(scope, smart_upgrades_only=True)
            self.assertEqual(deals.total_deals, 1)
            self.assertEqual(deals.smart_upgrades_count, 1)
            self.assertEqual(deals.deals[0].edition_tier, "deluxe")
            self.assertEqual(deals.deals[0].platform_family, "Xbox")

            # Add non-owned wishlist item and test strictly idempotent mark_purchased
            silksong = wish.add_item(
                scope,
                title="Hollow Knight: Silksong",
                platform_targets=[{"platform_family": "PC", "target_price_usd": 20.0}],
            )
            first_buy = wish.mark_purchased(
                scope,
                silksong.id,
                platform="PC (Steam)",
                edition_name="Standard Edition",
                price_paid_usd=19.99,
            )
            self.assertFalse(first_buy.already_purchased)
            self.assertEqual(first_buy.item.status, "purchased")

            second_buy = wish.mark_purchased(
                scope,
                silksong.id,
                platform="PC (Steam)",
                edition_name="Standard Edition",
                price_paid_usd=19.99,
            )
            self.assertTrue(second_buy.already_purchased)
            self.assertEqual(first_buy.library_game_id, second_buy.library_game_id)

            matches = [g for g in lib.list_games(scope) if g.title == "Hollow Knight: Silksong"]
            self.assertEqual(len(matches), 1)

            # Delete & missing NotFoundError
            wish.delete_item(scope, item.id)
            with self.assertRaises(NotFoundError):
                wish.get_item(scope, item.id)

    def test_homeassistant_csv_and_events_deduplicate_and_stay_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, _, _, lib, act, _, _, ha = self._setup(Path(tmp))

            with self.assertRaises(ValidationError):
                parse_ha_csv_to_spans("")
            with self.assertRaises(ValidationError):
                parse_ha_csv_to_spans("foo,bar\n1,2\n")

            # Two brief idle blips inside 10 min coalesced into 1 session (75 min), then long 2h break -> second session (30 min)
            csv_text = "\n".join(
                [
                    "entity_id,state,last_changed",
                    "sensor.xbox_game,Halo Infinite,2026-10-01T18:00:00Z",
                    "sensor.xbox_game,idle,2026-10-01T18:30:00Z",
                    "sensor.xbox_game,Halo Infinite,2026-10-01T18:40:00Z",
                    "sensor.xbox_game,unknown,2026-10-01T19:15:00Z",
                    "sensor.xbox_game,Halo Infinite,2026-10-01T21:15:00Z",
                    "sensor.xbox_game,off,2026-10-01T21:45:00Z",
                ]
            )
            spans = parse_ha_csv_to_spans(csv_text, max_gap_seconds=1800)
            self.assertEqual(len(spans), 2)
            self.assertEqual(spans[0].duration_minutes, 75)
            self.assertEqual(spans[1].duration_minutes, 30)

            res1 = ha.import_csv(scope, csv_content=csv_text, platform="Xbox Series X")
            self.assertEqual(res1.imported_count, 2)
            self.assertEqual(res1.skipped_count, 0)

            # Re-importing identical CSV is idempotent
            res2 = ha.import_csv(scope, csv_content=csv_text, platform="Xbox Series X")
            self.assertEqual(res2.imported_count, 0)
            self.assertEqual(res2.skipped_count, 2)

            # start -> stop event flow creates exact session
            s_evt = ha.ingest_event(
                scope,
                event="start",
                title="Forza Horizon 5",
                platform="Xbox",
                external_id="xbox_forza_1",
                started_at="2026-10-02T19:00:00Z",
            )
            self.assertEqual(s_evt.status, "started")

            stop_evt = ha.ingest_event(
                scope,
                event="stop",
                external_id="xbox_forza_1",
                ended_at="2026-10-02T20:00:00Z",
            )
            self.assertEqual(stop_evt.status, "session_created")
            self.assertEqual(stop_evt.duration_minutes, 60)

            # Overlapping cumulative sync delta is deduplicated against exact HA session
            forza = lib.get_game(scope, stop_evt.game_id)
            act.record_cumulative_observation(
                scope,
                game_id=forza.id,
                source="xbox_sync",
                platform="Xbox",
                observed_minutes=0,
                observed_at="2026-10-02T18:50:00Z",
                idempotency_key="xbox_cum:forza:0",
            )
            cum_res = act.record_cumulative_observation(
                scope,
                game_id=forza.id,
                source="xbox_sync",
                platform="Xbox",
                observed_minutes=60,
                observed_at="2026-10-02T20:05:00Z",
                idempotency_key="xbox_cum:forza:1",
            )
            self.assertEqual(cum_res.status, "covered_by_exact_session")
            sessions = act.list_sessions(scope, game_id=forza.id)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].precision_level, "exact")

    def test_catalog_search_enrich_reconcile_reset_prompts_and_achievements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, mgr, _, lib, act, _, _, _ = self._setup(Path(tmp), outbound=True)
            provider = _FakeLookupProvider()
            catalog = CatalogService(mgr, outbound_sync_enabled=True, lookup_provider=provider)
            offline_catalog = CatalogService(mgr, outbound_sync_enabled=False, lookup_provider=provider)

            # Seed offline cache and verify offline search works without hitting provider
            catalog.upsert_public_metadata(
                title="Portal 2",
                source="seed",
                genres=["Puzzle", "InvalidGenre"],
                tags=["Co-op", "InvalidTag"],
                release_year=2011,
                critic_score=95,
            )
            cached_results = asyncio.run(offline_catalog.search_catalog("Portal"))
            self.assertEqual(len(cached_results), 1)
            self.assertEqual(list(cached_results[0].genres), ["Puzzle"])
            self.assertEqual(provider.searches, [])

            # Online search filters taxonomy & caches result
            online_results = asyncio.run(catalog.search_catalog("Hollow Knight"))
            self.assertEqual(len(online_results), 1)
            self.assertEqual(list(online_results[0].genres), ["Metroidvania", "Action"])
            self.assertEqual(list(online_results[0].tags), ["Exploration", "Dark"])

            # Enrich library merges discovered metadata without overwriting existing genres/tags/locked status
            g_cel = lib.add_manual_game(
                scope,
                title="Celeste",
                platform="PC (Steam)",
                completion_status="Beaten",
                status_locked=True,
                genres=["Action"],
                tags=["Indie"],
            )
            rep = asyncio.run(catalog.enrich_library(scope, lib, limit=50))
            self.assertEqual(rep.games_enriched, 1)
            refreshed = lib.get_game(scope, g_cel.id)
            self.assertEqual(list(refreshed.genres), ["Action", "Platformer"])
            self.assertIn("Story-Rich", refreshed.tags)
            self.assertEqual(refreshed.completion_status, "Beaten")
            self.assertTrue(refreshed.status_locked)

            # Reconcile dismiss + reset-dismissals
            g1 = lib.add_manual_game(scope, title="Control", platform="PC (Steam)")
            g2 = lib.add_manual_game(scope, title="Control", platform="PlayStation 5")
            cands_before = lib.get_reconcile_candidates(scope)
            self.assertEqual(len(cands_before), 1)
            lib.dismiss_reconcile_candidate(scope, [g1.id, g2.id])
            self.assertEqual(lib.get_reconcile_candidates(scope), [])
            cleared = lib.reset_reconcile_dismissals(scope)
            self.assertEqual(cleared, 1)
            self.assertEqual(len(lib.get_reconcile_candidates(scope)), 1)

            # Merge and test unified cross-platform achievements
            lib.merge_games(scope, [g1.id, g2.id])
            with mgr.library_connection(scope, immediate=True) as conn:
                conn.execute(
                    """
                    INSERT INTO game_achievements (
                        game_id, external_ach_id, source, platform, name, description, unlocked, unlock_time, updated_at
                    )
                    VALUES (?, 'ach1', 'Steam', 'PC (Steam)', 'Welcome to the Oldest House', 'Enter the Bureau', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
                           (?, 'trp1', 'PSN', 'PlayStation 5', 'Welcome to the Oldest House', 'Enter the Bureau', 0, NULL, '2026-01-01T00:00:00Z'),
                           (?, 'ach2', 'Steam', 'PC (Steam)', 'Director of the FBC', 'Finish story', 1, '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z'),
                           (?, 'trp2', 'PSN', 'PlayStation 5', 'Director of the FBC', 'Finish story', 1, '2026-01-03T00:00:00Z', '2026-01-03T00:00:00Z')
                    """,
                    (g1.id, g2.id, g1.id, g2.id),
                )
            ach_detail = lib.get_game_achievements(scope, g1.id)
            self.assertEqual(len(ach_detail.per_platform_summary), 2)
            self.assertEqual(ach_detail.unified_summary.total, 2)
            self.assertEqual(ach_detail.unified_summary.unlocked, 2)
            self.assertEqual(ach_detail.unified_summary.overlap_count, 1)

            with self.assertRaises(NotFoundError):
                lib.get_game_achievements(scope, 999999)

            # Playtime prompt created on status_locked=True non-Playing game when >= 30m delta observed
            g_locked = lib.add_manual_game(
                scope,
                title="Balatro",
                platform="PC (Steam)",
                completion_status="Beaten",
                status_locked=True,
                playtime_hours=10.0,
            )
            act.record_cumulative_observation(
                scope,
                game_id=g_locked.id,
                source="steam_sync",
                platform="PC (Steam)",
                observed_minutes=600,
                observed_at="2026-10-10T11:00:00Z",
                idempotency_key="steam_cum:balatro:0",
            )
            act.record_cumulative_observation(
                scope,
                game_id=g_locked.id,
                source="steam_sync",
                platform="PC (Steam)",
                observed_minutes=645,  # +45 mins over 600 baseline -> should trigger prompt!
                observed_at="2026-10-10T12:00:00Z",
                idempotency_key="steam_cum:balatro:1",
            )
            prompts = act.list_playtime_prompts(scope)
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0].game_id, g_locked.id)
            # Locked status must still remain Beaten!
            self.assertEqual(lib.get_game(scope, g_locked.id).completion_status, "Beaten")

            # Accept transitions locked game to Playing & resolves prompt
            res = act.resolve_playtime_prompt(scope, prompts[0].id, "accept")
            self.assertEqual(res.action, "accept")
            self.assertEqual(res.completion_status, "Playing")
            self.assertTrue(res.status_locked)
            self.assertEqual(act.list_playtime_prompts(scope), [])
            self.assertEqual(lib.get_game(scope, g_locked.id).completion_status, "Playing")

            # Also verify dismiss & mute actions on another locked game
            g_muted = lib.add_manual_game(
                scope,
                title="Slay the Spire",
                platform="PC (Steam)",
                completion_status="Beaten",
                status_locked=True,
                playtime_hours=5.0,
            )
            act.record_cumulative_observation(
                scope,
                game_id=g_muted.id,
                source="steam_sync",
                platform="PC (Steam)",
                observed_minutes=300,
                observed_at="2026-10-10T10:00:00Z",
                idempotency_key="steam_cum:sts:0",
            )
            act.record_cumulative_observation(
                scope,
                game_id=g_muted.id,
                source="steam_sync",
                platform="PC (Steam)",
                observed_minutes=350,
                observed_at="2026-10-10T11:00:00Z",
                idempotency_key="steam_cum:sts:1",
            )
            p_sts = act.list_playtime_prompts(scope)
            self.assertEqual(len(p_sts), 1)
            dismiss_res = act.resolve_playtime_prompt(scope, p_sts[0].id, "dismiss")
            self.assertEqual(dismiss_res.action, "dismiss")
            self.assertEqual(lib.get_game(scope, g_muted.id).completion_status, "Beaten")

            act.record_cumulative_observation(
                scope,
                game_id=g_muted.id,
                source="steam_sync",
                platform="PC (Steam)",
                observed_minutes=395,
                observed_at="2026-10-10T12:00:00Z",
                idempotency_key="steam_cum:sts:2",
            )
            p_sts2 = act.list_playtime_prompts(scope)
            self.assertEqual(len(p_sts2), 1)
            mute_res = act.resolve_playtime_prompt(scope, p_sts2[0].id, "mute")
            self.assertEqual(mute_res.action, "mute")
            self.assertTrue(mute_res.playtime_prompt_muted)

            # Further large observations should NOT create prompts once muted
            act.record_cumulative_observation(
                scope,
                game_id=g_muted.id,
                source="steam_sync",
                platform="PC (Steam)",
                observed_minutes=450,
                observed_at="2026-10-10T13:00:00Z",
                idempotency_key="steam_cum:sts:3",
            )
            self.assertEqual(act.list_playtime_prompts(scope), [])

    def test_regression_analytics_filters_platform_family_and_cadence_and_list_games(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, _, _, lib, act, ana, _, _ = self._setup(Path(tmp))
            self.assertEqual(_platform_family("FPS Arcade"), "FPS Arcade")
            self.assertEqual(_platform_family("PS5"), "PlayStation")

            g_ps5 = lib.add_manual_game(scope, title="Astro Bot", platform="PS5")
            g_steam = lib.add_manual_game(scope, title="Portal 2", platform="PC (Steam)")
            self.assertEqual([g.title for g in lib.list_games(scope, platform="Steam")], ["Portal 2"])
            self.assertEqual([g.title for g in lib.list_games(scope, platform="PlayStation")], ["Astro Bot"])

            now = datetime.now(timezone.utc)
            for d in (5, 10, 40, 50, 60, 70):
                s = (now - timedelta(days=d, hours=1)).isoformat()
                e = (now - timedelta(days=d)).isoformat()
                act.record_interval_observation(
                    scope,
                    game_id=g_ps5.id,
                    source="manual",
                    platform="PS5",
                    started_at=s,
                    ended_at=e,
                    duration_minutes=60,
                    idempotency_key=f"ps5:{d}",
                )

            # Platform family filter matches PS5 sessions + PlayStation ledger consistently
            sa_ps = ana.get_session_analytics(scope, days=30, platform="PlayStation")
            self.assertEqual(sa_ps.total_sessions, 2)
            # 6 sessions over 90d -> 0.5/week even when viewing a 30-day window
            self.assertEqual(sa_ps.kpis["sweet_spot"]["sessions_per_week"], 0.5)
            # Non-matching title filter returns empty instead of all sessions
            self.assertEqual(ana.get_session_analytics(scope, title="DoesNotExist").total_sessions, 0)
            self.assertEqual(len(lib.list_games(scope)), 2)
            self.assertGreater(g_steam.id, 0)

    def test_regression_http_catalog_lookup_wikipedia_and_steam_fallback(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host or ""
            if host == "store.steampowered.com" and request.url.path.endswith("/api/appdetails"):
                return httpx.Response(500, text="upstream error")
            if host == "store.steampowered.com" and request.url.path.endswith("/api/storesearch/"):
                return httpx.Response(200, json={"items": []})
            if host == "en.wikipedia.org":
                return httpx.Response(
                    200,
                    json={
                        "query": {
                            "pages": {
                                "10": {
                                    "index": 2,
                                    "title": "Tetris Effect",
                                    "description": "2018 puzzle video game",
                                    "thumbnail": {"source": "https://upload.example.test/te.jpg"},
                                },
                                "2": {
                                    "index": 1,
                                    "title": "Tetris (video game)",
                                    "description": "1985 puzzle video game",
                                    "extract": "Marketed with a strategy involving sports and action.",
                                    "thumbnail": {"source": "https://upload.example.test/tetris.jpg"},
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        prov = HttpCatalogLookupProvider(client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        hits = asyncio.run(prov.search("Tetris", limit=5))
        self.assertEqual(hits[0]["title"], "Tetris")
        self.assertEqual(hits[0]["genres"], ["Puzzle"])
        self.assertEqual(hits[0]["release_year"], 1985)

        # lookup_game falls back to Wikipedia/search when steam appdetails returns 500
        fallback = asyncio.run(prov.lookup_game(title="Tetris", external_id="steam:999999"))
        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertEqual(fallback["title"], "Tetris")

    def test_regression_ha_duplicate_stop_without_title_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, _, _, _, _, _, _, ha = self._setup(Path(tmp))
            ha.ingest_event(
                scope,
                event="start",
                title="Forza Horizon 5",
                platform="Xbox",
                external_id="xbox_fh5",
                started_at="2026-10-02T19:00:00Z",
            )
            first = ha.ingest_event(
                scope,
                event="stop",
                external_id="xbox_fh5",
                ended_at="2026-10-02T20:00:00Z",
                idempotency_key="ha_stop_retry_1",
            )
            self.assertEqual(first.status, "session_created")
            retry = ha.ingest_event(
                scope,
                event="stop",
                external_id="xbox_fh5",
                ended_at="2026-10-02T20:00:00Z",
                idempotency_key="ha_stop_retry_1",
            )
            self.assertEqual(retry.status, "duplicate_ignored")
            self.assertEqual(retry.game_id, first.game_id)

    def test_post_completion_congrats_and_exact_session_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope, _, mgr, lib, act, _, _, _ = self._setup(Path(tmp))
            now_dt = datetime.now(timezone.utc).replace(microsecond=0)
            g = lib.add_manual_game(
                scope,
                title="Elden Ring",
                platform="Xbox",
                completion_status="Playing",
                status_locked=True,
                playtime_hours=80.0,
            )
            act.seed_baseline_if_absent(
                scope,
                game_id=g.id,
                source="Xbox",
                baseline_minutes=4800,
                observed_at=(now_dt - timedelta(hours=3)).isoformat(),
            )
            # Exact telemetry session ended 30m ago
            act.record_interval_observation(
                scope,
                game_id=g.id,
                source="xbox_telemetry",
                platform="Xbox",
                started_at=(now_dt - timedelta(minutes=90)).isoformat(),
                ended_at=(now_dt - timedelta(minutes=30)).isoformat(),
                duration_minutes=60,
                precision_level="exact",
                idempotency_key="er-exact-1",
            )
            # User switches status Playing -> Beaten
            lib.update_game(scope, g.id, completion_status="Beaten")
            prompts = act.list_playtime_prompts(scope)
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0].prompt_type, "congrats")
            self.assertEqual(prompts[0].current_status, "Beaten")
            self.assertEqual(prompts[0].sessions_count, 1)

            # Delayed cumulative sync covered by exact session must not overwrite congrats with playtime nudge
            covered = act.record_cumulative_observation(
                scope,
                game_id=g.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=4860,
                observed_at=(now_dt - timedelta(minutes=28)).isoformat(),
                idempotency_key="er-cum-covered",
            )
            self.assertEqual(covered.status, "covered_by_exact_session")
            prompts2 = act.list_playtime_prompts(scope)
            self.assertEqual(len(prompts2), 1)
            self.assertEqual(prompts2[0].prompt_type, "congrats")

            # Uncovered +45m cumulative sync within 24h updates congrats rather than creating playtime nudge
            act.record_cumulative_observation(
                scope,
                game_id=g.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=4905,
                observed_at=(now_dt + timedelta(hours=2)).isoformat(),
                idempotency_key="er-cum-late",
            )
            prompts3 = act.list_playtime_prompts(scope)
            self.assertEqual(len(prompts3), 1)
            self.assertEqual(prompts3[0].prompt_type, "congrats")

            # Dismiss keeps it dismissed across subsequent 24h cumulative syncs
            act.resolve_playtime_prompt(scope, prompts3[0].id, "dismiss")
            self.assertEqual(act.list_playtime_prompts(scope), [])
            act.record_cumulative_observation(
                scope,
                game_id=g.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=4950,
                observed_at=(now_dt + timedelta(hours=4)).isoformat(),
                idempotency_key="er-cum-late-2",
            )
            self.assertEqual(act.list_playtime_prompts(scope), [])

            # Cumulative observation > 24h after marking Beaten triggers standard replay playtime nudge
            act.record_cumulative_observation(
                scope,
                game_id=g.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=5010,
                observed_at=(now_dt + timedelta(hours=30)).isoformat(),
                idempotency_key="er-cum-replay",
            )
            replay_prompts = act.list_playtime_prompts(scope)
            self.assertEqual(len(replay_prompts), 1)
            self.assertEqual(replay_prompts[0].prompt_type, "playtime")

            # Also verify exact session starting AFTER status_updated_at triggers playtime nudge on uncovered delta
            g2 = lib.add_manual_game(
                scope,
                title="Cyberpunk 2077",
                platform="Xbox",
                completion_status="Playing",
                status_locked=True,
                playtime_hours=20.0,
            )
            act.seed_baseline_if_absent(
                scope,
                game_id=g2.id,
                source="Xbox",
                baseline_minutes=1200,
                observed_at=(now_dt - timedelta(hours=2)).isoformat(),
            )
            lib.update_game(scope, g2.id, completion_status="Completed")
            # New exact session starts 1 hour AFTER marking Completed
            act.record_interval_observation(
                scope,
                game_id=g2.id,
                source="xbox_telemetry",
                platform="Xbox",
                started_at=(now_dt + timedelta(hours=1)).isoformat(),
                ended_at=(now_dt + timedelta(hours=1, minutes=20)).isoformat(),
                duration_minutes=20,
                precision_level="exact",
                idempotency_key="cp-post-exact",
            )
            act.record_cumulative_observation(
                scope,
                game_id=g2.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=1270,
                observed_at=(now_dt + timedelta(hours=3)).isoformat(),
                idempotency_key="cp-post-cum",
            )
            cp_prompts = [p for p in act.list_playtime_prompts(scope) if p.game_id == g2.id]
            self.assertEqual(len(cp_prompts), 1)
            self.assertEqual(cp_prompts[0].prompt_type, "playtime")


if __name__ == "__main__":
    unittest.main()
