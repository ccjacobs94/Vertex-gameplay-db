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

        # OpenXBL {"content": ...} envelope format (Offline)
        payload_openxbl_offline = {
            "content": {
                "xuid": "2533274978424164",
                "state": "Offline",
                "lastSeen": {
                    "deviceType": "Scarlett",
                    "titleId": "750323071",
                    "titleName": "Home",
                    "timestamp": "2026-11-09T20:45:26.5997916"
                }
            },
            "code": 200
        }
        self.assertIsNone(monitor.extract_active_game_title(payload_openxbl_offline))

        # OpenXBL {"content": ...} envelope format (Active Game)
        payload_openxbl_active = {
            "content": {
                "xuid": "2533274978424164",
                "state": "Online",
                "devices": [
                    {
                        "type": "Scarlett",
                        "titles": [
                            {
                                "id": "1679168855",
                                "name": "A Quiet Place: The Road Ahead",
                                "state": "Active"
                            }
                        ]
                    }
                ]
            },
            "code": 200
        }
        extracted_xbl = monitor.extract_active_game_title(payload_openxbl_active)
        self.assertIsNotNone(extracted_xbl)
        self.assertEqual(extracted_xbl["titleId"], "1679168855")
        self.assertEqual(extracted_xbl["name"], "A Quiet Place: The Road Ahead")

    # Regression (2026-11-19): Xbox presence "Online" non-game ghost entries
    def test_legacy_extract_active_game_title_filters_online_and_background(self):
        monitor = telemetry_engine.XboxSessionMonitor(
            api_key=self.api_key,
            target_xuid=self.target_xuid,
            db_path=self.db_path,
        )
        for tid, name in [
            ("704208617", "Online"),
            ("1022622766", "Online"),
            ("1626579248", ""),
            ("994947109", "Xbox Assist"),
            ("999999", "Xbox Title 1626579248"),
        ]:
            self.assertTrue(telemetry_engine.is_system_app(tid, name))

        for tid, name in [
            ("1001", "Grand Theft Auto Online"),
            ("1003", "Homefront"),
            ("1005", "Edge of Eternity"),
        ]:
            self.assertFalse(telemetry_engine.is_system_app(tid, name))

        for bad_payload in [
            {"state": "Online", "devices": [{"titles": [{"id": "704208617", "name": "Online", "state": "Active"}]}]},
            {"state": "Online", "devices": [{"titles": [{"id": "1022622766", "name": "Online", "state": "Active"}]}]},
            {"state": "Online", "devices": [{"titles": [{"id": "1626579248", "name": "", "state": "Active"}]}]},
            {"state": "Online", "devices": [{"titles": [{"id": "994947109", "name": "Xbox Assist", "state": "Active"}]}]},
            {
                "state": "Online",
                "devices": [{"titles": [{"id": "1001", "name": "Halo Infinite", "state": "Active", "placement": "Background"}]}],
            },
        ]:
            self.assertIsNone(monitor.extract_active_game_title(bad_payload))

        prefer_full = {
            "state": "Online",
            "devices": [
                {"titles": [{"id": "1003", "name": "Homefront", "state": "Active"}]},
                {"titles": [{"id": "2082978535", "name": "Halo: Campaign Evolved", "state": "Active", "placement": "Full"}]},
            ],
        }
        picked = monitor.extract_active_game_title(prefer_full)
        self.assertIsNotNone(picked)
        self.assertEqual(picked["titleId"], "2082978535")
        self.assertEqual(picked["name"], "Halo: Campaign Evolved")

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

    def test_circadian_respects_user_timezone_and_avoids_min_hour_collapse(self):
        """
        Regression test (2026-11-13):
        1) Ledger rows originally baked in UTC must not lock circadian hours to UTC
           when user_timezone is passed to get_sessions_analytics().
        2) Multiple sessions on the same UTC date must not collapse all minutes into
           MIN(first_session_hour).
        """
        g = db.add_manual_game(title="Dispatch", platform="Xbox", playtime_hours=10.0, db_path=self.db_path)

        # Simulate initial backfill/recording in UTC before user set America/Chicago:
        # Session 1: 2025-12-04 01:47 UTC -> 2025-12-04 03:47 UTC (120m)
        #            In America/Chicago (CST, UTC-6): Dec 3, 19:47 - 21:47
        # Session 2: 2025-12-04 18:32 UTC -> 2025-12-05 00:32 UTC (360m)
        #            In America/Chicago (CST, UTC-6): Dec 4, 12:32 - 18:32
        db.record_reconciled_session(
            game_id=g["id"],
            delta_minutes=120.0,
            last_time_played_iso="2025-12-04T03:47:00+00:00",
            detection_method="achievement_backfill",
            user_timezone="UTC",
            db_path=self.db_path,
        )
        db.record_reconciled_session(
            game_id=g["id"],
            delta_minutes=360.0,
            last_time_played_iso="2025-12-05T00:32:00+00:00",
            detection_method="achievement_backfill",
            user_timezone="UTC",
            db_path=self.db_path,
        )

        # In America/Chicago, ZERO minutes occurred between 12am and 4am!
        analytics_chi = db.get_sessions_analytics(
            user_timezone="America/Chicago",
            db_path=self.db_path,
        )
        chi_hours = analytics_chi["circadian"]["hours"]
        self.assertEqual(sum(chi_hours[0:4]), 0)
        self.assertGreater(sum(chi_hours[12:19]), 300)
        self.assertGreater(sum(chi_hours[19:22]), 100)
        self.assertNotIn(analytics_chi["circadian"]["peak_hour"], ("12 AM", "1 AM", "2 AM", "3 AM"))

        # Even in UTC, Session 2's 360m (18:32-00:32 UTC) must NOT be dumped into hour 1!
        analytics_utc = db.get_sessions_analytics(
            user_timezone="UTC",
            db_path=self.db_path,
        )
        utc_hours = analytics_utc["circadian"]["hours"]
        self.assertLessEqual(utc_hours[1], 75)
        self.assertGreater(sum(utc_hours[18:24]), 250)

    def test_circadian_respects_game_and_platform_filters(self):
        """Regression test (2026-11-13): circadian clock must honor game_id/platform filters."""
        g1 = db.add_manual_game(title="Night Game", platform="Xbox", playtime_hours=2.0, db_path=self.db_path)
        g2 = db.add_manual_game(title="Noon Game", platform="PC (Steam)", playtime_hours=2.0, db_path=self.db_path)

        db.record_reconciled_session(
            game_id=g1["id"],
            delta_minutes=60.0,
            last_time_played_iso="2026-10-10T02:00:00+00:00",
            user_timezone="UTC",
            db_path=self.db_path,
        )
        db.record_reconciled_session(
            game_id=g2["id"],
            delta_minutes=60.0,
            last_time_played_iso="2026-10-10T14:00:00+00:00",
            user_timezone="UTC",
            db_path=self.db_path,
        )

        filtered = db.get_sessions_analytics(
            platform="Steam",
            user_timezone="UTC",
            db_path=self.db_path,
        )
        hours = filtered["circadian"]["hours"]
        self.assertEqual(hours[1], 0)
        self.assertEqual(hours[13], 60)

    def test_rebuild_daily_playtime_ledger_realigns_dates_and_hours(self):
        """Regression test (2026-11-13): rebuilding ledger updates stale UTC dates/hours."""
        g = db.add_manual_game(title="Alan Wake 2", platform="Xbox", playtime_hours=3.0, db_path=self.db_path)
        # 2025-10-11 02:11 UTC -> 2025-10-11 03:11 UTC (in America/Chicago CDT UTC-5: Oct 10, 21:11 - 22:11)
        db.record_reconciled_session(
            game_id=g["id"],
            delta_minutes=60.0,
            last_time_played_iso="2025-10-11T03:11:00+00:00",
            user_timezone="UTC",
            db_path=self.db_path,
        )

        with db.get_connection(self.db_path) as conn:
            before = dict(conn.execute("SELECT * FROM daily_playtime_ledger WHERE game_id = ?", (g["id"],)).fetchone())
            self.assertEqual(before["date"], "2025-10-11")
            self.assertEqual(before["first_session_hour"], 2)

        db.rebuild_daily_playtime_ledger(
            user_timezone="America/Chicago",
            db_path=self.db_path,
        )

        with db.get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM daily_playtime_ledger WHERE game_id = ?", (g["id"],)).fetchall()
            self.assertEqual(len(rows), 1)
            after = dict(rows[0])
            self.assertEqual(after["date"], "2025-10-10")
            self.assertEqual(after["first_session_hour"], 21)
            self.assertEqual(after["minutes_played"], 60.0)


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
            "content": {
                "statlistscollection": [
                    {
                        "stats": [
                            {"titleid": "123456", "name": "MinutesPlayed", "value": "180"}
                        ]
                    }
                ]
            }
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
            self.assertEqual(res["reconciled_count"], 1)

            with db.get_connection(self.user_db_path) as conn:
                sess_row = conn.execute("SELECT * FROM game_sessions WHERE detection_method = 'daily_reconciliation'").fetchone()
                self.assertIsNotNone(sess_row)
                self.assertEqual(sess_row["status"], "reconciled")
                self.assertEqual(sess_row["active_minutes"], 180.0)

    def test_timezone_patch_rebuilds_ledger_and_webhooks_respect_timezone(self):
        """Regression test (2026-11-13): PATCH /api/account/timezone rebuilds daily_playtime_ledger."""
        self.client.cookies.set("vertex_session", self.session_cookie)
        g = db.add_manual_game("Control", "Xbox", playtime_hours=4.0, db_path=self.user_db_path)
        # 2025-12-04 02:00 UTC -> 03:00 UTC (60m). In America/Chicago (UTC-6): Dec 3, 20:00 - 21:00
        db.record_reconciled_session(
            game_id=g["id"],
            delta_minutes=60.0,
            last_time_played_iso="2025-12-04T03:00:00+00:00",
            user_timezone="UTC",
            db_path=self.user_db_path,
        )

        res = self.client.patch(
            "/api/account/timezone",
            json={"timezone": "America/Chicago", "timezone_eras": []},
        )
        self.assertEqual(res.status_code, 200)

        with db.get_connection(self.user_db_path) as conn:
            row = conn.execute("SELECT * FROM daily_playtime_ledger WHERE game_id = ?", (g["id"],)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["date"], "2025-12-03")
            self.assertEqual(row["first_session_hour"], 20)

        # Also verify webhook record uses user's saved timezone
        res_rec = self.client.post(
            "/api/webhooks/session-transition",
            json={
                "source": "playnite",
                "event": "record",
                "title": "Prey",
                "platform": "PC",
                "active_minutes": 60,
                "ended_at": "2025-12-05T03:00:00+00:00",
            },
        )
        self.assertEqual(res_rec.status_code, 200)
        with db.get_connection(self.user_db_path) as conn:
            prey = conn.execute("SELECT id FROM games WHERE title = 'Prey'").fetchone()
            prey_ledger = conn.execute("SELECT * FROM daily_playtime_ledger WHERE game_id = ?", (prey["id"],)).fetchone()
            self.assertEqual(prey_ledger["date"], "2025-12-04")
            self.assertEqual(prey_ledger["first_session_hour"], 20)


if __name__ == "__main__":
    unittest.main()
