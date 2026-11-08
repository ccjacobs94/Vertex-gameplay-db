"""
Pluggable billing scaffold and Stripe integration for Vertex hosted edition.
Provides Stripe Checkout, Customer Portal session creation, HMAC webhook verification,
subscription lifecycle transitions, and guardrails protecting admin actions.
"""

import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

import httpx
from starlette.responses import HTMLResponse

import system_db
import web_auth

logger = logging.getLogger("gamehub.billing")


class BillingNotConfiguredError(Exception):
    """Raised when a billing action is requested but provider credentials are missing."""
    pass


@dataclass
class BillingEvent:
    event_id: str
    event_type: str
    provider: str = "stripe"
    customer_id: Optional[str] = None
    subscription_id: Optional[str] = None
    user_id: Optional[str] = None
    status: str = "active"
    current_period_end: Optional[str] = None


class BillingProvider(Protocol):
    def create_checkout_url(self, user: Dict[str, Any], return_url: str) -> str:
        ...

    def create_portal_url(self, user: Dict[str, Any], return_url: str) -> str:
        ...

    def verify_and_parse_webhook(self, payload: bytes, signature_header: str) -> Optional[BillingEvent]:
        ...


class StripeProvider:
    """Stripe billing provider using standard library HMAC and httpx."""

    def __init__(
        self,
        secret_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        price_id: Optional[str] = None,
    ):
        self.secret_key = secret_key or os.environ.get("STRIPE_SECRET_KEY", "").strip()
        self.webhook_secret = webhook_secret or os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        self.price_id = price_id or os.environ.get("STRIPE_PRICE_ID", "").strip()

    def _ensure_configured(self) -> None:
        if not self.secret_key or not self.price_id:
            raise BillingNotConfiguredError("Stripe keys (STRIPE_SECRET_KEY, STRIPE_PRICE_ID) are not configured")

    def create_checkout_url(self, user: Dict[str, Any], return_url: str) -> str:
        self._ensure_configured()
        data = {
            "mode": "subscription",
            "client_reference_id": user["id"],
            "line_items[0][price]": self.price_id,
            "line_items[0][quantity]": "1",
            "success_url": f"{return_url}?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": return_url,
        }
        if user.get("email"):
            data["customer_email"] = user["email"]

        resp = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            headers={"Authorization": f"Bearer {self.secret_key}"},
            data=data,
            timeout=15.0,
        )
        if resp.status_code >= 400:
            logger.error("Failed to create Stripe checkout session: %s", resp.text)
            raise RuntimeError(f"Stripe error: {resp.text}")
        return resp.json().get("url", "")

    def create_portal_url(self, user: Dict[str, Any], return_url: str) -> str:
        self._ensure_configured()
        sub = system_db.get_user_subscription(user["id"])
        if not sub or not sub.get("customer_id"):
            raise ValueError("No active customer found for this account to open billing portal")

        resp = httpx.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            headers={"Authorization": f"Bearer {self.secret_key}"},
            data={"customer": sub["customer_id"], "return_url": return_url},
            timeout=15.0,
        )
        if resp.status_code >= 400:
            logger.error("Failed to create Stripe customer portal session: %s", resp.text)
            raise RuntimeError(f"Stripe portal error: {resp.text}")
        return resp.json().get("url", "")

    def verify_and_parse_webhook(self, payload: bytes, signature_header: str) -> Optional[BillingEvent]:
        if not self.webhook_secret:
            raise BillingNotConfiguredError("STRIPE_WEBHOOK_SECRET is not configured")

        if not signature_header:
            logger.warning("Missing Stripe signature header")
            return None

        # Parse t=timestamp,v1=signature[,v1=...]
        sig_map: Dict[str, List[str]] = {}
        for item in signature_header.split(","):
            parts = item.split("=", 1)
            if len(parts) == 2:
                k, v = parts[0].strip(), parts[1].strip()
                sig_map.setdefault(k, []).append(v)

        timestamps = sig_map.get("t", [])
        v1_signatures = sig_map.get("v1", [])
        if not timestamps or not v1_signatures:
            logger.warning("Malformed Stripe signature header")
            return None

        ts = timestamps[0]
        signed_payload = f"{ts}.".encode("utf-8") + payload
        expected_sig = hmac.new(
            self.webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()

        matched = any(secrets.compare_digest(expected_sig, s) for s in v1_signatures)
        if not matched:
            logger.warning("Stripe webhook HMAC signature mismatch")
            return None

        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return None

        event_id = data.get("id")
        event_type = data.get("type")
        obj = data.get("data", {}).get("object", {})

        if not event_id or not event_type:
            return None

        user_id = None
        customer_id = obj.get("customer")
        subscription_id = None
        status = "active"
        current_period_end = None

        if event_type == "checkout.session.completed":
            user_id = obj.get("client_reference_id")
            subscription_id = obj.get("subscription")
            status = "active"
        elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
            subscription_id = obj.get("id")
            status = obj.get("status", "active")
            period_end_ts = obj.get("current_period_end")
            if period_end_ts:
                current_period_end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(period_end_ts))
        elif event_type == "customer.subscription.deleted":
            subscription_id = obj.get("id")
            status = "canceled"
        elif event_type == "invoice.payment_failed":
            subscription_id = obj.get("subscription")
            status = "past_due"
        else:
            # Unhandled event types are safely acknowledged
            return BillingEvent(
                event_id=event_id,
                event_type=event_type,
                status="ignored",
            )

        return BillingEvent(
            event_id=event_id,
            event_type=event_type,
            provider="stripe",
            customer_id=customer_id,
            subscription_id=subscription_id,
            user_id=user_id,
            status=status,
            current_period_end=current_period_end,
        )


