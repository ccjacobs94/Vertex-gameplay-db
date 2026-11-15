"""Integration tests for whole-instance SQLite backup/restore and maintenance CLI commands."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from tests.migration.legacy_fixtures import build_synthetic_multiuser_legacy_dir
from vertex.config import Settings
from vertex.infrastructure.backup import restore_backup_set, verify_backup_set
from vertex.infrastructure.errors import BackupError, SecretDecryptionError
from vertex.infrastructure.scope import UserScope
from vertex.interfaces.cli import main as cli_main
from vertex.main import create_app


def _run_cli(argv: list[str], *, env_overrides: dict[str, str] | None = None) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    env = dict(os.environ)
    env.pop("GAMEHUB_SECRET_KEY", None)
    env.pop("VERTEX_SECRET_KEY", None)
    if env_overrides:
        env.update(env_overrides)
    with mock.patch.dict(os.environ, env, clear=True):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli_main(argv)
    return rc, stdout.getvalue(), stderr.getvalue()


class TestBackupRestoreAndCli(unittest.TestCase):
    def test_whole_instance_backup_tamper_detection_and_restore_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_data = root / "src_data"
            backup_dir = root / "backup_set"
            restore_dir = root / "restored_data"
            secret = "backup-restore-test-secret-key"

            app = create_app(
                Settings(
                    data_dir=src_data,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=root / "dist",
                    secret_key=secret,
                    worker_enabled=False,
                )
            )
            with TestClient(app) as client_a:
                client_a.post(
                    "/api/auth/bootstrap",
                    json={"username": "alice", "password": "AlicePassword!1"},
                )
                bob_created = client_a.post(
                    "/api/admin/users",
                    json={"username": "bob", "password": "BobPassword!1", "role": "member"},
                ).json()
                client_a.put(
                    "/api/connections",
                    json={"steam_id": "76561198000000001", "steam_api_key": "ALICE-STEAM-SECRET"},
                )
                client_a.post(
                    "/api/games",
                    json={"title": "Alice Restored Game", "platform": "PC (Steam)", "playtime_hours": 5.0},
                )

                client_b = TestClient(app)
                client_b.post("/api/auth/login", json={"username": "bob", "password": "BobPassword!1"})
                client_b.put(
                    "/api/connections",
                    json={"psn_npsso": "BOB-PSN-NPSSO-SECRET"},
                )
                client_b.post(
                    "/api/games",
                    json={"title": "Bob Restored Game", "platform": "Nintendo Switch", "playtime_hours": 1.0},
                )

            rc, out, err = _run_cli(
                ["--data-dir", str(src_data), "--migrations-dir", str(MIGRATIONS_DIR), "backup", "--dest", str(backup_dir)],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)
            manifest_raw = (backup_dir / "manifest.json").read_text(encoding="utf-8")
            manifest = json.loads(manifest_raw)
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(len(manifest["users"]), 2)
            self.assertNotIn(secret, manifest_raw)
            self.assertNotIn("ALICE-STEAM-SECRET", manifest_raw)
            self.assertNotIn(secret, out)

            # Tampering a backup SQLite file must fail verification/restore cleanly
            tampered_copy = root / "backup_tampered"
            import shutil

            shutil.copytree(backup_dir, tampered_copy)
            alice_bkp_db = next(u["library_file"] for u in manifest["users"] if u["username"] == "alice")
            with (tampered_copy / alice_bkp_db).open("r+b") as f:
                data = bytearray(f.read())
                data[-1] ^= 0xFF
                f.seek(0)
                f.write(data)
            with self.assertRaises(BackupError):
                verify_backup_set(tampered_copy)
            with self.assertRaises(BackupError):
                restore_backup_set(tampered_copy, root / "should_not_exist", MIGRATIONS_DIR)

            # Clean restore succeeds via CLI and works with original secret key
            rc, out, err = _run_cli(
                [
                    "--data-dir",
                    str(restore_dir),
                    "--migrations-dir",
                    str(MIGRATIONS_DIR),
                    "restore",
                    "--backup-dir",
                    str(backup_dir),
                    "--dest",
                    str(restore_dir),
                ],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)

            restored_app = create_app(
                Settings(
                    data_dir=restore_dir,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=root / "dist",
                    secret_key=secret,
                    worker_enabled=False,
                )
            )
            with TestClient(restored_app) as rc_a:
                la = rc_a.post("/api/auth/login", json={"username": "alice", "password": "AlicePassword!1"})
                self.assertEqual(la.status_code, 200)
                a_games = rc_a.get("/api/games").json()
                self.assertEqual([g["title"] for g in a_games], ["Alice Restored Game"])
                a_scope = UserScope(user_id=la.json()["account"]["id"], username="alice", role="admin", status="active")
                self.assertEqual(
                    restored_app.state.services.identity.get_decrypted_connections(a_scope).steam_api_key,
                    "ALICE-STEAM-SECRET",
                )

                rc_b = TestClient(restored_app)
                lb = rc_b.post("/api/auth/login", json={"username": "bob", "password": "BobPassword!1"})
                self.assertEqual(lb.status_code, 200)
                b_games = rc_b.get("/api/games").json()
                self.assertEqual([g["title"] for g in b_games], ["Bob Restored Game"])
                b_scope = UserScope(user_id=bob_created["id"], username="bob", role="member", status="active")
                self.assertEqual(
                    restored_app.state.services.identity.get_decrypted_connections(b_scope).psn_npsso,
                    "BOB-PSN-NPSSO-SECRET",
                )

            # Wrong secret key fails closed on credential decryption after restore
            wrong_app = create_app(
                Settings(
                    data_dir=restore_dir,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=root / "dist",
                    secret_key="completely-wrong-secret-key",
                    worker_enabled=False,
                )
            )
            with wrong_app.state.services.db_manager.system_connection():
                pass
            with self.assertRaises(SecretDecryptionError):
                wrong_app.state.services.identity.get_decrypted_connections(a_scope)

    def test_cli_bootstrap_reset_password_import_legacy_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "cli_data"
            secret = "cli-test-secret-key-999"

            rc, out, err = _run_cli(
                ["--data-dir", str(data_dir), "--migrations-dir", str(MIGRATIONS_DIR), "migrate"],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("0001_initial", out)

            rc, out, err = _run_cli(
                [
                    "--data-dir",
                    str(data_dir),
                    "--migrations-dir",
                    str(MIGRATIONS_DIR),
                    "bootstrap-admin",
                    "--username",
                    "cliadmin",
                    "--password",
                    "InitialPass!1",
                ],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)

            rc, out, err = _run_cli(
                [
                    "--data-dir",
                    str(data_dir),
                    "--migrations-dir",
                    str(MIGRATIONS_DIR),
                    "reset-password",
                    "--username",
                    "cliadmin",
                    "--password",
                    "RotatedPass!2",
                ],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)

            app = create_app(
                Settings(
                    data_dir=data_dir,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=root / "dist",
                    secret_key=secret,
                    worker_enabled=False,
                )
            )
            with TestClient(app) as client:
                self.assertEqual(
                    client.post("/api/auth/login", json={"username": "cliadmin", "password": "InitialPass!1"}).status_code,
                    401,
                )
                self.assertEqual(
                    client.post("/api/auth/login", json={"username": "cliadmin", "password": "RotatedPass!2"}).status_code,
                    200,
                )

            rc, diag_out, err = _run_cli(
                ["--data-dir", str(data_dir), "--migrations-dir", str(MIGRATIONS_DIR), "diagnostics"],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)
            diag = json.loads(diag_out)
            self.assertEqual(diag["users_total"], 1)
            self.assertTrue(diag["secret_key_configured"])
            self.assertNotIn(secret, diag_out)

            # Test CLI import-legacy command
            legacy_src = build_synthetic_multiuser_legacy_dir(root / "legacy_src", legacy_secret_key="old-legacy-key")
            imported_dest = root / "imported_dest"
            report_file = root / "import-report.json"
            rc, imp_out, err = _run_cli(
                [
                    "--migrations-dir",
                    str(MIGRATIONS_DIR),
                    "import-legacy",
                    "--source-dir",
                    str(legacy_src),
                    "--dest-dir",
                    str(imported_dest),
                    "--report",
                    str(report_file),
                    "--legacy-key",
                    "old-legacy-key",
                ],
                env_overrides={"VERTEX_SECRET_KEY": secret},
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertTrue(report_file.exists())
            parsed_report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(parsed_report["layout"], "multi_user")
            self.assertEqual(len(parsed_report["users_migrated"]), 6)
