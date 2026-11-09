"""
Security regression tests for the remaining High findings (audit 2026-11-08):
- H3a: MCP transport rejects browser clients because allowed_origins was empty
- H3b: MCP non-transport routes (/.well-known, /oauth, /login, /) lacked Host validation
- H3c: No CSRF Origin / Sec-Fetch-Site check on cookie-authenticated state-changing requests
- H3d: is_allowed_host() mangled bracketed IPv6 Host headers ("[::1]:8095" -> "[")
- C2 regression: REST requests authenticated only by a Bearer token had no user context
- H4: Credentials encrypted with the hardcoded fallback key; VAPID private key stored in plaintext
"""

import base64
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet, InvalidToken
from starlette.testclient import TestClient

import db
import system_db
import web_auth

MCP_ENV = {
    "MCP_PUBLIC_DOMAIN": "https://gaming-mcp.crjacobs.cc",
    "PUBLIC_BASE_URL": "https://vertex.crjacobs.cc",
    "VERTEX_ALLOWED_HOSTS": "vertex.crjacobs.cc,gaming-mcp.crjacobs.cc,*.crjacobs.cc,*.ts.net,chrisserver",
    "MCP_ALLOWED_ORIGINS": "https://custom-client.example.com",
    "MCP_REQUIRE_AUTH": "true",
}

INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "sec-test", "version": "1.0"},
    },
}


def _legacy_fallback_fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"gamehub-envelope-key-fallback").digest()))


def _fernet_for(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


class _TempSystemBase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.sys_db_path = str(Path(self.test_dir) / "system.db")
        self.default_db_path = str(Path(self.test_dir) / "default_games.db")
        self.users_dir = str(Path(self.test_dir) / "users")
        self._patches = [
            mock.patch.object(system_db, "DEFAULT_SYSTEM_DB_PATH", self.sys_db_path),
            mock.patch.object(db, "USERS_DB_DIR", self.users_dir),
            mock.patch.object(db, "DEFAULT_DB_PATH", self.default_db_path),
        ]
        for p in self._patches:
            p.start()
        system_db.init_system_db(self.sys_db_path)
        db.init_db(self.default_db_path)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)


