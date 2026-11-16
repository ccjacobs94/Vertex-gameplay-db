"""Unit tests for library preservation invariants, taxonomy, reconciliation, and activity rules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.activity.service import ActivityService, allocate_minutes_by_local_day
from vertex.catalog.taxonomy import validate_genres, validate_tags
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.service import LibraryService


class TestDomainInvariants(unittest.TestCase):
    def _setup(self, tmp_path: Path):
        cfg = Settings(
            data_dir=tmp_path,
            migrations_dir=MIGRATIONS_DIR,
            frontend_dist_dir=tmp_path / "dist",
            secret_key="test-secret-key-unit-1234",
            worker_enabled=False,
        )
        mgr = DatabaseManager(cfg)
        mgr.ensure_system_ready()
        ident = IdentityService(mgr, SecretBox(cfg.secret_key), JobQueue(mgr))
        acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
        return acct.to_scope(), LibraryService(mgr), ActivityService(mgr), ident

    def test_sync_preserves_locked_status_ownership_edits_and_merge_only_taxonomy(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope, lib, _act, _ident = self._setup(Path(tmp))
            created, is_new = lib.apply_platform_snapshot(
                scope,
                external_id="steam:620",
                title="Portal 2",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=120,
                genres=["Action", "Free To Play", "Indie"],  # Retired genres must NOT stick
                tags=["Stealth", "Co-op"],
            )
            self.assertTrue(is_new)
            self.assertEqual(created.genres, ("Action",))
            self.assertEqual(created.tags, ("Stealth", "Co-op"))

            # User locks status, overrides ownership to Borrowed + Game Pass, adds notes/rating/fav
            edited = lib.update_game(
                scope,
                created.id,
                completion_status="Completed",
                ownership_status="Borrowed",
                is_game_pass=True,
                user_rating=10,
                notes="Finished co-op campaign",
                favorite=True,
                genres=["Puzzle Platformer"],  # merges into existing ("Action", "Puzzle Platformer")
            )
            self.assertTrue(edited.status_locked)
            self.assertTrue(edited.ownership_locked)
            self.assertEqual(edited.genres, ("Action", "Puzzle Platformer"))

            # Subsequent sync with lower playtime regression, different genres, GP false -> MUST preserve user edits!
            synced, is_new2 = lib.apply_platform_snapshot(
                scope,
                external_id="steam:620",
                title="Portal 2",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=60,  # Regression! Must keep 120
                genres=["Adventure", "Utilities"],
                tags=["Puzzle", "NotARealTag"],
                is_game_pass=False,
            )
            self.assertFalse(is_new2)
            self.assertEqual(synced.playtime_minutes, 120)
            self.assertEqual(synced.completion_status, "Completed")
            self.assertEqual(synced.ownership_status, "Borrowed")
            self.assertFalse(synced.is_game_pass)  # independent catalog availability updates
            self.assertEqual(synced.user_rating, 10)
            self.assertEqual(synced.notes, "Finished co-op campaign")
            self.assertTrue(synced.favorite)
            self.assertEqual(synced.genres, ("Action", "Puzzle Platformer", "Adventure"))
            self.assertEqual(synced.tags, ("Stealth", "Co-op", "Puzzle"))

            # Explicit user label replacement via Edit Dialog replaces cleanly
            replaced = lib.update_game(
                scope,
                created.id,
                genres=["Puzzle Platformer", "Indie"],
                tags=["Co-op"],
                replace_labels=True,
            )
            self.assertEqual(replaced.genres, ("Puzzle Platformer",))
            self.assertEqual(replaced.tags, ("Co-op",))

    def test_reconcile_merge_and_unlink_keep_independent_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope, lib, _act, _ident = self._setup(Path(tmp))
            g1, _ = lib.apply_platform_snapshot(
                scope,
                external_id="steam:100",
                title="Control",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=300,
            )
            g2 = lib.add_manual_game(
                scope,
                title="Control",
                platform="Xbox",
                ownership_status="Subscription",
                is_game_pass=True,
                playtime_hours=2.0,
            )

            cands = lib.get_reconcile_candidates(scope)
            self.assertEqual(len(cands), 1)
            canon = lib.merge_games(scope, [g1.id, g2.id])
            self.assertEqual(lib.get_game(scope, g1.id).canonical_id, canon)
            self.assertEqual(lib.get_game(scope, g2.id).canonical_id, canon)
            self.assertEqual(lib.get_game(scope, g1.id).ownership_status, "Owned")
            self.assertEqual(lib.get_game(scope, g2.id).ownership_status, "Subscription")

            stats = lib.get_summary_stats(scope)
            self.assertEqual(stats.total_entries, 2)
            self.assertEqual(stats.unique_titles, 1)

            lib.unlink_game(scope, g2.id)
            self.assertIsNone(lib.get_game(scope, g1.id).canonical_id)
            self.assertIsNone(lib.get_game(scope, g2.id).canonical_id)

    def test_activity_baseline_deletion_coalescing_locks_and_regression_invariants(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope, lib, act, _ident = self._setup(Path(tmp))
            game = lib.add_manual_game(
                scope,
                title="Hades",
                platform="PC (Steam)",
                completion_status="Plan to Play",
            )
            # Unlock completion status so auto-transition can run on unlocked game
            lib.update_game(scope, game.id, status_locked=False)

            # 1. Baseline initialization must NOT invent retroactive sessions
            init_out = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=300,
                observed_at="2026-10-10T10:00:00+00:00",
                idempotency_key="obs-1",
            )
            self.assertEqual(init_out.status, "baseline_initialized")
            self.assertEqual(len(act.list_sessions(scope)), 0)

            # 2. +30 min delta creates session and transitions unlocked Plan to Play -> Playing
            out2 = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=330,
                observed_at="2026-10-10T11:00:00+00:00",
                idempotency_key="obs-2",
            )
            self.assertEqual(out2.status, "session_created")
            self.assertIsNotNone(out2.session)
            self.assertEqual(out2.session.duration_minutes, 30)
            self.assertEqual(lib.get_game(scope, game.id).completion_status, "Playing")

            # 3. Replay same idempotency_key -> duplicate_ignored
            dup = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=330,
                observed_at="2026-10-10T11:00:00+00:00",
                idempotency_key="obs-2",
            )
            self.assertEqual(dup.status, "duplicate_ignored")

            # 4. Within 30 minutes & unlocked -> coalesces into same session
            out3 = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=345,
                observed_at="2026-10-10T11:15:00+00:00",
                idempotency_key="obs-3",
            )
            self.assertEqual(out3.status, "session_coalesced")
            self.assertEqual(out3.session.duration_minutes, 45)
            self.assertEqual(len(act.list_sessions(scope)), 1)

            # 5. Lock session (user edit) -> next delta within 30 min MUST create new session!
            act.update_session(scope, out3.session.id, notes="Great run", is_locked=True)
            out4 = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=365,
                observed_at="2026-10-10T11:30:00+00:00",
                idempotency_key="obs-4",
            )
            self.assertEqual(out4.status, "session_created")
            sessions = act.list_sessions(scope)
            self.assertEqual(len(sessions), 2)
            locked_sess = [s for s in sessions if s.id == out3.session.id][0]
            self.assertEqual(locked_sess.duration_minutes, 45)
            self.assertTrue(locked_sess.is_locked)

            # 6. Deleting session preserves provider_baselines (365) -> next sync at 365 does NOT recreate!
            act.delete_session(scope, out4.session.id)
            out5 = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=365,
                observed_at="2026-10-10T12:00:00+00:00",
                idempotency_key="obs-5",
            )
            self.assertEqual(out5.status, "no_change")
            self.assertEqual(len(act.list_sessions(scope)), 1)

            # 7. Provider decrease (regression to 300) emits no negative session and keeps baseline 365
            out6 = act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=300,
                observed_at="2026-10-10T12:30:00+00:00",
                idempotency_key="obs-6",
            )
            self.assertEqual(out6.status, "regression_ignored")
            self.assertEqual(out6.baseline_minutes, 365)

    def test_midnight_dst_and_timezone_era_daily_ledger_allocation(self):
        # 2026-03-08 US spring-forward in America/Chicago (CST -06 -> CDT -05 at 02:00 local = 08:00 UTC)
        # 2026-03-08T05:00:00Z = 2026-03-07 23:00 CST -> 2026-03-08T07:00:00Z = 2026-03-08 01:00 CST
        slices = allocate_minutes_by_local_day(
            "2026-03-08T05:00:00+00:00",
            "2026-03-08T07:00:00+00:00",
            120,
            ZoneInfo("America/Chicago"),
        )
        self.assertEqual(len(slices), 2)
        self.assertEqual(slices[0][0], "2026-03-07")
        self.assertEqual(slices[1][0], "2026-03-08")
        self.assertEqual(slices[0][1], 60.0)
        self.assertEqual(slices[1][1], 60.0)

        # Verify ledger rebuild across timezone era changes
        with tempfile.TemporaryDirectory() as tmp:
            scope, lib, act, ident = self._setup(Path(tmp))
            ident.update_timezone(
                scope,
                timezone_name="America/Chicago",
                timezone_eras=[{"tz": "America/Chicago", "effective_from": "2026-01-01T00:00:00+00:00"}],
            )
            game = lib.add_manual_game(scope, title="Celeste", platform="PC (Steam)")
            act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=0,
                observed_at="2026-03-08T04:00:00+00:00",
                idempotency_key="cel-0",
            )
            act.record_cumulative_observation(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=120,
                observed_at="2026-03-08T07:00:00+00:00",
                idempotency_key="cel-1",
            )
            ledger = {entry.date: entry.minutes_played for entry in act.get_daily_ledger(scope)}
            self.assertEqual(ledger.get("2026-03-07"), 60.0)
            self.assertEqual(ledger.get("2026-03-08"), 60.0)
            self.assertEqual(validate_genres(["Roguelike", "Indie"]), ["Roguelike"])
            self.assertEqual(validate_tags(["Indie", "Story-Rich"]), ["Indie", "Story-Rich"])

    def test_snapshot_updates_last_synced_and_supports_legacy_until_timezone_eras(self):
        from vertex.infrastructure.errors import ValidationError

        with tempfile.TemporaryDirectory() as tmp:
            scope, lib, act, ident = self._setup(Path(tmp))
            g0, _ = lib.apply_platform_snapshot(
                scope,
                external_id="steam:777",
                title="Hades II",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=0,
            )
            self.assertEqual(g0.last_synced_playtime_minutes, 0)
            g1, _ = lib.apply_platform_snapshot(
                scope,
                external_id="steam:777",
                title="Hades II",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=60,
            )
            self.assertEqual(g1.last_synced_playtime_minutes, 60)
            self.assertEqual(g1.completion_status, "Playing")

            # Unlock and mark Plan to Play; unchanged 60-min sync must NOT revert to Playing
            lib.update_game(scope, g1.id, completion_status="Plan to Play", status_locked=False)
            g2, _ = lib.apply_platform_snapshot(
                scope,
                external_id="steam:777",
                title="Hades II",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=60,
            )
            self.assertEqual(g2.completion_status, "Plan to Play")

            # Beaten & Dropped & Borrowed & None statuses are canonical
            beaten = lib.update_game(scope, g1.id, completion_status="Beaten", ownership_status="None")
            self.assertEqual(beaten.completion_status, "Beaten")
            self.assertEqual(beaten.ownership_status, "None")
            self.assertEqual(lib.get_summary_stats(scope).completed, 1)

            # Legacy `until` timezone_eras resolve historical sessions accurately
            ident.update_timezone(
                scope,
                timezone_name="Asia/Tokyo",
                timezone_eras=[
                    {"timezone": "America/New_York", "until": "2026-06-01T00:00:00+00:00", "label": "East"},
                ],
            )
            act.record_cumulative_observation(
                scope,
                game_id=g1.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=60,
                observed_at="2026-05-01T00:00:00+00:00",
                idempotency_key="tz-init",
            )
            # 2026-05-02 03:00 UTC is 2026-05-01 23:00 EDT in America/New_York (before until), NOT 2026-05-02 in Tokyo!
            out = act.record_cumulative_observation(
                scope,
                game_id=g1.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=90,
                observed_at="2026-05-02T03:00:00+00:00",
                idempotency_key="tz-play",
            )
            ledger = {e.date: e.minutes_played for e in act.get_daily_ledger(scope)}
            self.assertEqual(ledger.get("2026-05-01"), 30.0)

            with self.assertRaises(ValidationError):
                act.update_session(
                    scope,
                    out.session.id,
                    started_at="2026-05-02T05:00:00+00:00",
                    ended_at="2026-05-02T04:00:00+00:00",
                )
            with self.assertRaises(ValidationError):
                act.record_cumulative_observation(
                    scope,
                    game_id=g1.id,
                    source="Steam",
                    platform="PC (Steam)",
                    observed_minutes=100,
                    observed_at="not-an-iso-timestamp",
                    idempotency_key="bad-ts",
                )
