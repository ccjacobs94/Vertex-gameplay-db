"""
Unit tests for multi-user settings, personal access tokens, and data portability (library export/import).
"""

import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import system_db
from context import reset_current_user_id, set_current_user_id
import sync_engine


class TestUserSettingsAndPortability(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sys_db = str(Path(self.temp_dir.name) / "system.db")
        self.users_dir = str(Path(self.temp_dir.name) / "users")
        self.default_db = str(Path(self.temp_dir.name) / "games.db")

        system_db.init_system_db(self.sys_db)
        db.init_db(self.default_db)

        self.patch_sys = mock.patch.object(system_db, "DEFAULT_SYSTEM_DB_PATH", self.sys_db)
        self.patch_users_dir = mock.patch.object(db, "USERS_DB_DIR", self.users_dir)
        self.patch_def_db = mock.patch.object(db, "DEFAULT_DB_PATH", self.default_db)
        self.patch_sys.start()
        self.patch_users_dir.start()
        self.patch_def_db.start()

    def tearDown(self):
        self.patch_def_db.stop()
        self.patch_users_dir.stop()
        self.patch_sys.stop()
        self.temp_dir.cleanup()

    def test_user_connections_crud(self):
        # 1. Create a user
        uid = system_db.ensure_user("testuser", db_path=self.sys_db)
        self.assertEqual(uid, "testuser")

        # Initially empty connections
        conns = system_db.get_user_connections(uid, db_path=self.sys_db)
        self.assertEqual(conns.get("steam_id"), "")
        self.assertEqual(conns.get("xbl_api_key"), "")
        self.assertEqual(conns.get("psn_npsso"), "")

        # Set connections
        system_db.set_user_connections(
            uid,
            steam_id="76561198000000001",
            xbl_api_key="xbl_secret_key_1",
            psn_npsso="psn_secret_token_1",
            db_path=self.sys_db,
        )

        conns = system_db.get_user_connections(uid, db_path=self.sys_db)
        self.assertEqual(conns.get("steam_id"), "76561198000000001")
        self.assertEqual(conns.get("xbl_api_key"), "xbl_secret_key_1")
        self.assertEqual(conns.get("psn_npsso"), "psn_secret_token_1")

        # Update only steam_id, leave others untouched
        system_db.set_user_connections(
            uid,
            steam_id="76561198000000002",
            db_path=self.sys_db,
        )
        conns = system_db.get_user_connections(uid, db_path=self.sys_db)
        self.assertEqual(conns.get("steam_id"), "76561198000000002")
        self.assertEqual(conns.get("xbl_api_key"), "xbl_secret_key_1")
        self.assertEqual(conns.get("psn_npsso"), "psn_secret_token_1")

    def test_personal_access_tokens(self):
        uid = system_db.ensure_user("agent_user", db_path=self.sys_db)
        tokens = system_db.list_user_oauth_tokens(uid, db_path=self.sys_db)
        self.assertEqual(len(tokens), 0)

        # Generate a PAT
        token_val = system_db.create_personal_access_token(
            uid, label="Claude Agent", db_path=self.sys_db
        )
        self.assertTrue(token_val.startswith("pat_"))

        # Verify token exists in list
        tokens = system_db.list_user_oauth_tokens(uid, db_path=self.sys_db)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["client_id"], "Claude Agent")
        self.assertEqual(tokens[0]["access_token"], token_val)

        # Verify OAuth token verification accepts PAT
        import oauth_provider
        verified_uid = oauth_provider.verify_bearer_token(token_val, db_path=self.sys_db)
        self.assertEqual(verified_uid, uid)

        # Revoke token
        revoked = system_db.revoke_oauth_token(uid, token_val, db_path=self.sys_db)
        self.assertTrue(revoked)
        self.assertEqual(len(system_db.list_user_oauth_tokens(uid, db_path=self.sys_db)), 0)
        self.assertIsNone(oauth_provider.verify_bearer_token(token_val, db_path=self.sys_db))

    def test_sync_engine_resolves_user_connections(self):
        uid = system_db.ensure_user("gamer_steve", db_path=self.sys_db)
        system_db.set_user_connections(
            uid,
            steam_id="76561198999999999",
            xbl_api_key="steve_xbl_key",
            psn_npsso="steve_npsso",
            db_path=self.sys_db,
        )

        token = set_current_user_id(uid)
        try:
            creds = sync_engine._resolve_credentials()
            self.assertEqual(creds.steam_id, "76561198999999999")
            self.assertEqual(creds.xbl_api_key, "steve_xbl_key")
            self.assertEqual(creds.psn_npsso, "steve_npsso")
        finally:
            reset_current_user_id(token)

    def test_export_and_import_library(self):
        from starlette.testclient import TestClient
        import app

        client = TestClient(app.app)

        # Create a user with games
        username = "alice"
        uid = system_db.ensure_user(username, db_path=self.sys_db)
        user_db_path = Path(self.users_dir) / uid / "games.db"
        user_db_path.parent.mkdir(parents=True, exist_ok=True)
        db.init_db(str(user_db_path))

        # Add games for alice
        db.upsert_synced_game(
            external_id="steam:101",
            title="Alice Adventure",
            platform="PC",
            source="Steam",
            playtime_hours=15.0,
            db_path=str(user_db_path),
        )

        # Mock authenticated session for alice
        client.cookies.set("vertex_session", "alice_session")
        session_token = set_current_user_id(uid)
        try:
            with mock.patch("web_auth.verify_web_session", return_value=username), \
                 mock.patch("app.verify_web_session", return_value=username):

                # Test connections API
                conn_get = client.get("/api/user/connections")
                self.assertEqual(conn_get.status_code, 200)
                self.assertEqual(conn_get.json()["username"], username)

                conn_post = client.post(
                    "/api/user/connections",
                    json={"steam_id": "76561198000000099", "xbl_api_key": "xbl_abc", "psn_npsso": "psn_xyz"},
                )
                self.assertEqual(conn_post.status_code, 200)
                self.assertEqual(conn_post.json()["status"], "ok")

                # Test MCP tokens API
                mcp_get = client.get("/api/user/mcp")
                self.assertEqual(mcp_get.status_code, 200)
                self.assertIn("/mcp", mcp_get.json()["mcp_endpoint"])

                tok_gen = client.post("/api/user/mcp/tokens", json={"label": "Laptop Agent"})
                self.assertEqual(tok_gen.status_code, 200)
                new_tok = tok_gen.json()["token"]
                self.assertTrue(new_tok.startswith("pat_"))

                # Test token revocation
                tok_del = client.delete(f"/api/user/mcp/tokens/{new_tok}")
                self.assertEqual(tok_del.status_code, 200)

                # Export library
                resp = client.get("/api/user/library/export")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.headers.get("content-type"), "application/x-sqlite3")
                db_bytes = resp.content

                # Verify exported content is a valid SQLite DB with the game
                exported_tmp = Path(self.temp_dir.name) / "exported_test.db"
                exported_tmp.write_bytes(db_bytes)
                with sqlite3.connect(str(exported_tmp)) as c:
                    row = c.execute("SELECT title, playtime_hours FROM games WHERE external_id = 'steam:101'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row[0], "Alice Adventure")
                    self.assertEqual(row[1], 15.0)

                # Now prepare a new DB to import
                import_tmp = Path(self.temp_dir.name) / "new_import.db"
                db.init_db(str(import_tmp))
                db.upsert_synced_game(
                    external_id="steam:202",
                    title="Imported Champion",
                    platform="PC",
                    source="Steam",
                    playtime_hours=42.0,
                    db_path=str(import_tmp),
                )

                # Test importing invalid file (corrupted)
                bad_resp = client.post(
                    "/api/user/library/import",
                    files={"file": ("corrupt.db", b"not an sqlite database", "application/octet-stream")},
                )
                self.assertEqual(bad_resp.status_code, 400)

                # Test importing valid file
                with open(str(import_tmp), "rb") as f:
                    good_resp = client.post(
                        "/api/user/library/import",
                        files={"file": ("games.db", f, "application/octet-stream")},
                    )
                self.assertEqual(good_resp.status_code, 200)
                data = good_resp.json()
                self.assertEqual(data.get("status"), "ok")

                # Verify user's database now contains Imported Champion
                with sqlite3.connect(str(user_db_path)) as c:
                    row = c.execute("SELECT title, playtime_hours FROM games WHERE external_id = 'steam:202'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row[0], "Imported Champion")
                    self.assertEqual(row[1], 42.0)
        finally:
            reset_current_user_id(session_token)

    def test_api_stats_public_for_healthcheck(self):
        from starlette.testclient import TestClient
        import app

        client = TestClient(app.app)
        # Unauthenticated request without session cookie
        resp = client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_games", data)
        self.assertIn("configured_sources", data)

    def test_single_port_mcp_forwarding_endpoint_exists(self):
        from starlette.testclient import TestClient
        import app

        client = TestClient(app.app)
        # Calling /mcp without auth should return 401/503 from MCP auth or proxy, not 404 or redirect to /login
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        self.assertIn(resp.status_code, (401, 503))
        # Ensure it didn't redirect to /login
        self.assertNotEqual(resp.status_code, 303)


if __name__ == "__main__":
    unittest.main()
