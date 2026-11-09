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
        self.assertIn("circadian_rhythm", data)
        self.assertIn("quit_curve", data)
        self.assertIn("rotation_focus", data)
        self.assertIn("contrarian_index", data)
        self.assertIn("genre_matrix", data)
        self.assertIn("mechanic_dna", data)
        self.assertIn("subscription_roi", data)
        self.assertIn("cross_platform", data)
        self.assertIn("trophy_pyramid", data)
        self.assertIn("almost_perfect", data)

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

        # 4. Behavioral & advanced metrics tests
        self.assertIn("circadian_rhythm", all_stats)
        cr = all_stats["circadian_rhythm"]
        self.assertIn("grid", cr)
        self.assertEqual(len(cr["grid"]), 7)  # 7 days
        self.assertEqual(len(cr["grid"][0]), 24)  # 24 hours
        self.assertIn("peak_day", cr)
        self.assertIn("peak_hour", cr)
        self.assertIn("archetype", cr)

        self.assertIn("quit_curve", all_stats)
        qc = all_stats["quit_curve"]
        self.assertIn("intervals", qc)
        self.assertIn("drop_bins", qc)
        self.assertEqual(qc["median_drop_hours"], 2.0)  # Flop game dropped at 2.0h

        self.assertIn("rotation_focus", all_stats)
        rf = all_stats["rotation_focus"]
        self.assertIn("focus_score", rf)
        self.assertIn("archetype", rf)
        self.assertIn("recent_games", rf)

        self.assertIn("contrarian_index", all_stats)
        ci = all_stats["contrarian_index"]
        self.assertIn("contrarian_score", ci)
        self.assertIn("guilty_pleasures", ci)
        self.assertIn("overhyped_misses", ci)

        self.assertIn("genre_matrix", all_stats)
        self.assertTrue(len(all_stats["genre_matrix"]) > 0)
        fps_matrix = next((g for g in all_stats["genre_matrix"] if g["genre"] == "FPS"), None)
        self.assertIsNotNone(fps_matrix)
        self.assertEqual(fps_matrix["completion_rate"], 100.0)

        self.assertIn("mechanic_dna", all_stats)
        self.assertIn("tags", all_stats["mechanic_dna"])

        self.assertIn("subscription_roi", all_stats)
        sub_roi = all_stats["subscription_roi"]
        self.assertIn("owned", sub_roi)
        self.assertIn("subscription", sub_roi)

        self.assertIn("cross_platform", all_stats)
        cp = all_stats["cross_platform"]
        self.assertIn("double_dipped_count", cp)
        self.assertIn("pairs", cp)

        self.assertIn("trophy_pyramid", all_stats)
        tp = all_stats["trophy_pyramid"]
        self.assertIn("ultra_rare", tp)
        self.assertIn("rare", tp)
        self.assertIn("common", tp)

        self.assertIn("almost_perfect", all_stats)

    def test_advanced_behavioral_and_mastery_metrics(self):
        # 1. Add game with achievements and prompt timestamps
        g1 = db.add_manual_game(
            title="BioShock",
            platform="Steam",
            completion_status="Playing",
            playtime_hours=12.0,
            critic_score=96,
            user_rating=10,  # 10/10 -> 100/100
            release_year=2007,
            genres=["FPS", "Action"],
            tags=["Story-Rich", "Sci-Fi", "Stealth"],
            db_path=self.db_path,
        )
        db.update_game(
            g1["id"],
            {"achievements_unlocked": 8, "achievements_total": 10},
            db_path=self.db_path,
        )

        # Insert achievements for BioShock: 1 ultra rare, 2 rare, 5 common
        achievements = [
            {"external_ach_id": "ach_ur", "name": "Brass Balls", "unlocked": True, "unlock_time": "2026-10-24T21:15:00Z", "rarity_pct": 2.5},
            {"external_ach_id": "ach_r1", "name": "Historian", "unlocked": True, "unlock_time": "2026-10-24T21:45:00Z", "rarity_pct": 12.0},
            {"external_ach_id": "ach_r2", "name": "Weapon Specialist", "unlocked": False, "rarity_pct": 18.0},
            {"external_ach_id": "ach_c1", "name": "Completed Welcome", "unlocked": True, "unlock_time": "2026-10-24T22:00:00Z", "rarity_pct": 85.0},
        ]
        db.upsert_game_achievements(
            g1["id"],
            source="Steam",
            platform="Steam",
            achievements=achievements,
            db_path=self.db_path,
        )

        # Insert playtime prompt
        with db.get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO playtime_prompts (game_id, previous_hours, new_hours, delta_hours, current_status, detected_at)
                VALUES (?, 8.0, 12.0, 4.0, 'Playing', '2026-10-24T23:30:00Z')
                """,
                (g1["id"],),
            )

        # 2. Add Subscription / Game Pass game (Tourist: < 1h)
        g2 = db.add_manual_game(
            title="Game Pass Sample",
            platform="Xbox",
            completion_status="Dropped",
            playtime_hours=0.5,
            critic_score=85,
            user_rating=2,  # 2/10 -> 20/100 (Overhyped miss!)
            is_game_pass=1,
            ownership_status="Subscription",
            genres=["Action"],
            tags=["Open World"],
            db_path=self.db_path,
        )

        # 3. Add Double-Dipped title across PC and Xbox
        g3 = db.add_manual_game(
            title="Hades",
            platform="PC",
            completion_status="Completed",
            playtime_hours=40.0,
            critic_score=93,
            user_rating=9,
            genres=["Roguelike", "Action"],
            tags=["Indie", "Story-Rich"],
            db_path=self.db_path,
        )
        db.update_game(g3["id"], {"canonical_id": 999}, db_path=self.db_path)

        g4 = db.add_manual_game(
            title="Hades",
            platform="Xbox",
            completion_status="Beaten",
            playtime_hours=15.0,
            critic_score=93,
            genres=["Roguelike", "Action"],
            tags=["Indie", "Story-Rich"],
            db_path=self.db_path,
        )
        db.update_game(g4["id"], {"canonical_id": 999}, db_path=self.db_path)

        # 4. Add a Dropped game with 0 hours to test Mechanic & Theme DNA drop rate bounds
        g5 = db.add_manual_game(
            title="Zero Hour Flop",
            platform="Steam",
            completion_status="Dropped",
            playtime_hours=0.0,
            genres=["Strategy"],
            tags=["Turn-Based"],
            db_path=self.db_path,
        )

        stats = db.get_habits_analytics(db_path=self.db_path)

        # Circadian rhythm: Saturday 2026-10-24 had 4 events (21h, 21h, 22h, 23h)
        cr = stats["circadian_rhythm"]
        self.assertEqual(cr["total_events"], 4)
        self.assertEqual(cr["peak_day"], "Saturday")
        self.assertIn("Weekend", cr["archetype"])

        # Trophy pyramid
        tp = stats["trophy_pyramid"]
        self.assertEqual(tp["ultra_rare"]["total"], 1)
        self.assertEqual(tp["ultra_rare"]["unlocked"], 1)
        self.assertEqual(tp["ultra_rare"]["pct"], 100.0)
        self.assertEqual(tp["rare"]["total"], 2)
        self.assertEqual(tp["rare"]["unlocked"], 1)
        self.assertEqual(tp["rare"]["pct"], 50.0)

        # Almost perfect games: BioShock has 3/4 (75%)
        ap = stats["almost_perfect"]
        self.assertEqual(len(ap), 1)
        self.assertEqual(ap[0]["title"], "BioShock")
        self.assertEqual(ap[0]["remaining"], 1)
        self.assertEqual(ap[0]["pct"], 75.0)

        # Contrarian index: BioShock (user 10 -> norm 100 vs critic 96), Game Pass Sample (user 2 -> norm 20 vs critic 85)
        ci = stats["contrarian_index"]
        overhyped_titles = [m["title"] for m in ci["overhyped_misses"]]
        self.assertIn("Game Pass Sample", overhyped_titles)
        bioshock_entry = next(r for r in ci["scatter_points"] if r["title"] == "BioShock")
        self.assertEqual(bioshock_entry["user_rating"], 10)
        self.assertEqual(bioshock_entry["user_score_norm"], 100)
        self.assertEqual(bioshock_entry["delta"], 4.0)

        # Subscription ROI: Game Pass Sample is a tourist game (< 1h)
        sub_roi = stats["subscription_roi"]
        self.assertEqual(sub_roi["subscription"]["tourist_count"], 1)
        self.assertEqual(sub_roi["subscription"]["tourist_rate"], 100.0)
        self.assertEqual(sub_roi["owned"]["count"], 4)

        # Cross-platform double-dipping: Hades is on PC and Xbox
        cp = stats["cross_platform"]
        self.assertEqual(cp["double_dipped_count"], 1)
        self.assertEqual(cp["top_titles"][0]["title"], "Hades")
        self.assertEqual(cp["double_dipped_hours"], 55.0)

        # Mechanic & Theme DNA: Turn-Based game dropped with 0h must not exceed 100% drop rate
        dna = stats["mechanic_dna"]
        tag_names = [t["tag"] for t in dna["tags"]]
        self.assertIn("Story-Rich", tag_names)
        self.assertIn("Open World", tag_names)
        self.assertIn("Turn-Based", tag_names)
        turn_based = next(t for t in dna["tags"] if t["tag"] == "Turn-Based")
        self.assertLessEqual(turn_based["drop_rate"], 100.0)
        self.assertEqual(turn_based["drop_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
