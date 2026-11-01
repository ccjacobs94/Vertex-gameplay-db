"""Unit tests for multi-tenant context (context.py) and central system DB (system_db.py)."""

import asyncio
import tempfile
import unittest
from pathlib import Path

import context
import system_db


class TestMultiTenantContext(unittest.TestCase):
    def setUp(self):
        # Ensure clean default state before each test
        context.current_user_id.set(None)

    def tearDown(self):
        context.current_user_id.set(None)

    def test_default_is_none(self):
        self.assertIsNone(context.current_user_id.get())
        self.assertIsNone(context.get_current_user_id())

    def test_set_and_reset_current_user_id(self):
        token = context.set_current_user_id("user-123")
        self.assertEqual(context.current_user_id.get(), "user-123")
        self.assertEqual(context.get_current_user_id(), "user-123")
        context.reset_current_user_id(token)
        self.assertIsNone(context.get_current_user_id())

    def test_async_task_context_isolation(self):
        async def _worker(uid: str) -> str:
            context.set_current_user_id(uid)
            await asyncio.sleep(0.01)
            return context.get_current_user_id()

        async def _run():
            results = await asyncio.gather(_worker("alice"), _worker("bob"))
            self.assertEqual(results, ["alice", "bob"])
            # Parent task context remains untouched
            self.assertIsNone(context.get_current_user_id())

        asyncio.run(_run())


class TestSystemDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "system.db")
        system_db.init_system_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _table_columns(self, table_name: str):
        with system_db.get_system_connection(self.db_path) as conn:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            return [row["name"] for row in rows]

    def test_all_required_tables_and_columns_exist(self):
        expected_schema = {
            "users": ["id", "username", "email", "password_hash", "created_at"],
            "user_connections": [
                "user_id",
                "steam_id",
                "xbl_api_key_encrypted",
                "psn_npsso_encrypted",
                "updated_at",
            ],
            "web_sessions": [
                "session_id",
                "user_id",
                "created_at",
                "expires_at",
                "user_agent",
            ],
            "oauth_tokens": [
                "access_token",
                "refresh_token",
                "client_id",
                "user_id",
                "scope",
                "created_at",
            ],
            "catalog_cache": [
                "normalized_title",
                "steam_appid",
                "genres_json",
                "release_year",
                "critic_score",
                "cover_url",
                "updated_at",
            ],
        }
        for table, expected_cols in expected_schema.items():
            with self.subTest(table=table):
                actual_cols = self._table_columns(table)
                for col in expected_cols:
                    self.assertIn(col, actual_cols)

    def test_init_system_db_is_idempotent(self):
        with system_db.get_system_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO users (id, username, email, password_hash, created_at)
                VALUES ('u1', 'chris', 'chris@example.com', 'hash123', '2026-11-01T00:00:00Z')
                """
            )
            conn.execute(
                """
                INSERT INTO catalog_cache (normalized_title, steam_appid, genres_json, release_year, critic_score, cover_url, updated_at)
                VALUES ('portal 2', '620', '["Puzzle"]', 2011, 95, 'https://example.com/p2.jpg', '2026-11-01T00:00:00Z')
                """
            )

        # Re-initializing must not drop or corrupt existing data
        system_db.init_system_db(self.db_path)

        with system_db.get_system_connection(self.db_path) as conn:
            user = conn.execute("SELECT * FROM users WHERE id = 'u1'").fetchone()
            self.assertIsNotNone(user)
            self.assertEqual(user["username"], "chris")
            cache = conn.execute(
                "SELECT * FROM catalog_cache WHERE normalized_title = 'portal 2'"
            ).fetchone()
            self.assertIsNotNone(cache)
            self.assertEqual(cache["steam_appid"], "620")

    def test_foreign_key_cascade_on_user_delete(self):
        with system_db.get_system_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO users (id, username, email, password_hash, created_at)
                VALUES ('u2', 'alex', 'alex@example.com', 'hash456', '2026-11-01T00:00:00Z')
                """
            )
            conn.execute(
                """
                INSERT INTO user_connections (user_id, steam_id, xbl_api_key_encrypted, psn_npsso_encrypted, updated_at)
                VALUES ('u2', '76561198000000000', 'enc_xbl', 'enc_psn', '2026-11-01T00:00:00Z')
                """
            )
            conn.execute(
                """
                INSERT INTO web_sessions (session_id, user_id, created_at, expires_at, user_agent)
                VALUES ('sess_1', 'u2', 1000.0, 2000.0, 'Mozilla/5.0')
                """
            )
            conn.execute(
                """
                INSERT INTO oauth_tokens (access_token, refresh_token, client_id, user_id, scope, created_at)
                VALUES ('tok_a', 'ref_a', 'game-hub-mcp', 'u2', 'gamehub:read', 1000.0)
                """
            )

            conn.execute("DELETE FROM users WHERE id = 'u2'")

            self.assertIsNone(
                conn.execute("SELECT 1 FROM user_connections WHERE user_id = 'u2'").fetchone()
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM web_sessions WHERE user_id = 'u2'").fetchone()
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM oauth_tokens WHERE user_id = 'u2'").fetchone()
            )


if __name__ == "__main__":
    unittest.main()
