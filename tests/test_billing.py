"""
Tests for Vertex Hosted Edition billing scaffold, Stripe HMAC webhook verification,
subscription transitions, billing-restricted web/API sessions, and admin protection invariants.
"""

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import app
import billing
import db
import system_db
import web_auth


def _make_stripe_signature(payload: bytes, secret: str, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


class TestBillingWebhookVerification(unittest.TestCase):
    def setUp(self):
        self.secret = "whsec_test_secret_12345"
        self.provider = billing.StripeProvider(
            secret_key="sk_test_123",
            webhook_secret=self.secret,
            price_id="price_monthly_999",
        )

    def test_webhook_unconfigured_raises(self):
        unconfigured = billing.StripeProvider(secret_key="", webhook_secret="", price_id="")
        with self.assertRaises(billing.BillingNotConfiguredError):
            unconfigured.verify_and_parse_webhook(b"{}", "t=1,v1=abc")

    def test_valid_signature_parses_checkout_completed(self):
        payload_dict = {
            "id": "evt_checkout_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": "usr_abc",
                    "customer": "cus_999",
                    "subscription": "sub_888",
                }
            },
        }
        payload = json.dumps(payload_dict).encode("utf-8")
        ts = int(time.time())
        sig = _make_stripe_signature(payload, self.secret, ts)

        event = self.provider.verify_and_parse_webhook(payload, sig)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_id, "evt_checkout_123")
        self.assertEqual(event.event_type, "checkout.session.completed")
        self.assertEqual(event.user_id, "usr_abc")
        self.assertEqual(event.customer_id, "cus_999")
        self.assertEqual(event.subscription_id, "sub_888")
        self.assertEqual(event.status, "active")

    def test_tampered_payload_rejected(self):
        payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
        ts = int(time.time())
        sig = _make_stripe_signature(payload, self.secret, ts)
        tampered_payload = b'{"id":"evt_1","type":"checkout.session.completed","tampered":true}'

        event = self.provider.verify_and_parse_webhook(tampered_payload, sig)
        self.assertIsNone(event)

    def test_invalid_signature_rejected(self):
        payload = b'{"id":"evt_1"}'
        event = self.provider.verify_and_parse_webhook(payload, "t=123,v1=bad_signature")
        self.assertIsNone(event)

    def test_missing_or_malformed_header_rejected(self):
        payload = b'{"id":"evt_1"}'
        self.assertIsNone(self.provider.verify_and_parse_webhook(payload, ""))
        self.assertIsNone(self.provider.verify_and_parse_webhook(payload, "invalid_header"))


