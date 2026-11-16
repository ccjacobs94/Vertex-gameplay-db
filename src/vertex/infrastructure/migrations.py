"""Checksummed, ordered SQL schema migration runner."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from vertex.infrastructure.errors import MigrationError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """
    )


def list_applied_migrations(conn: sqlite3.Connection) -> list[dict[str, str]]:
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if has_table is None:
        return []
    rows = conn.execute(
        "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version ASC"
    ).fetchall()
    return [
        {"version": r["version"], "checksum": r["checksum"], "applied_at": r["applied_at"]}
        for r in rows
    ]


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    """Apply pending SQL migrations in lexical order and verify historical checksums."""
    if not migrations_dir.exists() or not migrations_dir.is_dir():
        raise MigrationError(f"Migrations directory not found: {migrations_dir}")

    _ensure_migrations_table(conn)
    applied_rows = {
        r["version"]: r["checksum"]
        for r in conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    }

    migration_files = sorted(migrations_dir.glob("*.sql"))
    newly_applied: list[str] = []

    for sql_file in migration_files:
        version = sql_file.stem
        sql_text = sql_file.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()

        if version in applied_rows:
            if applied_rows[version] != checksum:
                raise MigrationError(
                    f"Migration checksum mismatch for '{version}' in {migrations_dir}: "
                    f"expected {applied_rows[version]}, got {checksum}"
                )
            continue

        try:
            conn.executescript(sql_text)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)",
                (version, checksum, _utc_now()),
            )
            conn.commit()
            newly_applied.append(version)
        except sqlite3.Error as exc:
            conn.rollback()
            raise MigrationError(f"Failed applying migration '{version}': {exc}") from exc

    return newly_applied
