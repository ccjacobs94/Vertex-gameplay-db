"""
Unit tests for the Telemetry, Session Reconstruction & Daily Metrics Engine.
Tests the database models, session state machine, 45s cloud flush reconciliation,
daily 4:00 AM sweep, historical backfill engine, analytics aggregation, webhooks, and MCP tools.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import db
import telemetry_engine


class TestTelemetryDatabaseAndBackfill(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_telemetry.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_created(self):
        with db.get_connection(self.db_path) as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("game_sessions", tables)
            self.assertIn("daily_playtime_ledger", tables)

    def test_session_lifecycle_and_reconciliation(self):
        # 1. Create a game
        game = db.add_manual_game(
            title="Starfield",
            platform="Xbox",
            completion_status="Playing",
            playtime_hours=10.0,
            db_path=self.db_path,
        )
        game_id = game["id"]

        now = datetime(2026, 11, 8, 14, 0, 0, tzinfo=timezone.utc)
        start_iso = now.isoformat()

        # 2. Start session
        session = db.start_session(
            game_id=game_id,
            external_title_id="1794228",
            started_at=start_iso,
            detection_method="presence_poll",
            source="Xbox",
            platform="Xbox",
            db_path=self.db_path,
        )
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["game_id"], game_id)
        self.assertEqual(session["external_title_id"], "1794228")

        # 3. Touch session (heartbeat)
        touch_time = (now + timedelta(minutes=15)).isoformat()
        touched = db.touch_session(session["id"], last_seen_at=touch_time, db_path=self.db_path)
        self.assertEqual(touched["last_seen_at"], touch_time)

        # 4. End and reconcile session
        # Wall clock: 120 minutes (2.0 hours)
        # Cloud delta: 90 minutes (1.5 hours of active gameplay, 30 mins idle)
        ended_time = (now + timedelta(minutes=120)).isoformat()
        committed = db.commit_session_to_database(
            session_id=session["id"],
            ended_at=ended_time,
            wall_clock_minutes=120.0,
            active_minutes=90.0,
            idle_minutes=30.0,
            achievements_earned=2,
            status="completed",
            user_timezone="UTC",
            db_path=self.db_path,
        )
        self.assertEqual(committed["status"], "completed")
        self.assertEqual(committed["wall_clock_minutes"], 120.0)
        self.assertEqual(committed["active_minutes"], 90.0)
        self.assertEqual(committed["idle_minutes"], 30.0)
        self.assertEqual(committed["achievements_earned"], 2)

        # 5. Verify daily_playtime_ledger rollup
        with db.get_connection(self.db_path) as conn:
            ledger_rows = conn.execute(
                "SELECT * FROM daily_playtime_ledger WHERE game_id = ?", (game_id,)
            ).fetchall()
            self.assertEqual(len(ledger_rows), 1)
            row = dict(ledger_rows[0])
            self.assertEqual(row["date"], "2026-11-08")
            self.assertEqual(row["minutes_played"], 90.0)
            self.assertEqual(row["sessions_count"], 1)
            self.assertEqual(row["achievements_unlocked"], 2)
            self.assertEqual(row["first_session_hour"], 14)

    def test_historical_backfill_clustering(self):
        # Create game
        game = db.add_manual_game(
            title="Prey",
            platform="PC (Steam)",
            completion_status="Playing",
            playtime_hours=8.0,
            db_path=self.db_path,
        )
        game_id = game["id"]

        # Insert achievements with non-null unlock_time:
        # Cluster 1: 3 achievements separated by <= 180 min on 2026-10-01
        # Cluster 2: 1 achievement on 2026-10-05 (> 180 min gap from cluster 1)
        t1 = datetime(2026, 10, 1, 19, 0, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(minutes=45)
        t3 = t1 + timedelta(minutes=120)
        t4 = datetime(2026, 10, 5, 21, 0, 0, tzinfo=timezone.utc)

        achs = [
            {"external_ach_id": "ach_1", "name": "First Step", "unlocked": 1, "unlock_time": t1.isoformat()},
            {"external_ach_id": "ach_2", "name": "Engineer", "unlocked": 1, "unlock_time": t2.isoformat()},
            {"external_ach_id": "ach_3", "name": "Mimic Hunter", "unlocked": 1, "unlock_time": t3.isoformat()},
            {"external_ach_id": "ach_4", "name": "Typhon Slayer", "unlocked": 1, "unlock_time": t4.isoformat()},
        ]
        db.upsert_game_achievements(
            game_id=game_id,
            source="Steam",
            platform="PC (Steam)",
            achievements=achs,
            db_path=self.db_path,
        )

        # Run historical backfill
        backfill_result = db.backfill_historical_sessions_from_achievements(
            user_timezone="UTC",
            db_path=self.db_path,
        )
        self.assertGreaterEqual(backfill_result["sessions_created"], 2)

        # Check synthesized session boundaries per Section 4:
        # started_at = first_unlock - 30 mins
        # ended_at = last_unlock + 15 mins
        with db.get_connection(self.db_path) as conn:
            sessions = conn.execute(
                "SELECT * FROM game_sessions WHERE game_id = ? ORDER BY started_at ASC", (game_id,)
            ).fetchall()
            self.assertEqual(len(sessions), 2)

            s1 = dict(sessions[0])
            self.assertEqual(s1["detection_method"], "achievement_backfill")
            self.assertEqual(s1["status"], "reconciled")
            expected_start1 = (t1 - timedelta(minutes=30)).isoformat()
            expected_end1 = (t3 + timedelta(minutes=15)).isoformat()
            self.assertEqual(s1["started_at"], expected_start1)
            self.assertEqual(s1["ended_at"], expected_end1)
            self.assertEqual(s1["achievements_earned"], 3)

            s2 = dict(sessions[1])
            expected_start2 = (t4 - timedelta(minutes=30)).isoformat()
            expected_end2 = (t4 + timedelta(minutes=15)).isoformat()
            self.assertEqual(s2["started_at"], expected_start2)
            self.assertEqual(s2["ended_at"], expected_end2)
            self.assertEqual(s2["achievements_earned"], 1)

            # Check daily_playtime_ledger populated
            ledger = conn.execute(
                "SELECT * FROM daily_playtime_ledger WHERE game_id = ? ORDER BY date ASC", (game_id,)
            ).fetchall()
            self.assertEqual(len(ledger), 2)
            self.assertEqual(ledger[0]["date"], "2026-10-01")
            self.assertEqual(ledger[0]["achievements_unlocked"], 3)
            self.assertEqual(ledger[1]["date"], "2026-10-05")
            self.assertEqual(ledger[1]["achievements_unlocked"], 1)

        # Ensure idempotency: running backfill again should create 0 new sessions
        second_run = db.backfill_historical_sessions_from_achievements(
            user_timezone="UTC",
            db_path=self.db_path,
        )
        self.assertEqual(second_run["sessions_created"], 0)


class TestXboxSessionMonitor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_monitor.db")
        db.init_db(self.db_path)
        self.target_xuid = "2533274812345678"
        self.api_key = "mock_xbl_key"

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_system_apps_filtering(self):
        monitor = telemetry_engine.XboxSessionMonitor(
            api_key=self.api_key,
            target_xuid=self.target_xuid,
            db_path=self.db_path,
        )
        # Payload with system dashboard / edge
        payload_sys = [
            {
                "devices": [
                    {
                        "type": "XboxSeriesX",
                        "titles": [
                            {"id": 750323071, "name": "Xbox Home", "state": "Active"},
                            {"id": 328178078, "name": "Microsoft Edge", "state": "Active"},
                        ],
                    }
                ]
            }
        ]
        self.assertIsNone(monitor.extract_active_game_title(payload_sys))

        # Payload with real game
        payload_game = [
            {
                "devices": [
                    {
                        "type": "XboxSeriesX",
                        "titles": [
                            {"id": 123456, "name": "Halo Infinite", "state": "Active"},
                        ],
                    }
                ]
            }
        ]
        extracted = monitor.extract_active_game_title(payload_game)
        self.assertIsNotNone(extracted)
        self.assertEqual(extracted["titleId"], "123456")
        self.assertEqual(extracted["name"], "Halo Infinite")

    async def test_session_state_machine_and_reconciliation(self):
        # Create game in DB
        db.upsert_synced_game(
            external_id="xbox:123456",
            title="Halo Infinite",
            platform="Xbox",
            source="Xbox",
            playtime_hours=5.0,  # 300 baseline minutes
            db_path=self.db_path,
        )

        monitor = telemetry_engine.XboxSessionMonitor(
            api_key=self.api_key,
            target_xuid=self.target_xuid,
            db_path=self.db_path,
            flush_delay_seconds=0.0,  # no delay in unit test
        )

        # 1. First poll: User is playing Halo Infinite
        payload_game = [
            {
                "devices": [
                    {"titles": [{"id": 123456, "name": "Halo Infinite", "state": "Active"}]}
                ]
            }
        ]
        with patch.object(monitor, "_fetch_presence", new=AsyncMock(return_value=payload_game)):
            await monitor.poll_presence_heartbeat()

        self.assertIsNotNone(monitor.active_session)
        self.assertEqual(monitor.active_session["titleId"], "123456")
        self.assertEqual(monitor.active_session["baselineMinutes"], 300)

        # 2. Second poll: Still playing (touch session)
        with patch.object(monitor, "_fetch_presence", new=AsyncMock(return_value=payload_game)):
            await monitor.poll_presence_heartbeat()
        self.assertIsNotNone(monitor.active_session)

        # 3. Third poll: User went offline (close & reconcile)
        payload_offline = []
        stats_response = {
            "statlistscollection": [
                {
                    "stats": [
                        {"titleid": 123456, "name": "MinutesPlayed", "value": "390"}  # +90 minutes
                    ]
                }
            ]
        }
        with patch.object(monitor, "_fetch_presence", new=AsyncMock(return_value=payload_offline)), \
             patch.object(monitor, "_fetch_targeted_stats", new=AsyncMock(return_value=stats_response)), \
             patch.object(monitor, "_check_targeted_achievements", new=AsyncMock(return_value=1)):
            await monitor.poll_presence_heartbeat()

        self.assertIsNone(monitor.active_session)

        # Verify session record in DB
        with db.get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM game_sessions").fetchall()
            self.assertEqual(len(rows), 1)
            s = dict(rows[0])
            self.assertEqual(s["status"], "completed")
            self.assertEqual(s["active_minutes"], 90.0)
            self.assertEqual(s["achievements_earned"], 1)


class TestNerdStatsAndEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_nerd_stats.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_nerd_stats_kpis_and_components(self):
        # Create games and sessions
        g1 = db.add_manual_game(title="Starfield", platform="Xbox", playtime_hours=20.0, db_path=self.db_path)
        g2 = db.add_manual_game(title="Forza Horizon 5", platform="Xbox", playtime_hours=5.0, db_path=self.db_path)

        # Seed 3 consecutive days in daily_playtime_ledger with >= 15m
        now = datetime.now(timezone.utc)
        d1 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        d2 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        d3 = now.strftime("%Y-%m-%d")

        with db.get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO daily_playtime_ledger (date, game_id, platform_family, minutes_played, sessions_count, achievements_unlocked, first_session_hour) VALUES (?, ?, 'Xbox', 60.0, 1, 1, 20)",
                (d1, g1["id"]),
            )
            conn.execute(
                "INSERT INTO daily_playtime_ledger (date, game_id, platform_family, minutes_played, sessions_count, achievements_unlocked, first_session_hour) VALUES (?, ?, 'Xbox', 90.0, 1, 2, 21)",
                (d2, g1["id"]),
            )
            conn.execute(
                "INSERT INTO daily_playtime_ledger (date, game_id, platform_family, minutes_played, sessions_count, achievements_unlocked, first_session_hour) VALUES (?, ?, 'Xbox', 45.0, 1, 0, 19)",
                (d3, g2["id"]),
            )
            # Insert completed sessions with active & idle minutes
            conn.execute(
                """
                INSERT INTO game_sessions (
                    game_id, platform, source, started_at, last_seen_at, ended_at,
                    wall_clock_minutes, active_minutes, idle_minutes, achievements_earned,
                    detection_method, status
                ) VALUES (?, 'Xbox', 'Xbox', ?, ?, ?, 100.0, 90.0, 10.0, 2, 'presence_poll', 'completed')
                """,
                (g1["id"], (now - timedelta(hours=3)).isoformat(), now.isoformat(), now.isoformat()),
            )

        analytics = db.get_sessions_analytics(db_path=self.db_path)
        self.assertIn("kpis", analytics)
        kpis = analytics["kpis"]
        self.assertIn("streaks", kpis)
        self.assertEqual(kpis["streaks"]["current_streak_days"], 3)
        self.assertGreaterEqual(kpis["streaks"]["longest_streak_days"], 3)

        self.assertIn("sweet_spot", kpis)
        self.assertIn("focus_30d", kpis)
        self.assertIn("afk_menu_tax", kpis)

        self.assertIn("heatmap_365", analytics)
        self.assertIn("circadian_clock", analytics)
        self.assertIn("snack_vs_binge", analytics)
        self.assertIn("rotation_gantt_30d", analytics)


class TestTelemetryEndpointsAndMCP(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        import app
        import system_db
        import web_auth

        self.test_dir = tempfile.mkdtemp()
        self.system_db_path = str(Path(self.test_dir) / "system.db")
        self.users_dir = str(Path(self.test_dir) / "users")
        self.default_db_path = str(Path(self.test_dir) / "default.db")

        self.orig_system_db = system_db.DEFAULT_SYSTEM_DB_PATH
        self.orig_users_dir = db.USERS_DB_DIR
        self.orig_default_db = db.DEFAULT_DB_PATH

        system_db.DEFAULT_SYSTEM_DB_PATH = self.system_db_path
        db.USERS_DB_DIR = self.users_dir
        db.DEFAULT_DB_PATH = self.default_db_path

        system_db.init_system_db(self.system_db_path)
        db.init_db(self.default_db_path)

        self.client = TestClient(app.app, base_url="http://testserver")

        self.uid = system_db.create_user(
            username="gamer1",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        login_res = self.client.post(
            "/login",
            data={"username": "gamer1", "password": "Password123!"},
            follow_redirects=False,
        )
        self.session_cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        self.user_db_path = str(Path(self.users_dir) / self.uid / "games.db")
        db.init_db(self.user_db_path)

    def tearDown(self):
        import shutil
        import system_db
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_analytics_sessions_endpoint(self):
        # Create a game and completed session in user's DB
        g = db.add_manual_game("Prey", "Xbox", playtime_hours=15.0, db_path=self.user_db_path)
        now = datetime.now(timezone.utc).isoformat()
        db.record_reconciled_session(
            game_id=g["id"],
            delta_minutes=90,
            last_time_played_iso=now,
            detection_method="manual",
            db_path=self.user_db_path,
        )

        self.client.cookies.set("vertex_session", self.session_cookie)
        res = self.client.get("/api/analytics/sessions?days=30")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("kpis", data)
        self.assertIn("recent_sessions", data)
        self.assertIn("heatmap_365", data)
        self.assertIn("circadian", data)
        self.assertIn("session_histogram", data)
        self.assertIn("rotation_gantt", data)
        self.assertEqual(len(data["recent_sessions"]), 1)
        self.assertEqual(data["recent_sessions"][0]["game_id"], g["id"])

    def test_session_transition_webhook(self):
        self.client.cookies.set("vertex_session", self.session_cookie)
        # 1. Start session via webhook
        res_start = self.client.post(
            "/api/webhooks/session-transition",
            json={
                "source": "playnite",
                "event": "start",
                "title": "Starfield",
                "platform": "PC",
                "external_id": "sf_123",
            },
        )
        self.assertEqual(res_start.status_code, 200)
        start_data = res_start.json()
        self.assertEqual(start_data["status"], "started")
        sess_id = start_data["session_id"]
        self.assertIsNotNone(sess_id)

        # 2. Stop session via webhook
        res_stop = self.client.post(
            "/api/webhooks/session-transition",
            json={
                "source": "playnite",
                "event": "stop",
                "title": "Starfield",
                "platform": "PC",
                "external_id": "sf_123",
                "wall_clock_minutes": 100,
                "active_minutes": 85,
                "idle_minutes": 15,
                "achievements_unlocked": 1,
            },
        )
        self.assertEqual(res_stop.status_code, 200)
        stop_data = res_stop.json()
        self.assertEqual(stop_data["status"], "committed")
        self.assertEqual(stop_data["active_minutes"], 85)

        # 3. Direct record event via webhook
        res_rec = self.client.post(
            "/api/webhooks/session-transition",
            json={
                "source": "home_assistant",
                "event": "record",
                "title": "Forza Motorsport",
                "platform": "Xbox",
                "active_minutes": 45,
                "idle_minutes": 5,
            },
        )
        self.assertEqual(res_rec.status_code, 200)
        rec_data = res_rec.json()
        self.assertEqual(rec_data["status"], "recorded")

    def test_mcp_get_session_history(self):
        import mcp_server
        from context import set_current_user_id, reset_current_user_id

        # Seed session in default user DB
        g = db.add_manual_game("Halo Infinite", "Xbox", playtime_hours=12.0, db_path=self.user_db_path)
        db.record_reconciled_session(
            game_id=g["id"],
            delta_minutes=120,
            last_time_played_iso=datetime.now(timezone.utc).isoformat(),
            db_path=self.user_db_path,
        )

        token = set_current_user_id(self.uid)
        try:
            res = mcp_server.get_session_history(title="Halo Infinite", days=30)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["filter"]["title"], "Halo Infinite")
            self.assertIn("kpis", res)
            self.assertIn("recent_sessions", res)
            self.assertEqual(len(res["recent_sessions"]), 1)
            self.assertEqual(res["recent_sessions"][0]["game_id"], g["id"])
        finally:
            reset_current_user_id(token)

    def test_daily_reconciliation_sweep(self):
        import asyncio
        db.add_manual_game("Handheld Game", "Xbox", playtime_hours=1.0, db_path=self.user_db_path)
        with db.get_connection(self.user_db_path) as conn:
            conn.execute("UPDATE games SET external_id = 'xbox:123456' WHERE title = 'Handheld Game'")

        recent_response = [
            {
                "titleId": "123456",
                "name": "Handheld Game",
                "lastTimePlayed": datetime.now(timezone.utc).isoformat(),
            },
        ]
        stats_response = {
            "statlistscollection": [
                {
                    "stats": [
                        {"titleid": 123456, "name": "MinutesPlayed", "value": "180"}
                    ]
                }
            ]
        }
        with patch("telemetry_engine.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # Return recent titles
            mock_r1 = MagicMock()
            mock_r1.status_code = 200
            mock_r1.json.return_value = recent_response

            # Return stats
            mock_r2 = MagicMock()
            mock_r2.status_code = 200
            mock_r2.json.return_value = stats_response

            mock_client.get.return_value = mock_r1
            mock_client.post.return_value = mock_r2

            res = asyncio.run(telemetry_engine.run_daily_reconciliation_sweep(
                api_key="test_key",
                target_xuid="2533274",
                db_path=self.user_db_path,
            ))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["titles_checked"], 1)


if __name__ == "__main__":
    unittest.main()