class TestBillingLifecycleAndTransitions(unittest.TestCase):
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

    def tearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        db.USERS_DB_DIR = self.orig_users_dir
        db.DEFAULT_DB_PATH = self.orig_default_db
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_checkout_activates_pending_user(self):
        uid = system_db.create_user(
            username="sub_user",
            password="Password123!",
            status="pending",
            status_reason="awaiting_payment",
            db_path=self.system_db_path,
        )
        user = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertEqual(user["status"], "pending")

        event = billing.BillingEvent(
            event_id="evt_start_1",
            event_type="checkout.session.completed",
            user_id=uid,
            customer_id="cus_111",
            subscription_id="sub_111",
            status="active",
        )
        billing.apply_billing_event(event, db_path=self.system_db_path)

        user_after = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertEqual(user_after["status"], "active")
        self.assertIsNone(user_after["status_reason"])

        sub = system_db.get_user_subscription(uid, db_path=self.system_db_path)
        self.assertIsNotNone(sub)
        self.assertEqual(sub["customer_id"], "cus_111")
        self.assertEqual(sub["subscription_id"], "sub_111")
        self.assertEqual(sub["status"], "active")

    def test_idempotent_event_processing(self):
        uid = system_db.create_user(
            username="idem_user",
            password="Password123!",
            status="pending",
            status_reason="awaiting_payment",
            db_path=self.system_db_path,
        )
        event = billing.BillingEvent(
            event_id="evt_idem_1",
            event_type="checkout.session.completed",
            user_id=uid,
            customer_id="cus_222",
            subscription_id="sub_222",
            status="active",
        )
        billing.apply_billing_event(event, db_path=self.system_db_path)
        self.assertTrue(system_db.is_billing_event_seen("evt_idem_1", db_path=self.system_db_path))

        # Re-applying same event should be a no-op
        billing.apply_billing_event(event, db_path=self.system_db_path)
        user = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertEqual(user["status"], "active")

    def test_admin_suspended_guardrail(self):
        """Invariant Guard: Billing events MUST NEVER override an admin suspension."""
        uid = system_db.create_user(
            username="bad_actor",
            password="Password123!",
            status="inactive",
            status_reason="admin_suspended",
            db_path=self.system_db_path,
        )
        event = billing.BillingEvent(
            event_id="evt_susp_attempt",
            event_type="checkout.session.completed",
            user_id=uid,
            customer_id="cus_suspended",
            subscription_id="sub_suspended",
            status="active",
        )
        billing.apply_billing_event(event, db_path=self.system_db_path)

        # Account MUST remain inactive with reason admin_suspended
        user = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertEqual(user["status"], "inactive")
        self.assertEqual(user["status_reason"], "admin_suspended")

        # But subscription record is still updated
        sub = system_db.get_user_subscription(uid, db_path=self.system_db_path)
        self.assertEqual(sub["status"], "active")

    def test_subscription_deleted_marks_inactive(self):
        uid = system_db.create_user(
            username="canceler",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        system_db.upsert_user_subscription(
            user_id=uid,
            provider="stripe",
            status="active",
            customer_id="cus_canceler",
            subscription_id="sub_canceler",
            db_path=self.system_db_path,
        )

        event = billing.BillingEvent(
            event_id="evt_canceled_1",
            event_type="customer.subscription.deleted",
            customer_id="cus_canceler",
            subscription_id="sub_canceler",
            status="canceled",
        )
        billing.apply_billing_event(event, db_path=self.system_db_path)

        user = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertEqual(user["status"], "inactive")
        self.assertEqual(user["status_reason"], "subscription_lapsed")


class TestBillingEndpointsAndSessions(unittest.TestCase):
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

    def test_billing_route_404_on_selfhosted(self):
        system_db.set_setting("edition", "selfhosted", db_path=self.system_db_path)
        uid = system_db.create_user(
            username="selfhosted_user",
            password="Password123!",
            status="active",
            db_path=self.system_db_path,
        )
        login_res = self.client.post(
            "/login",
            data={"username": "selfhosted_user", "password": "Password123!"},
            follow_redirects=False,
        )
        session_cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        headers = {"Cookie": f"{web_auth.SESSION_COOKIE_NAME}={session_cookie}"}
        res = self.client.get("/billing", headers=headers)
        self.assertEqual(res.status_code, 404)

    def test_hosted_registration_sets_awaiting_payment_and_redirects_to_billing(self):
        system_db.set_setting("edition", "hosted", db_path=self.system_db_path)
        system_db.set_registration_policy("open", db_path=self.system_db_path)

        res = self.client.post(
            "/signup",
            data={
                "username": "new_subscriber",
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers.get("location"), "/billing")
        self.assertIn(web_auth.SESSION_COOKIE_NAME, res.cookies)

        user = system_db.get_user_by_username("new_subscriber", db_path=self.system_db_path)
        self.assertIsNotNone(user)
        self.assertEqual(user["status"], "pending")
        self.assertEqual(user["status_reason"], "awaiting_payment")

    def test_billing_session_enforcement(self):
        system_db.set_setting("edition", "hosted", db_path=self.system_db_path)
        uid = system_db.create_user(
            username="unpaid_user",
            password="Password123!",
            status="pending",
            status_reason="awaiting_payment",
            db_path=self.system_db_path,
        )

        # Login creates a billing-allowed session
        login_res = self.client.post(
            "/login",
            data={"username": "unpaid_user", "password": "Password123!"},
            follow_redirects=False,
        )
        self.assertEqual(login_res.status_code, 303)
        self.assertEqual(login_res.headers.get("location"), "/billing")
        session_cookie = login_res.cookies.get(web_auth.SESSION_COOKIE_NAME)
        self.assertIsNotNone(session_cookie)

        headers = {"Cookie": f"{web_auth.SESSION_COOKIE_NAME}={session_cookie}"}

        # 1. Access to /billing is permitted
        billing_res = self.client.get("/billing", headers=headers)
        self.assertEqual(billing_res.status_code, 200)
        self.assertIn("Subscription & Billing", billing_res.text)
        self.assertIn("Subscription Required", billing_res.text)

        # 2. Access to library API (/api/games) is blocked with 402
        api_res = self.client.get("/api/games", headers=headers)
        self.assertEqual(api_res.status_code, 402)
        self.assertEqual(api_res.json().get("error"), "subscription_required")

        # 3. Access to main page (/) redirects to /billing
        home_res = self.client.get("/", headers=headers, follow_redirects=False)
        self.assertEqual(home_res.status_code, 303)
        self.assertEqual(home_res.headers.get("location"), "/billing")

    @patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_test_secret", "VERTEX_EDITION": "hosted"})
    def test_webhook_endpoint_processing(self):
        system_db.set_setting("edition", "hosted", db_path=self.system_db_path)
        uid = system_db.create_user(
            username="paying_user",
            password="Password123!",
            status="pending",
            status_reason="awaiting_payment",
            db_path=self.system_db_path,
        )

        payload_dict = {
            "id": "evt_webhook_test_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": uid,
                    "customer": "cus_pay_1",
                    "subscription": "sub_pay_1",
                }
            },
        }
        payload = json.dumps(payload_dict).encode("utf-8")
        ts = int(time.time())
        sig = _make_stripe_signature(payload, "whsec_test_secret", ts)

        res = self.client.post(
            "/billing/webhook",
            content=payload,
            headers={"stripe-signature": sig, "content-type": "application/json"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("received"))

        # Verify account activated
        user = system_db.get_user(uid, db_path=self.system_db_path)
        self.assertEqual(user["status"], "active")
        self.assertIsNone(user["status_reason"])
