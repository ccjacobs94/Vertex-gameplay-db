"""Optional, disabled-by-default Web Push / VAPID notification delivery."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
import ipaddress
import json
from typing import Any, Callable
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import ec

from vertex.config import Settings
from vertex.identity.models import UserScope
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.platforms.base import sanitize_error_message
from vertex.wishlist.service import WishlistService

DEFAULT_ALLOWED_PUSH_HOSTS: tuple[str, ...] = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
    ".wns.windows.com",
)


class WebPushError(Exception):
    """Base error for Web Push operations."""


class WebPushDisabledError(WebPushError):
    """Raised when Web Push endpoints/operations are called while disabled."""


def is_valid_push_endpoint(
    endpoint: str,
    *,
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_PUSH_HOSTS,
) -> bool:
    """Validate push subscription URLs against an HTTPS allowlist (SSRF protection)."""
    raw = (endpoint or "").strip()
    if not raw or len(raw) > 2048:
        return False
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False

    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass

    for rule in allowed_hosts:
        cleaned = rule.strip().lower()
        if not cleaned:
            continue
        if cleaned.startswith("."):
            if host.endswith(cleaned) and len(host) > len(cleaned):
                return True
        elif host == cleaned:
            return True
    return False


def _generate_vapid_keypair() -> tuple[str, str]:
    """Generate an ECDSA P-256 VAPID (public_b64, private_b64) tuple."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    priv_num = private_key.private_numbers().private_value
    priv_bytes = priv_num.to_bytes(32, "big")
    pub_nums = private_key.public_key().public_numbers()
    pub_bytes = b"\x04" + pub_nums.x.to_bytes(32, "big") + pub_nums.y.to_bytes(32, "big")
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("ascii")
    priv_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode("ascii")
    return pub_b64, priv_b64


def _default_pywebpush_sender(
    subscription_info: dict[str, Any],
    data: str,
    vapid_private_key: str,
    vapid_claims: dict[str, str],
) -> None:
    from pywebpush import webpush

    webpush(
        subscription_info=subscription_info,
        data=data,
        vapid_private_key=vapid_private_key,
        vapid_claims=vapid_claims,
        timeout=10,
    )


