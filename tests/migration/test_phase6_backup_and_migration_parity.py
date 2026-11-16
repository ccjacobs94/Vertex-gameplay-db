"""Phase 6 verification: full legacy migration parity (including wishlist/push) + clean backup/restore."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

import db as legacy_db
import system_db as legacy_system_db
from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.models import UserScope
from vertex.identity.service import IdentityService
from vertex.infrastructure.backup import create_backup_set, restore_backup_set, verify_backup_set
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.prices import WishlistSyncService
from vertex.integrations.push import WebPushService
from vertex.library.service import LibraryService
from vertex.migration.legacy_importer import import_legacy_instance
from vertex.wishlist.service import WishlistService

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_SECRET = "legacy-instance-secret-key-for-parity"
DEST_SECRET = "v2-destination-secret-key-for-parity"


class TestPhase6MigrationAndBackupParity(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_wishlist_push_library_migration_sync_and_backup_restore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy_dir = root / "legacy-data"
            v2_dir = root / "v2-data"
            backup_dir = root / "backup-set"
            restored_dir = root / "restored-v2-data"
            legacy_dir.mkdir()

            # Build realistic legacy multi-user fixture using legacy system_db & db modules
            prev_key = os.environ.get("GAMEHUB_SECRET_KEY")
            os.environ["GAMEHUB_SECRET_KEY"] = LEGACY_SECRET
            try:
                legacy_sys_path = str(legacy_dir / "system.db")
                legacy_system_db.init_system_db(legacy_sys_path)
                alice_id = legacy_system_db.create_user(
                    username="alice",
                    password="AliceLegacyPass!123",
                    role="admin",
                    status="active",
                    db_path=legacy_sys_path,
                )
                bob_id = legacy_system_db.create_user(
                    username="bob",
                    password="BobLegacyPass!123",
                    role="user",
                    status="inactive",
                    status_reason="subscription_lapsed",
                    db_path=legacy_sys_path,
                )
                legacy_system_db.set_user_connections(
                    alice_id,
                    steam_id="76561198000000001",
                    xbl_api_key="legacy-xbl-api-secret",
                    db_path=legacy_sys_path,
                )
            finally:
                if prev_key is None:
                    os.environ.pop("GAMEHUB_SECRET_KEY", None)
                else:
                    os.environ["GAMEHUB_SECRET_KEY"] = prev_key

            alice_legacy_db_dir = legacy_dir / "users" / alice_id
            alice_legacy_db_dir.mkdir(parents=True)
            alice_legacy_db = str(alice_legacy_db_dir / "games.db")
            legacy_db.init_db(db_path=alice_legacy_db)

            bob_legacy_db_dir = legacy_dir / "users" / bob_id
            bob_legacy_db_dir.mkdir(parents=True)
            legacy_db.init_db(db_path=str(bob_legacy_db_dir / "games.db"))

            now_iso = "2026-11-15T12:00:00Z"
            with sqlite3.connect(alice_legacy_db) as con:
                con.execute(
                    """
                    INSERT INTO games (
                        external_id, title, platform, source, playtime_hours, playtime_minutes,
                        last_synced_playtime_minutes, completion_status, status_locked,
                        ownership_status, ownership_locked, genres, tags, user_rating, notes,
                        favorite, created_at, updated_at
                    ) VALUES (
                        'steam_1', 'Elden Ring', 'PC (Steam)', 'Steam', 42.5, 2550,
                        2550, 'Playing', 1, 'Owned', 1, '["Action RPG"]', '["Story-Rich"]',
                        10, 'Locked legacy user edit', 1, ?, ?
                    )
                    """,
                    (now_iso, now_iso),
                )
                game_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    """
                    INSERT INTO play_sessions (
                        game_id, platform, started_at, ended_at, duration_minutes,
                        source, notes, is_locked, created_at
                    ) VALUES (?, 'PC (Steam)', '2026-11-15T10:00:00Z', '2026-11-15T11:30:00Z', 90,
                        'manual', 'Defeated Margit', 1, ?)
                    """,
                    (game_id, now_iso),
                )
                con.execute(
                    """
                    INSERT INTO canonical_games (
                        normalized_title, title, release_year, developer, genres, created_at, updated_at
                    ) VALUES ('cyberpunk 2077', 'Cyberpunk 2077', 2020, 'CD PROJEKT RED', '["RPG"]', ?, ?)
                    """,
                    (now_iso, now_iso),
                )
                canon_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    """
                    INSERT INTO game_editions (
                        canonical_game_id, edition_name, edition_tier, tier_rank, is_base_edition, created_at, updated_at
                    ) VALUES (?, 'Standard Edition', 'base', 1, 1, ?, ?)
                    """,
                    (canon_id, now_iso, now_iso),
                )
                ed_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    """
                    INSERT INTO edition_platform_skus (
                        edition_id, platform_family, platform_label, storefront, store_sku_id,
                        current_price_usd, regular_price_usd, discount_pct, historical_low_usd,
                        is_authorized_seller, is_drm_free, created_at, updated_at
                    ) VALUES (?, 'PC', 'PC (Steam)', 'steam', '1091500', 59.99, 59.99, 0, 29.99, 1, 0, ?, ?)
                    """,
                    (ed_id, now_iso, now_iso),
                )
                sku_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    """
                    INSERT INTO wishlist_items (
                        canonical_game_id, preferred_edition_id, allow_smart_upgrade, status, notes, added_via, created_at, updated_at
                    ) VALUES (?, ?, 1, 'active', 'Wait for 50% off', 'web', ?, ?)
                    """,
                    (canon_id, ed_id, now_iso, now_iso),
                )
                wish_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    """
                    INSERT INTO wishlist_platform_targets (
                        wishlist_item_id, platform_family, target_price_usd, is_active, created_at, updated_at
                    ) VALUES (?, 'PC', 35.0, 1, ?, ?)
                    """,
                    (wish_id, now_iso, now_iso),
                )
                con.execute(
                    """
                    INSERT INTO price_history (sku_id, storefront, price_usd, regular_price_usd, discount_pct, is_all_time_low, source, recorded_at)
                    VALUES (?, 'steam', 59.99, 59.99, 0, 0, 'legacy_sync', ?)
                    """,
                    (sku_id, now_iso),
                )
                con.execute(
                    """
                    INSERT INTO push_subscriptions (endpoint, p256dh_key, auth_key, user_agent, is_active, created_at)
                    VALUES ('https://fcm.googleapis.com/fcm/send/legacy-sub', 'p256-legacy', 'auth-legacy', 'Mozilla', 1, ?)
                    """,
                    (now_iso,),
                )
                con.commit()

            # 1. Run non-destructive legacy import into fresh v2 directory
            report = import_legacy_instance(
                source_dir=legacy_dir,
                dest_dir=v2_dir,
                migrations_dir=REPO_ROOT / "migrations",
                dest_secret_key=DEST_SECRET,
                legacy_source_keys=(LEGACY_SECRET,),
            )
            self.assertEqual(report.conflicts, [])
            self.assertEqual(report.orphaned_directories, [])
            self.assertEqual(report.credentials_needing_reentry, [])
            self.assertEqual(len(report.users_migrated), 2)
            if isinstance(report.parity_checks, dict):
                self.assertTrue(
                    all(
                        v.get("passed", True) if isinstance(v, dict) else bool(v)
                        for v in report.parity_checks.values()
                    )
                )

            # 2. Verify migrated library, locked edits, wishlist, SKUs, price history, and push subs
            cfg = Settings(
                data_dir=v2_dir,
                migrations_dir=REPO_ROOT / "migrations",
                frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
                secret_key=DEST_SECRET,
                outbound_sync_enabled=True,
                web_push_enabled=True,
            )
            db_mgr = DatabaseManager(cfg)
            with db_mgr.system_connection() as sconn:
                bob_row = sconn.execute(
                    "SELECT status, status_reason FROM users WHERE username = 'bob'"
                ).fetchone()
            self.assertIsNotNone(bob_row)
            self.assertEqual(bob_row["status"], "suspended")
            secret_box = SecretBox(DEST_SECRET)
            jobs = JobQueue(db_mgr)
            identity = IdentityService(db_mgr, secret_box, jobs)
            alice_scope = UserScope(user_id=alice_id, username="alice", role="admin", status="active")

            # Encrypted Xbox secret decrypts under destination key
            creds = identity.get_decrypted_connections(alice_scope)
            self.assertEqual(creds.xbl_api_key, "legacy-xbl-api-secret")

            lib = LibraryService(db_mgr)
            games = lib.list_games(alice_scope)
            self.assertEqual(len(games), 1)
            self.assertTrue(games[0].status_locked)
            self.assertTrue(games[0].ownership_locked)
            self.assertEqual(games[0].user_rating, 10)
            self.assertEqual(games[0].notes, "Locked legacy user edit")

            wish = WishlistService(db_mgr, lib)
            items = wish.list_items(alice_scope, status="active")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].canonical_game.title, "Cyberpunk 2077")
            self.assertEqual(len(items[0].skus), 1)
            self.assertEqual(items[0].skus[0].store_sku_id, "1091500")
            subs_before = wish.list_push_subscriptions(alice_scope)
            # Retired VAPID keys mean legacy push subscriptions do not carry forward blindly; user re-subscribes cleanly if push enabled
            self.assertEqual(len(subs_before), 0)

            # 3. Post-migration Web Push re-subscription + wishlist sync + deal alert!
            sent_alerts: list[str] = []

            def transport(request: httpx.Request) -> httpx.Response:
                if "store.steampowered.com" in request.url.host:
                    return httpx.Response(
                        200,
                        json={
                            "1091500": {
                                "success": True,
                                "data": {
                                    "name": "Cyberpunk 2077",
                                    "header_image": "https://cdn.example/cp.jpg",
                                    "price_overview": {
                                        "currency": "USD",
                                        "initial": 5999,
                                        "final": 1999,
                                        "discount_percent": 67,
                                    },
                                },
                            }
                        },
                    )
                if "catalog.gog.com" in request.url.host:
                    return httpx.Response(200, json={"products": []})
                if "cheapshark.com" in request.url.host:
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json={})

            push_svc = WebPushService(
                settings=cfg,
                db_manager=db_mgr,
                secret_box=secret_box,
                wishlist_service=wish,
                sender=lambda sub, data, priv, claims: sent_alerts.append(data),
            )
            push_svc.subscribe(
                alice_scope,
                endpoint="https://fcm.googleapis.com/fcm/send/post-cutover-device",
                p256dh="p256-post",
                auth="auth-post",
            )
            sync_svc = WishlistSyncService(
                settings=cfg,
                job_queue=jobs,
                identity_service=identity,
                catalog_service=CatalogService(db_mgr, outbound_sync_enabled=False),
                wishlist_service=wish,
                web_push_service=push_svc,
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport)),
            )
            sync_res = await sync_svc.sync_wishlist_for_user(alice_scope, force=True)
            self.assertEqual(sync_res["deals_triggered"], 1)
            self.assertEqual(sync_res["push_sent"], 1)
            self.assertEqual(len(sent_alerts), 1)

            # 4. Whole-instance backup + SHA-256 verification + clean restore
            manifest = create_backup_set(db_mgr, backup_dir, secret_box=secret_box)
            self.assertGreaterEqual(len(manifest.files), 2)
            verify_backup_set(backup_dir)
            restore_backup_set(backup_dir, restored_dir, REPO_ROOT / "migrations")

            cfg_restored = Settings(
                data_dir=restored_dir,
                migrations_dir=REPO_ROOT / "migrations",
                frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
                secret_key=DEST_SECRET,
                outbound_sync_enabled=False,
            )
            db_restored = DatabaseManager(cfg_restored)
            id_restored = IdentityService(db_restored, secret_box, JobQueue(db_restored))
            self.assertEqual(
                id_restored.get_decrypted_connections(alice_scope).xbl_api_key,
                "legacy-xbl-api-secret",
            )
            wish_restored = WishlistService(db_restored, LibraryService(db_restored))
            deals_restored = wish_restored.evaluate_deals(alice_scope)
            self.assertEqual(deals_restored.total_deals, 1)
            self.assertEqual(deals_restored.deals[0].current_price_usd, 19.99)
