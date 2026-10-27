import os
import tempfile
import unittest
from pathlib import Path

import db
import taxonomy
from taxonomy import merge_lists, remove_from_list, validate_genres, validate_tags


class TestTaxonomy(unittest.TestCase):
    def test_merge_preserves_existing_and_adds_new(self):
        # Regression test for the 2026-10-27 audit: enriching Portal 2
        # with Steam's Action/Adventure must not drop Platformer/Puzzle.
        existing = ["Action", "Adventure", "Platformer", "Puzzle"]
        merged = merge_lists(existing, validate_genres(["Action", "Adventure"]))
        self.assertEqual(merged, existing)

        # Adding a genuinely new genre appends it, in order.
        merged = merge_lists(existing, validate_genres(["Shooter"]))
        self.assertEqual(merged, existing + ["Shooter"])

    def test_merge_deduplicates_and_preserves_order(self):
        self.assertEqual(
            merge_lists(["Action"], ["Adventure", "Action", "Puzzle"]),
            ["Action", "Adventure", "Puzzle"],
        )
        self.assertEqual(merge_lists([], ["Stealth"]), ["Stealth"])

    def test_validate_genres_drops_retired_and_unknown(self):
        # Free To Play, Casual, Utilities, and Indie are not genres.
        self.assertEqual(
            validate_genres(["Action", "Free To Play", "Casual", "Utilities", "Indie"]),
            ["Action"],
        )
        # Unknown labels are held for review, never written silently.
        self.assertEqual(validate_genres(["Action", "Not A Genre"]), ["Action"])
        # Case-insensitive, canonical casing returned.
        self.assertEqual(validate_genres(["fps", "action rpg"]), ["FPS", "Action RPG"])

    def test_validate_tags_accepts_stealth_and_open_world(self):
        # Stealth is a Tag, not a Steam genre. It must survive validation.
        self.assertEqual(
            validate_tags(["Stealth", "Open World", "Co-op", "Survival Horror"]),
            ["Stealth", "Open World", "Co-op", "Survival Horror"],
        )
        self.assertEqual(validate_tags(["stealth"]), ["Stealth"])
        self.assertEqual(validate_tags(["Not A Tag"]), [])

    def test_remove_from_list(self):
        self.assertEqual(
            remove_from_list(["Action", "Adventure", "Stealth"], ["Stealth"]),
            ["Action", "Adventure"],
        )


class TestGenreTagsInDb(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_games.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tags_column_exists_after_init(self):
        game = db.add_manual_game(
            title="Portal 2",
            platform="Steam",
            completion_status="Beaten",
            playtime_hours=57.0,
            genres=["Action", "Adventure", "Platformer", "Puzzle"],
            tags=["Puzzle", "Co-op"],
            db_path=self.db_path,
        )
        self.assertEqual(game["tags"], ["Puzzle", "Co-op"])
        self.assertEqual(
            game["genres"], ["Action", "Adventure", "Platformer", "Puzzle"]
        )

    def test_update_game_tags_and_query_by_tag(self):
        game = db.add_manual_game(
            title="Tom Clancy's Splinter Cell Blacklist",
            platform="Xbox",
            completion_status="Beaten",
            playtime_hours=64.7,
            genres=["Action", "Adventure"],
            db_path=self.db_path,
        )
        # Merge Stealth in via update_game, the way update_game_genres does.
        current_tags = game.get("tags") or []
        updated = db.update_game(
            game["id"],
            {"tags": merge_lists(current_tags, ["Stealth"])},
            db_path=self.db_path,
        )
        self.assertEqual(updated["tags"], ["Stealth"])
        # Existing genres are untouched by a tags-only update.
        self.assertEqual(updated["genres"], ["Action", "Adventure"])

        found = db.query_games(tag="Stealth", db_path=self.db_path)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Tom Clancy's Splinter Cell Blacklist")

    def test_query_by_tag_also_matches_genre_for_backwards_compat(self):
        # Games that still carry Stealth as a genre (pre-migration)
        # must still be found by a tag search.
        db.add_manual_game(
            title="Dishonored 2",
            platform="Steam",
            completion_status="Beaten",
            playtime_hours=10.0,
            genres=["Action-Adventure", "Stealth"],
            db_path=self.db_path,
        )
        found = db.query_games(tag="Stealth", db_path=self.db_path)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Dishonored 2")

    def test_tags_column_migration_on_existing_db(self):
        # Simulate a pre-existing database created before the tags
        # column existed: drop the fresh schema from setUp, create
        # the old schema by hand, insert a game, then run init_db
        # and confirm the column is added and existing data is kept.
        import os
        import sqlite3

        os.remove(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                platform TEXT NOT NULL,
                source TEXT NOT NULL,
                playtime_hours REAL DEFAULT 0.0,
                last_played TEXT,
                completion_status TEXT DEFAULT 'Not Played',
                status_locked INTEGER DEFAULT 0,
                achievements_unlocked INTEGER DEFAULT 0,
                achievements_total INTEGER DEFAULT 0,
                genres TEXT DEFAULT '[]',
                critic_score INTEGER,
                user_rating INTEGER,
                release_year INTEGER,
                cover_url TEXT,
                notes TEXT DEFAULT '',
                favorite INTEGER DEFAULT 0,
                canonical_id INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO games (
                external_id, title, platform, source, genres,
                created_at, updated_at
            ) VALUES (
                'manual:steam:portal-2', 'Portal 2', 'Steam', 'Manual',
                '["Action", "Adventure", "Platformer", "Puzzle"]',
                '2026-10-27T00:00:00+00:00', '2026-10-27T00:00:00+00:00'
            );
            """
        )
        conn.commit()
        conn.close()

        db.init_db(self.db_path)
        game = db.query_games(search="Portal 2", db_path=self.db_path)[0]
        self.assertEqual(
            game["genres"], ["Action", "Adventure", "Platformer", "Puzzle"]
        )
        self.assertEqual(game["tags"], [])

    def test_library_stats_include_tags(self):
        db.add_manual_game(
            title="Portal 2",
            platform="Steam",
            completion_status="Beaten",
            playtime_hours=57.0,
            genres=["Action", "Adventure", "Platformer", "Puzzle"],
            tags=["Puzzle", "Co-op"],
            db_path=self.db_path,
        )
        stats = db.get_library_stats(db_path=self.db_path)
        self.assertIn("Puzzle", stats["all_tags"])
        self.assertIn("Co-op", stats["all_tags"])
        self.assertEqual(stats["top_tags"][0]["tag"], "Puzzle")


if __name__ == "__main__":
    unittest.main()
