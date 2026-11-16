"""SQLite storage factory enforcing WAL, foreign keys, and mandatory UserScope."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator

from vertex.config import Settings
from vertex.infrastructure.errors import ScopeViolationError
from vertex.infrastructure.migrations import apply_migrations
from vertex.infrastructure.scope import UserScope, validate_user_id


def configure_sqlite_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def _exclusive_init_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class DatabaseManager:
    """Manages connections to system.db and scoped per-user library.db files."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._system_migrated = False
        self._user_migrated: set[str] = set()

    @property
    def system_db_path(self) -> Path:
        return self.settings.system_db_path

    @property
    def users_dir(self) -> Path:
        return self.settings.users_dir

    def user_db_path(self, scope: UserScope) -> Path:
        if not isinstance(scope, UserScope):
            raise ScopeViolationError("A valid UserScope is required for personal storage")
        safe_id = validate_user_id(scope.user_id)
        return self.users_dir / safe_id / "library.db"

    def ensure_system_ready(self) -> list[str]:
        with self._lock:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.users_dir.mkdir(parents=True, exist_ok=True)
            with _exclusive_init_lock(self.system_db_path.parent / ".system.db.init.lock"):
                conn = configure_sqlite_connection(
                    sqlite3.connect(str(self.system_db_path), timeout=15.0, check_same_thread=False)
                )
                try:
                    applied = apply_migrations(conn, self.settings.migrations_dir / "system")
                    self._system_migrated = True
                    return applied
                finally:
                    conn.close()

    def ensure_user_library(self, scope: UserScope, *, require_active: bool = True) -> list[str]:
        if not isinstance(scope, UserScope):
            raise ScopeViolationError("UserScope is required to access a user library")
        if require_active:
            scope.require_active()
        elif scope.status == "deleting":
            raise ScopeViolationError("Cannot open library belonging to an account being deleted")

        db_path = self.user_db_path(scope)
        with self._lock:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with _exclusive_init_lock(db_path.parent / ".library.db.init.lock"):
                conn = configure_sqlite_connection(
                    sqlite3.connect(str(db_path), timeout=15.0, check_same_thread=False)
                )
                try:
                    applied = apply_migrations(conn, self.settings.migrations_dir / "library")
                    self._user_migrated.add(scope.user_id)
                    return applied
                finally:
                    conn.close()

    @contextmanager
    def system_connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        if not self._system_migrated:
            self.ensure_system_ready()
        conn = configure_sqlite_connection(
            sqlite3.connect(
                str(self.system_db_path),
                timeout=15.0,
                isolation_level=None if immediate else "",
                check_same_thread=False,
            )
        )
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def library_connection(
        self,
        scope: UserScope,
        *,
        require_active: bool = True,
        immediate: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        if not isinstance(scope, UserScope):
            raise ScopeViolationError("Personal library access requires a resolved UserScope")
        if require_active:
            scope.require_active()
        elif scope.status == "deleting":
            raise ScopeViolationError("Cannot open library while account deletion is in progress")

        db_path = self.user_db_path(scope)
        if scope.user_id not in self._user_migrated or not db_path.exists():
            self.ensure_user_library(scope, require_active=require_active)

        conn = configure_sqlite_connection(
            sqlite3.connect(
                str(db_path),
                timeout=15.0,
                isolation_level=None if immediate else "",
                check_same_thread=False,
            )
        )
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def invalidate_user_cache(self, user_id: str) -> None:
        with self._lock:
            self._user_migrated.discard(user_id)