class NullProvider:
    """Default null provider for self-hosted instances."""

    def create_checkout_url(self, user: Dict[str, Any], return_url: str) -> str:
        raise BillingNotConfiguredError("Billing is not available on this self-hosted instance")

    def create_portal_url(self, user: Dict[str, Any], return_url: str) -> str:
        raise BillingNotConfiguredError("Billing is not available on this self-hosted instance")

    def verify_and_parse_webhook(self, payload: bytes, signature_header: str) -> Optional[BillingEvent]:
        raise BillingNotConfiguredError("Billing webhooks are disabled on this self-hosted instance")


def get_billing_provider() -> BillingProvider:
    edition = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
    if edition == "hosted":
        return StripeProvider()
    return NullProvider()


def apply_billing_event(event: BillingEvent, db_path: Optional[str] = None) -> None:
    """Apply subscription state transition with idempotency and admin-protection guards."""
    if not event or not event.event_id:
        return

    if system_db.is_billing_event_seen(event.event_id, db_path=db_path):
        logger.info("Billing event %s already processed; ignoring", event.event_id)
        return

    if event.status == "ignored":
        system_db.record_billing_event_seen(event.event_id, db_path=db_path)
        return

    # Resolve user
    user = None
    if event.user_id:
        user = system_db.get_user(event.user_id, db_path=db_path)
    elif event.customer_id:
        user = system_db.get_user_by_customer_id(event.customer_id, db_path=db_path)

    if not user:
        logger.warning(
            "Billing event %s: cannot resolve user (user_id=%s, customer_id=%s)",
            event.event_id,
            event.user_id,
            event.customer_id,
        )
        system_db.record_billing_event_seen(event.event_id, db_path=db_path)
        return

    uid = user["id"]

    # Invariant Guard: admin_suspended CANNOT be overridden by billing!
    if user.get("status_reason") == "admin_suspended":
        logger.warning("User %s is admin_suspended; billing status %s will NOT activate account", uid, event.status)
        system_db.upsert_user_subscription(
            user_id=uid,
            provider=event.provider,
            status=event.status,
            customer_id=event.customer_id,
            subscription_id=event.subscription_id,
            current_period_end=event.current_period_end,
            db_path=db_path,
        )
        system_db.record_billing_event_seen(event.event_id, db_path=db_path)
        return

    # State transitions
    if event.status in ("active", "trialing"):
        system_db.set_user_status(uid, status="active", reason=None, db_path=db_path)
        logger.info("Account %s activated via billing event %s", uid, event.event_id)
    elif event.status == "past_due":
        # Keep active during grace / retry period, but log
        logger.warning("Subscription past_due for user %s", uid)
    elif event.status in ("canceled", "unpaid", "incomplete_expired"):
        system_db.set_user_status(
            uid, status="inactive", reason="subscription_lapsed", db_path=db_path
        )
        web_auth.revoke_sessions_for_username(user["username"])
        logger.info("Account %s marked inactive (subscription_lapsed) via event %s", uid, event.event_id)

    system_db.upsert_user_subscription(
        user_id=uid,
        provider=event.provider,
        status=event.status,
        customer_id=event.customer_id,
        subscription_id=event.subscription_id,
        current_period_end=event.current_period_end,
        db_path=db_path,
    )
    system_db.record_billing_event_seen(event.event_id, db_path=db_path)


