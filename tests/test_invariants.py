"""Guardrail tests for the data-preservation invariants in AGENTS.md.

These encode the "Preserve User Edits" and "Genres & Tags merge, never
replace" rules. They must never be deleted, skipped, or weakened to make a
feature pass. If a feature genuinely needs to change one of these
behaviours, the user must approve the rule change in AGENTS.md first.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import db

STEAM_ID = "steam:620"


class TestUserEditPreservation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "invariants.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _sync(self, **overrides):
        kwargs = dict(
            external_id=STEAM_ID,
            title="Portal 2",
            platform="PC",
            source="Steam",
            playtime_hours=10.0,
            db_path=self.db_path,
        )
        kwargs.update(overrides)
        return db.upsert_synced_game(**kwargs)

    # --- completion_status / status_locked ---------------------------------

    def test_manual_status_change_sets_lock(self):
        game = self._sync()
        updated = db.update_game(game["id"], {"completion_status": "Dropped"}, db_path=self.db_path)
        self.assertTrue(updated["status_locked"])
        self.assertEqual(updated["completion_status"], "Dropped")

    def test_sync_does_not_override_locked_status(self):
        game = self._sync()
        db.update_game(game["id"], {"completion_status": "Dropped"}, db_path=self.db_path)

        # A sync reporting 100% achievements would normally infer "Completed".
        after = self._sync(playtime_hours=50.0, achievements_unlocked=51, achievements_total=51)
        self.assertEqual(after["completion_status"], "Dropped")
        self.assertTrue(after["status_locked"])

    def test_sync_infers_completion_when_unlocked(self):
        # Positive control: proves the locked test above is not vacuous.
        self._sync()
        after = self._sync(achievements_unlocked=51, achievements_total=51)
        self.assertEqual(after["completion_status"], "Completed")

    def test_achievement_sync_does_not_override_locked_status(self):
        game = self._sync()
        db.update_game(game["id"], {"completion_status": "Plan to Play"}, db_path=self.db_path)
        db.upsert_game_achievements(
            game["id"],
            source="Steam",
            platform="PC",
            achievements=[{"external_ach_id": "A1", "name": "Done", "unlocked": True}],
            db_path=self.db_path,
        )
        after = db.get_game_by_id(game["id"], db_path=self.db_path)
        self.assertEqual(after["completion_status"], "Plan to Play")

    # --- user_rating / notes / favorite ------------------------------------

    def test_sync_never_overwrites_rating_notes_favorite(self):
        game = self._sync()
        db.update_game(
            game["id"],
            {"user_rating": 9, "notes": "Co-op with Sam", "favorite": True},
            db_path=self.db_path,
        )
        after = self._sync(playtime_hours=12.0, critic_score=95, release_year=2011)
        self.assertEqual(after["user_rating"], 9)
        self.assertEqual(after["notes"], "Co-op with Sam")
        self.assertTrue(after["favorite"])

    # --- genres / tags: merge, never replace -------------------------------

    def test_sync_merges_genres_and_tags_without_dropping(self):
        game = self._sync(genres=["Puzzle"], tags=["Co-op"])
        db.update_game(
            game["id"],
            {"genres": ["Puzzle", "Platformer"], "tags": ["Co-op", "Story Rich"]},
            db_path=self.db_path,
        )
        after = self._sync(genres=["Action"], tags=["Singleplayer"])
        self.assertEqual(after["genres"], ["Puzzle", "Platformer", "Action"])
        self.assertEqual(after["tags"], ["Co-op", "Story Rich", "Singleplayer"])

        # An empty sync payload must not wipe anything.
        after = self._sync(genres=[], tags=[])
        self.assertEqual(after["genres"], ["Puzzle", "Platformer", "Action"])
        self.assertEqual(after["tags"], ["Co-op", "Story Rich", "Singleplayer"])

    # --- ownership_status / ownership_locked --------------------------------
    def test_manual_ownership_change_sets_lock(self):
        game = self._sync(ownership_status="Game Pass")
        updated = db.update_game(game["id"], {"ownership_status": "Owned"}, db_path=self.db_path)
        self.assertTrue(updated["ownership_locked"])
        self.assertEqual(updated["ownership_status"], "Owned")

    def test_sync_does_not_override_locked_ownership(self):
        game = self._sync(ownership_status="Game Pass")
        db.update_game(game["id"], {"ownership_status": "Owned"}, db_path=self.db_path)
        # Sync reporting Game Pass again must not override locked Owned status
        after = self._sync(ownership_status="Game Pass")
        self.assertEqual(after["ownership_status"], "Owned")
        self.assertTrue(after["ownership_locked"])

    def test_sticky_gamepass_never_downgraded_by_subsequent_sync(self):
        # Initial sync with Game Pass = True
        game = self._sync(ownership_status="Game Pass")
        self.assertEqual(game["ownership_status"], "Game Pass")
        # Title leaves Game Pass catalog, sync reports None/Owned without user lock
        after = self._sync(ownership_status=None)
        self.assertEqual(after["ownership_status"], "Game Pass")

    def test_cross_platform_reconciliation_ownership(self):
        g_xbox = db.upsert_synced_game(
            "xbox:123", "Halo", "Xbox", "Xbox", 10.0,
            ownership_status="Game Pass", db_path=self.db_path
        )
        g_steam = db.upsert_synced_game(
            "steam:123", "Halo", "PC (Steam)", "Steam", 5.0,
            ownership_status="Owned", db_path=self.db_path
        )
        merged = db.reconcile_games([g_xbox["id"], g_steam["id"]], db_path=self.db_path)
        self.assertEqual(merged["ownership_status"], "Owned")
        self.assertTrue(merged["has_game_pass_copy"])

    def test_reconciled_game_general_update_preserves_per_copy_ownership(self):
        g_xbox = db.upsert_synced_game(
            "xbox:123", "Halo", "Xbox", "Xbox", 10.0,
            ownership_status="Game Pass", db_path=self.db_path
        )
        g_steam = db.upsert_synced_game(
            "steam:123", "Halo", "PC (Steam)", "Steam", 5.0,
            ownership_status="Owned", db_path=self.db_path
        )
        merged = db.reconcile_games([g_xbox["id"], g_steam["id"]], db_path=self.db_path)
        # General update to notes/status must NOT overwrite individual copies' ownership
        updated = db.update_game(
            merged["id"],
            {"notes": "Favorite FPS", "completion_status": "Playing"},
            db_path=self.db_path,
        )
        self.assertEqual(updated["notes"], "Favorite FPS")
        self.assertEqual(updated["completion_status"], "Playing")
        self.assertEqual(updated["ownership_status"], "Owned")
        self.assertTrue(updated["has_game_pass_copy"])

        xbox_copy = next(c for c in updated["linked_games"] if c["platform"] == "Xbox")
        steam_copy = next(c for c in updated["linked_games"] if "Steam" in c["platform"] or "PC" in c["platform"])
        self.assertEqual(xbox_copy["ownership_status"], "Game Pass")
        self.assertEqual(steam_copy["ownership_status"], "Owned")
        self.assertFalse(xbox_copy["ownership_locked"])

    def test_query_games_ownership_filter_matches_reconciled_copies(self):
        g_xbox = db.upsert_synced_game(
            "xbox:123", "Halo", "Xbox", "Xbox", 10.0,
            ownership_status="Game Pass", db_path=self.db_path
        )
        g_steam = db.upsert_synced_game(
            "steam:123", "Halo", "PC (Steam)", "Steam", 5.0,
            ownership_status="Owned", db_path=self.db_path
        )
        db.reconcile_games([g_xbox["id"], g_steam["id"]], db_path=self.db_path)

        # Standalone Game Pass game
        db.upsert_synced_game(
            "xbox:456", "Starfield", "Xbox", "Xbox", 20.0,
            ownership_status="Game Pass", db_path=self.db_path
        )

        # Standalone Owned game
        db.upsert_synced_game(
            "steam:789", "Portal 2", "PC", "Steam", 15.0,
            ownership_status="Owned", db_path=self.db_path
        )

        # Filtering by Game Pass matches both Starfield and the reconciled Halo
        gp_games = db.query_games(ownership="Game Pass", db_path=self.db_path)
        gp_titles = [g["title"] for g in gp_games]
        self.assertIn("Starfield", gp_titles)
        self.assertIn("Halo", gp_titles)
        self.assertNotIn("Portal 2", gp_titles)

        # Filtering by Owned matches both Portal 2 and the reconciled Halo
        owned_games = db.query_games(ownership="Owned", db_path=self.db_path)
        owned_titles = [g["title"] for g in owned_games]
        self.assertIn("Portal 2", owned_titles)
        self.assertIn("Halo", owned_titles)
        self.assertNotIn("Starfield", owned_titles)

    def test_multi_copy_xbox_ownership_reconciliation(self):
        # Edge case: Xbox 360 copy Owned + Xbox One copy Game Pass
        g_x360 = db.upsert_synced_game(
            "xbox:360_1", "Skyrim", "Xbox", "Xbox", 80.0,
            ownership_status="Owned", db_path=self.db_path
        )
        g_xb1 = db.upsert_synced_game(
            "xbox:xb1_2", "Skyrim", "Xbox", "Xbox", 40.0,
            ownership_status="Game Pass", db_path=self.db_path
        )
        merged = db.reconcile_games([g_x360["id"], g_xb1["id"]], db_path=self.db_path)
        self.assertEqual(merged["ownership_status"], "Owned")
        self.assertTrue(merged["has_game_pass_copy"])

    def test_manual_game_creation_locks_ownership(self):
        game = db.add_manual_game(
            title="Sea of Stars",
            platform="Xbox",
            ownership_status="Game Pass",
            db_path=self.db_path,
        )
        self.assertEqual(game["ownership_status"], "Game Pass")
        self.assertTrue(game["ownership_locked"])

    # --- sync data monotonicity --------------------------------------------

    def test_sync_never_decreases_playtime(self):
        self._sync(playtime_hours=40.0)
        after = self._sync(playtime_hours=5.0)
        self.assertEqual(after["playtime_hours"], 40.0)


class TestSchemaMigrations(unittest.TestCase):
    def test_init_db_is_idempotent_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "migrate.db")
            db.init_db(path)
            game = db.add_manual_game(
                title="Celeste", platform="Switch", user_rating=10, notes="Strawberries",
                db_path=path,
            )
            # Re-running init (as every container restart does) must be safe.
            db.init_db(path)
            db.init_db(path)
            after = db.get_game_by_id(game["id"], db_path=path)
            self.assertEqual(after["user_rating"], 10)
            self.assertEqual(after["notes"], "Strawberries")

            with sqlite3.connect(path) as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
            for required in (
                "status_locked", "ownership_status", "ownership_locked",
                "user_rating", "notes", "favorite", "genres", "tags"
            ):
                self.assertIn(required, cols)


if __name__ == "__main__":
    unittest.main()
