"""
Tests for self-hosted user registration policies (disabled, open, approval, invite),
invite lifecycle, bot honeypot protection, and admin registration settings.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import app
import db
import passwords
import signup
import system_db
import web_auth


class TestRegistrationPolicies(unittest.TestCase):
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

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_signup_disabled_by_default(self):
        # Default policy is disabled
        self.assertEqual(system_db.get_registration_policy(self.system_db_path), "disabled")
        res = self.client.get("/signup")
        self.assertEqual(res.status_code, 403)
        self.assertIn("Registration Closed", res.text)

        post_res = self.client.post(
            "/signup",
            data={
                "username": "intruder",
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
        )
        self.assertEqual(post_res.status_code, 403)
        self.assertIsNone(system_db.get_user_by_username("intruder", self.system_db_path))

    def test_signup_open_policy(self):
        system_db.set_registration_policy("open", self.system_db_path)
        res = self.client.get("/signup")
        self.assertEqual(res.status_code, 200)

        # Successful registration
        post_res = self.client.post(
            "/signup",
            data={
                "username": "open_gamer",
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
            follow_redirects=False,
        )
        self.assertEqual(post_res.status_code, 303)
        self.assertIn(web_auth.SESSION_COOKIE_NAME, post_res.cookies)

        user = system_db.get_user_by_username("open_gamer", self.system_db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["status"], "active")

    def test_signup_approval_policy(self):
        system_db.set_registration_policy("approval", self.system_db_path)

        post_res = self.client.post(
            "/signup",
            data={
                "username": "waitlist_user",
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
        )
        self.assertEqual(post_res.status_code, 200)
        self.assertIn("Registration Received", post_res.text)

        user = system_db.get_user_by_username("waitlist_user", self.system_db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["status"], "pending")
        self.assertEqual(user["status_reason"], "awaiting_approval")

        # Pending user tries to sign in at /login -> rejected
        login_res = self.client.post(
            "/login",
            data={"username": "waitlist_user", "password": "Password123!"},
        )
        self.assertEqual(login_res.status_code, 401)
        self.assertIn("This account is not active", login_res.text)

        # Admin approves user
        system_db.set_user_status(user["id"], "active", db_path=self.system_db_path)
        approved_login = self.client.post(
            "/login",
            data={"username": "waitlist_user", "password": "Password123!"},
            follow_redirects=False,
        )
        self.assertEqual(approved_login.status_code, 303)
        self.assertIn(web_auth.SESSION_COOKIE_NAME, approved_login.cookies)

    def test_signup_invite_policy(self):
        system_db.set_registration_policy("invite", self.system_db_path)

        # Generating invite as admin
        admin_id = system_db.create_user(
            username="admin1",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        code = system_db.create_invite(
            created_by=admin_id, note="For my friend", ttl_days=7, db_path=self.system_db_path
        )
        self.assertTrue(len(code) > 10)

        # Attempt registration with invalid invite
        bad_invite_res = self.client.post(
            "/signup",
            data={
                "username": "invited_friend",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "invite_code": "wrong_code_xyz",
            },
        )
        self.assertEqual(bad_invite_res.status_code, 400)
        self.assertIn("Invalid, expired, or already-used invite", bad_invite_res.text)

        # Attempt registration with valid invite
        ok_invite_res = self.client.post(
            "/signup",
            data={
                "username": "invited_friend",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "invite_code": code,
            },
            follow_redirects=False,
        )
        self.assertEqual(ok_invite_res.status_code, 303)
        self.assertIn(web_auth.SESSION_COOKIE_NAME, ok_invite_res.cookies)

        user = system_db.get_user_by_username("invited_friend", self.system_db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["status"], "active")

        # Re-using the same invite code fails
        reuse_res = self.client.post(
            "/signup",
            data={
                "username": "second_friend",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "invite_code": code,
            },
        )
        self.assertEqual(reuse_res.status_code, 400)
        self.assertIn("Invalid, expired, or already-used invite", reuse_res.text)

    def test_bot_honeypot_silently_redirects(self):
        system_db.set_registration_policy("open", self.system_db_path)
        res = self.client.post(
            "/signup",
            data={
                "username": "spambot",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "website": "http://spam-link.example.com",  # Honeypot filled!
            },
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/login")
        # Bot account must NOT have been created
        self.assertIsNone(system_db.get_user_by_username("spambot", self.system_db_path))

    def test_admin_settings_and_invites_api(self):
        system_db.create_user(
            username="main_admin",
            password="Password123!",
            role="admin",
            db_path=self.system_db_path,
        )
        self.client.cookies.set(web_auth.SESSION_COOKIE_NAME, "mock_admin_session")

        with patch("web_auth.verify_web_session", return_value="main_admin"), \
             patch("app.verify_web_session", return_value="main_admin"):

            # Get current settings
            res = self.client.get("/api/admin/settings")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["registration_policy"], "disabled")

            # Update settings
            put_res = self.client.put(
                "/api/admin/settings",
                json={"registration_policy": "approval"},
            )
            self.assertEqual(put_res.status_code, 200)
            self.assertEqual(put_res.json()["registration_policy"], "approval")
            self.assertEqual(system_db.get_registration_policy(self.system_db_path), "approval")

            # Create invite
            inv_res = self.client.post(
                "/api/admin/invites",
                json={"note": "VIP Pass", "ttl_days": 14},
            )
            self.assertEqual(inv_res.status_code, 200)
            code = inv_res.json()["code"]
            self.assertIn("/signup?invite=", inv_res.json()["invite_url"])

            # List invites
            list_res = self.client.get("/api/admin/invites")
            self.assertEqual(list_res.status_code, 200)
            invites = list_res.json()["invites"]
            self.assertEqual(len(invites), 1)
            code_hash = invites[0]["code_hash"]

            # Revoke invite
            del_res = self.client.delete(f"/api/admin/invites/{code_hash}")
            self.assertEqual(del_res.status_code, 200)

            # Confirm invite is gone
            list_res2 = self.client.get("/api/admin/invites")
            self.assertEqual(len(list_res2.json()["invites"]), 0)
