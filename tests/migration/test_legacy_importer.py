"""Acceptance tests for legacy SQLite single-user & multi-user migration importer."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from tests.migration.legacy_fixtures import (
    build_synthetic_multiuser_legacy_dir,
    build_synthetic_singleuser_legacy_dir,
)
from vertex.activity.service import ActivityService
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import MigrationError
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.service import LibraryService
from vertex.migration.legacy_importer import import_legacy_instance, inventory_legacy_source


class TestLegacyImporter(unittest.TestCase):
    def test_multiuser_migration_preserves_edits_revokes_sessions_and_suspends_billing_states(self):
        legacy_secret = "legacy-fixture-secret-key-1234"
        new_secret = "new-vertex-secret-key-5678"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = build_synthetic_multiuser_legacy_dir(tmp_path / "legacy", legacy_secret_key=legacy_secret)
            dest = tmp_path / "rebuilt"

            inv = inventory_legacy_source(src)
            self.assertEqual(inv.layout, "multi_user")
            self.assertIn("stray_ghost", inv.orphaned_directories)
            self.assertEqual(inv.legacy_sessions_to_revoke, 1)
            self.assertEqual(inv.legacy_oauth_tokens_to_revoke, 1)

            report = import_legacy_instance(
                source_dir=src,
                dest_dir=dest,
                migrations_dir=MIGRATIONS_DIR,
                dest_secret_key=new_secret,
                legacy_source_keys=[legacy_secret],
            )
            self.assertTrue(report.parity_checks["all_passed"])
            self.assertEqual(report.credentials_needing_reentry, [])
            self.assertEqual(report.revoked_web_sessions, 1)
            self.assertEqual(report.revoked_oauth_tokens, 1)
            self.assertEqual(report.orphaned_directories, ["stray_ghost"])
            self.assertEqual(report.catalog_entries_migrated, 1)

            settings = Settings(
                data_dir=dest,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=dest / "no-ui",
                secret_key=new_secret,
                worker_enabled=False,
            )
            db_mgr = DatabaseManager(settings)
            box = SecretBox(new_secret)
            ident = IdentityService(db_mgr, box, JobQueue(db_mgr))
            lib = LibraryService(db_mgr)
            act = ActivityService(db_mgr)

            # Old legacy session cannot resolve
            self.assertIsNone(ident.resolve_session("legacy-sess-alice"))

            # Alice & Bob log in with preserved legacy scrypt passwords
            alice_scope, _ = ident.login(username="alice", password="AlicePass!2026")
            bob_scope, _ = ident.login(username="Bob", password="BobPass!2026")
            self.assertEqual(alice_scope.role, "admin")
            self.assertEqual(bob_scope.role, "member")
            self.assertEqual(bob_scope.user_id, "u-bob")

            # Pending/lapsed/suspended legacy accounts MUST NOT activate silently
            statuses = {u.id: u for u in ident.list_users(alice_scope)}
            for suspended_id in ("carol", "dave", "eve", "frank"):
                self.assertEqual(statuses[suspended_id].status, "suspended")
                self.assertTrue(statuses[suspended_id].status_reason)

            # Encrypted credentials re-encrypted with new key decrypt cleanly
            creds = ident.get_decrypted_connections(alice_scope)
            self.assertEqual(creds.steam_id, "76561198000000001")
            self.assertEqual(creds.xbl_api_key, "xbl-secret-token")
            self.assertEqual(creds.psn_npsso, "psn-npsso-secret")
            self.assertEqual(creds.ha_token, "ha-long-token")

            # Alice's games, reconciled group, independent ownership, locks, notes, rating preserved
            alice_games = lib.list_games(alice_scope)
            self.assertEqual(len(alice_games), 2)
            hades_steam = [g for g in alice_games if g.external_id == "steam:1145360"][0]
            hades_xbox = [g for g in alice_games if g.external_id == "xbox:hades"][0]
            self.assertEqual(hades_steam.canonical_id, hades_xbox.canonical_id)
            self.assertEqual(hades_steam.completion_status, "Completed")
            self.assertTrue(hades_steam.status_locked)
            self.assertEqual(hades_steam.ownership_status, "Owned")
            self.assertEqual(hades_xbox.ownership_status, "Subscription")
            self.assertTrue(hades_xbox.is_game_pass)
            self.assertTrue(hades_xbox.is_play_anywhere)
            self.assertEqual(hades_steam.notes, "Masterpiece")
            self.assertEqual(hades_steam.user_rating, 9)
            self.assertTrue(hades_steam.favorite)

            # Sessions & baseline initialized so post-migration sync does NOT invent retroactive sessions
            sessions = act.list_sessions(alice_scope)
            self.assertEqual(len(sessions), 1)
            self.assertTrue(sessions[0].is_locked)
            outcome = act.record_cumulative_observation(
                alice_scope,
                game_id=hades_steam.id,
                source="Steam",
                platform="PC (Steam)",
                observed_minutes=600,
                observed_at="2026-10-03T12:00:00+00:00",
                idempotency_key="post-mig-1",
            )
            self.assertEqual(outcome.status, "no_change")
            self.assertEqual(len(act.list_sessions(alice_scope)), 1)

            # Retired billing setting ignored while safe setting preserved
            inst_settings = ident.get_instance_settings(alice_scope)
            self.assertEqual(inst_settings.get("instance_name"), "Household Hub")
            self.assertNotIn("signup_mode", inst_settings)

    def test_wrong_source_key_fails_closed_and_reports_reentry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = build_synthetic_multiuser_legacy_dir(tmp_path / "legacy", legacy_secret_key="actual-key-12345")
            dest = tmp_path / "rebuilt"

            report = import_legacy_instance(
                source_dir=src,
                dest_dir=dest,
                migrations_dir=MIGRATIONS_DIR,
                dest_secret_key="new-secret-key-99999",
                legacy_source_keys=["wrong-source-key"],
            )
            self.assertEqual(len(report.credentials_needing_reentry), 3)
            fields = {c["field"] for c in report.credentials_needing_reentry}
            self.assertEqual(fields, {"xbl_api_key_encrypted", "psn_npsso_encrypted", "ha_token_encrypted"})

            conn = sqlite3.connect(str(dest / "system.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM user_connections WHERE user_id = 'alice'").fetchone()
                self.assertIsNone(row["xbl_api_key_encrypted"])
                self.assertIsNone(row["psn_npsso_encrypted"])
                self.assertIsNone(row["ha_token_encrypted"])
            finally:
                conn.close()

    def test_interrupted_migration_leaves_no_partial_destination_and_reruns_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = build_synthetic_multiuser_legacy_dir(tmp_path / "legacy")
            dest_failed = tmp_path / "dest-failed"

            def _boom(_uid: str) -> None:
                raise RuntimeError("simulated power loss")

            with self.assertRaises(RuntimeError):
                import_legacy_instance(
                    source_dir=src,
                    dest_dir=dest_failed,
                    migrations_dir=MIGRATIONS_DIR,
                    dest_secret_key="new-secret-key-99999",
                    legacy_source_keys=["legacy-fixture-secret-key-1234"],
                    _fail_after_user_hook=_boom,
                )
            self.assertFalse(dest_failed.exists())

            # Fresh destination runs produce identical logical parity
            dest_a = tmp_path / "dest-a"
            dest_b = tmp_path / "dest-b"
            rep_a = import_legacy_instance(
                source_dir=src,
                dest_dir=dest_a,
                migrations_dir=MIGRATIONS_DIR,
                dest_secret_key="new-secret-key-99999",
                legacy_source_keys=["legacy-fixture-secret-key-1234"],
            )
            rep_b = import_legacy_instance(
                source_dir=src,
                dest_dir=dest_b,
                migrations_dir=MIGRATIONS_DIR,
                dest_secret_key="new-secret-key-99999",
                legacy_source_keys=["legacy-fixture-secret-key-1234"],
            )
            self.assertEqual(rep_a.parity_checks, rep_b.parity_checks)
            self.assertEqual(len(rep_a.users_migrated), len(rep_b.users_migrated))

    def test_single_user_legacy_layout_imports_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = build_synthetic_singleuser_legacy_dir(tmp_path / "legacy-single")
            dest = tmp_path / "rebuilt-single"

            with self.assertRaises(MigrationError):
                import_legacy_instance(
                    source_dir=src,
                    dest_dir=dest,
                    migrations_dir=MIGRATIONS_DIR,
                    dest_secret_key="new-secret-key-99999",
                )

            report = import_legacy_instance(
                source_dir=src,
                dest_dir=dest,
                migrations_dir=MIGRATIONS_DIR,
                dest_secret_key="new-secret-key-99999",
                single_user_admin_username="chris",
                single_user_admin_password="ChrisPassword!23",
            )
            self.assertEqual(report.layout, "single_user")
            self.assertTrue(report.parity_checks["all_passed"])
            self.assertEqual(report.parity_checks["users"]["chris"]["dest_games"], 1)

    def test_real_system_db_lifecycle_reasons_retired_settings_and_until_eras(self):
        import system_db as legacy_sys
        import db as legacy_db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "real_legacy"
            src.mkdir()
            sys_path = str(src / "system.db")
            legacy_sys.init_system_db(sys_path)

            uid_admin = legacy_sys.create_user("alice", "AlicePass!2026", role="admin", status="active", db_path=sys_path)
            legacy_sys.create_user("carol", "CarolPass!2026", status="pending", status_reason="awaiting_payment", db_path=sys_path)
            legacy_sys.create_user("dave", "DavePass!2026", status="pending", status_reason="awaiting_approval", db_path=sys_path)
            uid_eve = legacy_sys.create_user("eve", "EvePass!2026", status="active", db_path=sys_path)
            legacy_sys.set_user_status(uid_eve, "inactive", reason="subscription_lapsed", db_path=sys_path)
            uid_frank = legacy_sys.create_user("frank", "FrankPass!2026", status="active", db_path=sys_path)
            legacy_sys.set_user_status(uid_frank, "inactive", reason="admin_suspended", db_path=sys_path)

            legacy_sys.set_setting("edition", "hosted", db_path=sys_path)
            legacy_sys.set_setting("registration_policy", "open", db_path=sys_path)
            legacy_sys.set_setting("instance_name", "Real Household", db_path=sys_path)
            legacy_sys.update_user_timezone_settings(
                uid_admin,
                "America/Chicago",
                [{"timezone": "America/New_York", "until": "2026-06-01T00:00:00+00:00", "label": None}],
                db_path=sys_path,
            )

            alice_db = src / "users" / uid_admin / "games.db"
            legacy_db.init_db(str(alice_db))
            legacy_db.upsert_synced_game(
                "steam:620",
                "Portal 2",
                "PC (Steam)",
                "Steam",
                playtime_minutes=180,
                ownership_status="Borrowed",
                db_path=str(alice_db),
            )
            legacy_db.update_game(1, {"completion_status": "Beaten"}, db_path=str(alice_db))

            dest = tmp_path / "dest_real"
            report = import_legacy_instance(
                source_dir=src,
                dest_dir=dest,
                migrations_dir=MIGRATIONS_DIR,
                dest_secret_key="new-secret-key-99999",
            )
            self.assertTrue(report.parity_checks["all_passed"])

            cfg = Settings(
                data_dir=dest,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=dest / "no-ui",
                secret_key="new-secret-key-99999",
                worker_enabled=False,
            )
            mgr = DatabaseManager(cfg)
            ident = IdentityService(mgr, SecretBox(cfg.secret_key), JobQueue(mgr))
            lib = LibraryService(mgr)
            scope, _ = ident.login(username="alice", password="AlicePass!2026")

            users = {u.username: u for u in ident.list_users(scope)}
            self.assertEqual(users["carol"].status, "suspended")
            self.assertIn("awaiting_payment", users["carol"].status_reason or "")
            self.assertEqual(users["dave"].status, "suspended")
            self.assertIn("awaiting_approval", users["dave"].status_reason or "")
            self.assertEqual(users["eve"].status, "suspended")
            self.assertIn("subscription_lapsed", users["eve"].status_reason or "")
            self.assertEqual(users["frank"].status, "suspended")
            self.assertEqual(users["frank"].status_reason, "admin_suspended")

            inst = ident.get_instance_settings(scope)
            self.assertEqual(inst.get("instance_name"), "Real Household")
            self.assertNotIn("edition", inst)
            self.assertNotIn("registration_policy", inst)

            games = lib.list_games(scope)
            self.assertEqual(games[0].completion_status, "Beaten")
            self.assertEqual(games[0].ownership_status, "Borrowed")