class TestMcpOriginAndHost(_TempSystemBase):
    """H3a / H3b"""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, MCP_ENV)
        self.env.start()
        self.uid = system_db.create_user("mcpuser", "Password123!", db_path=self.sys_db_path)
        self.token = system_db.create_personal_access_token(self.uid, db_path=self.sys_db_path)

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_allowed_origins_list(self):
        import app
        origins = app.get_allowed_mcp_origins()
        for expected in (
            "https://gaming-mcp.crjacobs.cc",
            "https://vertex.crjacobs.cc",
            "https://chrisserver",
            "https://claude.ai",
            "https://chatgpt.com",
            "https://gemini.google.com",
            "https://custom-client.example.com",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ):
            self.assertIn(expected, origins)
        self.assertFalse(any("*." in o for o in origins), origins)

    def _post_mcp(self, client, origin=None, host="gaming-mcp.crjacobs.cc"):
        headers = {
            "Host": host,
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if origin:
            headers["Origin"] = origin
        return client.post("/mcp", json=INIT_BODY, headers=headers)

    def test_mcp_origin_matrix(self):
        import app
        mcp_app = app.build_mcp_app()
        with TestClient(mcp_app, base_url="http://gaming-mcp.crjacobs.cc") as client:
            for origin in (None, "https://claude.ai", "https://gaming-mcp.crjacobs.cc", "http://localhost:6274"):
                res = self._post_mcp(client, origin=origin)
                self.assertNotEqual(res.status_code, 403, f"origin={origin}: {res.text}")
                self.assertEqual(res.status_code, 200, f"origin={origin}: {res.text}")
            for origin in ("https://evil.com", "https://claude.ai.evil.com", "null"):
                res = self._post_mcp(client, origin=origin)
                self.assertEqual(res.status_code, 403, f"origin={origin}")

    def test_mcp_well_known_rejects_bad_host(self):
        import app
        mcp_app = app.build_mcp_app()
        with TestClient(mcp_app, base_url="http://gaming-mcp.crjacobs.cc") as client:
            bad = client.get("/.well-known/oauth-authorization-server", headers={"Host": "evil.com"})
            self.assertEqual(bad.status_code, 400)
            for path in ("/", "/login", "/oauth/authorize"):
                self.assertEqual(client.get(path, headers={"Host": "evil.com"}).status_code, 400, path)
            good = client.get("/.well-known/oauth-authorization-server", headers={"Host": "gaming-mcp.crjacobs.cc"})
            self.assertEqual(good.status_code, 200)
            local = client.get("/.well-known/oauth-authorization-server", headers={"Host": "localhost:8096"})
            self.assertEqual(local.status_code, 200)


class TestIsAllowedHostIPv6(unittest.TestCase):
    """H3d"""

    def test_bracketed_ipv6_hosts(self):
        import app
        with mock.patch.dict(os.environ, {"VERTEX_ALLOWED_HOSTS": "", "PUBLIC_BASE_URL": "", "MCP_PUBLIC_DOMAIN": ""}):
            self.assertTrue(app.is_allowed_host("[::1]:8095"))
            self.assertTrue(app.is_allowed_host("[::1]"))
            self.assertTrue(app.is_allowed_host("::1"))
            self.assertTrue(app.is_allowed_host("[fd7a:115c:a1e0::1]:8095"))
            self.assertTrue(app.is_allowed_host("localhost:8095"))
            self.assertFalse(app.is_allowed_host("["))
            self.assertFalse(app.is_allowed_host("[evil.com]:80"))
            self.assertFalse(app.is_allowed_host("evil.com:8095"))


class TestWebCsrfAndBearer(_TempSystemBase):
    """H3c / C2 regression"""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(
            os.environ,
            {
                "VERTEX_PASSWORD": "EnvSecret123!",
                "VERTEX_REQUIRE_AUTH": "true",
                "VERTEX_LOCAL_BYPASS": "false",
                "PUBLIC_BASE_URL": "https://vertex.crjacobs.cc",
            },
        )
        self.env.start()
        self.alice = system_db.create_user("alice", "Password123!", db_path=self.sys_db_path)
        self.bob = system_db.create_user("bob", "Password123!", db_path=self.sys_db_path)
        self.alice_token = system_db.create_personal_access_token(self.alice, db_path=self.sys_db_path)
        self.bob_token = system_db.create_personal_access_token(self.bob, db_path=self.sys_db_path)
        import app
        self.client = TestClient(app.app)

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def _cookie_client(self):
        import app
        c = TestClient(app.app)
        c.cookies.set(web_auth.SESSION_COOKIE_NAME, web_auth.create_web_session("alice"))
        return c

    def _pw_post(self, client, headers):
        return client.post(
            "/api/account/password",
            json={"current_password": "wrong-password", "new_password": "Whatever12345!"},
            headers=headers,
        )

    def test_cross_site_cookie_post_rejected(self):
        c = self._cookie_client()
        self.assertEqual(self._pw_post(c, {"Origin": "https://evil.com"}).status_code, 403)
        self.assertEqual(self._pw_post(c, {"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self._pw_post(c, {"Sec-Fetch-Site": "same-site"}).status_code, 403)
        self.assertEqual(self._pw_post(c, {"Origin": "null"}).status_code, 403)
        self.assertEqual(c.delete("/api/games/1", headers={"Origin": "https://evil.com"}).status_code, 403)

    def test_same_origin_cookie_post_allowed(self):
        c = self._cookie_client()
        # 400 = reached the handler (wrong current password), i.e. CSRF check passed
        self.assertEqual(self._pw_post(c, {}).status_code, 400)
        self.assertEqual(self._pw_post(c, {"Origin": "http://testserver"}).status_code, 400)
        self.assertEqual(self._pw_post(c, {"Origin": "https://vertex.crjacobs.cc"}).status_code, 400)
        self.assertEqual(self._pw_post(c, {"Sec-Fetch-Site": "same-origin"}).status_code, 400)
        self.assertEqual(self._pw_post(c, {"Sec-Fetch-Site": "none"}).status_code, 400)

    def test_get_with_foreign_origin_not_blocked(self):
        c = self._cookie_client()
        self.assertEqual(c.get("/api/stats", headers={"Origin": "https://evil.com"}).status_code, 200)

    def test_bearer_only_post_exempt_from_csrf(self):
        res = self.client.post(
            "/api/games",
            json={"title": "Bearer Game", "platform": "Nintendo Switch"},
            headers={"Authorization": f"Bearer {self.alice_token}", "Origin": "https://evil.com"},
        )
        self.assertEqual(res.status_code, 200, res.text)

    def test_bearer_plus_cookie_still_checked(self):
        c = self._cookie_client()
        res = c.post(
            "/api/games",
            json={"title": "X", "platform": "Nintendo Switch"},
            headers={"Authorization": f"Bearer {self.alice_token}", "Origin": "https://evil.com"},
        )
        self.assertEqual(res.status_code, 403)

    def test_billing_webhook_exempt(self):
        res = self.client.post(
            "/billing/webhook",
            content=b"{}",
            headers={"Origin": "https://evil.com", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertNotEqual(res.status_code, 403)

    def test_bearer_only_rest_is_scoped_to_token_user(self):
        # C2 regression (2026-11-08): bearer-only /api/stats raised RuntimeError (500) in multi-user mode
        self.assertTrue(db.is_multiuser_mode())
        add = self.client.post(
            "/api/games",
            json={"title": "Alice Only Game", "platform": "Nintendo Switch"},
            headers={"Authorization": f"Bearer {self.alice_token}"},
        )
        self.assertEqual(add.status_code, 200, add.text)

        a = self.client.get("/api/stats", headers={"Authorization": f"Bearer {self.alice_token}"})
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(a.json().get("total_games"), 1)
        b = self.client.get("/api/stats", headers={"Authorization": f"Bearer {self.bob_token}"})
        self.assertEqual(b.status_code, 200, b.text)
        self.assertEqual(b.json().get("total_games"), 0)

        games = self.client.get("/api/games", headers={"Authorization": f"Bearer {self.bob_token}"})
        self.assertEqual(games.status_code, 200, games.text)

        wl = self.client.get("/api/wishlist", headers={"Authorization": f"Bearer {self.alice_token}"})
        self.assertEqual(wl.status_code, 200, wl.text)
        conns = self.client.get("/api/user/connections", headers={"Authorization": f"Bearer {self.alice_token}"})
        self.assertEqual(conns.status_code, 200, conns.text)

    def test_bearer_for_inactive_user_rejected(self):
        with system_db.get_system_connection(self.sys_db_path) as conn:
            conn.execute(
                "UPDATE users SET status = 'inactive', status_reason = 'admin_suspended' WHERE id = ?",
                (self.alice,),
            )
        res = self.client.get("/api/stats", headers={"Authorization": f"Bearer {self.alice_token}"})
        self.assertEqual(res.status_code, 401)


class TestCredentialKeyRotation(_TempSystemBase):
    """H4"""

    NEW_KEY = "unit-test-primary-secret-key-0123456789abcdef"

    def _insert_conn(self, uid, xbl, psn):
        with system_db.get_system_connection(self.sys_db_path) as conn:
            conn.execute(
                "INSERT INTO user_connections (user_id, xbl_api_key_encrypted, psn_npsso_encrypted, updated_at) "
                "VALUES (?, ?, ?, 'now')",
                (uid, xbl, psn),
            )

    def _raw_conn(self, uid):
        with system_db.get_system_connection(self.sys_db_path) as conn:
            return conn.execute(
                "SELECT xbl_api_key_encrypted, psn_npsso_encrypted FROM user_connections WHERE user_id = ?",
                (uid,),
            ).fetchone()

    def test_fallback_row_decrypts_and_rotates_to_primary(self):
        uid = system_db.create_user("rotuser", "Password123!", db_path=self.sys_db_path)
        with mock.patch.dict(os.environ, {"GAMEHUB_SECRET_KEY": "", "VERTEX_PASSWORD": ""}):
            enc_xbl = system_db.encrypt_credential("xbl-secret-value")
            enc_psn = system_db.encrypt_credential("psn-secret-value")
        _legacy_fallback_fernet().decrypt(enc_xbl.encode())  # really under the legacy key
        self._insert_conn(uid, enc_xbl, enc_psn)

        with mock.patch.dict(os.environ, {"GAMEHUB_SECRET_KEY": self.NEW_KEY, "VERTEX_PASSWORD": ""}):
            # Prod deploy picking up the new key can still read existing fallback rows (MultiFernet)
            self.assertEqual(system_db.decrypt_credential(enc_xbl), "xbl-secret-value")

            rotated = system_db.migrate_secrets_at_rest(self.sys_db_path)
            self.assertGreaterEqual(rotated, 2)
            row = self._raw_conn(uid)
            primary = _fernet_for(self.NEW_KEY)
            self.assertEqual(primary.decrypt(row[0].encode()).decode(), "xbl-secret-value")
            self.assertEqual(primary.decrypt(row[1].encode()).decode(), "psn-secret-value")
            with self.assertRaises(InvalidToken):
                _legacy_fallback_fernet().decrypt(row[0].encode())

            conns = system_db.get_user_connections(uid, db_path=self.sys_db_path)
            self.assertEqual(conns["xbl_api_key"], "xbl-secret-value")
            self.assertEqual(conns["psn_npsso"], "psn-secret-value")

            # Idempotent
            self.assertEqual(system_db.migrate_secrets_at_rest(self.sys_db_path), 0)

    def test_new_encryption_uses_primary_key(self):
        with mock.patch.dict(os.environ, {"GAMEHUB_SECRET_KEY": self.NEW_KEY, "VERTEX_PASSWORD": "pw-legacy"}):
            enc = system_db.encrypt_credential("abc")
        self.assertEqual(_fernet_for(self.NEW_KEY).decrypt(enc.encode()).decode(), "abc")

    def test_no_secret_key_keeps_legacy_and_warns(self):
        uid = system_db.create_user("legacyuser", "Password123!", db_path=self.sys_db_path)
        with mock.patch.dict(os.environ, {"GAMEHUB_SECRET_KEY": "", "VERTEX_PASSWORD": ""}):
            enc = system_db.encrypt_credential("legacy-value")
            self._insert_conn(uid, enc, "")
            self.assertEqual(system_db.migrate_secrets_at_rest(self.sys_db_path), 0)
            self.assertEqual(self._raw_conn(uid)[0], enc)
            self.assertEqual(system_db.decrypt_credential(enc), "legacy-value")
            with self.assertLogs("gamehub.system_db", level="CRITICAL"):
                self.assertFalse(system_db.warn_if_secret_key_missing())
        with mock.patch.dict(os.environ, {"GAMEHUB_SECRET_KEY": self.NEW_KEY}):
            self.assertTrue(system_db.warn_if_secret_key_missing())


class TestVapidKeyAtRest(_TempSystemBase):
    """H4: VAPID private key encrypted in instance_settings"""

    NEW_KEY = "unit-test-vapid-secret-key-abcdef0123456789"

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(
            os.environ,
            {"GAMEHUB_SECRET_KEY": self.NEW_KEY, "VERTEX_PASSWORD": "", "VAPID_PUBLIC_KEY": "", "VAPID_PRIVATE_KEY": ""},
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_generated_vapid_key_stored_encrypted_and_usable(self):
        import web_push
        keys = web_push.get_or_create_vapid_keys(db_path=self.sys_db_path)
        raw = system_db.get_setting("vapid_private_key", db_path=self.sys_db_path)
        self.assertTrue(raw.startswith("gAAAAA"))
        self.assertNotEqual(raw, keys["private_key"])
        self.assertEqual(_fernet_for(self.NEW_KEY).decrypt(raw.encode()).decode(), keys["private_key"])
        again = web_push.get_or_create_vapid_keys(db_path=self.sys_db_path)
        self.assertEqual(again, keys)

    def test_legacy_plaintext_vapid_key_migrated(self):
        import web_push
        system_db.set_setting("vapid_public_key", "PUBKEY", db_path=self.sys_db_path)
        system_db.set_setting("vapid_private_key", "plain-private-key", db_path=self.sys_db_path)
        system_db.migrate_secrets_at_rest(self.sys_db_path)
        raw = system_db.get_setting("vapid_private_key", db_path=self.sys_db_path)
        self.assertTrue(raw.startswith("gAAAAA"))
        keys = web_push.get_or_create_vapid_keys(db_path=self.sys_db_path)
        self.assertEqual(keys, {"public_key": "PUBKEY", "private_key": "plain-private-key"})

    def test_env_vapid_keys_still_win(self):
        import web_push
        with mock.patch.dict(os.environ, {"VAPID_PUBLIC_KEY": "envpub", "VAPID_PRIVATE_KEY": "envpriv"}):
            self.assertEqual(
                web_push.get_or_create_vapid_keys(db_path=self.sys_db_path),
                {"public_key": "envpub", "private_key": "envpriv"},
            )


if __name__ == "__main__":
    unittest.main()
