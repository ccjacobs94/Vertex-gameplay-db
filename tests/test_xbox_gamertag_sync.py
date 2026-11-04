"""
Tests for OpenXBL Gamertag-based lookup and sync without personal OpenXBL API keys.
Verifies:
  1. Resolving Gamertags to XUID via OpenXBL friends/search and player endpoints.
  2. Syncing games and achievements using server-wide developer XBL_API_KEY + friend's Gamertag.
  3. Subaccount isolation (unconfigured subaccounts skip Xbox sync even if server key is present).
  4. Connection verification and REST API endpoints for Gamertag configuration.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

import app
import context
import db
import sync_engine
import system_db
from sync_engine import UserCredentials, resolve_xbox_gamertag, sync_xbox


class TestXboxGamertagSync(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.test_dir) / "test_games.db")
        self.system_db_path = str(Path(self.test_dir) / "system.db")
        self.users_dir = str(Path(self.test_dir) / "users")

        self.orig_system_db = system_db.DEFAULT_SYSTEM_DB_PATH
        self.orig_users_dir = db.USERS_DB_DIR
        self.orig_default_db = db.DEFAULT_DB_PATH

        system_db.DEFAULT_SYSTEM_DB_PATH = self.system_db_path
        db.USERS_DB_DIR = self.users_dir
        db.DEFAULT_DB_PATH = self.db_path

        db.init_db(self.db_path)
        system_db.init_system_db(self.system_db_path)
        self.client = TestClient(app.app, base_url="http://testserver")

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)
        context.current_user_id.set(None)

    def test_resolve_xbox_gamertag_friends_search_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "profileUsers": [
                {
                    "id": "2533274812345678",
                    "hostId": None,
                    "settings": [
                        {"id": "Gamertag", "value": "Major Nelson"},
                        {"id": "Gamerscore", "value": "125000"},
                        {"id": "GameDisplayPicRaw", "value": "https://avatar.xbox.com/pic.png"},
                    ],
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        res = asyncio.run(resolve_xbox_gamertag(mock_client, "dev_api_key", "Major Nelson"))
        self.assertIsNotNone(res)
        self.assertEqual(res["xuid"], "2533274812345678")
        self.assertEqual(res["gamertag"], "Major Nelson")
        self.assertEqual(res["gamerscore"], "125000")
        self.assertEqual(res["avatar_url"], "https://avatar.xbox.com/pic.png")

    def test_resolve_xbox_gamertag_player_endpoint_fallback(self):
        # First call (friends/search) 404s, fallback to /player/gamertag/ succeeds
        mock_err_resp = MagicMock()
        mock_err_resp.status_code = 404

        mock_ok_resp = MagicMock()
        mock_ok_resp.status_code = 200
        mock_ok_resp.json.return_value = {
            "profileUsers": [
                {
                    "id": "2533274999999999",
                    "settings": [
                        {"id": "Gamertag", "value": "Chief117"},
                        {"id": "Gamerscore", "value": "77700"},
                    ],
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_err_resp, mock_ok_resp]

        res = asyncio.run(resolve_xbox_gamertag(mock_client, "dev_api_key", "Chief117"))
        self.assertIsNotNone(res)
        self.assertEqual(res["xuid"], "2533274999999999")
        self.assertEqual(res["gamertag"], "Chief117")
        self.assertEqual(res["gamerscore"], "77700")

    def test_resolve_xbox_gamertag_not_found(self):
        mock_err_resp = MagicMock()
        mock_err_resp.status_code = 404
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_err_resp

        res = asyncio.run(resolve_xbox_gamertag(mock_client, "dev_api_key", "NonExistentUser12345"))
        self.assertIsNone(res)

    def test_sync_xbox_with_gamertag_uses_server_key(self):
        mock_resolve = {
            "xuid": "2533274812345678",
            "gamertag": "FriendGamer",
            "gamerscore": "5000",
            "avatar_url": "https://avatar.xbox.com/friend.png",
        }
        mock_ach_resp = MagicMock()
        mock_ach_resp.status_code = 200
        mock_ach_resp.json.return_value = {
            "xuid": "2533274812345678",
            "titles": [
                {
                    "titleId": "1001",
                    "name": "Forza Horizon 5",
                    "type": "Game",
                    "devices": ["XboxSeries", "Win32"],
                    "achievement": {"currentAchievements": 20, "totalAchievements": 80},
                }
            ],
        }
        mock_titles_resp = MagicMock()
        mock_titles_resp.status_code = 200
        mock_titles_resp.json.return_value = {"titles": []}

        mock_stats_resp = MagicMock()
        mock_stats_resp.status_code = 200
        mock_stats_resp.json.return_value = {"content": {"statlistscollection": []}}

        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_ach_resp, mock_titles_resp]
        mock_client.post.return_value = mock_stats_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.dict(os.environ, {"XBL_API_KEY": "server_master_openxbl_key"}, clear=False), \
             patch("sync_engine._make_async_client", return_value=mock_client) as mock_make_client, \
             patch("sync_engine.resolve_xbox_gamertag", new_callable=AsyncMock, return_value=mock_resolve) as mock_res_gt:

            creds = {"xbox_gamertag": "FriendGamer"}
            res = asyncio.run(sync_xbox(db_path=self.db_path, credentials=creds))

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["games_synced"], 1)

            # Check that client was created with server's master key
            extra_headers = mock_make_client.call_args.kwargs.get("extra_headers", {})
            self.assertEqual(extra_headers.get("X-Authorization"), "server_master_openxbl_key")

            # Check that player-specific achievements endpoint was queried using resolved XUID
            mock_res_gt.assert_called_once()
            get_urls = [call.args[0] for call in mock_client.get.call_args_list]
            self.assertTrue(any("2533274812345678" in u for u in get_urls))

            # Verify game was saved in DB
            with db.get_connection(self.db_path) as conn:
                row = conn.execute("SELECT title, platform, achievements_unlocked FROM games WHERE external_id = 'xbox:1001'").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["title"], "Forza Horizon 5")
                self.assertEqual(row["achievements_unlocked"], 20)

    def test_sync_xbox_subaccount_isolation_skips_when_no_xbox_configured(self):
        # Server has XBL_API_KEY, but subaccount hasn't configured gamertag or xbl_api_key
        sub_id = system_db.create_user(
            username="sub_friend",
            password="Password123!",
            role="user",
            status="active",
            db_path=self.system_db_path,
        )
        context.current_user_id.set(sub_id)

        with patch.dict(os.environ, {"XBL_API_KEY": "server_master_key"}, clear=False):
            res = asyncio.run(sync_xbox(db_path=self.db_path))
            self.assertEqual(res["status"], "skipped")
            self.assertIn("not configured", res["message"].lower())

    def test_verify_xbox_connection_with_gamertag(self):
        mock_resolve = {
            "xuid": "2533274812345678",
            "gamertag": "Chief117",
            "gamerscore": "88500",
            "avatar_url": "https://avatar.xbox.com/chief.png",
        }
        with patch.dict(os.environ, {"XBL_API_KEY": "server_master_key"}, clear=False), \
             patch("app.resolve_xbox_gamertag", new_callable=AsyncMock, return_value=mock_resolve):

            res = asyncio.run(app._verify_xbox_connection("Chief117"))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["gamertag"], "Chief117")
            self.assertEqual(res["gamerscore"], "88500")
            self.assertEqual(res["xuid"], "2533274812345678")
            self.assertIn("verified successfully", res["message"].lower())

    def test_user_connections_api_gamertag_flow(self):
        uid = system_db.create_user(
            username="gamer_friend",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        self.client.post("/login", data={"username": "gamer_friend", "password": "Password123!"})

        with patch.dict(os.environ, {"XBL_API_KEY": "server_key_present"}, clear=False):
            # Initially no gamertag
            res = self.client.get("/api/user/connections")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("xbox_gamertag", ""), "")
            self.assertTrue(data.get("server_defaults", {}).get("xbox"))

            # Save Xbox gamertag
            post_res = self.client.post(
                "/api/user/connections",
                json={"xbox_gamertag": "HaloPlayer42"},
            )
            self.assertEqual(post_res.status_code, 200)
            conns = post_res.json().get("connections", {})
            self.assertEqual(conns.get("xbox_gamertag"), "HaloPlayer42")

            # Check GET /api/user/connections reflects saved gamertag
            get_res = self.client.get("/api/user/connections")
            self.assertEqual(get_res.status_code, 200)
            self.assertEqual(get_res.json().get("xbox_gamertag"), "HaloPlayer42")

    @patch("app.sync_xbox", new_callable=AsyncMock)
    @patch("app.sync_all_platforms", new_callable=AsyncMock)
    def test_api_trigger_sync_platform_xbox_filter(self, mock_sync_all, mock_sync_xbox):
        uid = system_db.create_user(
            username="xbox_sync_user",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        self.client.post("/login", data={"username": "xbox_sync_user", "password": "Password123!"})

        mock_sync_xbox.return_value = {"source": "Xbox", "status": "success", "games_synced": 4}
        res = self.client.post("/api/sync?platform=xbox")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "completed")
        mock_sync_xbox.assert_called_once()
        mock_sync_all.assert_not_called()

    def test_user_connections_disconnect_xbox(self):
        uid = system_db.create_user(
            username="xbox_disconnect_user",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        self.client.post("/login", data={"username": "xbox_disconnect_user", "password": "Password123!"})

        # Save gamertag
        self.client.post("/api/user/connections", json={"xbox_gamertag": "TestGamer"})
        res = self.client.get("/api/user/connections")
        self.assertEqual(res.json().get("xbox_gamertag"), "TestGamer")

        # Disconnect by sending __CLEAR__
        disc_res = self.client.post("/api/user/connections", json={"xbox_gamertag": "__CLEAR__"})
        self.assertEqual(disc_res.status_code, 200)
        self.assertEqual(disc_res.json().get("connections", {}).get("xbox_gamertag"), "")

        # Verify it is cleared
        res_after = self.client.get("/api/user/connections")
        self.assertEqual(res_after.json().get("xbox_gamertag"), "")


if __name__ == "__main__":
    unittest.main()
