"""
Tests for Xbox Play Anywhere library tracking, database layer, sync engine,
REST endpoints, and MCP server tools.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import app
import db
import system_db
import web_auth


class TestPlayAnywhereDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        db.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_schema_and_defaults(self):
        # Adding a game without is_play_anywhere defaults to False
        g = db.add_manual_game(
            title="Standard Game",
            platform="Xbox",
            playtime_hours=5.0,
            db_path=self.db_path,
        )
        self.assertIn("is_play_anywhere", g)
        self.assertFalse(g["is_play_anywhere"])

        fetched = db.get_game_by_id(g["id"], db_path=self.db_path)
        self.assertFalse(fetched["is_play_anywhere"])

    def test_create_and_update_play_anywhere(self):
        g = db.add_manual_game(
            title="Forza Horizon 5",
            platform="Xbox",
            is_play_anywhere=True,
            db_path=self.db_path,
        )
        self.assertTrue(g["is_play_anywhere"])

        # Update to False
        up = db.update_game(g["id"], {"is_play_anywhere": False}, db_path=self.db_path)
        self.assertFalse(up["is_play_anywhere"])

        # Update back to True
        up2 = db.update_game(g["id"], {"is_play_anywhere": True}, db_path=self.db_path)
        self.assertTrue(up2["is_play_anywhere"])

    def test_upsert_synced_game_and_preservation(self):
        # 1. Sync reports is_play_anywhere=True
        synced = db.upsert_synced_game(
            external_id="xbox:12345",
            title="Halo Infinite",
            platform="Xbox / PC",
            source="Xbox",
            is_play_anywhere=True,
            db_path=self.db_path,
        )
        self.assertTrue(synced["is_play_anywhere"])

        # 2. Existing game with user-set is_play_anywhere=True is preserved if sync has False
        disc_game = db.add_manual_game(
            title="Gears of War 4",
            platform="Xbox",
            is_play_anywhere=True,
            db_path=self.db_path,
        )
        self.assertTrue(disc_game["is_play_anywhere"])

        # Update external_id to match an incoming sync
        with db.get_connection(self.db_path) as conn:
            conn.execute("UPDATE games SET external_id = 'xbox:99999' WHERE id = ?", (disc_game["id"],))

        # Automated sync runs with is_play_anywhere=False
        after_sync = db.upsert_synced_game(
            external_id="xbox:99999",
            title="Gears of War 4",
            platform="Xbox",
            source="Xbox",
            is_play_anywhere=False,
            db_path=self.db_path,
        )
        # Invariant: manual true is preserved
        self.assertTrue(after_sync["is_play_anywhere"])

    def test_query_games_filter(self):
        db.add_manual_game(
            title="XPA Game 1",
            platform="Xbox",
            is_play_anywhere=True,
            db_path=self.db_path,
        )
        db.add_manual_game(
            title="Non-XPA Game",
            platform="Xbox",
            is_play_anywhere=False,
            db_path=self.db_path,
        )
        db.add_manual_game(
            title="Steam Only Game",
            platform="PC (Steam)",
            is_play_anywhere=False,
            db_path=self.db_path,
        )

        all_games = db.query_games(db_path=self.db_path)
        self.assertEqual(len(all_games), 3)

        xpa_games = db.query_games(is_play_anywhere=True, db_path=self.db_path)
        self.assertEqual(len(xpa_games), 1)
        self.assertEqual(xpa_games[0]["title"], "XPA Game 1")

        non_xpa = db.query_games(is_play_anywhere=False, db_path=self.db_path)
        self.assertEqual(len(non_xpa), 2)

    def test_unified_copies_aggregation(self):
        # Create Xbox copy with is_play_anywhere=True
        g_xbox = db.upsert_synced_game(
            external_id="xbox:sea_of_thieves",
            title="Sea of Thieves",
            platform="Xbox",
            source="Xbox",
            is_play_anywhere=True,
            db_path=self.db_path,
        )
        # Create Steam copy with is_play_anywhere=False
        g_steam = db.upsert_synced_game(
            external_id="steam:sea_of_thieves",
            title="Sea of Thieves",
            platform="PC (Steam)",
            source="Steam",
            is_play_anywhere=False,
            db_path=self.db_path,
        )
        # Reconcile / link them
        db.reconcile_games([g_xbox["id"], g_steam["id"]], db_path=self.db_path)

        unified = db.query_games(db_path=self.db_path)
        self.assertEqual(len(unified), 1)
        self.assertTrue(unified[0]["is_play_anywhere"])


class TestPlayAnywhereAPI(unittest.TestCase):
    def setUp(self):
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

        # Create active test user and login
        self.uid = system_db.create_user(
            username="player1",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        self.user_db_path = str(Path(self.users_dir) / str(self.uid) / "games.db")
        os.makedirs(Path(self.user_db_path).parent, exist_ok=True)
        db.init_db(self.user_db_path)

        login_res = self.client.post(
            "/login",
            data={"username": "player1", "password": "Password123!"},
            follow_redirects=False,
        )
        self.cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        self.headers = {
            "Cookie": f"{web_auth.SESSION_COOKIE_NAME}={self.cookie}",
            "Sec-Fetch-Site": "same-origin",
        }

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_api_filter_and_patch(self):
        g1 = db.add_manual_game(
            title="Ori and the Will of the Wisps",
            platform="Xbox",
            is_play_anywhere=True,
            db_path=self.user_db_path,
        )
        g2 = db.add_manual_game(
            title="Dark Souls",
            platform="PlayStation",
            is_play_anywhere=False,
            db_path=self.user_db_path,
        )

        # Query API without filter
        resp = self.client.get("/api/games", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 2)

        # Query API with is_play_anywhere=true
        resp = self.client.get("/api/games?is_play_anywhere=true", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        games = resp.json()["games"]
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["id"], g1["id"])
        self.assertTrue(games[0]["is_play_anywhere"])

        # Query API with is_play_anywhere=false
        resp = self.client.get("/api/games?is_play_anywhere=false", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        games = resp.json()["games"]
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["id"], g2["id"])

        # Patch game to toggle is_play_anywhere
        patch_resp = self.client.patch(
            f"/api/games/{g2['id']}",
            json={"is_play_anywhere": True},
            headers=self.headers,
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.json()["game"]["is_play_anywhere"])


class TestPlayAnywhereSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        db.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_sync_xbox_auto_detects_play_anywhere(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from sync_engine import sync_xbox

        mock_ach_resp = MagicMock()
        mock_ach_resp.status_code = 200
        mock_ach_resp.json.return_value = {
            "xuid": "xuid_123",
            "titles": [
                {
                    "titleId": "111",
                    "name": "Play Anywhere Game",
                    "type": "Game",
                    "devices": ["XboxSeries", "PC"],
                    "achievement": {"currentAchievements": 1, "totalAchievements": 10},
                },
                {
                    "titleId": "222",
                    "name": "Console Only Game",
                    "type": "Game",
                    "devices": ["XboxSeries"],
                    "achievement": {"currentAchievements": 0, "totalAchievements": 10},
                },
            ],
        }
        mock_titles_resp = MagicMock()
        mock_titles_resp.status_code = 200
        mock_titles_resp.json.return_value = {"xuid": "xuid_123", "titles": []}
        mock_stats_resp = MagicMock()
        mock_stats_resp.status_code = 200
        mock_stats_resp.json.return_value = {"content": {"statlistscollection": []}}

        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_ach_resp, mock_titles_resp]
        mock_client.post.return_value = mock_stats_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.dict(os.environ, {"XBL_API_KEY": ""}, clear=False), patch(
            "sync_engine._make_async_client", return_value=mock_client
        ):
            creds = {"xbl_api_key": "user_custom_xbl_key"}
            res = asyncio.run(sync_xbox(db_path=self.db_path, credentials=creds))
            self.assertEqual(res["games_synced"], 2)

        g1 = db.query_games(search="Play Anywhere Game", db_path=self.db_path)[0]
        self.assertTrue(g1["is_play_anywhere"])

        g2 = db.query_games(search="Console Only Game", db_path=self.db_path)[0]
        self.assertFalse(g2["is_play_anywhere"])


class TestPlayAnywhereMCP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        db.init_db(self.db_path)

        self.orig_default_db = db.DEFAULT_DB_PATH
        db.DEFAULT_DB_PATH = self.db_path

    def tearDown(self):
        db.DEFAULT_DB_PATH = self.orig_default_db
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_mcp_tools_play_anywhere(self):
        import mcp_server

        db.add_manual_game(
            title="Starfield",
            platform="Xbox",
            completion_status="Playing",
            is_play_anywhere=True,
            db_path=self.db_path,
        )
        db.add_manual_game(
            title="Bloodborne",
            platform="PlayStation",
            completion_status="Plan to Play",
            is_play_anywhere=False,
            db_path=self.db_path,
        )

        with patch("mcp_server.query_games", wraps=lambda **kwargs: db.query_games(**{**kwargs, "db_path": self.db_path})):
            # search_game_library with is_play_anywhere
            pa_games = mcp_server.search_game_library(is_play_anywhere=True)
            self.assertEqual(len(pa_games), 1)
            self.assertEqual(pa_games[0]["title"], "Starfield")

            non_pa = mcp_server.search_game_library(is_play_anywhere=False)
            self.assertEqual(len(non_pa), 1)
            self.assertEqual(non_pa[0]["title"], "Bloodborne")

        with patch("mcp_server.query_games", wraps=lambda **kwargs: db.query_games(**{**kwargs, "db_path": self.db_path})), patch(
            "mcp_server.update_game", wraps=lambda gid, up: db.update_game(gid, up, db_path=self.db_path)
        ):
            # update_game_progress toggling is_play_anywhere
            up_res = mcp_server.update_game_progress(title="Bloodborne", is_play_anywhere=True)
            self.assertTrue(up_res["game"]["is_play_anywhere"])

            check = db.query_games(search="Bloodborne", db_path=self.db_path)[0]
            self.assertTrue(check["is_play_anywhere"])

