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

    def test_portability_preserves_legacy_statuses_copies_dismissals_and_validates_nested_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scope, _ident, lib, port = self._setup(tmp_path / "app")

            # Malformed session / baseline raises ValidationError, never KeyError
            with self.assertRaises(ValidationError):
                port.import_json(
                    scope,
                    {
                        "format_version": 1,
                        "games": [{"external_id": "m:1", "title": "Game A", "platform": "PC"}],
                        "play_sessions": [{"game_external_id": "m:1", "duration_minutes": 10}],
                    },
                )
            with self.assertRaises(ValidationError):
                port.import_json(
                    scope,
                    {
                        "format_version": 1,
                        "games": [{"external_id": "m:1", "title": "Game A", "platform": "PC"}],
                        "provider_baselines": [{"game_external_id": "m:1", "baseline_minutes": 10}],
                    },
                )

            # Real legacy statuses (Beaten, Dropped, Borrowed, None) and reconcile_dismissals round-trip cleanly
            g1 = lib.add_manual_game(
                scope,
                title="Control",
                platform="PC (Steam)",
                completion_status="Beaten",
                ownership_status="Borrowed",
            )
            g2 = lib.add_manual_game(
                scope,
                title="Control",
                platform="Xbox",
                completion_status="Dropped",
                ownership_status="None",
            )
            lib.dismiss_reconcile_candidate(scope, [g1.id, g2.id])
            self.assertEqual(lib.get_reconcile_candidates(scope), [])

            exported = port.export_json(scope)
            rep = port.import_json(scope, exported, mode="replace", dry_run=False)
            self.assertEqual(rep.games_added, 2)
            # Dismissals preserved after replace import!
            self.assertEqual(lib.get_reconcile_candidates(scope), [])
            by_ext = {g.external_id: g for g in lib.list_games(scope)}
            self.assertEqual(by_ext[g1.external_id].completion_status, "Beaten")
            self.assertEqual(by_ext[g1.external_id].ownership_status, "Borrowed")
            self.assertEqual(by_ext[g2.external_id].completion_status, "Dropped")
            self.assertEqual(by_ext[g2.external_id].ownership_status, "None")

            # Imported games also populate platform_copies
            with port.db_manager.library_connection(scope) as conn:
                copies_cnt = conn.execute("SELECT COUNT(*) FROM platform_copies").fetchone()[0]
                self.assertEqual(copies_cnt, 2)

            # Legacy SQLite snapshot with Beaten/Dropped/Borrowed/reconcile_dismissals imports cleanly
            snap_db = tmp_path / "legacy_beaten.db"
            legacy_db.init_db(str(snap_db))
            conn = sqlite3.connect(str(snap_db))
            conn.execute(
                """
                INSERT INTO games (
                    id, external_id, title, platform, source, playtime_hours, playtime_minutes,
                    completion_status, ownership_status, created_at, updated_at
                ) VALUES
                    (5, 'steam:10', 'Disco Elysium', 'PC (Steam)', 'Steam', 20.0, 1200, 'Beaten', 'Borrowed', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
                    (6, 'xbox:10', 'Disco Elysium', 'Xbox', 'Xbox', 1.0, 60, 'Dropped', 'None', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')
                """
            )
            conn.execute("INSERT INTO reconcile_dismissals (pair_key, created_at) VALUES ('5|6', '2026-10-01T00:00:00Z')")
            conn.commit()
            export_db = tmp_path / "legacy_beaten_export.db"
            dst_conn = sqlite3.connect(str(export_db))
            try:
                conn.backup(dst_conn)
            finally:
                dst_conn.close()
                conn.close()

            sql_rep = port.import_legacy_sqlite_bytes(scope, export_db.read_bytes(), mode="replace", dry_run=False)
            self.assertEqual(sql_rep.games_added, 2)
            self.assertEqual(lib.get_reconcile_candidates(scope), [])
            imported_by_ext = {g.external_id: g for g in lib.list_games(scope)}
            self.assertEqual(imported_by_ext["steam:10"].completion_status, "Beaten")
            self.assertEqual(imported_by_ext["steam:10"].ownership_status, "Borrowed")
            self.assertEqual(imported_by_ext["xbox:10"].completion_status, "Dropped")
            self.assertEqual(imported_by_ext["xbox:10"].ownership_status, "None")

    def test_portability_imports_rebuild_daily_playtime_ledger(self):
        # Regression test: importing play_sessions via JSON or legacy SQLite must rebuild daily_playtime_ledger.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scope, ident, _lib, port = self._setup(tmp_path / "app")
            ident.update_timezone(scope, timezone_name="America/Chicago")

            # 2026-10-10T04:00:00Z -> 2026-10-09 23:00 CDT; 2026-10-10T06:00:00Z -> 2026-10-10 01:00 CDT
            json_payload = {
                "format_version": 1,
                "games": [
                    {
                        "external_id": "steam:620",
                        "title": "Portal 2",
                        "platform": "PC (Steam)",
                        "source": "Steam",
                    }
                ],
                "play_sessions": [
                    {
                        "game_external_id": "steam:620",
                        "platform": "PC (Steam)",
                        "started_at": "2026-10-10T04:00:00+00:00",
                        "ended_at": "2026-10-10T06:00:00+00:00",
                        "duration_minutes": 120,
                        "source": "Steam",
                        "precision_level": "estimated",
                        "is_locked": False,
                    }
                ],
            }

            # Dry run must NOT populate daily_playtime_ledger
            dry_rep = port.import_json(scope, json_payload, mode="merge", dry_run=True)
            self.assertEqual(dry_rep.sessions_added, 1)
            with port.db_manager.library_connection(scope) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_playtime_ledger").fetchone()[0], 0)

            rep = port.import_json(scope, json_payload, mode="merge", dry_run=False)
            self.assertEqual(rep.sessions_added, 1)
            with port.db_manager.library_connection(scope) as conn:
                rows = {
                    r["date"]: float(r["minutes_played"])
                    for r in conn.execute("SELECT date, minutes_played FROM daily_playtime_ledger").fetchall()
                }
            self.assertEqual(rows, {"2026-10-09": 60.0, "2026-10-10": 60.0})

            # Now test legacy SQLite snapshot with play_sessions in replace mode
            snap_db = tmp_path / "legacy_sessions.db"
            legacy_db.init_db(str(snap_db))
            conn = sqlite3.connect(str(snap_db))
            conn.execute(
                """
                INSERT INTO games (
                    id, external_id, title, platform, source, playtime_hours, playtime_minutes,
                    completion_status, created_at, updated_at
                ) VALUES (1, 'xbox:1', 'Halo Infinite', 'Xbox', 'Xbox', 1.5, 90, 'Playing', '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z')
                """
            )
            conn.execute(
                """
                INSERT INTO play_sessions (
                    game_id, platform, started_at, ended_at, duration_minutes, source, is_locked
                ) VALUES (1, 'Xbox', '2026-10-15T18:00:00Z', '2026-10-15T19:30:00Z', 90, 'Xbox', 0)
                """
            )
            conn.commit()
            export_db = tmp_path / "legacy_sessions_export.db"
            dst_conn = sqlite3.connect(str(export_db))
            try:
                conn.backup(dst_conn)
            finally:
                dst_conn.close()
                conn.close()

            sql_rep = port.import_legacy_sqlite_bytes(scope, export_db.read_bytes(), mode="replace", dry_run=False)
            self.assertEqual(sql_rep.sessions_added, 1)
            with port.db_manager.library_connection(scope) as conn2:
                rows2 = {
                    r["date"]: float(r["minutes_played"])
                    for r in conn2.execute("SELECT date, minutes_played FROM daily_playtime_ledger").fetchall()
                }
            self.assertEqual(rows2, {"2026-10-15": 90.0})
