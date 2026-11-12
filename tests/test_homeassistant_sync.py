"""
Unit tests for Home Assistant history state parsing and play session ingestion.
"""

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import db
import homeassistant_sync


SAMPLE_HA_CSV = """entity_id,state,last_changed
sensor.chamelon94_now_playing,unknown,2026-11-08T12:00:00.000Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T12:31:08.519Z
sensor.chamelon94_now_playing,unknown,2026-11-08T12:31:09.778Z
sensor.chamelon94_now_playing,Prey,2026-11-08T15:50:54.454Z
sensor.chamelon94_now_playing,unknown,2026-11-08T16:06:40.369Z
sensor.chamelon94_now_playing,Prey,2026-11-08T16:07:12.559Z
sensor.chamelon94_now_playing,unknown,2026-11-08T16:22:01.447Z
sensor.chamelon94_now_playing,Prey,2026-11-08T16:22:32.233Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T16:30:24.743Z
sensor.chamelon94_now_playing,Prey,2026-11-08T16:30:26.576Z
sensor.chamelon94_now_playing,unknown,2026-11-08T17:04:18.482Z
sensor.chamelon94_now_playing,Prey,2026-11-08T17:04:49.715Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T17:30:05.439Z
sensor.chamelon94_now_playing,Prey,2026-11-08T17:30:07.040Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T18:29:46.797Z
sensor.chamelon94_now_playing,Prey,2026-11-08T18:29:48.461Z
sensor.chamelon94_now_playing,unknown,2026-11-08T18:40:35.848Z
sensor.chamelon94_now_playing,Prey,2026-11-08T18:41:06.541Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T19:29:30.649Z
sensor.chamelon94_now_playing,Prey,2026-11-08T19:29:32.111Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T20:29:12.965Z
sensor.chamelon94_now_playing,Prey,2026-11-08T20:29:14.466Z
sensor.chamelon94_now_playing,unavailable,2026-11-08T21:28:56.670Z
sensor.chamelon94_now_playing,Prey,2026-11-08T21:29:00.499Z
sensor.chamelon94_now_playing,unknown,2026-11-08T21:37:08.957Z
sensor.chamelon94_now_playing,unknown,2026-11-09T03:09:00.121Z
sensor.chamelon94_now_playing,A Quiet Place: The Road Ahead,2026-11-09T03:24:45.464Z
sensor.chamelon94_now_playing,unavailable,2026-11-09T03:27:13.874Z
sensor.chamelon94_now_playing,A Quiet Place: The Road Ahead,2026-11-09T03:27:16.799Z
sensor.chamelon94_now_playing,unknown,2026-11-09T03:33:55.158Z
sensor.chamelon94_now_playing,A Quiet Place: The Road Ahead,2026-11-09T03:34:27.616Z
sensor.chamelon94_now_playing,unavailable,2026-11-09T03:39:07.336Z
sensor.chamelon94_now_playing,A Quiet Place: The Road Ahead,2026-11-09T03:39:38.381Z
sensor.chamelon94_now_playing,unavailable,2026-11-09T03:49:26.337Z
sensor.chamelon94_now_playing,A Quiet Place: The Road Ahead,2026-11-09T03:49:56.908Z
sensor.chamelon94_now_playing,unavailable,2026-11-09T04:26:56.767Z
sensor.chamelon94_now_playing,A Quiet Place: The Road Ahead,2026-11-09T04:26:58.204Z
sensor.chamelon94_now_playing,unknown,2026-11-09T04:36:33.966Z
"""


