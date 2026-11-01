"""
Unit tests for Steam OpenID 2.0 authentication and 1-click account linking.
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


class TestSteamOpenID(unittest.TestCase):
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
            username="steam_player",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        login_res = self.client.post(
            "/login",
            data={"username": "steam_player", "password": "Password123!"},
            follow_redirects=False,
        )
        self.cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        self.headers = {"Cookie": f"{web_auth.SESSION_COOKIE_NAME}={self.cookie}"}

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_steam_login_redirect_url(self):
        res = self.client.get(
            "/api/auth/steam/login?return_to=/",
            headers=self.headers,
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (302, 303, 307))
        location = res.headers.get("location", "")
        self.assertTrue(location.startswith("https://steamcommunity.com/openid/login"))

        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("openid.mode"), ["checkid_setup"])
        self.assertEqual(params.get("openid.ns"), ["http://specs.openid.net/auth/2.0"])
        self.assertTrue(params.get("openid.return_to")[0].startswith("http://testserver/api/auth/steam/callback"))

    @patch("app.validate_steam_openid_response")
    def test_steam_callback_success_links_account(self, mock_validate):
        mock_validate.return_value = "76561198012345678"

        res = self.client.get(
            "/api/auth/steam/callback?openid.claimed_id=https://steamcommunity.com/openid/id/76561198012345678&return_to=/",
            headers=self.headers,
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (302, 303, 307))
        self.assertIn("steam_connected=1", res.headers.get("location", ""))

        # Check user_connections now has the linked steam_id
        conns = system_db.get_user_connections(self.uid, db_path=self.system_db_path)
        self.assertEqual(conns.get("steam_id"), "76561198012345678")

    @patch("app.validate_steam_openid_response")
    def test_steam_callback_invalid_signature_rejected(self, mock_validate):
        mock_validate.return_value = None

        res = self.client.get(
            "/api/auth/steam/callback?openid.claimed_id=bad&return_to=/",
            headers=self.headers,
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (302, 303, 307, 400))
        if res.status_code in (302, 303, 307):
            self.assertIn("error=steam_verification_failed", res.headers.get("location", ""))

    def test_steam_callback_cancel_mode(self):
        res = self.client.get(
            "/api/auth/steam/callback?openid.mode=cancel&return_to=/",
            headers=self.headers,
            follow_redirects=False,
        )
        self.assertIn(res.status_code, (302, 303, 307))
        self.assertIn("cancelled=1", res.headers.get("location", ""))
