import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from starlette.applications import Starlette
from starlette.testclient import TestClient

import oauth_provider
import system_db


class OAuthUserScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "system.db")
        patcher = mock.patch.object(system_db, "DEFAULT_SYSTEM_DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        system_db.init_system_db(self.db)
        with system_db.get_system_connection(self.db) as conn:
            for uid, name in (("u-alice", "alice"), ("u-bob", "bob")):
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, 'x', 'now')",
                    (uid, name),
                )
        oauth_provider._auth_codes.clear()
        self.client = TestClient(
            Starlette(routes=oauth_provider.get_oauth_routes()), follow_redirects=False
        )

    def _authorize_params(self):
        return {
            "client_id": "c1",
            "redirect_uri": "https://claude.ai/cb",
            "state": "s1",
        }

    def test_legacy_oauth_tokens_table_gets_user_id_column(self):
        legacy = os.path.join(self.tmp.name, "legacy.db")
        conn = sqlite3.connect(legacy)
        conn.execute(
            "CREATE TABLE oauth_tokens (access_token TEXT PRIMARY KEY, refresh_token TEXT UNIQUE,"
            " client_id TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        conn.execute("INSERT INTO oauth_tokens VALUES ('old', 'oldr', 'c', 1.0)")
        conn.commit()
        conn.close()
        system_db.init_system_db(legacy)
        system_db.init_system_db(legacy)  # idempotent
        with system_db.get_system_connection(legacy) as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(oauth_tokens)")}
        self.assertIn("user_id", cols)
        # Legacy unbound tokens are never valid
        self.assertIsNone(oauth_provider.verify_bearer_token("old", db_path=legacy))

    def test_verify_bearer_token_returns_user_id_or_none(self):
        oauth_provider._store_token_pair("tok-a", "ref-a", "c1", "u-alice")
        self.assertEqual(oauth_provider.verify_bearer_token("tok-a"), "u-alice")
        self.assertIsNone(oauth_provider.verify_bearer_token("nope"))
        self.assertIsNone(oauth_provider.verify_bearer_token(""))

    def test_shared_client_secret_is_not_a_valid_bearer(self):
        with mock.patch.dict(os.environ, {"MCP_CLIENT_SECRET": "shh"}):
            self.assertIsNone(oauth_provider.verify_bearer_token("shh"))

    def test_store_token_pair_requires_user(self):
        with self.assertRaises(ValueError):
            oauth_provider._store_token_pair("a", "b", "c", "")

    def test_authorize_prompts_login_without_session(self):
        r = self.client.get("/oauth/authorize", params=self._authorize_params())
        self.assertEqual(r.status_code, 200)
        self.assertIn('action="/login"', r.text)
        self.assertIn("redirect_uri", r.text)  # resume URL preserved in 'next'
        self.assertEqual(oauth_provider._auth_codes, {})

    def test_authorize_post_without_session_issues_no_code(self):
        r = self.client.post("/oauth/authorize", data=self._authorize_params())
        self.assertIn('action="/login"', r.text)
        self.assertEqual(oauth_provider._auth_codes, {})

    def test_authorize_ignores_env_client_secret(self):
        with mock.patch.dict(os.environ, {"MCP_CLIENT_SECRET": "shh"}):
            data = dict(self._authorize_params(), access_key="shh")
            r = self.client.post("/oauth/authorize", data=data)
        self.assertEqual(oauth_provider._auth_codes, {})
        self.assertIn('action="/login"', r.text)

    def test_session_without_system_user_is_forbidden(self):
        with mock.patch.object(oauth_provider, "verify_web_session", return_value="ghost"):
            r = self.client.get(
                "/oauth/authorize", params=self._authorize_params(), cookies={"vertex_session": "x"}
            )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(oauth_provider._auth_codes, {})

    def test_full_flow_binds_token_to_signed_in_user(self):
        with mock.patch.object(oauth_provider, "verify_web_session", return_value="Bob"):
            page = self.client.get(
                "/oauth/authorize", params=self._authorize_params(), cookies={"vertex_session": "x"}
            )
            self.assertEqual(page.status_code, 200)
            self.assertIn("bob", page.text)
            self.assertNotIn("access_key", page.text)
            r = self.client.post(
                "/oauth/authorize", data=self._authorize_params(), cookies={"vertex_session": "x"}
            )
        self.assertEqual(r.status_code, 302)
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        self.assertEqual(oauth_provider._auth_codes[code]["user_id"], "u-bob")

        tok = self.client.post(
            "/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": "c1"}
        ).json()
        self.assertEqual(oauth_provider.verify_bearer_token(tok["access_token"]), "u-bob")

        refreshed = self.client.post(
            "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}
        ).json()
        self.assertEqual(oauth_provider.verify_bearer_token(refreshed["access_token"]), "u-bob")

    def test_client_credentials_grant_is_rejected(self):
        with mock.patch.dict(os.environ, {"MCP_CLIENT_SECRET": "shh"}):
            r = self.client.post(
                "/oauth/token", data={"grant_type": "client_credentials", "client_secret": "shh"}
            )
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("access_token", r.json())

    def test_userinfo_requires_valid_token_and_reports_user(self):
        self.assertEqual(self.client.get("/oauth/userinfo").status_code, 401)
        oauth_provider._store_token_pair("tok-a", "ref-a", "c1", "u-alice")
        r = self.client.get("/oauth/userinfo", headers={"Authorization": "Bearer tok-a"})
        self.assertEqual(r.json()["sub"], "u-alice")


class McpOutputSanitizationTests(unittest.TestCase):
    def test_profile_omits_sync_log_messages_and_credentials(self):
        import mcp_server

        stats = {
            "total_games": 3,
            "last_syncs": [
                {"source": "steam", "status": "error", "games_synced": 0,
                 "message": "401 key=SECRET123", "synced_at": "t"}
            ],
            "nested": {"xbl_api_key": "k", "psn_npsso": "n", "ok": 1},
        }
        with mock.patch.object(mcp_server, "get_library_stats", return_value=stats):
            out = mcp_server.get_gaming_profile_and_metrics()
        self.assertEqual(out["total_games"], 3)
        self.assertEqual(out["last_syncs"][0]["status"], "error")
        self.assertNotIn("message", out["last_syncs"][0])
        self.assertEqual(out["nested"], {"ok": 1})
        self.assertNotIn("SECRET123", repr(out))


if __name__ == "__main__":
    unittest.main()
