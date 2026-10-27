import os
import tempfile
import unittest
from pathlib import Path

import db


class TestHabitsAnalytics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_games.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_db_analytics(self):
        data = db.get_habits_analytics(db_path=self.db_path)
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["total_games"], 0)
        self.assertEqual(data["summary"]["total_hours"], 0.0)
        self.assertEqual(data["summary"]["completion_rate"], 0.0)
        self.assertEqual(data["summary"]["drop_rate"], 0.0)
        self.assertEqual(data["platforms"], [])
        self.assertEqual(data["genres"], [])
        self.assertEqual(data["hall_of_fame"], [])

    def test_populated_analytics_and_filtering(self):
        # Insert test games
        g1 = db.add_manual_game(
            title="Halo Infinite",
            platform="Xbox",
            completion_status="Beaten",
            playtime_hours=45.0,
            critic_score=87,
            user_rating=9,
            release_year=2021,
            genres=["Action", "FPS"],
            db_path=self.db_path,
        )
        g2 = db.add_manual_game(
            title="Half-Life 2",
            platform="Steam",
            completion_status="Completed",
            playtime_hours=25.0,
            critic_score=96,
            user_rating=10,
            release_year=2004,
            genres=["Action", "FPS"],
            favorite=True,
            db_path=self.db_path,
        )
        g3 = db.add_manual_game(
            title="Flop Game",
            platform="Steam",
            completion_status="Dropped",
            playtime_hours=2.0,
            critic_score=50,
            user_rating=3,
            release_year=2018,
            genres=["Casual"],
            db_path=self.db_path,
        )
        g4 = db.add_manual_game(
            title="Elden Ring",
            platform="Xbox",
            completion_status="Plan to Play",
            playtime_hours=0.0,
            critic_score=96,
            release_year=2022,
            genres=["Action RPG", "Action"],
            db_path=self.db_path,
        )

        # 1. Overall analytics
        all_stats = db.get_habits_analytics(db_path=self.db_path)
        summary = all_stats["summary"]
        self.assertEqual(summary["total_games"], 4)
        self.assertEqual(summary["total_hours"], 72.0)
        self.assertEqual(summary["played_games"], 3)
        self.assertEqual(summary["beaten_count"], 2)
        self.assertEqual(summary["dropped_count"], 1)
        self.assertEqual(summary["shelf_of_shame_count"], 1)
        # avg beaten hours: (45 + 25) / 2 = 35.0
        self.assertEqual(summary["avg_beaten_hours"], 35.0)
        # avg dropped hours: 2.0 / 1 = 2.0
        self.assertEqual(summary["avg_dropped_hours"], 2.0)
        # completion rate: 2 beaten / 3 played = 66.7%
        self.assertEqual(summary["completion_rate"], 66.7)

        # Platforms check
        plat_names = [p["name"] for p in all_stats["platforms"]]
        self.assertIn("Xbox", plat_names)
        self.assertIn("Steam", plat_names)
        xbox_stat = next(p for p in all_stats["platforms"] if p["name"] == "Xbox")
        self.assertEqual(xbox_stat["count"], 2)
        self.assertEqual(xbox_stat["hours"], 45.0)

        # Eras check: 2021/2022 -> Modern (2), 2018 -> 2010s (1), 2004 -> 2000s (1)
        era_counts = {e["key"]: e["count"] for e in all_stats["eras"]}
        self.assertEqual(era_counts["modern"], 2)
        self.assertEqual(era_counts["golden"], 1)
        self.assertEqual(era_counts["classics"], 1)

        # Backlog gems check: Elden Ring (critic_score 96, Plan to Play)
        self.assertEqual(len(all_stats["ratings"]["backlog_gems"]), 1)
        self.assertEqual(all_stats["ratings"]["backlog_gems"][0]["title"], "Elden Ring")

        # 2. Filter by platform "Steam"
        steam_stats = db.get_habits_analytics(platform="Steam", db_path=self.db_path)
        self.assertEqual(steam_stats["summary"]["total_games"], 2)
        self.assertEqual(steam_stats["summary"]["total_hours"], 27.0)

        # 3. Filter by favorites
        fav_stats = db.get_habits_analytics(favorite_only=True, db_path=self.db_path)
        self.assertEqual(fav_stats["summary"]["total_games"], 1)
        self.assertEqual(fav_stats["summary"]["total_hours"], 25.0)
        self.assertEqual(fav_stats["hall_of_fame"][0]["title"], "Half-Life 2")


if __name__ == "__main__":
    unittest.main()
