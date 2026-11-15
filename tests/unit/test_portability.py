"""Unit tests for versioned JSON export/import and validated legacy SQLite import."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
import db as legacy_db
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import ValidationError
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.portability import LibraryPortabilityService
from vertex.library.service import LibraryService


class TestPortability(unittest.TestCase):
    def _setup(self, tmp_path: Path):
        cfg = Settings(
            data_dir=tmp_path,
            migrations_dir=MIGRATIONS_DIR,
            frontend_dist_dir=tmp_path / "dist",
            secret_key="portability-test-secret-key",
            worker_enabled=False,
        )
        mgr = DatabaseManager(cfg)
        mgr.ensure_system_ready()
        ident = IdentityService(mgr, SecretBox(cfg.secret_key), JobQueue(mgr))
        lib = LibraryService(mgr)
        port = LibraryPortabilityService(mgr, ident)
        alice, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
        ident.update_connections(
            alice.to_scope(),
            steam_id="76561198000000001",
            steam_api_key="SUPER-SECRET-STEAM-API-KEY",
            psn_npsso="SUPER-SECRET-PSN-NPSSO",
        )
        return alice.to_scope(), ident, lib, port

    def test_json_export_excludes_secrets_and_supports_dry_run_merge_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope, _ident, lib, port = self._setup(Path(tmp))
            g = lib.add_manual_game(
                scope,
                title="Hollow Knight",
                platform="Nintendo Switch",
                completion_status="Completed",
                notes="True ending done",
                genres=["Metroidvania"],
            )
            exported = port.export_json(scope)
            self.assertEqual(exported["format_version"], 1)
            serialized = str(exported)
            self.assertNotIn("SUPER-SECRET-STEAM-API-KEY", serialized)
            self.assertNotIn("SUPER-SECRET-PSN-NPSSO", serialized)
            self.assertNotIn("password_hash", serialized)
            self.assertEqual(len(exported["games"]), 1)

            incoming = {
                "format_version": 1,
                "games": [
                    {
                        "external_id": g.external_id,
                        "title": "Hollow Knight",
                        "platform": "Nintendo Switch",
                        "source": "Manual",
                        "completion_status": "Not Played",  # must not overwrite locked status
                        "genres": ["Action"],
                    },
                    {
                        "external_id": "manual:new-1",
                        "title": "Outer Wilds",
                        "platform": "PC (Steam)",
                        "source": "Manual",
                        "completion_status": "Plan to Play",
                    },
                ],
            }
            dry = port.import_json(scope, incoming, mode="merge", dry_run=True)
            self.assertTrue(dry.dry_run)
            self.assertEqual(dry.games_added, 1)
            self.assertEqual(dry.games_skipped, 1)
            self.assertEqual(len(lib.list_games(scope)), 1)  # Dry run did NOT write!

            applied = port.import_json(scope, incoming, mode="merge", dry_run=False)
            self.assertEqual(applied.games_added, 1)
            self.assertEqual(len(lib.list_games(scope)), 2)
            kept = lib.get_game(scope, g.id)
            self.assertEqual(kept.completion_status, "Completed")
            self.assertEqual(kept.notes, "True ending done")
            self.assertEqual(kept.genres, ("Metroidvania", "Action"))

            # Replace mode wipes previous rows cleanly
            rep = port.import_json(
                scope,
                {
                    "format_version": 1,
                    "games": [
                        {
                            "external_id": "manual:only-one",
                            "title": "Balatro",
                            "platform": "PC (Steam)",
                            "source": "Manual",
                        }
                    ],
                },
                mode="replace",
                dry_run=False,
            )
            self.assertEqual(rep.games_added, 1)
            self.assertEqual([x.title for x in lib.list_games(scope)], ["Balatro"])

    def test_legacy_sqlite_bytes_validation_and_safe_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scope, _ident, lib, port = self._setup(tmp_path / "app")

            with self.assertRaises(ValidationError):
                port.import_legacy_sqlite_bytes(scope, b"not-a-sqlite-file")

            # Valid SQLite DB missing games table rejected
            empty_db = tmp_path / "empty.db"
            sqlite3.connect(str(empty_db)).close()
            with self.assertRaises(ValidationError):
                port.import_legacy_sqlite_bytes(scope, empty_db.read_bytes())

            # Real legacy snapshot imports cleanly via validated pipeline
            snap_db = tmp_path / "legacy_snap.db"
            legacy_db.init_db(str(snap_db))
            conn = sqlite3.connect(str(snap_db))
            conn.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours, playtime_minutes,
                    completion_status, status_locked, notes, favorite, created_at, updated_at
                ) VALUES ('steam:1145360', 'Hades', 'PC (Steam)', 'Steam', 12.0, 720,
                          'Completed', 1, 'Escape!', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')
                """
            )
            conn.commit()
            export_db = tmp_path / "legacy_snap_export.db"
            dst_conn = sqlite3.connect(str(export_db))
            try:
                conn.backup(dst_conn)
            finally:
                dst_conn.close()
                conn.close()

            dry = port.import_legacy_sqlite_bytes(scope, export_db.read_bytes(), dry_run=True)
            self.assertEqual(dry.games_added, 1)
            self.assertEqual(len(lib.list_games(scope)), 0)

            rep = port.import_legacy_sqlite_bytes(scope, export_db.read_bytes(), dry_run=False)
            self.assertEqual(rep.games_added, 1)
            games = lib.list_games(scope)
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].title, "Hades")
            self.assertTrue(games[0].status_locked)
            self.assertEqual(games[0].notes, "Escape!")