class TestHomeAssistantHistoryParsing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_ha.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_csv_spans(self):
        spans = homeassistant_sync.parse_ha_csv_to_spans(SAMPLE_HA_CSV, max_gap_seconds=1800)
        self.assertEqual(len(spans), 2)

        # 1. Prey session
        s_prey = spans[0]
        self.assertEqual(s_prey["title"], "Prey")
        self.assertEqual(s_prey["started_at"], "2026-11-08T15:50:54.454000+00:00")
        self.assertEqual(s_prey["ended_at"], "2026-11-08T21:37:08.957000+00:00")
        # 15:50:54 to 21:37:08 is 5h 46m 14s = 346 minutes
        self.assertEqual(s_prey["duration_minutes"], 346)

        # 2. A Quiet Place session
        s_qp = spans[1]
        self.assertEqual(s_qp["title"], "A Quiet Place: The Road Ahead")
        self.assertEqual(s_qp["started_at"], "2026-11-09T03:24:45.464000+00:00")
        self.assertEqual(s_qp["ended_at"], "2026-11-09T04:36:33.966000+00:00")
        # 03:24:45 to 04:36:33 is 1h 11m 48s = 72 minutes
        self.assertEqual(s_qp["duration_minutes"], 72)

    def test_ingest_ha_spans_into_db(self):
        spans = homeassistant_sync.parse_ha_csv_to_spans(SAMPLE_HA_CSV, max_gap_seconds=1800)
        res = homeassistant_sync.ingest_ha_spans(spans, platform="Xbox", db_path=self.db_path)
        self.assertEqual(res["imported_count"], 2)

        sessions = db.get_play_sessions(db_path=self.db_path)["sessions"]
        self.assertEqual(len(sessions), 2)
        titles = {s["title"] for s in sessions}
        self.assertIn("Prey", titles)
        self.assertIn("A Quiet Place: The Road Ahead", titles)

    def test_api_import_csv(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import app
        import system_db

        sys_db = str(Path(self.temp_dir.name) / "sys.db")
        system_db.init_system_db(sys_db)
        uid = system_db.create_user(
            username="ha_player",
            password="Password123!",
            status="active",
            db_path=sys_db,
        )

        with patch("system_db.DEFAULT_SYSTEM_DB_PATH", sys_db), \
             patch("app._get_active_user_db_path", return_value=Path(self.db_path)):
            client = TestClient(app, base_url="http://testserver")
            login_resp = client.post(
                "/login",
                data={"username": "ha_player", "password": "Password123!"},
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            self.assertEqual(login_resp.status_code, 200)

            resp = client.post(
                "/api/integrations/homeassistant/import-csv",
                json={"csv_content": SAMPLE_HA_CSV, "platform": "Xbox"},
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["result"]["imported_count"], 2)

    def test_api_sync_mocked(self):
        from unittest.mock import patch, AsyncMock
        from fastapi.testclient import TestClient
        from app import app
        import system_db

        sys_db = str(Path(self.temp_dir.name) / "sys.db")
        system_db.init_system_db(sys_db)
        uid = system_db.create_user(
            username="ha_player2",
            password="Password123!",
            status="active",
            db_path=sys_db,
        )

        mock_states = [
            {"state": "unknown", "last_changed": "2026-11-10T12:00:00Z"},
            {"state": "Halo Infinite", "last_changed": "2026-11-10T13:00:00Z"},
            {"state": "unknown", "last_changed": "2026-11-10T14:30:00Z"},
        ]

        with patch("system_db.DEFAULT_SYSTEM_DB_PATH", sys_db), \
             patch("app._get_active_user_db_path", return_value=Path(self.db_path)), \
             patch("homeassistant_sync.fetch_ha_history", new=AsyncMock(return_value=mock_states)):
            client = TestClient(app, base_url="http://testserver")
            login_resp = client.post(
                "/login",
                data={"username": "ha_player2", "password": "Password123!"},
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            self.assertEqual(login_resp.status_code, 200)

            resp = client.post(
                "/api/integrations/homeassistant/sync",
                json={
                    "ha_url": "http://ha.local:8123",
                    "ha_token": "secret_token",
                    "entity_id": "sensor.chamelon94_now_playing",
                    "days_back": 3,
                },
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["result"]["imported_count"], 1)

            sessions = db.get_play_sessions(db_path=self.db_path)["sessions"]
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["title"], "Halo Infinite")
            self.assertEqual(sessions[0]["duration_minutes"], 90)


if __name__ == "__main__":
    unittest.main()
