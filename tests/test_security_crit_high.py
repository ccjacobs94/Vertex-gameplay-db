"""
Security tests for Critical and High findings:
- C1: Image & data boundaries
- C2: Session invalidation and deleted user protection
- H1: /api/stats auth, /healthz public endpoint, fail-closed db connection
- H2: OAuth 2.1 PKCE S256 requirement and redirect_uri binding
- H3: Local bypass default disabled
- H4: Platform credentials encrypted at rest with Fernet
- H5: Steam/API key scrubbing from logs and sync responses
- H6: Web push endpoint SSRF prevention
- H8: Env password master credential mitigation
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import db
import passwords
import system_db
import web_auth
from context import reset_current_user_id, set_current_user_id


class TestSecurityCriticalAndHigh(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.sys_db_path = str(Path(self.test_dir) / "system.db")
        self.default_db_path = str(Path(self.test_dir) / "default_games.db")
        self.users_dir = str(Path(self.test_dir) / "users")

        self.patch_sys = mock.patch.object(system_db, "DEFAULT_SYSTEM_DB_PATH", self.sys_db_path)
        self.patch_users = mock.patch.object(db, "USERS_DB_DIR", self.users_dir)
        self.patch_def = mock.patch.object(db, "DEFAULT_DB_PATH", self.default_db_path)

        self.patch_sys.start()
        self.patch_users.start()
        self.patch_def.start()

        system_db.init_system_db(self.sys_db_path)
        db.init_db(self.default_db_path)

    def tearDown(self):
        self.patch_def.stop()
        self.patch_users.stop()
        self.patch_sys.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # --- C2: Session Invalidation & Deleted User Protection ---

    def test_session_invalidated_on_logout(self):
        uid = system_db.create_user("alice", "Password123!", db_path=self.sys_db_path)
        sess = web_auth.create_web_session("alice")
        self.assertEqual(web_auth.verify_web_session(sess), "alice")

        web_auth.revoke_web_session(sess)
        self.assertIsNone(web_auth.verify_web_session(sess))

    def test_session_invalidated_on_password_change(self):
        uid = system_db.create_user("bob", "Password123!", db_path=self.sys_db_path)
        sess = web_auth.create_web_session("bob")
        self.assertEqual(web_auth.verify_web_session(sess), "bob")

        web_auth.revoke_sessions_for_username("bob")
        self.assertIsNone(web_auth.verify_web_session(sess))

    def test_deleted_user_sessions_revoked_and_cannot_resurrect(self):
        import app
        uid = system_db.create_user("charlie", "Password123!", db_path=self.sys_db_path)
        sess = web_auth.create_web_session("charlie")
        self.assertEqual(web_auth.verify_web_session(sess), "charlie")

        system_db.delete_user(uid, db_path=self.sys_db_path, users_dir=self.users_dir)
        self.assertIsNone(web_auth.verify_web_session(sess))

        # Using the stale session cookie on an authenticated request must NOT re-create the user
        client = TestClient(app.app)
        client.cookies.set(web_auth.SESSION_COOKIE_NAME, sess)
        res = client.get("/api/account")
        self.assertIn(res.status_code, (401, 303))
        # Ensure user row does NOT exist in system_db
        self.assertIsNone(system_db.get_user_by_username("charlie", db_path=self.sys_db_path))

    # --- H1: /api/stats Auth & /healthz Public ---

    def test_healthz_is_public_and_returns_ok(self):
        import app
        client = TestClient(app.app)
        res = client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True})

    def test_api_stats_requires_auth(self):
        import app
        client = TestClient(app.app)
        with mock.patch.dict(os.environ, {"VERTEX_PASSWORD": "TestSecret123!", "VERTEX_REQUIRE_AUTH": "true"}):
            res = client.get("/api/stats")
            self.assertIn(res.status_code, (401, 303))

    def test_auth_enabled_when_users_have_passwords_without_env_password(self):
        import app
        client = TestClient(app.app)
        with mock.patch.dict(os.environ, {"VERTEX_PASSWORD": "", "VERTEX_REQUIRE_AUTH": "true"}):
            # Initially, no users with real passwords exist
            self.assertFalse(system_db.has_users_with_passwords(self.sys_db_path))
            self.assertFalse(web_auth.is_web_auth_enabled())

            bootstrap_admin = web_auth.get_auth_username()
            # Users created with placeholder passwords (managed_by_web_auth) do NOT activate auth
            # Even with surrounding whitespace or uppercase
            system_db.ensure_bootstrap_admin(bootstrap_admin, db_path=self.sys_db_path)
            with system_db.get_system_connection(self.sys_db_path) as conn:
                conn.execute(
                    "UPDATE users SET password_hash = '  MANAGED_BY_WEB_AUTH  ' WHERE username = ?",
                    (bootstrap_admin,),
                )
            self.assertFalse(system_db.has_users_with_passwords(self.sys_db_path))
            self.assertFalse(web_auth.is_web_auth_enabled())

            # Initial bootstrap account endpoint access is permitted when auth is not yet enabled
            acc_res = client.get("/api/account")
            self.assertEqual(acc_res.status_code, 200)
            self.assertEqual(acc_res.json()["username"], bootstrap_admin)

            # Bootstrap admin can establish their initial password via /api/account/password
            pw_res = client.post(
                "/api/account/password",
                json={"current_password": "init", "new_password": "AdminPassword123!"},
            )
            self.assertEqual(pw_res.status_code, 200)

            # Creating a user password activates auth automatically
            self.assertTrue(system_db.has_users_with_passwords(self.sys_db_path))
            self.assertTrue(web_auth.is_web_auth_enabled())

            # A new unauthenticated client to /api/stats and /api/account must now return 401 Unauthorized
            unauthed_client = TestClient(app.app)
            res = unauthed_client.get("/api/stats")
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.json().get("error"), "unauthorized")

            acc_unauthed = unauthed_client.get("/api/account")
            self.assertEqual(acc_unauthed.status_code, 401)

            # Healthcheck remains public and returns 200 OK
            health_res = unauthed_client.get("/healthz")
            self.assertEqual(health_res.status_code, 200)

            # Authenticated session succeeds
            sess = web_auth.create_web_session(bootstrap_admin)
            unauthed_client.cookies.set(web_auth.SESSION_COOKIE_NAME, sess)
            authed_res = unauthed_client.get("/api/stats")
            self.assertEqual(authed_res.status_code, 200)

            # After password is set, attempting to change password with incorrect old password fails
            wrong_pw_res = unauthed_client.post(
                "/api/account/password",
                json={"current_password": "wrong_password", "new_password": "AnotherPassword456!"},
            )
            self.assertEqual(wrong_pw_res.status_code, 400)

            # Changing with correct old password succeeds
            correct_pw_res = unauthed_client.post(
                "/api/account/password",
                json={"current_password": "AdminPassword123!", "new_password": "AnotherPassword456!"},
            )
            self.assertEqual(correct_pw_res.status_code, 200)

    def test_auth_enabled_in_multiuser_mode_without_env_password(self):
        with mock.patch.dict(os.environ, {"VERTEX_PASSWORD": "", "VERTEX_REQUIRE_AUTH": "true"}):
            # Two placeholder users without passwords
            system_db.ensure_user("user_a", db_path=self.sys_db_path)
            self.assertFalse(web_auth.is_web_auth_enabled(self.sys_db_path))

            system_db.ensure_user("user_b", db_path=self.sys_db_path)
            # In multi-user mode (count_active_users > 1), auth is enabled to protect user data
            self.assertTrue(web_auth.is_web_auth_enabled(self.sys_db_path))

    def test_auth_respects_explicit_require_auth_false(self):
        system_db.create_user("user_pass", "Password123!", db_path=self.sys_db_path)
        self.assertTrue(system_db.has_users_with_passwords(self.sys_db_path))
        with mock.patch.dict(os.environ, {"VERTEX_REQUIRE_AUTH": "false", "VERTEX_PASSWORD": ""}):
            self.assertFalse(web_auth.is_web_auth_enabled())

    def test_db_get_connection_fails_closed_without_user_context_in_multiuser(self):
        # Create 2 users to be in multi-user mode
        system_db.create_user("user1", "Password123!", db_path=self.sys_db_path)
        system_db.create_user("user2", "Password123!", db_path=self.sys_db_path)

        with self.assertRaises(RuntimeError):
            db.get_connection()

    # --- H2: OAuth 2.1 PKCE S256 & Binding ---

    def test_oauth_requires_pkce_s256(self):
        import oauth_provider
        client = TestClient(oauth_provider.app if hasattr(oauth_provider, "app") else None)
        # Authorize endpoint requires code_challenge
        from starlette.applications import Starlette
        oauth_app = Starlette(routes=oauth_provider.get_oauth_routes())
        client = TestClient(oauth_app)

        # Missing code_challenge
        r1 = client.get("/oauth/authorize", params={"client_id": "c1", "redirect_uri": "https://example.com/cb"})
        self.assertEqual(r1.status_code, 400)

        # Plain code_challenge_method rejected
        r2 = client.get("/oauth/authorize", params={
            "client_id": "c1",
            "redirect_uri": "https://example.com/cb",
            "code_challenge": "plain_challenge",
            "code_challenge_method": "plain",
        })
        self.assertEqual(r2.status_code, 400)

    def test_oauth_token_exchange_validates_redirect_uri_and_client_id(self):
        import oauth_provider
        from starlette.applications import Starlette
        oauth_app = Starlette(routes=oauth_provider.get_oauth_routes())
        client = TestClient(oauth_app)

        code = "gh_code_test_binding"
        oauth_provider._auth_codes[code] = {
            "client_id": "c1",
            "user_id": "u1",
            "redirect_uri": "https://example.com/cb",
            "code_challenge": "xyz",
            "code_challenge_method": "S256",
            "expires_at": time.time() + 600,
        }

        # Token exchange with mismatched redirect_uri must fail
        resp = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "c1",
            "redirect_uri": "https://evil.com/cb",
            "code_verifier": "xyz",
        })
        self.assertEqual(resp.status_code, 400)

        # Re-insert code and verify token exchange with missing or wrong client_id fails
        oauth_provider._auth_codes[code] = {
            "client_id": "c1",
            "user_id": "u1",
            "redirect_uri": "https://example.com/cb",
            "code_challenge": "xyz",
            "code_challenge_method": "S256",
            "expires_at": time.time() + 600,
        }
        resp2 = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "",
            "redirect_uri": "https://example.com/cb",
            "code_verifier": "xyz",
        })
        self.assertEqual(resp2.status_code, 400)
        self.assertEqual(resp2.json().get("error"), "invalid_grant")

    def test_oauth_token_exchange_validates_client_secret(self):
        import oauth_provider
        from starlette.applications import Starlette
        oauth_app = Starlette(routes=oauth_provider.get_oauth_routes())
        client = TestClient(oauth_app)

        code = "gh_code_test_secret"
        oauth_provider._auth_codes[code] = {
            "client_id": "game-hub-mcp",
            "user_id": "u1",
            "redirect_uri": "https://example.com/cb",
            "code_challenge": "xyz",
            "code_challenge_method": "S256",
            "expires_at": time.time() + 600,
        }

        with mock.patch.dict(os.environ, {"MCP_CLIENT_SECRET": "RealSuperSecret123"}):
            # Wrong secret rejected
            resp = client.post("/oauth/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": "game-hub-mcp",
                "client_secret": "WrongSecret",
                "redirect_uri": "https://example.com/cb",
                "code_verifier": "xyz",
            })
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(resp.json().get("error"), "invalid_client")

    def test_oauth_consent_requires_csrf_token_and_displays_redirect_host(self):
        import oauth_provider
        import re
        from starlette.applications import Starlette
        oauth_app = Starlette(routes=oauth_provider.get_oauth_routes())

        uid = system_db.create_user("dan", "Password123!", db_path=self.sys_db_path)
        sess = web_auth.create_web_session("dan")

        client = TestClient(oauth_app, cookies={web_auth.SESSION_COOKIE_NAME: sess})
        params = {
            "client_id": "c1",
            "redirect_uri": "https://example.com/callback",
            "code_challenge": "xyz",
            "code_challenge_method": "S256",
        }

        # GET renders consent page with redirect host and CSRF token
        page = client.get("/oauth/authorize", params=params)
        self.assertEqual(page.status_code, 200)
        self.assertIn("example.com", page.text)
        self.assertIn('name="csrf_token"', page.text)

        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        self.assertTrue(m, "Consent form must contain csrf_token hidden input")
        csrf_tok = m.group(1)

        # POST without CSRF token is rejected with 403 Forbidden
        bad_post = client.post("/oauth/authorize", data=params, follow_redirects=False)
        self.assertEqual(bad_post.status_code, 403)

        # POST with valid CSRF token succeeds with 302 Redirect
        good_post = client.post(
            "/oauth/authorize",
            data={**params, "csrf_token": csrf_tok},
            follow_redirects=False,
        )
        self.assertEqual(good_post.status_code, 302)
        self.assertIn("code=gh_code_", good_post.headers["location"])

    # --- H3: Local Bypass Default Disabled ---

    def test_local_bypass_defaults_to_false(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(web_auth.is_local_bypass_enabled())

    # --- H4: Platform Credentials Encrypted at Rest ---

    def test_platform_credentials_fernet_encryption(self):
        uid = system_db.create_user("encrypted_gamer", "Password123!", db_path=self.sys_db_path)
        system_db.set_user_connections(
            uid,
            xbl_api_key="xbl_super_secret_key_123",
            psn_npsso="psn_npsso_cookie_456",
            db_path=self.sys_db_path,
        )

        # Check raw database contents in SQLite
        with system_db.get_system_connection(self.sys_db_path) as conn:
            row = conn.execute("SELECT * FROM user_connections WHERE user_id = ?", (uid,)).fetchone()
            raw_xbl = row["xbl_api_key_encrypted"]
            raw_psn = row["psn_npsso_encrypted"]

            self.assertNotEqual(raw_xbl, "xbl_super_secret_key_123")
            self.assertNotEqual(raw_psn, "psn_npsso_cookie_456")
            self.assertTrue(raw_xbl.startswith("gAAAAA"), "XBL key must be stored Fernet-encrypted")
            self.assertTrue(raw_psn.startswith("gAAAAA"), "PSN NPSSO must be stored Fernet-encrypted")

        # Check decrypted return value
        conns = system_db.get_user_connections(uid, db_path=self.sys_db_path)
        self.assertEqual(conns["xbl_api_key"], "xbl_super_secret_key_123")
        self.assertEqual(conns["psn_npsso"], "psn_npsso_cookie_456")

    # --- H5: Scrubbing Keys from Logs & Sync Responses ---

    def test_steam_api_key_scrubbed_from_sync_logs(self):
        secret_key = "ABCDEF1234567890ABCDEF1234567890"
        err_msg = f"Client error '401 Unauthorized' for url 'https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={secret_key}&steamid=123'"
        
        db.log_sync_event("Steam", "error", 0, err_msg, db_path=self.default_db_path)
        with sqlite3.connect(self.default_db_path) as conn:
            conn.row_factory = sqlite3.Row
            logs = conn.execute("SELECT * FROM sync_logs").fetchall()
        self.assertTrue(len(logs) > 0)
        self.assertNotIn(secret_key, logs[0]["message"])
        self.assertIn("[REDACTED]", logs[0]["message"])

    def test_steam_id_validation(self):
        import sync_engine
        self.assertTrue(sync_engine.validate_steam_id("76561197960435530"))
        self.assertTrue(sync_engine.validate_steam_id("custom_vanity-name"))
        self.assertFalse(sync_engine.validate_steam_id("123&inject=true"))
        self.assertFalse(sync_engine.validate_steam_id("vanity/../../test"))

    # --- H6: Web Push SSRF Prevention ---

    def test_web_push_endpoint_validation(self):
        import app
        self.assertTrue(app.is_valid_push_endpoint("https://fcm.googleapis.com/fcm/send/sample-token"))
        self.assertTrue(app.is_valid_push_endpoint("https://updates.push.services.mozilla.com/wpush/v2/sample"))
        self.assertTrue(app.is_valid_push_endpoint("https://web.push.apple.com/sample"))
        # Reject non-https
        self.assertFalse(app.is_valid_push_endpoint("http://fcm.googleapis.com/fcm/send/sample-token"))
        # Reject loopback and local SSRF
        self.assertFalse(app.is_valid_push_endpoint("https://127.0.0.1:8096/mcp"))
        self.assertFalse(app.is_valid_push_endpoint("https://localhost:8090/healthz"))
        self.assertFalse(app.is_valid_push_endpoint("https://192.168.1.1/admin"))
        self.assertFalse(app.is_valid_push_endpoint("https://evil-push-gateway.attacker.com/steal"))

    # --- H8: Env Password Master Credential Mitigation ---

    def test_env_password_cannot_login_after_user_changes_password(self):
        uid = system_db.create_user("owner", "InitialSecret123!", role="admin", db_path=self.sys_db_path)
        # User changes password
        system_db.set_user_password(uid, "ChangedSecret456!", db_path=self.sys_db_path)

        with mock.patch.dict(os.environ, {"VERTEX_PASSWORD": "InitialSecret123!", "VERTEX_USERNAME": "owner"}):
            user = system_db.get_user(uid, db_path=self.sys_db_path)
            # Verify verify_password with the env password fails
            self.assertFalse(passwords.verify_password("InitialSecret123!", user.get("password_hash")))


    def test_host_header_missing_or_disallowed_rejected(self):
        import app
        client = TestClient(app.app)
        # Empty Host header
        r1 = client.get("/healthz", headers={"host": ""})
        self.assertEqual(r1.status_code, 400)

        # Disallowed Host header (DNS rebinding)
        r2 = client.get("/healthz", headers={"host": "evil-rebinding.com"})
        self.assertEqual(r2.status_code, 400)

        # Allowed localhost / testserver
        r3 = client.get("/healthz", headers={"host": "localhost:8090"})
        self.assertEqual(r3.status_code, 200)

    def test_multiuser_lifespan_startup_succeeds(self):
        import asyncio
        import app
        # Create 2 users to trigger is_multiuser_mode()
        system_db.create_user("user_a", "Pass1234!", db_path=self.sys_db_path)
        system_db.create_user("user_b", "Pass1234!", db_path=self.sys_db_path)
        self.assertTrue(db.is_multiuser_mode())

        async def run_lifespan():
            async with app.lifespan(app.app):
                pass

        # Should not raise RuntimeError in multi-user mode
        asyncio.run(run_lifespan())

    def test_init_db_in_multiuser_mode_without_user_context(self):
        # Create 2 users to trigger is_multiuser_mode()
        system_db.create_user("init_a", "Pass1234!", db_path=self.sys_db_path)
        system_db.create_user("init_b", "Pass1234!", db_path=self.sys_db_path)
        self.assertTrue(db.is_multiuser_mode())
        # Calling init_db() with no arguments when current_user_id is unset
        # should initialize DEFAULT_DB_PATH schema safely and not raise RuntimeError
        db.init_db()

    def test_multifernet_backward_compatible_decryption(self):
        # 1. Encrypt with VERTEX_PASSWORD
        with mock.patch.dict(os.environ, {"VERTEX_PASSWORD": "OldLegacyPassword123!"}, clear=True):
            enc_legacy = system_db.encrypt_credential("my_xbl_secret_key")
            self.assertTrue(enc_legacy.startswith("gAAAAA"))

        # 2. Admin later defines GAMEHUB_SECRET_KEY
        with mock.patch.dict(os.environ, {
            "GAMEHUB_SECRET_KEY": "NewStrongSecretKey456!",
            "VERTEX_PASSWORD": "OldLegacyPassword123!",
        }, clear=True):
            decrypted = system_db.decrypt_credential(enc_legacy)
            self.assertEqual(decrypted, "my_xbl_secret_key")

    def test_sanitize_sync_message_cookie_and_bearer_headers(self):
        cookie_text = "Client error 403 on Cookie: npsso=abcd1234efgh5678; session=xyz"
        sanitized = db.sanitize_sync_message(cookie_text)
        self.assertNotIn("abcd1234efgh5678", sanitized)
        self.assertIn("[REDACTED]", sanitized)

        auth_text = "Failed request with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test"
        sanitized_auth = db.sanitize_sync_message(auth_text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.test", sanitized_auth)
        self.assertIn("[REDACTED]", sanitized_auth)

    def test_create_web_session_fails_for_nonexistent_user(self):
        with self.assertRaises(ValueError):
            web_auth.create_web_session("nonexistent_ghost_user")

    def test_mcp_allowed_hosts_includes_public_domains(self):
        import app
        with mock.patch.dict(os.environ, {"MCP_PUBLIC_DOMAIN": "https://gaming-mcp.crjacobs.cc"}, clear=False):
            hosts = app.get_allowed_mcp_hosts()
            self.assertIn("gaming-mcp.crjacobs.cc", hosts)
            self.assertIn("gaming-mcp.crjacobs.cc:*", hosts)


if __name__ == "__main__":
    unittest.main()
