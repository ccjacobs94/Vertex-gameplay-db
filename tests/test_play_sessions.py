"""
Unit and integration tests for Automated Play Session Tracking (Sync Delta Engine),
REST API endpoints, and MCP Server tools.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import db
import mcp_server
from app import app


class TestPlaySessionEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_sessions.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_game_import_initializes_baseline_without_session(self):
        g = db.upsert_synced_game(
            external_id="steam:101",
            title="Elden Ring",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=20.0,
            db_path=self.db_path,
        )
        game = db.get_game_by_id(g["id"], db_path=self.db_path)
        self.assertEqual(game["last_synced_playtime_minutes"], 1200)
        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 0)

    def test_zero_or_negative_delta_does_not_create_session(self):
        g = db.upsert_synced_game(
            external_id="steam:102",
            title="Hades",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=5.0,
            db_path=self.db_path,
        )
        # Sync again with same playtime
        db.upsert_synced_game(
            external_id="steam:102",
            title="Hades",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=5.0,
            db_path=self.db_path,
        )
        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 0)

        # Sync with lower playtime (e.g. platform glitch)
        db.upsert_synced_game(
            external_id="steam:102",
            title="Hades",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=4.0,
            db_path=self.db_path,
        )
        sessions2 = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions2), 0)

    def test_positive_delta_creates_session_with_inferred_start(self):
        g = db.upsert_synced_game(
            external_id="steam:103",
            title="Celeste",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=1.0,
            db_path=self.db_path,
        )
        # Delta of 45 minutes
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        db.upsert_synced_game(
            external_id="steam:103",
            title="Celeste",
            platform="PC (Steam)",
            source="Steam",
            playtime_minutes=105, # 60 + 45
            last_played=now_iso,
            db_path=self.db_path,
        )
        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess["duration_minutes"], 45)
        self.assertEqual(sess["platform"], "PC (Steam)")
        self.assertEqual(sess["is_locked"], 0)

        # Verify started_at = ended_at - 45 mins
        st = datetime.fromisoformat(sess["started_at"].replace("Z", "+00:00"))
        et = datetime.fromisoformat(sess["ended_at"].replace("Z", "+00:00"))
        self.assertEqual(int((et - st).total_seconds() / 60), 45)

    def test_coalescing_within_30_minutes_gap(self):
        g = db.upsert_synced_game(
            external_id="xbox:201",
            title="Forza Horizon 5",
            platform="Xbox",
            source="Xbox",
            playtime_hours=10.0,
            db_path=self.db_path,
        )
        base_time = datetime(2026, 11, 10, 18, 0, 0, tzinfo=timezone.utc)
        # First delta: 30m played between 17:30 and 18:00
        db.upsert_synced_game(
            external_id="xbox:201",
            title="Forza Horizon 5",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=630,
            last_played=base_time.isoformat(),
            db_path=self.db_path,
        )
        # Second delta: 20m played between 18:15 and 18:35 (gap of 15m <= 30m)
        t2 = base_time + timedelta(minutes=35)
        db.upsert_synced_game(
            external_id="xbox:201",
            title="Forza Horizon 5",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=650,
            last_played=t2.isoformat(),
            db_path=self.db_path,
        )
        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["duration_minutes"], 50)
        expected_merged_end = (t2 + timedelta(minutes=20)).isoformat()
        self.assertEqual(sessions[0]["ended_at"], expected_merged_end)

    def test_separate_session_created_when_gap_exceeds_30_minutes(self):
        g = db.upsert_synced_game(
            external_id="xbox:202",
            title="Halo Infinite",
            platform="Xbox",
            source="Xbox",
            playtime_hours=5.0,
            db_path=self.db_path,
        )
        t1 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        db.upsert_synced_game(
            external_id="xbox:202",
            title="Halo Infinite",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=330, # +30m
            last_played=t1.isoformat(),
            db_path=self.db_path,
        )
        # Next play is 2 hours later
        t2 = t1 + timedelta(hours=2)
        db.upsert_synced_game(
            external_id="xbox:202",
            title="Halo Infinite",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=360, # +30m
            last_played=t2.isoformat(),
            db_path=self.db_path,
        )
        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 2)

    def test_delete_play_session_does_not_reset_baseline(self):
        g = db.upsert_synced_game(
            external_id="steam:104",
            title="Tunic",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=2.0,
            db_path=self.db_path,
        )
        db.upsert_synced_game(
            external_id="steam:104",
            title="Tunic",
            platform="PC (Steam)",
            source="Steam",
            playtime_minutes=150, # +30m
            db_path=self.db_path,
        )
        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 1)
        sess_id = sessions[0]["id"]

        # Delete session
        ok = db.delete_play_session(sess_id, db_path=self.db_path)
        self.assertTrue(ok)

        # Baseline must still be 150 minutes!
        game = db.get_game_by_id(g["id"], db_path=self.db_path)
        self.assertEqual(game["last_synced_playtime_minutes"], 150)

        # Syncing again at 150 minutes must NOT recreate the deleted session
        db.upsert_synced_game(
            external_id="steam:104",
            title="Tunic",
            platform="PC (Steam)",
            source="Steam",
            playtime_minutes=150,
            db_path=self.db_path,
        )
        after_sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(after_sessions), 0)


class TestPlaySessionAPIAndMCP(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_api_sessions.db")
        db.init_db(self.db_path)

        import system_db
        self.system_db_path = str(Path(self.temp_dir.name) / "system.db")
        self.orig_system_db = system_db.DEFAULT_SYSTEM_DB_PATH
        system_db.DEFAULT_SYSTEM_DB_PATH = self.system_db_path
        system_db.init_system_db(self.system_db_path)

        # Seed sample game and sessions
        self.g1 = db.upsert_synced_game(
            external_id="steam:301",
            title="Chrono Trigger",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=5.0,
            cover_url="https://example.com/chrono.jpg",
            db_path=self.db_path,
        )
        # Create 2 sessions via delta
        t1 = datetime.now(timezone.utc) - timedelta(days=2)
        db.upsert_synced_game(
            external_id="steam:301",
            title="Chrono Trigger",
            platform="PC (Steam)",
            source="Steam",
            playtime_minutes=360, # +60m
            last_played=t1.isoformat(),
            db_path=self.db_path,
        )
        t2 = datetime.now(timezone.utc) - timedelta(days=1)
        db.upsert_synced_game(
            external_id="steam:301",
            title="Chrono Trigger",
            platform="PC (Steam)",
            source="Steam",
            playtime_minutes=405, # +45m
            last_played=t2.isoformat(),
            db_path=self.db_path,
        )

    def tearDown(self):
        import system_db
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        self.temp_dir.cleanup()

    def test_api_get_sessions_list_and_stats(self):
        with patch("app._get_active_user_db_path", return_value=Path(self.db_path)), \
             patch("app.is_web_auth_enabled", return_value=False):
            client = TestClient(app)
            # List sessions
            resp = client.get("/api/sessions")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("sessions", data)
            self.assertEqual(len(data["sessions"]), 2)
            self.assertEqual(data["sessions"][0]["title"], "Chrono Trigger")
            self.assertEqual(data["sessions"][0]["cover_url"], "https://example.com/chrono.jpg")

            # Stats
            s_resp = client.get("/api/sessions/stats?days=7")
            self.assertEqual(s_resp.status_code, 200)
            s_data = s_resp.json()
            self.assertEqual(s_data["total_sessions"], 2)
            self.assertEqual(s_data["total_minutes"], 105)
            self.assertAlmostEqual(s_data["average_duration_minutes"], 52.5, places=1)
            self.assertIn("daily_playtime_series", s_data)
            self.assertIn("platform_distribution", s_data)

    def test_api_patch_and_delete_session(self):
        with patch("app._get_active_user_db_path", return_value=Path(self.db_path)), \
             patch("app.is_web_auth_enabled", return_value=False):
            client = TestClient(app)
            resp = client.get("/api/sessions")
            sessions = resp.json()["sessions"]
            sess_id = sessions[0]["id"]

            # PATCH session notes and duration
            patch_resp = client.patch(
                f"/api/sessions/{sess_id}",
                json={"notes": "Defeated Magus!", "duration_minutes": 50},
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            self.assertEqual(patch_resp.status_code, 200)
            updated = patch_resp.json()
            self.assertEqual(updated["notes"], "Defeated Magus!")
            self.assertEqual(updated["duration_minutes"], 50)
            self.assertEqual(updated["is_locked"], 1)

            # DELETE session
            del_resp = client.delete(
                f"/api/sessions/{sess_id}",
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            self.assertEqual(del_resp.status_code, 200)

            # Confirm 1 session left
            after_resp = client.get("/api/sessions")
            self.assertEqual(len(after_resp.json()["sessions"]), 1)

    def test_mcp_tools(self):
        with patch("mcp_server.get_default_db_path", return_value=self.db_path):
            # get_play_sessions
            res = mcp_server.get_play_sessions(game_title="Chrono Trigger")
            self.assertIn("sessions", res)
            self.assertEqual(len(res["sessions"]), 2)

            # get_session_analytics
            analytics = mcp_server.get_session_analytics(days_back=30)
            self.assertIn("total_sessions", analytics)
            self.assertIn("playtime_velocity", analytics)
            self.assertIn("most_played_titles", analytics)
            self.assertEqual(analytics["total_sessions"], 2)

            # update_session_notes
            sess_id = res["sessions"][0]["id"]
            up_res = mcp_server.update_session_notes(session_id=sess_id, notes="Campfire scene")
            self.assertEqual(up_res["status"], "ok")
            self.assertEqual(up_res["session"]["notes"], "Campfire scene")
            self.assertEqual(up_res["session"]["is_locked"], 1)

    def test_session_includes_unlocked_achievements_details(self):
        g = db.upsert_synced_game(
            external_id="steam:999",
            title="Hollow Knight",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=5.0,
            db_path=self.db_path,
        )
        base_time = datetime(2026, 11, 10, 14, 0, 0, tzinfo=timezone.utc)
        ach1_time = base_time - timedelta(minutes=20)
        ach2_time = base_time - timedelta(minutes=10)

        # Insert achievements into game_achievements
        db.upsert_game_achievements(
            game_id=g["id"],
            source="Steam",
            platform="PC (Steam)",
            achievements=[
                {
                    "external_ach_id": "ach_1",
                    "name": "False Knight",
                    "description": "Defeat the False Knight in the Crossroads.",
                    "unlocked": True,
                    "unlock_time": ach1_time.isoformat(),
                    "icon_url": "https://example.com/ach1.png",
                },
                {
                    "external_ach_id": "ach_2",
                    "name": "Hornet",
                    "description": "Defeat Hornet in Greenpath.",
                    "unlocked": True,
                    "unlock_time": ach2_time.isoformat(),
                    "icon_url": "https://example.com/ach2.png",
                },
                {
                    "external_ach_id": "ach_3",
                    "name": "Mantis Lords",
                    "description": "Defeat the Mantis Lords.",
                    "unlocked": False,
                    "unlock_time": None,
                },
            ],
            db_path=self.db_path,
        )

        # Sync session with +30m and 2 achievements
        db.upsert_synced_game(
            external_id="steam:999",
            title="Hollow Knight",
            platform="PC (Steam)",
            source="Steam",
            playtime_minutes=330,
            last_played=base_time.isoformat(),
            achievements_unlocked=2,
            new_achievements_count=2,
            db_path=self.db_path,
        )

        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess["achievements_unlocked"], 2)
        self.assertIn("achievements", sess)
        self.assertEqual(len(sess["achievements"]), 2)
        ach_names = [a["name"] for a in sess["achievements"]]
        self.assertIn("False Knight", ach_names)
        self.assertIn("Hornet", ach_names)
        # Check descriptions are populated
        fk_ach = next(a for a in sess["achievements"] if a["name"] == "False Knight")
        self.assertEqual(fk_ach["description"], "Defeat the False Knight in the Crossroads.")
        self.assertEqual(fk_ach["icon_url"], "https://example.com/ach1.png")

        # Verify API response also returns achievements
        with patch("app._get_active_user_db_path", return_value=Path(self.db_path)), \
             patch("app._get_active_user", return_value=(1, "testuser")):
            client = TestClient(app)
            resp = client.get(f"/api/sessions?game_id={g['id']}")
            self.assertEqual(resp.status_code, 200)
            api_sess = resp.json()["sessions"][0]
            self.assertIn("achievements", api_sess)
            self.assertEqual(len(api_sess["achievements"]), 2)
            self.assertEqual(api_sess["achievements"][0]["name"], fk_ach["name"] if api_sess["achievements"][0]["name"] == "False Knight" else "Hornet")

    def test_xbox_last_time_played_anchors_session_start_not_end(self):
        """Regression test: Xbox titleHistory.lastTimePlayed is launch/start time, not exit time."""
        launch_time = datetime(2026, 11, 10, 20, 0, 0, tzinfo=timezone.utc)
        g = db.upsert_synced_game(
            external_id="xbox:1679168855",
            title="A Quiet Place: The Road Ahead",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=60,
            db_path=self.db_path,
        )

        # User plays 90 minutes; Xbox records lastTimePlayed as the launch timestamp (20:00 UTC)
        db.upsert_synced_game(
            external_id="xbox:1679168855",
            title="A Quiet Place: The Road Ahead",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=150,
            last_played=launch_time.isoformat(),
            db_path=self.db_path,
        )

        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess["duration_minutes"], 90)
        # started_at must be launch_time (20:00), ended_at must be launch_time + 90m (21:30)
        self.assertEqual(sess["started_at"], launch_time.isoformat())
        expected_end = (launch_time + timedelta(minutes=90)).isoformat()
        self.assertEqual(sess["ended_at"], expected_end)

    def test_sessions_do_not_share_identical_fallback_achievements(self):
        """Regression test: multiple play sessions must not duplicate identical historical achievements."""
        nov8_time = datetime(2026, 11, 8, 14, 0, 0, tzinfo=timezone.utc)
        g = db.upsert_synced_game(
            external_id="xbox:5001",
            title="Gears 5",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=120,
            db_path=self.db_path,
        )
        # Store achievements unlocked days earlier
        db.upsert_game_achievements(
            game_id=g["id"],
            source="Xbox",
            platform="Xbox",
            achievements=[
                {
                    "external_ach_id": "nov8_ach1",
                    "name": "Old Chapter 1",
                    "description": "Completed chapter on Nov 8.",
                    "unlocked": True,
                    "unlock_time": nov8_time.isoformat(),
                },
                {
                    "external_ach_id": "nov8_ach2",
                    "name": "Old Chapter 2",
                    "description": "Completed chapter on Nov 8.",
                    "unlocked": True,
                    "unlock_time": (nov8_time + timedelta(minutes=20)).isoformat(),
                },
            ],
            db_path=self.db_path,
        )

        # Session 1 played on Nov 10 (+60 min, count = 2)
        nov10_start = datetime(2026, 11, 10, 15, 0, 0, tzinfo=timezone.utc)
        db.upsert_synced_game(
            external_id="xbox:5001",
            title="Gears 5",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=180,
            last_played=nov10_start.isoformat(),
            achievements_unlocked=2,
            db_path=self.db_path,
        )

        # Session 2 played on Nov 11 (+60 min, cumulative unlocked = 4, so +2 more)
        nov11_start = datetime(2026, 11, 11, 18, 0, 0, tzinfo=timezone.utc)
        db.upsert_synced_game(
            external_id="xbox:5001",
            title="Gears 5",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=240,
            last_played=nov11_start.isoformat(),
            achievements_unlocked=4,
            db_path=self.db_path,
        )

        sessions = db.get_play_sessions(game_id=g["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 2)
        sess_nov11, sess_nov10 = sessions[0], sessions[1]

        # Nov 8 achievements MUST NOT be attached to Nov 10 or Nov 11 sessions
        achs_11 = [a["name"] for a in sess_nov11.get("achievements", [])]
        achs_10 = [a["name"] for a in sess_nov10.get("achievements", [])]

        self.assertNotIn("Old Chapter 1", achs_11)
        self.assertNotIn("Old Chapter 2", achs_11)
        self.assertNotIn("Old Chapter 1", achs_10)
        self.assertNotIn("Old Chapter 2", achs_10)

        # Intersect must be empty (identical achievements can never appear across different sessions)
        self.assertEqual(len(set(achs_11) & set(achs_10)), 0)

    def test_stale_last_played_fallback_does_not_overlap_other_game_session(self):
        """Regression test: stale Xbox lastTimePlayed fallback must not overlap an active session of another game."""
        gears = db.upsert_synced_game(
            external_id="xbox:1810111157",
            title="Gears of War: E-Day",
            platform="Xbox / PC",
            source="Xbox",
            playtime_minutes=2618,
            db_path=self.db_path,
        )
        quiet = db.upsert_synced_game(
            external_id="xbox:1679168855",
            title="A Quiet Place: The Road Ahead",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=355,
            db_path=self.db_path,
        )

        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        # Afternoon session of Gears of War (started 7h31m ago, 100m duration)
        afternoon_launch = now_utc - timedelta(hours=7, minutes=31)
        db.upsert_synced_game(
            external_id="xbox:1810111157",
            title="Gears of War: E-Day",
            platform="Xbox / PC",
            source="Xbox",
            playtime_minutes=2718,  # +100m
            last_played=afternoon_launch.isoformat(),
            db_path=self.db_path,
        )

        # Evening session of A Quiet Place (started 3h48m ago, 152m duration -> ends 1h16m ago)
        evening_quiet_launch = now_utc - timedelta(hours=3, minutes=48)
        quiet_expected_end = evening_quiet_launch + timedelta(minutes=152)
        db.upsert_synced_game(
            external_id="xbox:1679168855",
            title="A Quiet Place: The Road Ahead",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=507,  # 355 + 152m
            last_played=evening_quiet_launch.isoformat(),
            db_path=self.db_path,
        )

        # Evening sync of Gears of War (+125m total delta, but Xbox API still reports afternoon_launch!)
        db.upsert_synced_game(
            external_id="xbox:1810111157",
            title="Gears of War: E-Day",
            platform="Xbox / PC",
            source="Xbox",
            playtime_minutes=2843,  # +125m
            last_played=afternoon_launch.isoformat(),
            db_path=self.db_path,
        )

        gears_sessions = db.get_play_sessions(game_id=gears["id"], db_path=self.db_path)["sessions"]
        quiet_sessions = db.get_play_sessions(game_id=quiet["id"], db_path=self.db_path)["sessions"]
        self.assertEqual(len(quiet_sessions), 1)
        self.assertEqual(len(gears_sessions), 2)

        evening_gears = gears_sessions[0]
        afternoon_gears = gears_sessions[1]
        evening_quiet = quiet_sessions[0]

        # Evening Gears of War session must NOT start before Evening A Quiet Place ended
        gears_start = datetime.fromisoformat(evening_gears["started_at"].replace("Z", "+00:00"))
        quiet_end = datetime.fromisoformat(evening_quiet["ended_at"].replace("Z", "+00:00"))
        self.assertGreaterEqual(gears_start, quiet_end)
        self.assertEqual(gears_start, quiet_expected_end)
        # Duration of evening Gears should be ~76m (1h 16m), and overflow (~49m) rolled into afternoon Gears (100 + 49 = 149m)
        self.assertAlmostEqual(evening_gears["duration_minutes"], 76, delta=2)
        self.assertAlmostEqual(afternoon_gears["duration_minutes"], 149, delta=2)

    def test_fresh_launch_timestamps_across_games_clamp_overlap(self):
        """Regression test: switching from Game A to Game B clamps Game A's end time so sessions do not overlap."""
        g1 = db.upsert_synced_game(
            external_id="xbox:6001",
            title="Game Alpha",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=100,
            db_path=self.db_path,
        )
        g2 = db.upsert_synced_game(
            external_id="xbox:6002",
            title="Game Beta",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=100,
            db_path=self.db_path,
        )

        t_alpha = datetime(2026, 11, 11, 18, 0, 0, tzinfo=timezone.utc)
        t_beta = datetime(2026, 11, 11, 19, 0, 0, tzinfo=timezone.utc)

        # Alpha reports +90m (18:00 -> 19:30 due to background idle)
        db.upsert_synced_game(
            external_id="xbox:6001",
            title="Game Alpha",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=190,
            last_played=t_alpha.isoformat(),
            db_path=self.db_path,
        )
        # Beta reports launch at 19:00 for 60m (19:00 -> 20:00)
        db.upsert_synced_game(
            external_id="xbox:6002",
            title="Game Beta",
            platform="Xbox",
            source="Xbox",
            playtime_minutes=160,
            last_played=t_beta.isoformat(),
            db_path=self.db_path,
        )

        s_alpha = db.get_play_sessions(game_id=g1["id"], db_path=self.db_path)["sessions"][0]
        s_beta = db.get_play_sessions(game_id=g2["id"], db_path=self.db_path)["sessions"][0]

        alpha_end = datetime.fromisoformat(s_alpha["ended_at"].replace("Z", "+00:00"))
        beta_start = datetime.fromisoformat(s_beta["started_at"].replace("Z", "+00:00"))
        self.assertLessEqual(alpha_end, beta_start)
        self.assertEqual(s_alpha["ended_at"], t_beta.isoformat())
        self.assertEqual(s_alpha["duration_minutes"], 60)


if __name__ == "__main__":
    unittest.main()
