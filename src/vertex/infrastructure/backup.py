"""Whole-instance consistent SQLite online backup, manifest verification, and clean-directory restore."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vertex import __version__
from vertex.infrastructure.errors import BackupError
from vertex.infrastructure.migrations import apply_migrations, list_applied_migrations
from vertex.infrastructure.scope import validate_user_id
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager, configure_sqlite_connection


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sqlite_backup_file(src_path: Path, dst_path: Path) -> None:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(src_path), timeout=15.0)
    dst = sqlite3.connect(str(dst_path), timeout=15.0)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def _sqlite_integrity_ok(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise BackupError(f"SQLite integrity check failed for {db_path}: {row}")
    finally:
        conn.close()


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: int
    app_version: str
    created_at: str
    secret_key_fingerprint: str | None
    key_recovery_note: str
    system_schema_versions: list[str]
    users: list[dict[str, Any]]
    files: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "secret_key_fingerprint": self.secret_key_fingerprint,
            "key_recovery_note": self.key_recovery_note,
            "system_schema_versions": self.system_schema_versions,
            "users": self.users,
            "files": self.files,
        }


def create_backup_set(
    db_manager: DatabaseManager,
    dest_dir: Path,
    *,
    secret_box: SecretBox | None = None,
) -> BackupManifest:
    """Create a complete SQLite backup set with manifest checksums (never leaks secret keys)."""
    dest_dir = Path(dest_dir)
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise BackupError(f"Backup destination must be empty or non-existent: {dest_dir}")

    db_manager.ensure_system_ready()
    dest_dir.mkdir(parents=True, exist_ok=True)

    sys_dest = dest_dir / "system.db"
    _sqlite_backup_file(db_manager.system_db_path, sys_dest)
    _sqlite_integrity_ok(sys_dest)

    with db_manager.system_connection() as sys_conn:
        sys_versions = [m["version"] for m in list_applied_migrations(sys_conn)]
        user_rows = sys_conn.execute(
            "SELECT id, username, role, status FROM users ORDER BY created_at ASC"
        ).fetchall()

    files_meta: list[dict[str, Any]] = [
        {
            "path": "system.db",
            "sha256": _sha256_file(sys_dest),
            "bytes": sys_dest.stat().st_size,
        }
    ]
    users_meta: list[dict[str, Any]] = []

    for u in user_rows:
        uid = validate_user_id(u["id"])
        src_lib = db_manager.users_dir / uid / "library.db"
        rel_lib = f"users/{uid}/library.db"
        lib_versions: list[str] = []
        sha = None
        if src_lib.exists():
            dst_lib = dest_dir / rel_lib
            _sqlite_backup_file(src_lib, dst_lib)
            _sqlite_integrity_ok(dst_lib)
            lib_conn = sqlite3.connect(f"file:{dst_lib.as_posix()}?mode=ro", uri=True)
            lib_conn.row_factory = sqlite3.Row
            try:
                lib_versions = [m["version"] for m in list_applied_migrations(lib_conn)]
            finally:
                lib_conn.close()
            sha = _sha256_file(dst_lib)
            files_meta.append(
                {
                    "path": rel_lib,
                    "sha256": sha,
                    "bytes": dst_lib.stat().st_size,
                }
            )
        users_meta.append(
            {
                "user_id": uid,
                "username": u["username"],
                "role": u["role"],
                "status": u["status"],
                "library_file": rel_lib if sha else None,
                "library_sha256": sha,
                "schema_versions": lib_versions,
            }
        )

    manifest = BackupManifest(
        format_version=1,
        app_version=__version__,
        created_at=datetime.now(timezone.utc).isoformat(),
        secret_key_fingerprint=secret_box.key_fingerprint() if secret_box else None,
        key_recovery_note=(
            "Store VERTEX_SECRET_KEY / GAMEHUB_SECRET_KEY separately in your password manager. "
            "Without the matching key, encrypted user connection credentials cannot be decrypted after restore."
        ),
        system_schema_versions=sys_versions,
        users=users_meta,
        files=files_meta,
    )
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def verify_backup_set(backup_dir: Path) -> BackupManifest:
    """Verify manifest checksums and SQLite integrity across the entire backup set."""
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise BackupError(f"Missing manifest.json in backup directory: {backup_dir}")

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("format_version") != 1:
        raise BackupError(f"Unsupported backup format_version: {raw.get('format_version')}")

    files = raw.get("files") or []
    for entry in files:
        rel = entry.get("path") or ""
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise BackupError(f"Invalid manifest path: {rel}")
        target = backup_dir / rel
        if not target.exists():
            raise BackupError(f"Backup file missing: {rel}")
        actual_sha = _sha256_file(target)
        if actual_sha != entry.get("sha256"):
            raise BackupError(f"Checksum mismatch for {rel}: expected {entry.get('sha256')}, got {actual_sha}")
        if rel.endswith(".db"):
            _sqlite_integrity_ok(target)

    return BackupManifest(
        format_version=raw["format_version"],
        app_version=raw["app_version"],
        created_at=raw["created_at"],
        secret_key_fingerprint=raw.get("secret_key_fingerprint"),
        key_recovery_note=raw.get("key_recovery_note", ""),
        system_schema_versions=list(raw.get("system_schema_versions") or []),
        users=list(raw.get("users") or []),
        files=files,
    )


def restore_backup_set(
    backup_dir: Path,
    dest_data_dir: Path,
    migrations_dir: Path,
) -> BackupManifest:
    """Restore a verified backup set into a clean destination data directory."""
    manifest = verify_backup_set(backup_dir)
    dest_data_dir = Path(dest_data_dir)
    if dest_data_dir.exists() and any(dest_data_dir.iterdir()):
        raise BackupError(
            f"Restore destination must be empty to prevent overwriting active data: {dest_data_dir}"
        )

    dest_data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vertex-restore-", dir=str(dest_data_dir.parent)) as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "users").mkdir(parents=True, exist_ok=True)

        sys_src = backup_dir / "system.db"
        sys_dst = stage / "system.db"
        _sqlite_backup_file(sys_src, sys_dst)
        sys_conn = configure_sqlite_connection(sqlite3.connect(str(sys_dst)))
        try:
            apply_migrations(sys_conn, migrations_dir / "system")
        finally:
            sys_conn.close()

        for user in manifest.users:
            uid = validate_user_id(user["user_id"])
            lib_rel = user.get("library_file")
            if lib_rel:
                lib_src = backup_dir / lib_rel
                lib_dst = stage / "users" / uid / "library.db"
                _sqlite_backup_file(lib_src, lib_dst)
                lib_conn = configure_sqlite_connection(sqlite3.connect(str(lib_dst)))
                try:
                    apply_migrations(lib_conn, migrations_dir / "library")
                finally:
                    lib_conn.close()

        if dest_data_dir.exists():
            try:
                dest_data_dir.rmdir()
                shutil.move(str(stage), str(dest_data_dir))
            except OSError:
                for item in stage.iterdir():
                    shutil.move(str(item), str(dest_data_dir / item.name))
        else:
            shutil.move(str(stage), str(dest_data_dir))

    return manifest
