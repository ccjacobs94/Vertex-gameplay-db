"""Unit tests for config, secrets, SQLite UserScope, migrations, and durable jobs."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.config import Settings
from vertex.identity.passwords import hash_password
from vertex.infrastructure.errors import (
    MigrationError,
    MissingSecretKeyError,
    ScopeViolationError,
    SecretDecryptionError,
)
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.migrations import apply_migrations
from vertex.infrastructure.scope import UserScope, validate_user_id
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager


class TestInfrastructure(unittest.TestCase):
    def test_settings_defaults_loopback(self):
        cfg = Settings.from_env({})
        self.assertEqual(cfg.bind_host, "127.0.0.1")
        self.assertTrue(cfg.outbound_sync_enabled)

    def test_secret_box_fails_closed_when_missing_or_wrong_key(self):
        empty = SecretBox("")
        self.assertFalse(empty.is_configured)
        with self.assertRaises(MissingSecretKeyError):
            empty.encrypt("my-api-key")
        with self.assertRaises(MissingSecretKeyError):
            empty.decrypt("gAAAA-fake")

        box_a = SecretBox("alpha-secret-key-123456")
        cipher = box_a.encrypt("steam-key-xyz")
        self.assertEqual(box_a.decrypt(cipher), "steam-key-xyz")

        box_wrong = SecretBox("wrong-secret-key-999999")
        with self.assertRaises(SecretDecryptionError):
            box_wrong.decrypt(cipher)

        # Controlled rotation using previous_keys
        box_rotated = SecretBox("beta-new-key-654321", previous_keys=["alpha-secret-key-123456"])
        self.assertEqual(box_rotated.decrypt(cipher), "steam-key-xyz")
        new_cipher = box_rotated.rotate_ciphertext(cipher)
        self.assertEqual(SecretBox("beta-new-key-654321").decrypt(new_cipher), "steam-key-xyz")
        self.assertNotIn("beta-new-key-654321", box_rotated.key_fingerprint() or "")

    def test_user_scope_rejects_traversal_and_suspended_state(self):
        with self.assertRaises(ScopeViolationError):
            validate_user_id("")
        with self.assertRaises(ScopeViolationError):
            validate_user_id("../other")
        with self.assertRaises(ScopeViolationError):
            validate_user_id("alice/bob")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                data_dir=Path(tmp),
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=Path(tmp) / "dist",
                worker_enabled=False,
            )
            mgr = DatabaseManager(cfg)
            with self.assertRaises(ScopeViolationError):
                with mgr.library_connection(None):  # type: ignore[arg-type]
                    pass
            suspended = UserScope(user_id="bob", username="bob", role="member", status="suspended")
            with self.assertRaises(ScopeViolationError):
                with mgr.library_connection(suspended):
                    pass

    def test_migrations_reject_tampered_sql_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mig_dir = tmp_path / "migs"
            shutil.copytree(MIGRATIONS_DIR / "system", mig_dir)
            db_path = tmp_path / "sys.db"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            applied = apply_migrations(conn, mig_dir)
            self.assertEqual(applied, ["0001_initial"])
            # Idempotent second run
            self.assertEqual(apply_migrations(conn, mig_dir), [])
            # Tamper with applied migration
            (mig_dir / "0001_initial.sql").write_text("CREATE TABLE hacked (x INT);", encoding="utf-8")
            with self.assertRaises(MigrationError):
                apply_migrations(conn, mig_dir)
            conn.close()

    def test_durable_job_queue_dedup_lease_expiry_backoff_and_fairness(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = Settings(
                data_dir=tmp_path,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=tmp_path / "dist",
                worker_enabled=False,
            )
            mgr = DatabaseManager(cfg)
            mgr.ensure_system_ready()
            now_iso = datetime.now(timezone.utc).isoformat()
            with mgr.system_connection(immediate=True) as conn:
                for uid in ("alice", "bob"):
                    conn.execute(
                        """
                        INSERT INTO users (id, username, role, status, password_hash, created_at, updated_at)
                        VALUES (?, ?, 'member', 'active', ?, ?, ?)
                        """,
                        (uid, uid, hash_password("Password!123"), now_iso, now_iso),
                    )

            alice = UserScope("alice", "alice", "member", "active")
            bob = UserScope("bob", "bob", "member", "active")
            jq = JobQueue(mgr)

            j1 = jq.enqueue(alice, kind="steam_sync", provider="steam", dedup_key="steam:alice", max_attempts=2)
            j1_dup = jq.enqueue(alice, kind="steam_sync", provider="steam", dedup_key="steam:alice", max_attempts=2)
            self.assertEqual(j1.id, j1_dup.id)

            jq.enqueue(alice, kind="other_sync", provider="internal", dedup_key="other:alice")
            jq.enqueue(bob, kind="steam_sync", provider="steam", dedup_key="steam:bob")

            # Acquire first job for Alice
            leased1 = jq.acquire_next_lease("w1", lease_seconds=30)
            self.assertIsNotNone(leased1)
            self.assertEqual(leased1.owner_user_id, "alice")

            # Fair scheduling picks Bob's job next while Alice already has a running job
            leased2 = jq.acquire_next_lease("w2", lease_seconds=30)
            self.assertIsNotNone(leased2)
            self.assertEqual(leased2.owner_user_id, "bob")

            # Simulate worker crash & lease expiration -> reclaimed back to queued
            future = datetime.now(timezone.utc) + timedelta(seconds=120)
            reclaimed = jq.reclaim_expired_leases(now=future)
            self.assertEqual(reclaimed, 2)

            # Retryable failure with throttling sets provider backoff
            re_leased = jq.acquire_next_lease("w3", lease_seconds=30, now=future + timedelta(seconds=60))
            self.assertIsNotNone(re_leased)
            failed_once = jq.fail_job(
                re_leased.id,
                "w3",
                error_code="rate_limited",
                error_message="429",
                retryable=True,
                throttle_seconds=45,
                now=future + timedelta(seconds=60),
            )
            self.assertEqual(failed_once.state, "queued")
            # Immediate pick for throttled provider returns None until throttle expires
            blocked = jq.acquire_next_lease("w3", lease_seconds=30, now=future + timedelta(seconds=65))
            if blocked:
                self.assertNotEqual(blocked.provider, re_leased.provider)
