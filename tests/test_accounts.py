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

    def test_delete_user_and_all_associated_data(self):
        users_dir = Path(self.test_dir) / "users"
        users_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(db, "USERS_DB_DIR", str(users_dir)):
            uid = system_db.create_user(
                username="user_to_delete",
                password="Password123!",
                email="delete_me@example.com",
                role="user",
                db_path=self.system_db_path,
            )
            # Create associated data:
            # 1. User DB and files on disk
            user_folder = users_dir / uid
            user_folder.mkdir(parents=True, exist_ok=True)
            user_db_file = user_folder / "games.db"
            db.init_db(str(user_db_file))
            (user_folder / "backup.bak").write_text("backup-data")
            self.assertTrue(user_folder.exists())
            self.assertTrue(user_db_file.exists())

            # 2. Connections, tokens, sessions, events, invites
            system_db.set_user_connections(uid, steam_id="76561198000000001", db_path=self.system_db_path)
            tok = system_db.create_personal_access_token(uid, label="del_pat", db_path=self.system_db_path)
            with system_db.get_system_connection(self.system_db_path) as conn:
                conn.execute(
                    "INSERT INTO web_sessions (session_id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    ("sess_del_1", uid, 1000.0, 9999999999.0),
                )
                conn.execute(
                    "INSERT INTO invites (code_hash, created_by, created_at) VALUES (?, ?, ?)",
                    ("hash_del_invite", uid, "2026-11-01T00:00:00Z"),
                )

            # Perform deletion
            result = system_db.delete_user(uid, db_path=self.system_db_path, users_dir=str(users_dir))
            self.assertEqual(result["deleted_user_id"], uid)

            # Assert user is deleted from users table
            self.assertIsNone(system_db.get_user(uid, db_path=self.system_db_path))

            # Assert all associated DB data is purged
            with system_db.get_system_connection(self.system_db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM user_connections WHERE user_id = ?", (uid,)).fetchone()["c"], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM web_sessions WHERE user_id = ?", (uid,)).fetchone()["c"], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM oauth_tokens WHERE user_id = ?", (uid,)).fetchone()["c"], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM account_events WHERE user_id = ?", (uid,)).fetchone()["c"], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM invites WHERE created_by = ?", (uid,)).fetchone()["c"], 0)

            # Assert user directory on disk is completely gone
            self.assertFalse(user_folder.exists())

    def test_delete_user_guardrails(self):
        admin1 = system_db.create_user(
            username="admin_one",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        # Cannot delete non-existent user
        with self.assertRaises(ValueError):
            system_db.delete_user("ghost_user", db_path=self.system_db_path)

        # Self-deletion prevention
        with self.assertRaises(ValueError):
            system_db.delete_user(admin1, actor_id=admin1, db_path=self.system_db_path)

        # Cannot delete last active admin
        with self.assertRaises(ValueError):
            system_db.delete_user(admin1, actor_id="another_actor", db_path=self.system_db_path)

        # If second admin exists, deletion by other admin succeeds
        admin2 = system_db.create_user(
            username="admin_two",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        res = system_db.delete_user(admin1, actor_id=admin2, db_path=self.system_db_path)
        self.assertEqual(res["deleted_user_id"], admin1)
        self.assertEqual(system_db.count_active_admins(self.system_db_path), 1)

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

    def test_admin_delete_user_endpoint_and_guardrails(self):
        system_db.create_user(
            username="victim_user",
            password="Password123!",
            role="user",
            db_path=self.system_db_path,
        )
        # Ensure user folder exists
        victim_dir = Path(self.users_dir) / "victim_user"
        victim_dir.mkdir(parents=True, exist_ok=True)
        (victim_dir / "games.db").touch()
        self.assertTrue(victim_dir.exists())

        # Non-admin gets 403
        with patch("web_auth.verify_web_session", return_value="gamer_dan"), \
             patch("app.verify_web_session", return_value="gamer_dan"):
            res = self.client.delete("/api/admin/users/victim_user")
            self.assertEqual(res.status_code, 403)

        # Admin session
        system_db.create_user(
            username="the_admin",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        with patch("web_auth.verify_web_session", return_value="the_admin"), \
             patch("app.verify_web_session", return_value="the_admin"):
            # Cannot delete non-existent user -> 404
            res_404 = self.client.delete("/api/admin/users/nobody")
            self.assertEqual(res_404.status_code, 404)

            # Cannot delete self -> 400
            res_self = self.client.delete("/api/admin/users/the_admin")
            self.assertEqual(res_self.status_code, 400)
            self.assertIn("own account", res_self.json().get("detail", ""))

            # Successfully delete victim_user -> 200
            del_res = self.client.delete("/api/admin/users/victim_user")
            self.assertEqual(del_res.status_code, 200)
            self.assertEqual(del_res.json()["status"], "success")

            # Verify user and folder are gone
            self.assertIsNone(system_db.get_user("victim_user", db_path=self.system_db_path))
            self.assertFalse(victim_dir.exists())

    def test_admin_disable_and_enable_user(self):
        system_db.create_user(
            username="target_user",
            password="Password123!",
            role="user",
            db_path=self.system_db_path,
        )
        system_db.create_user(
            username="boss_admin",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        with patch("web_auth.verify_web_session", return_value="boss_admin"), \
             patch("app.verify_web_session", return_value="boss_admin"):
            # Disable user via status="inactive" or status="disabled"
            res = self.client.patch(
                "/api/admin/users/target_user",
                json={"status": "disabled"},
            )
            self.assertEqual(res.status_code, 200)
            user = system_db.get_user("target_user", db_path=self.system_db_path)
            self.assertEqual(user["status"], "inactive")
            self.assertEqual(user["status_reason"], "admin_suspended")

            # Re-enable user via status="active"
            res_active = self.client.patch(
                "/api/admin/users/target_user",
                json={"status": "active"},
            )
            self.assertEqual(res_active.status_code, 200)
            user_active = system_db.get_user("target_user", db_path=self.system_db_path)
            self.assertEqual(user_active["status"], "active")


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

