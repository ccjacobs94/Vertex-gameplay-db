"""
Tests for account lifecycle, password hashing, user roles, status transitions,
session invalidation, admin protections, and LAN bypass security.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import db
import passwords
import system_db
import web_auth
from context import get_current_user_id


class TestPasswordHashing(unittest.TestCase):
    def test_hash_and_verify_roundtrip(self):
        pw = "CorrectHorseBatteryStaple123!"
        hashed = passwords.hash_password(pw)
        self.assertTrue(hashed.startswith("scrypt$16384$8$1$"))
        self.assertTrue(passwords.verify_password(pw, hashed))
        self.assertFalse(passwords.verify_password("WrongPassword!", hashed))

    def test_legacy_placeholder_never_verifies(self):
        self.assertFalse(passwords.verify_password("any_password", "managed_by_web_auth"))
        self.assertFalse(passwords.verify_password("any_password", ""))
        self.assertFalse(passwords.verify_password("", "scrypt$16384$8$1$abc$def"))

    def test_password_policy(self):
        self.assertIsNotNone(passwords.validate_password_policy(""))
        self.assertIsNotNone(passwords.validate_password_policy("short"))
        self.assertIsNone(passwords.validate_password_policy("valid_password_123"))


class TestAccountLifecycle(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.system_db_path = str(Path(self.test_dir) / "system.db")
        system_db.init_system_db(self.system_db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_system_db_migration_adds_new_columns(self):
        with system_db.get_system_connection(self.system_db_path) as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
            self.assertIn("status", cols)
            self.assertIn("status_reason", cols)
            self.assertIn("role", cols)
            self.assertIn("updated_at", cols)
            self.assertIn("last_login_at", cols)

    def test_create_and_get_user(self):
        uid = system_db.create_user(
            username="gamer1",
            password="StrongPassword123!",
            email="gamer1@example.com",
            role="user",
            db_path=self.system_db_path,
        )
        self.assertEqual(uid, "gamer1")
        user = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "gamer1")
        self.assertEqual(user["email"], "gamer1@example.com")
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["status"], "active")
        self.assertTrue(passwords.verify_password("StrongPassword123!", user["password_hash"]))

    def test_invalid_usernames_rejected(self):
        with self.assertRaises(ValueError):
            system_db.create_user(
                username="../hacker",
                password="Password123!",
                db_path=self.system_db_path,
            )
        with self.assertRaises(ValueError):
            system_db.create_user(
                username="ab",  # too short (< 3 chars)
                password="Password123!",
                db_path=self.system_db_path,
            )
        with self.assertRaises(ValueError):
            system_db.create_user(
                username="User with spaces",
                password="Password123!",
                db_path=self.system_db_path,
            )

    def test_duplicate_username_rejected(self):
        system_db.create_user(
            username="uniqueuser",
            password="Password123!",
            db_path=self.system_db_path,
        )
        with self.assertRaises(ValueError):
            system_db.create_user(
                username="UniqueUser",  # Case-insensitive duplicate
                password="Password123!",
                db_path=self.system_db_path,
            )

    def test_admin_guardrails(self):
        admin_id = system_db.create_user(
            username="root_admin",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        self.assertEqual(system_db.count_active_admins(self.system_db_path), 1)

        # Cannot demote the last admin
        with self.assertRaises(ValueError):
            system_db.set_user_role(admin_id, "user", db_path=self.system_db_path)

        # Cannot deactivate the last admin
        with self.assertRaises(ValueError):
            system_db.set_user_status(admin_id, "inactive", db_path=self.system_db_path)

        # Create a second admin
        admin2_id = system_db.create_user(
            username="second_admin",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        self.assertEqual(system_db.count_active_admins(self.system_db_path), 2)

        # Self-deactivation prevention
        with self.assertRaises(ValueError):
            system_db.set_user_status(
                admin_id, "inactive", actor_id=admin_id, db_path=self.system_db_path
            )

        # Other admin can deactivate admin_id
        system_db.set_user_status(
            admin_id, "inactive", actor_id=admin2_id, db_path=self.system_db_path
        )
        self.assertEqual(system_db.count_active_admins(self.system_db_path), 1)

    def test_deactivation_revokes_sessions_and_tokens(self):
        uid = system_db.create_user(
            username="user_to_suspend",
            password="Password123!",
            db_path=self.system_db_path,
        )
        tok = system_db.create_personal_access_token(
            uid, label="test_pat", db_path=self.system_db_path
        )
        # Create web session in system.db
        sess_id = "test_sess_123"
        with system_db.get_system_connection(self.system_db_path) as conn:
            conn.execute(
                "INSERT INTO web_sessions (session_id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (sess_id, uid, 1000.0, 9999999999.0),
            )

        # Deactivate user
        system_db.set_user_status(
            uid, status="inactive", reason="admin_suspended", db_path=self.system_db_path
        )

        # Tokens and sessions must be gone
        with system_db.get_system_connection(self.system_db_path) as conn:
            tokens = conn.execute(
                "SELECT * FROM oauth_tokens WHERE user_id = ?", (uid,)
            ).fetchall()
            self.assertEqual(len(tokens), 0)
            sessions = conn.execute(
                "SELECT * FROM web_sessions WHERE user_id = ?", (uid,)
            ).fetchall()
            self.assertEqual(len(sessions), 0)

    def test_settings_storage(self):
        self.assertIsNone(system_db.get_setting("nonexistent", db_path=self.system_db_path))
        self.assertEqual(
            system_db.get_setting("nonexistent", default="foo", db_path=self.system_db_path),
            "foo",
        )
        system_db.set_setting("registration_policy", "invite", db_path=self.system_db_path)
        self.assertEqual(
            system_db.get_setting("registration_policy", db_path=self.system_db_path),
            "invite",
        )


class TestAccountApiEndpoints(unittest.TestCase):
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
        from starlette.testclient import TestClient
        import app
        self.client = TestClient(app.app, base_url="http://testserver")

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_account_info_and_password_change(self):
        system_db.create_user(
            username="gamer_dan",
            password="InitialPassword123!",
            db_path=self.system_db_path,
        )
        self.client.cookies.set(web_auth.SESSION_COOKIE_NAME, "mock_session_dan")
        with patch("web_auth.verify_web_session", return_value="gamer_dan"), \
             patch("app.verify_web_session", return_value="gamer_dan"):
            res = self.client.get("/api/account")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["username"], "gamer_dan")
            self.assertEqual(data["role"], "user")
            self.assertEqual(data["status"], "active")

            # Try changing password with wrong current password -> 400
            bad_pw_res = self.client.post(
                "/api/account/password",
                json={"current_password": "WrongPassword!", "new_password": "NewValidPassword123!"},
            )
            self.assertEqual(bad_pw_res.status_code, 400)

            # Change password successfully
            ok_res = self.client.post(
                "/api/account/password",
                json={
                    "current_password": "InitialPassword123!",
                    "new_password": "NewValidPassword123!",
                },
            )
            self.assertEqual(ok_res.status_code, 200)

        # Verify new password in system_db
        user = system_db.get_user("gamer_dan", db_path=self.system_db_path)
        self.assertTrue(passwords.verify_password("NewValidPassword123!", user["password_hash"]))

    def test_admin_endpoints_authorization(self):
        system_db.create_user(
            username="regular_user",
            password="Password123!",
            role="user",
            db_path=self.system_db_path,
        )
        system_db.create_user(
            username="admin_user",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )

        self.client.cookies.set(web_auth.SESSION_COOKIE_NAME, "mock_session_adm")

        # Regular user gets 403 Forbidden
        with patch("web_auth.verify_web_session", return_value="regular_user"), \
             patch("app.verify_web_session", return_value="regular_user"):
            res = self.client.get("/api/admin/users")
            self.assertEqual(res.status_code, 403)

        # Admin user gets 200 OK and lists users
        with patch("web_auth.verify_web_session", return_value="admin_user"), \
             patch("app.verify_web_session", return_value="admin_user"):
            res = self.client.get("/api/admin/users")
            self.assertEqual(res.status_code, 200)
            user_list = res.json().get("users", [])
            usernames = [u["username"] for u in user_list]
            self.assertIn("regular_user", usernames)
            self.assertIn("admin_user", usernames)

            # Admin creates new user
            create_res = self.client.post(
                "/api/admin/users",
                json={"username": "newbie", "role": "user"},
            )
            self.assertEqual(create_res.status_code, 200)
            self.assertIsNotNone(create_res.json().get("temporary_password"))

            # Admin deactivates newbie
            patch_res = self.client.patch(
                "/api/admin/users/newbie",
                json={"status": "inactive", "status_reason": "admin_suspended"},
            )
            self.assertEqual(patch_res.status_code, 200)
            self.assertEqual(patch_res.json()["user"]["status"], "inactive")

            # Admin resets newbie password
            reset_res = self.client.post(
                "/api/admin/users/newbie/reset-password",
                json={},
            )
            self.assertEqual(reset_res.status_code, 200)
            self.assertIsNotNone(reset_res.json().get("temporary_password"))


    def test_local_bypass_disables_with_multiple_active_users(self):
        system_db.create_user(
            username="admin_solo",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        # With 1 user and selfhosted mode, local bypass is enabled
        with patch.dict(os.environ, {"VERTEX_AUTH_BYPASS_LOCAL": "true", "VERTEX_EDITION": "selfhosted"}):
            self.assertTrue(web_auth.is_local_bypass_enabled())

            # Add second active user
            system_db.create_user(
                username="family_member",
                password="Password123!",
                role="user",
                db_path=self.system_db_path,
            )
            # Now local bypass MUST be disabled so family member doesn't automatically log in as admin
            self.assertFalse(web_auth.is_local_bypass_enabled())

