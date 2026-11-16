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
    xbl_api_key: str = ""
    gog_client_id: str = "46899977096215655"
    gog_client_secret: str = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 120
    scheduled_sync_enabled: bool = True
    scheduled_sync_interval_seconds: float = 6 * 3600.0
    wishlist_sync_enabled: bool = True
    wishlist_sync_interval_seconds: float = 24 * 3600.0
    itad_api_key: str = ""
    psn_product_retrieve_hash: str = "mock_hash"
    web_push_enabled: bool = False
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_claim_email: str = "mailto:admin@localhost.localdomain"
    allowed_push_hosts: tuple[str, ...] = ()
    mcp_enabled: bool = True
    mcp_bind_host: str = "127.0.0.1"
    mcp_port: int = 8096
    mcp_allowed_origins: tuple[str, ...] = ()
    telemetry_enabled: bool = True
    telemetry_poll_interval_seconds: float = 900.0
    telemetry_flush_delay_seconds: float = 45.0
    daily_sweep_interval_seconds: float = 24 * 3600.0
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

        sync_hours_raw = env.get("VERTEX_SYNC_INTERVAL_HOURS") or env.get("SYNC_INTERVAL_HOURS") or "6"
        sync_interval_secs = max(60.0, float(sync_hours_raw) * 3600.0)
        wishlist_secs_raw = env.get("VERTEX_WISHLIST_SYNC_INTERVAL_SECONDS")
        if wishlist_secs_raw is not None and wishlist_secs_raw.strip():
            wishlist_interval_secs = max(60.0, float(wishlist_secs_raw))
        else:
            wishlist_hours_raw = (
                env.get("VERTEX_WISHLIST_SYNC_INTERVAL_HOURS")
                or env.get("WISHLIST_SYNC_INTERVAL_HOURS")
                or "24"
            )
            wishlist_interval_secs = max(60.0, float(wishlist_hours_raw) * 3600.0)
        telem_poll_secs = max(10.0, float(env.get("VERTEX_TELEMETRY_POLL_INTERVAL") or "900.0"))
        telem_flush_secs = max(0.0, float(env.get("VERTEX_TELEMETRY_FLUSH_DELAY") or "45.0"))
        daily_sweep_secs = max(60.0, float(env.get("VERTEX_DAILY_SWEEP_INTERVAL") or str(24 * 3600.0)))
        allowed_push_hosts = _parse_csv(env.get("VERTEX_ALLOWED_PUSH_HOSTS"))
        mcp_allowed_origins = _parse_csv(
            env.get("VERTEX_MCP_ALLOWED_ORIGINS") or env.get("MCP_ALLOWED_ORIGINS")
        )

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
            xbl_api_key=(env.get("XBL_API_KEY") or "").strip(),
            gog_client_id=(env.get("GOG_CLIENT_ID") or "46899977096215655").strip(),
            gog_client_secret=(
                env.get("GOG_CLIENT_SECRET")
                or "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
            ).strip(),
            worker_enabled=_parse_bool(env.get("VERTEX_WORKER_ENABLED"), True),
            worker_poll_interval_seconds=float(env.get("VERTEX_WORKER_POLL_INTERVAL") or "1.0"),
            worker_lease_seconds=max(10, int(env.get("VERTEX_WORKER_LEASE_SECONDS") or "120")),
            scheduled_sync_enabled=_parse_bool(env.get("VERTEX_SCHEDULED_SYNC_ENABLED"), True),
            scheduled_sync_interval_seconds=sync_interval_secs,
            wishlist_sync_enabled=_parse_bool(env.get("VERTEX_WISHLIST_SYNC_ENABLED"), True),
            wishlist_sync_interval_seconds=wishlist_interval_secs,
            itad_api_key=(env.get("VERTEX_ITAD_API_KEY") or env.get("ITAD_API_KEY") or "").strip(),
            psn_product_retrieve_hash=(env.get("VERTEX_PSN_PRODUCT_HASH") or "mock_hash").strip() or "mock_hash",
            web_push_enabled=_parse_bool(env.get("VERTEX_WEB_PUSH_ENABLED"), False),
            vapid_public_key=(env.get("VERTEX_VAPID_PUBLIC_KEY") or env.get("VAPID_PUBLIC_KEY") or "").strip(),
            vapid_private_key=(env.get("VERTEX_VAPID_PRIVATE_KEY") or env.get("VAPID_PRIVATE_KEY") or "").strip(),
            vapid_claim_email=(
                env.get("VERTEX_VAPID_CLAIM_EMAIL")
                or env.get("VAPID_CLAIM_EMAIL")
                or "mailto:admin@localhost.localdomain"
            ).strip(),
            allowed_push_hosts=allowed_push_hosts,
            mcp_enabled=_parse_bool(env.get("VERTEX_MCP_ENABLED"), True),
            mcp_bind_host=(env.get("VERTEX_MCP_BIND_HOST") or "127.0.0.1").strip(),
            mcp_port=int(env.get("VERTEX_MCP_PORT") or env.get("MCP_PORT") or "8096"),
            mcp_allowed_origins=mcp_allowed_origins,
            telemetry_enabled=_parse_bool(env.get("VERTEX_TELEMETRY_ENABLED"), True),
            telemetry_poll_interval_seconds=telem_poll_secs,
            telemetry_flush_delay_seconds=telem_flush_secs,
            daily_sweep_interval_seconds=daily_sweep_secs,
            login_max_attempts=max(1, int(env.get("VERTEX_LOGIN_MAX_ATTEMPTS") or "8")),
            login_window_seconds=max(10, int(env.get("VERTEX_LOGIN_WINDOW_SECONDS") or "300")),
            max_import_bytes=max(1024, int(env.get("VERTEX_MAX_IMPORT_BYTES") or str(64 * 1024 * 1024))),
        )
