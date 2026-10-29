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
            for required in ("status_locked", "user_rating", "notes", "favorite", "genres", "tags"):
                self.assertIn(required, cols)


if __name__ == "__main__":
    unittest.main()
