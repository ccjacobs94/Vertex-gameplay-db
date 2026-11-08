"""Web Push notification engine (RFC 8030 / VAPID) for Vertex Game Hub.

Manages VAPID ECDSA P-256 keypairs (auto-generation and persistence in system_db),
subscription registration, and background alert dispatch for wishlist deals and
Smart Upgrades.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric import ec

import db
import system_db

logger = logging.getLogger("gamehub.web_push")


def get_or_create_vapid_keys(db_path: Optional[str] = None) -> Dict[str, str]:
    """
    Retrieve or auto-generate ECDSA P-256 VAPID keypair.
    Persists to system_db instance_settings so self-hosted instances have
    zero-config push notifications out of the box.
    """
    env_pub = os.environ.get("VAPID_PUBLIC_KEY")
    env_priv = os.environ.get("VAPID_PRIVATE_KEY")
    if env_pub and env_priv:
        return {"public_key": env_pub.strip(), "private_key": env_priv.strip()}

    stored_pub = system_db.get_setting("vapid_public_key", db_path=db_path)
    stored_priv = system_db.get_setting("vapid_private_key", db_path=db_path)
    if stored_pub and stored_priv:
        # Private key is Fernet-encrypted at rest; legacy plaintext values pass through unchanged
        priv = system_db.decrypt_credential(stored_priv)
        if priv:
            return {"public_key": stored_pub, "private_key": priv}
        logger.error("Stored VAPID private key could not be decrypted; generating a new keypair.")

    # Auto-generate standard P-256 keypair
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_numbers = private_key.public_key().public_numbers()
    x_bytes = pub_numbers.x.to_bytes(32, byteorder="big")
    y_bytes = pub_numbers.y.to_bytes(32, byteorder="big")
    raw_pub = b"\x04" + x_bytes + y_bytes
    pub_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")

    priv_val = private_key.private_numbers().private_value
    priv_bytes = priv_val.to_bytes(32, byteorder="big")
    priv_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode("ascii")

    system_db.set_setting("vapid_public_key", pub_b64, db_path=db_path)
    system_db.set_setting("vapid_private_key", system_db.encrypt_credential(priv_b64), db_path=db_path)
    logger.info("Generated new VAPID P-256 keypair and saved to instance settings.")
    return {"public_key": pub_b64, "private_key": priv_b64}


def _send_raw_push(
    subscription_info: Dict[str, Any],
    payload_json_str: str,
    vapid_private_key: str,
    vapid_claims: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatches push notification via pywebpush with graceful fallback."""
    try:
        from pywebpush import webpush
        response = webpush(
            subscription_info=subscription_info,
            data=payload_json_str,
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
        )
        return {"status": "sent", "response": str(response)}
    except ImportError:
        logger.info(
            "pywebpush not installed in environment; logging push dispatch mock: %s",
            payload_json_str,
        )
        return {"status": "mock_delivered", "info": "pywebpush not installed"}
    except Exception as e:
        logger.warning("Web Push delivery failed to %s: %s", subscription_info.get("endpoint"), e)
        return {"status": "failed", "error": str(e)}


async def dispatch_wishlist_deal_alerts(
    deals: List[Dict[str, Any]],
    db_path: Optional[str] = None,
    system_db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluates deals for deduplication against last_alerted state and dispatches
    push notifications to all active subscriptions.
    """
    if not deals:
        return []

    subscriptions = db.list_active_push_subscriptions(db_path=db_path)
    if not subscriptions:
        return []

    keys = get_or_create_vapid_keys(db_path=system_db_path)
    claim_email = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@localhost.localdomain")
    vapid_claims = {"sub": claim_email}

    dispatched = []
    now = datetime.now(timezone.utc).isoformat()

    with db.get_connection(db_path) as conn:
        for deal in deals:
            w_item_id = deal.get("wishlist_item_id")
            fam = deal.get("platform_family")
            if not w_item_id or not fam:
                continue

            target_row = conn.execute(
                """
                SELECT id, last_alerted_at, last_alerted_price_usd, last_alerted_edition_id
                FROM wishlist_platform_targets
                WHERE wishlist_item_id = ? AND platform_family = ?
                """,
                (w_item_id, fam),
            ).fetchone()

            if not target_row:
                continue

            last_price = target_row["last_alerted_price_usd"]
            last_ed_id = target_row["last_alerted_edition_id"]
            current_price = float(deal["current_price_usd"])

            # Deduplication rules:
            # 1. Never alerted before
            # 2. Price is lower than last alerted price
            # 3. Higher tier edition drop (Smart Upgrade)
            should_alert = False
            if last_price is None:
                should_alert = True
            elif current_price < float(last_price) - 0.005:
                should_alert = True
            elif deal.get("is_smart_upgrade"):
                last_tier_rank = 1
                if last_ed_id is not None:
                    ed_row = conn.execute(
                        "SELECT tier_rank FROM game_editions WHERE id = ?", (last_ed_id,)
                    ).fetchone()
                    if ed_row:
                        last_tier_rank = int(ed_row["tier_rank"] or 1)
                if int(deal.get("tier_rank", 1)) > last_tier_rank:
                    should_alert = True

            if not should_alert:
                continue

            # Format notification
            title_prefix = "🌟 Smart Upgrade Deal:" if deal.get("is_smart_upgrade") else "🏷️ Target Price Met:"
            game_title = deal.get("game_title") or "Wishlist Game"
            notification_payload = {
                "title": f"{title_prefix} {game_title}",
                "body": deal.get("deal_headline") or f"${current_price:.2f} on {deal.get('platform_label')}",
                "icon": deal.get("cover_url") or "/static/icons/icon-192.png",
                "badge": "/static/icons/icon-192.png",
                "tag": f"deal-{w_item_id}-{fam}",
                "data": {
                    "url": "/#tab-wishlist",
                    "wishlist_item_id": w_item_id,
                    "store_url": deal.get("store_url"),
                    "deal": deal,
                },
            }
            payload_str = json.dumps(notification_payload)

            # Send to all subscriptions
            delivered_any = False
            for sub in subscriptions:
                sub_info = {
                    "endpoint": sub["endpoint"],
                    "keys": {
                        "p256dh": sub["p256dh_key"],
                        "auth": sub["auth_key"],
                    },
                }
                res = _send_raw_push(
                    subscription_info=sub_info,
                    payload_json_str=payload_str,
                    vapid_private_key=keys["private_key"],
                    vapid_claims=vapid_claims,
                )
                if res.get("status") in ("sent", "mock_delivered"):
                    delivered_any = True

            if not delivered_any:
                continue

            # Update target record
            conn.execute(
                """
                UPDATE wishlist_platform_targets
                SET last_alerted_at = ?,
                    last_alerted_price_usd = ?,
                    last_alerted_edition_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, current_price, deal.get("edition_id"), now, target_row["id"]),
            )
            dispatched.append(deal)

    return dispatched
