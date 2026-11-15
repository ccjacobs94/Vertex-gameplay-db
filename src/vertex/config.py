"""Validated configuration and explicit private-host defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime settings for a self-hosted Vertex instance."""

    data_dir: Path
    migrations_dir: Path
    frontend_dist_dir: Path
    bind_host: str = "127.0.0.1"
    port: int = 8090
    secret_key: str = ""
    previous_secret_keys: tuple[str, ...] = ()
    cookie_name: str = "vertex_session"
    cookie_secure: bool = False
    session_ttl_seconds: int = 14 * 24 * 3600
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]", "testserver")
    trusted_proxies: tuple[str, ...] = ()
    outbound_sync_enabled: bool = True
    steam_api_key: str = ""
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 120
    login_max_attempts: int = 8
    login_window_seconds: int = 300
    max_import_bytes: int = 64 * 1024 * 1024

    @property
    def system_db_path(self) -> Path:
        return self.data_dir / "system.db"

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        data_dir: Path | None = None,
        migrations_dir: Path | None = None,
        frontend_dist_dir: Path | None = None,
    ) -> "Settings":
        env = environ if environ is not None else os.environ
        repo_root = Path(__file__).resolve().parents[2]

        resolved_data = data_dir or Path(
            env.get("VERTEX_DATA_DIR") or env.get("GAMEHUB_DATA_PATH") or (repo_root / "data-v2")
        )
        resolved_migrations = migrations_dir or Path(
            env.get("VERTEX_MIGRATIONS_DIR") or (repo_root / "migrations")
        )
        resolved_frontend = frontend_dist_dir or Path(
            env.get("VERTEX_FRONTEND_DIST_DIR") or (repo_root / "frontend" / "dist")
        )

        secret_key = (env.get("VERTEX_SECRET_KEY") or env.get("GAMEHUB_SECRET_KEY") or "").strip()
        prev_keys = _parse_csv(env.get("VERTEX_PREVIOUS_SECRET_KEYS"))

        allowed_hosts = _parse_csv(env.get("VERTEX_ALLOWED_HOSTS")) or (
            "localhost",
            "127.0.0.1",
            "[::1]",
            "testserver",
        )
        trusted_proxies = _parse_csv(env.get("VERTEX_TRUSTED_PROXIES"))

        return cls(
            data_dir=resolved_data,
            migrations_dir=resolved_migrations,
            frontend_dist_dir=resolved_frontend,
            bind_host=(env.get("VERTEX_BIND_HOST") or "127.0.0.1").strip(),
            port=int(env.get("VERTEX_PORT") or "8090"),
            secret_key=secret_key,
            previous_secret_keys=prev_keys,
            cookie_secure=_parse_bool(env.get("VERTEX_COOKIE_SECURE"), False),
            session_ttl_seconds=max(300, int(env.get("VERTEX_SESSION_TTL_SECONDS") or str(14 * 24 * 3600))),
            allowed_hosts=allowed_hosts,
            trusted_proxies=trusted_proxies,
            outbound_sync_enabled=_parse_bool(env.get("VERTEX_OUTBOUND_SYNC_ENABLED"), True),
            steam_api_key=(env.get("STEAM_API_KEY") or "").strip(),
            worker_enabled=_parse_bool(env.get("VERTEX_WORKER_ENABLED"), True),
            worker_poll_interval_seconds=float(env.get("VERTEX_WORKER_POLL_INTERVAL") or "1.0"),
            worker_lease_seconds=max(10, int(env.get("VERTEX_WORKER_LEASE_SECONDS") or "120")),
            login_max_attempts=max(1, int(env.get("VERTEX_LOGIN_MAX_ATTEMPTS") or "8")),
            login_window_seconds=max(10, int(env.get("VERTEX_LOGIN_WINDOW_SECONDS") or "300")),
            max_import_bytes=max(1024, int(env.get("VERTEX_MAX_IMPORT_BYTES") or str(64 * 1024 * 1024))),
        )
