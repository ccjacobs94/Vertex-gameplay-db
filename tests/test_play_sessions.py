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
        self.assertEqual(sessions[0]["ended_at"], t2.isoformat())

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


if __name__ == "__main__":
    unittest.main()