class WebPushService:
    """Manages VAPID keys, push subscriptions, and deal alert fan-out."""

    def __init__(
        self,
        *,
        settings: Settings,
        db_manager: DatabaseManager,
        secret_box: SecretBox,
        wishlist_service: WishlistService,
        sender: Callable[[dict[str, Any], str, str, dict[str, str]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.db_manager = db_manager
        self.secret_box = secret_box
        self.wishlist_service = wishlist_service
        self.sender = sender or _default_pywebpush_sender

    @property
    def is_enabled(self) -> bool:
        return bool(self.settings.web_push_enabled)

    def _require_enabled(self) -> None:
        if not self.is_enabled:
            raise WebPushDisabledError("Web Push notifications are disabled on this instance")

    def _allowed_hosts(self) -> tuple[str, ...]:
        configured = tuple(self.settings.allowed_push_hosts or ())
        return configured if configured else DEFAULT_ALLOWED_PUSH_HOSTS

    def _read_setting(self, key: str) -> str | None:
        with self.db_manager.system_connection() as conn:
            row = conn.execute(
                "SELECT value FROM instance_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if not row or not row["value"]:
            return None
        return str(row["value"])

    def _write_setting(self, key: str, value: str) -> None:
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.db_manager.system_connection() as conn:
            conn.execute(
                """
                INSERT INTO instance_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value), now),
            )
            conn.commit()

    def get_or_create_vapid_keys(self) -> tuple[str, str]:
        """Return (public_key_b64, private_key_b64), generating and encrypting if absent."""
        self._require_enabled()

        if self.settings.vapid_public_key and self.settings.vapid_private_key:
            return self.settings.vapid_public_key, self.settings.vapid_private_key

        stored_pub = self._read_setting("vapid_public_key")
        stored_priv_enc = self._read_setting("vapid_private_key")
        if stored_pub and stored_priv_enc:
            try:
                stored_priv = self.secret_box.decrypt(stored_priv_enc)
                if stored_priv:
                    return stored_pub, stored_priv
            except Exception:
                pass

        pub_b64, priv_b64 = _generate_vapid_keypair()
        self._write_setting("vapid_public_key", pub_b64)
        self._write_setting("vapid_private_key", self.secret_box.encrypt(priv_b64))
        return pub_b64, priv_b64

    def get_public_key(self) -> str:
        pub_b64, _ = self.get_or_create_vapid_keys()
        return pub_b64

    def subscribe(
        self,
        scope: UserScope,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str = "",
    ):
        self._require_enabled()
        if not is_valid_push_endpoint(endpoint, allowed_hosts=self._allowed_hosts()):
            raise WebPushError("Invalid or disallowed Web Push endpoint URL")
        if not (p256dh or "").strip() or not (auth or "").strip():
            raise WebPushError("Subscription keys p256dh and auth are required")
        return self.wishlist_service.upsert_push_subscription(
            scope,
            endpoint=endpoint.strip(),
            p256dh_key=p256dh.strip(),
            auth_key=auth.strip(),
            user_agent=(user_agent or "")[:255],
        )

    def unsubscribe(self, scope: UserScope, *, endpoint: str) -> bool:
        self._require_enabled()
        return self.wishlist_service.delete_push_subscription(scope, endpoint.strip())

    def _deliver_one(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        payload_json: str,
        priv_b64: str,
    ) -> tuple[bool, bool, str | None]:
        """Deliver a single notification; returns (ok, expired, error_msg)."""
        if not is_valid_push_endpoint(endpoint, allowed_hosts=self._allowed_hosts()):
            return False, True, "Blocked disallowed push endpoint"
        sub_info = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        }
        claim_email = self.settings.vapid_claim_email or "mailto:admin@gamehub.local"
        if not claim_email.startswith("mailto:"):
            claim_email = f"mailto:{claim_email}"
        try:
            self.sender(sub_info, payload_json, priv_b64, {"sub": claim_email})
            return True, False, None
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                return False, True, "Subscription expired"
            return False, False, sanitize_error_message(str(exc))

    async def send_test_notification(self, scope: UserScope) -> dict[str, Any]:
        self._require_enabled()
        subs = self.wishlist_service.list_push_subscriptions(scope)
        if not subs:
            return {"status": "no_subscriptions", "sent": 0, "failed": 0, "removed": 0, "errors": []}

        _, priv_b64 = self.get_or_create_vapid_keys()
        payload_json = json.dumps(
            {
                "title": "Vertex Deals — Test Alert",
                "body": "Web Push notifications are active for your wishlist.",
                "url": "/#wishlist",
                "tag": "vertex-test",
            }
        )
        sent = 0
        failed = 0
        removed = 0
        errors: list[str] = []

        for sub in subs:
            ok, expired, err = await asyncio.to_thread(
                self._deliver_one,
                endpoint=sub.endpoint,
                p256dh=sub.p256dh_key,
                auth=sub.auth_key,
                payload_json=payload_json,
                priv_b64=priv_b64,
            )
            if ok:
                sent += 1
            else:
                failed += 1
                if err:
                    errors.append(err)
                if expired:
                    self.wishlist_service.delete_push_subscription(scope, sub.endpoint)
                    removed += 1

        return {
            "status": "ok" if sent > 0 else "failed",
            "sent": sent,
            "failed": failed,
            "removed": removed,
            "errors": errors,
        }

    async def dispatch_deal_alerts(self, scope: UserScope) -> dict[str, Any]:
        """Deliver Web Push alerts for newly triggered deals and mark notified targets."""
        if not self.is_enabled:
            return {"enabled": False, "alerts": 0, "sent": 0, "failed": 0, "removed": 0}

        subs = self.wishlist_service.list_push_subscriptions(scope)
        all_deals = self.wishlist_service.evaluate_deals(scope).deals
        deals = self.wishlist_service.select_unalerted_deals(scope, all_deals)
        if not subs or not deals:
            return {
                "enabled": True,
                "alerts": len(deals),
                "sent": 0,
                "failed": 0,
                "removed": 0,
            }

        _, priv_b64 = self.get_or_create_vapid_keys()
        top_titles = ", ".join(d.game_title for d in deals[:3])
        if len(deals) > 3:
            top_titles += f" (+{len(deals) - 3} more)"
        body = (
            f"{deals[0].game_title} dropped to ${deals[0].current_price_usd:.2f}"
            if len(deals) == 1
            else f"{len(deals)} wishlist deals active: {top_titles}"
        )
        payload_json = json.dumps(
            {
                "title": "Vertex Wishlist Deal Alert",
                "body": body,
                "url": "/#wishlist",
                "tag": "vertex-deals",
                "deals": [
                    {
                        "wishlist_item_id": d.wishlist_item_id,
                        "title": d.game_title,
                        "price_usd": d.current_price_usd,
                        "storefront": d.storefront,
                    }
                    for d in deals[:5]
                ],
            }
        )

        sent = 0
        failed = 0
        removed = 0
        for sub in subs:
            ok, expired, _ = await asyncio.to_thread(
                self._deliver_one,
                endpoint=sub.endpoint,
                p256dh=sub.p256dh_key,
                auth=sub.auth_key,
                payload_json=payload_json,
                priv_b64=priv_b64,
            )
            if ok:
                sent += 1
            else:
                failed += 1
                if expired:
                    self.wishlist_service.delete_push_subscription(scope, sub.endpoint)
                    removed += 1

        # Only mark targets alerted when at least one push message actually succeeded
        if sent > 0:
            for deal in deals:
                self.wishlist_service.mark_target_alerted(
                    scope,
                    wishlist_item_id=deal.wishlist_item_id,
                    platform_family=deal.platform_family,
                    price_usd=deal.current_price_usd,
                    edition_id=deal.edition_id,
                )

        return {
            "enabled": True,
            "alerts": len(deals),
            "sent": sent,
            "failed": failed,
            "removed": removed,
        }
