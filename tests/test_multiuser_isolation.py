"""
Unit tests for multi-user library and credential isolation.
Guards that subaccounts/secondary users never inherit the server owner's
personal platform credentials (STEAM_ID, XBL_API_KEY, PSN_NPSSO) from environment variables,
and verifies that each user's library remains strictly isolated.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import context
import db
import sync_engine
import system_db
import web_auth


class TestMultiuserIsolation(unittest.TestCase):
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
        context.current_user_id.set(None)

    def tearDown(self):
        context.current_user_id.set(None)
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_subaccount_does_not_inherit_server_owner_credentials(self):
        env_vars = {
            "STEAM_API_KEY": "server_api_key_123",
            "STEAM_ID": "76561198000000001",
            "XBL_API_KEY": "owner_xbl_key_123",
            "PSN_NPSSO": "owner_npsso_token_123",
            "VERTEX_USERNAME": "owner_admin",
        }
        with patch.dict(os.environ, env_vars):
            # Create subaccount in system_db with NO connections configured
            sub_id = system_db.create_user(
                username="subaccount_bob",
                password="Password123!",
                role="user",
                status="active",
                db_path=self.system_db_path,
            )

            context.current_user_id.set(sub_id)
            creds = sync_engine._resolve_credentials()

            # Subaccount MUST NOT receive owner's personal IDs/keys
            self.assertEqual(creds.steam_id, "")
            self.assertEqual(creds.xbl_api_key, "")
            self.assertEqual(creds.psn_npsso, "")
            # Shared developer API key for Steam is available for all users
            self.assertEqual(creds.steam_api_key, "server_api_key_123")

    def test_owner_admin_retains_env_fallback(self):
        env_vars = {
            "STEAM_API_KEY": "server_api_key_123",
            "STEAM_ID": "76561198000000001",
            "XBL_API_KEY": "owner_xbl_key_123",
            "PSN_NPSSO": "owner_npsso_token_123",
            "VERTEX_USERNAME": "owner_admin",
        }
        with patch.dict(os.environ, env_vars):
            # 1. When running as owner_admin, fallback is retained
            context.current_user_id.set("owner_admin")
            creds_admin = sync_engine._resolve_credentials()
            self.assertEqual(creds_admin.steam_id, "76561198000000001")
            self.assertEqual(creds_admin.xbl_api_key, "owner_xbl_key_123")
            self.assertEqual(creds_admin.psn_npsso, "owner_npsso_token_123")

            # 2. When running un-scoped (legacy single-user mode), fallback is retained
            context.current_user_id.set(None)
            creds_legacy = sync_engine._resolve_credentials()
            self.assertEqual(creds_legacy.steam_id, "76561198000000001")
            self.assertEqual(creds_legacy.xbl_api_key, "owner_xbl_key_123")
            self.assertEqual(creds_legacy.psn_npsso, "owner_npsso_token_123")

    def test_subaccount_sync_skips_when_no_credentials(self):
        env_vars = {
            "STEAM_API_KEY": "server_api_key_123",
            "STEAM_ID": "76561198000000001",
            "XBL_API_KEY": "owner_xbl_key_123",
            "PSN_NPSSO": "owner_npsso_token_123",
            "VERTEX_USERNAME": "owner_admin",
        }
        with patch.dict(os.environ, env_vars):
            sub_id = system_db.create_user(
                username="subaccount_alice",
                password="Password123!",
                role="user",
                status="active",
                db_path=self.system_db_path,
            )

            sub_db = Path(self.users_dir) / sub_id / "games.db"
            context.current_user_id.set(sub_id)

            results = asyncio.run(sync_engine.sync_all_platforms(db_path=str(sub_db)))

            # Steam, Xbox, and PSN should all skip without error
            steam_res = next((r for r in results if r.get("source") == "Steam"), None)
            xbox_res = next((r for r in results if r.get("source") == "Xbox"), None)
            psn_res = next((r for r in results if r.get("source") == "PSN"), None)

            self.assertEqual(steam_res.get("status"), "skipped")
            self.assertEqual(xbox_res.get("status"), "skipped")
            self.assertEqual(psn_res.get("status"), "skipped")

            # Database must remain empty
            with db.get_connection(str(sub_db)) as conn:
                count = conn.execute("SELECT count(*) FROM games").fetchone()[0]
                self.assertEqual(count, 0)

    def test_subaccount_with_own_credentials_uses_only_their_credentials(self):
        env_vars = {
            "STEAM_API_KEY": "server_api_key_123",
            "STEAM_ID": "76561198000000001",
            "XBL_API_KEY": "owner_xbl_key_123",
            "PSN_NPSSO": "owner_npsso_token_123",
            "VERTEX_USERNAME": "owner_admin",
        }
        with patch.dict(os.environ, env_vars):
            sub_id = system_db.create_user(
                username="subaccount_gamer",
                password="Password123!",
                role="user",
                status="active",
                db_path=self.system_db_path,
            )
            # Gamer sets ONLY their Steam ID
            system_db.set_user_connections(
                user_id=sub_id,
                steam_id="76561198099999999",
                db_path=self.system_db_path,
            )

            context.current_user_id.set(sub_id)
            creds = sync_engine._resolve_credentials()

            self.assertEqual(creds.steam_id, "76561198099999999")
            self.assertEqual(creds.xbl_api_key, "")
            self.assertEqual(creds.psn_npsso, "")

    def test_clean_unconfigured_subaccount_libraries(self):
        import app as web_app
        # Setup: owner and an unconfigured subaccount
        owner_id = system_db.create_user(
            username="owner_admin",
            password="Password123!",
            role="admin",
            status="active",
            db_path=self.system_db_path,
        )
        sub_id = system_db.create_user(
            username="sub_victim",
            password="Password123!",
            role="user",
            status="active",
            db_path=self.system_db_path,
        )

        sub_db = Path(self.users_dir) / sub_id / "games.db"
        now = "2026-11-01T00:00:00"

        # Simulate leaked synced games in subaccount's DB
        with db.get_connection(str(sub_db)) as conn:
            conn.execute(
                "INSERT INTO games (external_id, title, platform, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("steam:20", "Team Fortress Classic", "PC (Steam)", "Steam", now, now),
            )
            conn.execute(
                "INSERT INTO games (external_id, title, platform, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("xbox:100", "Halo", "Xbox", "Xbox", now, now),
            )

        # Verify they exist
        with db.get_connection(str(sub_db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM games").fetchone()[0], 2)

        # Run cleanup
        cleaned = web_app.clean_unconfigured_subaccount_libraries(default_user="owner_admin")
        self.assertIn("sub_victim", cleaned)

        # Subaccount library should now be completely clean/empty
        with db.get_connection(str(sub_db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM games").fetchone()[0], 0)
