"""
Tests for platform credential cleaning, live connection verification endpoints,
and onboarding completion tracking.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

import app
import db
import system_db
import web_auth


class TestConnectionVerification(unittest.TestCase):
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
        self.client = TestClient(app.app, base_url="http://testserver")

        # Create active test user
        self.uid = system_db.create_user(
            username="player1",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        login_res = self.client.post(
            "/login",
            data={"username": "player1", "password": "Password123!"},
            follow_redirects=False,
        )
        self.cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        self.headers = {"Cookie": f"{web_auth.SESSION_COOKIE_NAME}={self.cookie}"}

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_credential_inputs(self):
        # Steam vanity / profile URL extraction
        self.assertEqual(app.clean_steam_input("chameleon94"), "chameleon94")
        self.assertEqual(
            app.clean_steam_input("https://steamcommunity.com/id/chameleon94/"),
            "chameleon94",
        )
        self.assertEqual(
            app.clean_steam_input("https://steamcommunity.com/profiles/76561198012345678"),
            "76561198012345678",
        )
        self.assertEqual(app.clean_steam_input("  76561198012345678  "), "76561198012345678")

        # PSN JSON extraction
        raw_json = '{"npsso": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"}'
        self.assertEqual(
            app.clean_psn_token(raw_json),
            "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        )
        self.assertEqual(
            app.clean_psn_token("  abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789  "),
            "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        )

    def test_onboarding_status_lifecycle(self):
        # Default is False
        self.assertFalse(system_db.is_user_onboarding_completed(self.uid, self.system_db_path))

        # Endpoint marks completed
        res = self.client.post("/api/user/onboarding/complete", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("onboarding_completed"))

        # Check DB reflects it
        self.assertTrue(system_db.is_user_onboarding_completed(self.uid, self.system_db_path))

        # /api/user/connections returns it
        conn_res = self.client.get("/api/user/connections", headers=self.headers)
        self.assertEqual(conn_res.status_code, 200)
        self.assertTrue(conn_res.json().get("onboarding_completed"))

    @patch("app._verify_steam_connection")
    def test_test_steam_connection_endpoint(self, mock_verify):
        mock_verify.return_value = {
            "status": "ok",
            "platform": "steam",
            "steam_id": "76561198012345678",
            "persona_name": "SteamPro",
            "avatar_url": "https://avatars.steam.com/pic.jpg",
            "message": "Steam account verified successfully",
        }

        res = self.client.post(
            "/api/user/connections/test",
            json={"platform": "steam", "credential": "https://steamcommunity.com/id/steampro"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("persona_name"), "SteamPro")
        self.assertEqual(data.get("steam_id"), "76561198012345678")

    @patch("app._verify_xbox_connection")
    def test_test_xbox_connection_endpoint(self, mock_verify):
        mock_verify.return_value = {
            "status": "ok",
            "platform": "xbox",
            "gamertag": "Chief117",
            "gamerscore": "65400",
            "avatar_url": "https://images-eds.xbox.com/pic.png",
            "message": "Xbox account verified successfully",
        }

        res = self.client.post(
            "/api/user/connections/test",
            json={"platform": "xbox", "credential": "xbl_key_12345"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("gamertag"), "Chief117")
        self.assertEqual(data.get("gamerscore"), "65400")

    @patch("app._verify_psn_connection")
    def test_test_psn_connection_endpoint(self, mock_verify):
        mock_verify.return_value = {
            "status": "ok",
            "platform": "psn",
            "online_id": "PlayStationGamer",
            "message": "PlayStation Network account verified successfully",
        }

        res = self.client.post(
            "/api/user/connections/test",
            json={
                "platform": "psn",
                "credential": '{"npsso": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"}',
            },
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("online_id"), "PlayStationGamer")

    def test_unauthenticated_connection_test_rejected(self):
        anon_client = TestClient(app.app, base_url="http://testserver")
        res = anon_client.post(
            "/api/user/connections/test",
            json={"platform": "steam", "credential": "test"},
        )
        self.assertEqual(res.status_code, 401)