def render_billing_page(
    user: Dict[str, Any],
    subscription: Optional[Dict[str, Any]] = None,
    error_msg: str = "",
) -> HTMLResponse:
    username = html.escape(user.get("username", ""))
    email = html.escape(user.get("email") or "None on file")
    status = user.get("status", "unknown")
    reason = user.get("status_reason") or ""
    sub_status = (subscription.get("status") if subscription else "No active subscription") or "No active subscription"
    period_end = subscription.get("current_period_end") if subscription else None

    # Status badge styling
    if status == "active":
        badge_class = "badge-active"
        badge_text = "Active Subscription"
    elif reason == "awaiting_payment":
        badge_class = "badge-pending"
        badge_text = "Subscription Required"
    elif reason == "subscription_lapsed":
        badge_class = "badge-lapsed"
        badge_text = "Subscription Inactive"
    elif reason == "admin_suspended":
        badge_class = "badge-suspended"
        badge_text = "Account Suspended"
    else:
        badge_class = "badge-neutral"
        badge_text = html.escape(status.capitalize())

    error_html = (
        f"""<div class="alert-box error" role="alert">
              <span>⚠️</span> <span>{html.escape(error_msg)}</span>
            </div>"""
        if error_msg
        else ""
    )

    period_end_html = (
        f"""<div class="info-row">
              <span class="label">Current Period End:</span>
              <span class="val">{html.escape(period_end)}</span>
            </div>"""
        if period_end
        else ""
    )

    if status == "active":
        action_html = """
        <div class="action-card">
          <p>Your subscription is active. Access your Stripe Customer Portal to update your payment method, view past receipts, or manage your renewal.</p>
          <div class="button-row">
            <form method="POST" action="/billing/portal">
              <button type="submit" class="btn-primary">Manage Subscription &rarr;</button>
            </form>
            <a href="/" class="btn-secondary">&larr; Return to Game Hub</a>
          </div>
        </div>
        """
    else:
        action_html = """
        <div class="action-card">
          <p>Subscribe to Vertex Hosted Edition to unlock unified cross-platform gaming sync, AI game advisor recommendations, and library analytics.</p>
          <div class="button-row">
            <form method="POST" action="/billing/checkout">
              <button type="submit" class="btn-primary">Start Monthly Subscription &rarr;</button>
            </form>
          </div>
        </div>
        """

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Subscription & Billing | Vertex</title>
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b0f19;
      --card-bg: #131b2e;
      --border: #26334d;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --green: #22c55e;
      --amber: #f59e0b;
      --red: #ef4444;
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100dvh;
      font-family: system-ui, -apple-system, sans-serif;
      background:
        radial-gradient(circle at 15% 20%, rgba(56, 189, 248, 0.1), transparent 45%),
        radial-gradient(circle at 85% 80%, rgba(139, 92, 246, 0.1), transparent 45%),
        var(--bg);
      color: var(--text);
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      padding: 1.5rem;
    }}
    .billing-card {{
      max-width: 520px; width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 2.25rem 2rem;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.55);
    }}
    .header {{
      display: flex; align-items: center; gap: 1rem; margin-bottom: 1.75rem;
      border-bottom: 1px solid var(--border); padding-bottom: 1.25rem;
    }}
    .header img {{ width: 48px; height: 48px; border-radius: 10px; }}
    .header h1 {{ margin: 0; font-size: 1.4rem; font-weight: 700; }}
    .header p {{ margin: 0.2rem 0 0; font-size: 0.85rem; color: var(--text-muted); }}
    .alert-box {{
      padding: 0.85rem 1rem; border-radius: 10px; font-size: 0.88rem; margin-bottom: 1.25rem;
      display: flex; align-items: center; gap: 0.6rem;
    }}
    .alert-box.error {{
      background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.45); color: #fca5a5;
    }}
    .details-box {{
      background: rgba(11, 15, 25, 0.6);
      border: 1px solid var(--border);
      border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem;
    }}
    .info-row {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 0.45rem 0; font-size: 0.9rem;
    }}
    .info-row:not(:last-child) {{ border-bottom: 1px dashed rgba(38, 51, 77, 0.7); }}
    .info-row .label {{ color: var(--text-muted); }}
    .info-row .val {{ font-weight: 600; color: #f8fafc; }}
    .badge {{
      display: inline-block; padding: 0.2rem 0.65rem; border-radius: 9999px;
      font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    }}
    .badge-active {{ background: rgba(34, 197, 94, 0.2); color: #86efac; border: 1px solid rgba(34, 197, 94, 0.5); }}
    .badge-pending {{ background: rgba(245, 158, 11, 0.2); color: #fde68a; border: 1px solid rgba(245, 158, 11, 0.5); }}
    .badge-lapsed {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.5); }}
    .badge-suspended {{ background: rgba(148, 163, 184, 0.2); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.5); }}
    .badge-neutral {{ background: rgba(56, 189, 248, 0.2); color: #bae6fd; border: 1px solid rgba(56, 189, 248, 0.5); }}
    .action-card {{
      background: rgba(30, 41, 59, 0.4); border: 1px solid var(--border);
      border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem;
    }}
    .action-card p {{ margin: 0 0 1rem 0; font-size: 0.88rem; line-height: 1.5; color: #cbd5e1; }}
    .button-row {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
    .btn-primary {{
      background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
      color: #fff; border: none; border-radius: 8px; padding: 0.75rem 1.25rem;
      font-size: 0.92rem; font-weight: 700; cursor: pointer; text-decoration: none;
      display: inline-flex; align-items: center; gap: 0.5rem;
      box-shadow: 0 4px 14px rgba(59, 130, 246, 0.35);
    }}
    .btn-primary:hover {{ filter: brightness(1.08); }}
    .btn-secondary {{
      background: transparent; color: var(--accent); border: 1px solid var(--accent);
      border-radius: 8px; padding: 0.75rem 1.25rem; font-size: 0.92rem; font-weight: 600;
      cursor: pointer; text-decoration: none; display: inline-flex; align-items: center;
    }}
    .btn-secondary:hover {{ background: rgba(56, 189, 248, 0.1); }}
    .footer {{
      display: flex; justify-content: space-between; align-items: center;
      font-size: 0.82rem; color: var(--text-muted); padding-top: 0.5rem;
    }}
    .footer a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="billing-card">
    <div class="header">
      <img src="/static/vertex_logo.png" alt="Vertex" width="48" height="48">
      <div>
        <h1>Subscription & Billing</h1>
        <p>Vertex Hosted Edition</p>
      </div>
    </div>

    {error_html}

    <div class="details-box">
      <div class="info-row">
        <span class="label">Account</span>
        <span class="val">{username}</span>
      </div>
      <div class="info-row">
        <span class="label">Email</span>
        <span class="val">{email}</span>
      </div>
      <div class="info-row">
        <span class="label">Subscription Status</span>
        <span class="val"><span class="badge {badge_class}">{badge_text}</span></span>
      </div>
      {period_end_html}
    </div>

    {action_html}

    <div class="footer">
      <span>Signed in as <strong>{username}</strong></span>
      <a href="/logout">Sign out &rarr;</a>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content, status_code=200)
