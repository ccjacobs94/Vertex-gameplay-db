"""
Tests for scheduled background multi-tenant sync loop scoping.
Ensures sync loop iterates through all active users and sets current_user_id.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app
import db
import system_db
from context import get_current_user_id


class TestScheduledSyncUserScope(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.system_db_path = str(Path(self.test_dir) / "system.db")
        self.users_dir = str(Path(self.test_dir) / "users")
        self.default_db_path = str(Path(self.test_dir) / "default_games.db")

        self.orig_system_db = system_db.DEFAULT_SYSTEM_DB_PATH
        self.orig_users_dir = db.USERS_DB_DIR
        self.orig_default_db = db.DEFAULT_DB_PATH

        system_db.DEFAULT_SYSTEM_DB_PATH = self.system_db_path
        db.USERS_DB_DIR = self.users_dir
        db.DEFAULT_DB_PATH = self.default_db_path

        system_db.init_system_db(self.system_db_path)

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_scheduled_sync_iterates_all_active_users(self):
        # Setup 2 users in system_db
        system_db.ensure_user("alice", db_path=self.system_db_path)
        system_db.ensure_user("bob", db_path=self.system_db_path)

        observed_user_ids = []

        async def fake_sync():
            uid = get_current_user_id()
            observed_user_ids.append(uid)
            # Verify the connection connects to the specific user's DB
            now = "2026-11-01T00:00:00"
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO games (external_id, title, platform, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"game_{uid}", f"Title for {uid}", "Steam", "steam", now, now),
                )
            return {"synced": True}

        with patch("app.sync_all_platforms", side_effect=fake_sync):
            results = await app.run_scheduled_sync_iteration()

        self.assertIn("alice", observed_user_ids)
        self.assertIn("bob", observed_user_ids)
        self.assertEqual(len(observed_user_ids), 2)

        # Check that databases were created in user directories and not default_db
        alice_db = Path(self.users_dir) / "alice" / "games.db"
        bob_db = Path(self.users_dir) / "bob" / "games.db"
        self.assertTrue(alice_db.exists(), "alice games.db should exist")
        self.assertTrue(bob_db.exists(), "bob games.db should exist")

        with db.get_connection(str(alice_db)) as conn:
            row = conn.execute("SELECT title FROM games WHERE external_id = 'game_alice'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "Title for alice")

        with db.get_connection(str(bob_db)) as conn:
            row = conn.execute("SELECT title FROM games WHERE external_id = 'game_bob'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "Title for bob")

    async def test_scheduled_sync_error_isolation(self):
        system_db.ensure_user("failing_user", db_path=self.system_db_path)
        system_db.ensure_user("succeeding_user", db_path=self.system_db_path)

        async def selective_sync():
            uid = get_current_user_id()
            if uid == "failing_user":
                raise RuntimeError("API Timeout")
            return {"status": "ok"}

        with patch("app.sync_all_platforms", side_effect=selective_sync):
            results = await app.run_scheduled_sync_iteration()

        self.assertIn("error", results["failing_user"])
        self.assertEqual(results["succeeding_user"], {"status": "ok"})
