"""Synthetic legacy Game Hub multi-user and single-user database fixtures."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet

import db as legacy_db
import passwords as legacy_passwords
import system_db as legacy_system_db


def _legacy_encrypt(secret: str, plaintext: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def build_synthetic_multiuser_legacy_dir(
    root: Path,
    *,
    legacy_secret_key: str = "legacy-fixture-secret-key-1234",
) -> Path:
    """Create a realistic legacy data dir using legacy system_db.init_system_db & db.init_db."""
    root.mkdir(parents=True, exist_ok=True)
    sys_path = str(root / "system.db")
    legacy_system_db.init_system_db(sys_path)

    now = "2026-10-01T12:00:00+00:00"
    alice_hash = legacy_passwords.hash_password("AlicePass!2026")
    bob_hash = legacy_passwords.hash_password("BobPass!2026")
    other_hash = legacy_passwords.hash_password("OtherPass!2026")

    conn = sqlite3.connect(sys_path)
    conn.row_factory = sqlite3.Row
    try:
        users = [
            ("alice", "alice", None, alice_hash, "active", None, "admin", "America/Chicago", json.dumps([{"tz": "America/Chicago", "effective_from": "2026-01-01T00:00:00+00:00"}])),
            ("u-bob", "Bob", None, bob_hash, "active", None, "user", "UTC", "[]"),
            ("carol", "carol", None, other_hash, "awaiting_payment", "checkout_started", "user", "UTC", "[]"),
            ("dave", "dave", None, other_hash, "awaiting_approval", "needs_admin", "user", "UTC", "[]"),
            ("eve", "eve", None, other_hash, "subscription_lapsed", "invoice_failed", "user", "UTC", "[]"),
            ("frank", "frank", None, other_hash, "admin_suspended", "abuse", "user", "UTC", "[]"),
        ]
        for uid, uname, email, ph, st, sr, role, tz, eras in users:
            conn.execute(
                """
                INSERT INTO users (
                    id, username, email, password_hash, created_at,
                    status, status_reason, role, updated_at, timezone, timezone_eras
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, uname, email, ph, now, st, sr, role, now, tz, eras),
            )

        conn.execute(
            """
            INSERT INTO user_connections (
                user_id, steam_id, xbl_api_key_encrypted, psn_npsso_encrypted,
                xbox_gamertag, ha_url, ha_token_encrypted, ha_entity_id,
                onboarding_completed, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "alice",
                "76561198000000001",
                _legacy_encrypt(legacy_secret_key, "xbl-secret-token"),
                _legacy_encrypt(legacy_secret_key, "psn-npsso-secret"),
                "AliceTag",
                "http://homeassistant.local:8123",
                _legacy_encrypt(legacy_secret_key, "ha-long-token"),
                "sensor.xbox_playing",
                1,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO web_sessions (session_id, user_id, created_at, expires_at, user_agent, username) VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-sess-alice", "alice", 1000.0, 9999999999.0, "Mozilla", "alice"),
        )
        conn.execute(
            "INSERT INTO oauth_tokens (access_token, refresh_token, client_id, user_id, scope, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-oauth-tok", "legacy-ref", "client-1", "alice", "read write", 1000.0),
        )
        conn.execute(
            "INSERT INTO catalog_cache (normalized_title, steam_appid, genres_json, release_year, critic_score, cover_url, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("hades", "1145360", '["Roguelike", "Action RPG"]', 2020, 93, "https://example.invalid/hades.jpg", now),
        )
        conn.execute(
            "INSERT INTO instance_settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("signup_mode", "invite", now),
        )
        conn.execute(
            "INSERT INTO instance_settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("instance_name", "Household Hub", now),
        )
        conn.execute(
            "INSERT INTO subscriptions (user_id, provider, customer_id, subscription_id, status, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("eve", "stripe", "cus_123", "sub_123", "canceled", now),
        )
        conn.execute(
            "INSERT INTO billing_events_seen (event_id, received_at) VALUES (?, ?)",
            ("evt_legacy_1", now),
        )
        conn.execute(
            "INSERT INTO invites (code_hash, created_by, note, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            ("hash-abc", "alice", "Friend invite", now, "2027-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    # Alice library under users/alice/games.db with reconciled multi-platform copies, locks, sessions, wishlist
    alice_db = root / "users" / "alice" / "games.db"
    alice_db.parent.mkdir(parents=True, exist_ok=True)
    legacy_db.init_db(str(alice_db))
    aconn = sqlite3.connect(str(alice_db))
    try:
        aconn.execute(
            """
            INSERT INTO games (
                id, external_id, title, platform, source, playtime_hours, playtime_minutes,
                last_synced_playtime_minutes, completion_status, status_locked,
                ownership_status, ownership_locked, is_game_pass, is_play_anywhere,
                genres, tags, user_rating, notes, favorite, canonical_id, created_at, updated_at
            ) VALUES (10, 'steam:1145360', 'Hades', 'PC (Steam)', 'Steam', 10.0, 600, 600,
                      'Completed', 1, 'Owned', 1, 0, 0, '["Roguelike"]', '["Story-Rich"]', 9, 'Masterpiece', 1, 10, ?, ?)
            """,
            (now, now),
        )
        aconn.execute(
            """
            INSERT INTO games (
                id, external_id, title, platform, source, playtime_hours, playtime_minutes,
                last_synced_playtime_minutes, completion_status, status_locked,
                ownership_status, ownership_locked, is_game_pass, is_play_anywhere,
                genres, tags, canonical_id, created_at, updated_at
            ) VALUES (11, 'xbox:hades', 'Hades', 'Xbox', 'Xbox', 2.0, 120, 120,
                      'Playing', 0, 'Subscription', 1, 1, 1, '["Action RPG"]', '[]', 10, ?, ?)
            """,
            (now, now),
        )
        aconn.execute(
            "INSERT INTO reconcile_dismissals (pair_key, created_at) VALUES ('10|11', ?)",
            (now,),
        )
        aconn.execute(
            """
            INSERT INTO play_sessions (
                id, game_id, platform, started_at, ended_at, duration_minutes,
                source, notes, achievements_unlocked, is_locked, created_at
            ) VALUES (1, 10, 'PC (Steam)', '2026-10-01T18:00:00+00:00', '2026-10-01T19:00:00+00:00',
                      60, 'manual', 'Cleared heat 8', 2, 1, ?)
            """,
            (now,),
        )
        aconn.execute(
            """
            INSERT INTO game_sessions (
                game_id, platform, source, started_at, last_seen_at, ended_at,
                wall_clock_minutes, active_minutes, detection_method, status
            ) VALUES (11, 'Xbox', 'telemetry', '2026-10-02T10:00:00+00:00', '2026-10-02T10:30:00+00:00',
                      '2026-10-02T10:30:00+00:00', 30.0, 30.0, 'xbl_presence', 'closed')
            """
        )
        aconn.execute(
            """
            INSERT INTO canonical_games (id, normalized_title, title, release_year, library_game_id, created_at, updated_at)
            VALUES (1, 'hollow knight silksong', 'Hollow Knight: Silksong', 2026, NULL, ?, ?)
            """,
            (now, now),
        )
        aconn.execute(
            """
            INSERT INTO game_editions (id, canonical_game_id, edition_name, edition_tier, tier_rank, is_base_edition, created_at, updated_at)
            VALUES (1, 1, 'Standard', 'base', 1, 1, ?, ?)
            """,
            (now, now),
        )
        aconn.execute(
            """
            INSERT INTO edition_platform_skus (
                id, edition_id, platform_family, platform_label, storefront, store_sku_id,
                current_price_usd, regular_price_usd, discount_pct, created_at, updated_at
            ) VALUES (1, 1, 'PC', 'PC (Steam)', 'steam', '1030300', 19.99, 29.99, 33, ?, ?)
            """,
            (now, now),
        )
        aconn.execute(
            "INSERT INTO wishlist_items (id, canonical_game_id, preferred_edition_id, status, notes, created_at, updated_at) VALUES (1, 1, 1, 'active', 'Day one watch', ?, ?)",
            (now, now),
        )
        aconn.execute(
            "INSERT INTO wishlist_platform_targets (wishlist_item_id, platform_family, target_price_usd, created_at, updated_at) VALUES (1, 'PC', 20.0, ?, ?)",
            (now, now),
        )
        aconn.execute(
            "INSERT INTO price_history (sku_id, storefront, price_usd, regular_price_usd, discount_pct, recorded_at) VALUES (1, 'steam', 19.99, 29.99, 33, ?)",
            (now,),
        )
        aconn.commit()
    finally:
        aconn.close()

    # Bob library under users/u-bob/games.db (arbitrary user_id)
    bob_db = root / "users" / "u-bob" / "games.db"
    bob_db.parent.mkdir(parents=True, exist_ok=True)
    legacy_db.init_db(str(bob_db))
    bconn = sqlite3.connect(str(bob_db))
    try:
        bconn.execute(
            """
            INSERT INTO games (
                external_id, title, platform, source, playtime_hours, playtime_minutes,
                last_synced_playtime_minutes, completion_status, status_locked,
                genres, tags, created_at, updated_at
            ) VALUES ('manual:zelda', 'Tears of the Kingdom', 'Nintendo Switch', 'Manual', 45.0, 2700, 2700,
                      'Playing', 1, '["Adventure"]', '["Open World"]', ?, ?)
            """,
            (now, now),
        )
        bconn.commit()
    finally:
        bconn.close()

    # Orphaned directory that does not match any system.db user
    orphan_db = root / "users" / "stray_ghost" / "games.db"
    orphan_db.parent.mkdir(parents=True, exist_ok=True)
    legacy_db.init_db(str(orphan_db))

    return root


def build_synthetic_singleuser_legacy_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "games.db"
    legacy_db.init_db(str(db_path))
    now = "2026-10-01T12:00:00+00:00"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO games (
                external_id, title, platform, source, playtime_hours, playtime_minutes,
                last_synced_playtime_minutes, completion_status, status_locked,
                notes, favorite, created_at, updated_at
            ) VALUES ('steam:620', 'Portal 2', 'PC (Steam)', 'Steam', 8.0, 480, 480,
                      'Completed', 1, 'Co-op classic', 1, ?, ?)
            """,
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return root
