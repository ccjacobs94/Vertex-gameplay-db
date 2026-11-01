"""
Unit tests for Microsoft / Xbox Live OAuth 2.0 authentication scaffold.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

import app
import db
import system_db
import web_auth


class TestXboxOAuth(unittest.TestCase):
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

        self.uid = system_db.create_user(
            username="xbox_gamer",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        login_res = self.client.post(
            "/login",
            data={"username": "xbox_gamer", "password": "Password123!"},
            follow_redirects=False,
        )
        self.cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        self.headers = {"Cookie": f"{web_auth.SESSION_COOKIE_NAME}={self.cookie}"}

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_xbox_login_unconfigured_fallback(self):
        with patch.dict(os.environ, {"MICROSOFT_CLIENT_ID": ""}):
            res = self.client.get(
                "/api/auth/xbox/login",
                headers=self.headers,
                follow_redirects=False,
            )
            # When unconfigured, should inform user or redirect with note to use OpenXBL
            self.assertIn(res.status_code, (302, 303, 307, 400, 404))
            if res.status_code in (302, 303, 307):
                self.assertIn("error=xbox_oauth_not_configured", res.headers.get("location", ""))

    def test_xbox_login_redirect_when_configured(self):
        with patch.dict(os.environ, {"MICROSOFT_CLIENT_ID": "mock_client_123"}):
            res = self.client.get(
                "/api/auth/xbox/login?return_to=/",
                headers=self.headers,
                follow_redirects=False,
            )
            self.assertIn(res.status_code, (302, 303, 307))
            location = res.headers.get("location", "")
            self.assertTrue(location.startswith("https://login.live.com/oauth20_authorize.srf"))

            parsed = urlparse(location)
            params = parse_qs(parsed.query)
            self.assertEqual(params.get("client_id"), ["mock_client_123"])
            self.assertEqual(params.get("response_type"), ["code"])
            self.assertIn("XboxLive.signin", params.get("scope", [""])[0])

    @patch("app.exchange_xbox_oauth_code")
    def test_xbox_callback_success(self, mock_exchange):
        mock_exchange.return_value = {
            "status": "ok",
            "gamertag": "MasterChief",
            "xuid": "2533274812345678",
            "user_hash": "mock_uhash",
            "xsts_token": "mock_xsts_token",
        }

        with patch.dict(os.environ, {"MICROSOFT_CLIENT_ID": "mock_client_123", "MICROSOFT_CLIENT_SECRET": "mock_secret"}):
            res = self.client.get(
                "/api/auth/xbox/callback?code=mock_auth_code&return_to=/",
                headers=self.headers,
                follow_redirects=False,
            )
            self.assertIn(res.status_code, (302, 303, 307))
            self.assertIn("xbox_connected=1", res.headers.get("location", ""))
