"""FastAPI dependency container, host/CSRF guards, and explicit UserScope extraction."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request

from vertex.activity.service import ActivityService
from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.platforms.sync_service import PlatformSyncService
from vertex.library.portability import LibraryPortabilityService
from vertex.library.service import LibraryService

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(slots=True)
class AppServices:
    settings: Settings
    db_manager: DatabaseManager
    secret_box: SecretBox
    job_queue: JobQueue
    identity: IdentityService
    catalog: CatalogService
    library: LibraryService
    portability: LibraryPortabilityService
    activity: ActivityService
    platform_sync: PlatformSyncService


def get_services(request: Request) -> AppServices:
    return request.app.state.services  # type: ignore[no-any-return]


def _extract_host(raw_host: str | None) -> str:
    val = (raw_host or "").strip().lower()
    if not val:
        return ""
    if val.startswith("["):
        end = val.find("]")
        return val[: end + 1] if end != -1 else val
    return val.split(":", 1)[0]


def resolve_client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "127.0.0.1"
    if peer in settings.trusted_proxies:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",", 1)[0].strip() or peer
    return peer


def validate_request_host_and_csrf(request: Request, settings: Settings) -> None:
    peer = request.client.host if request.client else ""
    raw_host = request.headers.get("host")
    if peer in settings.trusted_proxies and request.headers.get("x-forwarded-host"):
        raw_host = request.headers.get("x-forwarded-host")
    host = _extract_host(raw_host)
    if host and host not in {h.lower() for h in settings.allowed_hosts}:
        raise HTTPException(status_code=400, detail="Disallowed Host header")

    if request.method in _MUTATING_METHODS:
        auth_hdr = request.headers.get("authorization", "")
        cookie_val = request.cookies.get(settings.cookie_name)
        is_pure_bearer = auth_hdr.lower().startswith("bearer ") and not cookie_val
        if is_pure_bearer:
            return

        sec_fetch = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if sec_fetch:
            if sec_fetch not in ("same-origin", "none"):
                raise HTTPException(status_code=403, detail="Cross-site request blocked by CSRF policy")
            return

        origin = (request.headers.get("origin") or "").strip()
        if origin:
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            allowed = {h.lower().strip("[]") for h in settings.allowed_hosts}
            if host:
                allowed.add(host.strip("[]"))
            if not origin_host or origin_host not in allowed:
                raise HTTPException(status_code=403, detail="Untrusted Origin header")


def _resolve_scope(request: Request, services: AppServices, *, required_pat_scope: str | None) -> UserScope:
    auth_hdr = request.headers.get("authorization", "")
    if auth_hdr.lower().startswith("bearer "):
        raw_token = auth_hdr.split(" ", 1)[1].strip()
        scope, _pat = services.identity.resolve_pat(raw_token, required_scope=required_pat_scope)
        return scope

    cookie_token = request.cookies.get(services.settings.cookie_name)
    scope = services.identity.resolve_session(cookie_token)
    if scope is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return scope


def require_read_scope(
    request: Request,
    services: AppServices = Depends(get_services),
) -> UserScope:
    return _resolve_scope(request, services, required_pat_scope="read:library")


def require_write_scope(
    request: Request,
    services: AppServices = Depends(get_services),
) -> UserScope:
    return _resolve_scope(request, services, required_pat_scope="write:library")


def require_sync_scope(
    request: Request,
    services: AppServices = Depends(get_services),
) -> UserScope:
    return _resolve_scope(request, services, required_pat_scope="sync:platforms")


def require_session_scope(
    request: Request,
    services: AppServices = Depends(get_services),
) -> UserScope:
    cookie_token = request.cookies.get(services.settings.cookie_name)
    scope = services.identity.resolve_session(cookie_token)
    if scope is None:
        raise HTTPException(status_code=401, detail="Interactive session authentication required")
    return scope


def require_admin_scope(
    request: Request,
    services: AppServices = Depends(get_services),
) -> UserScope:
    scope = _resolve_scope(request, services, required_pat_scope=None)
    auth_hdr = request.headers.get("authorization", "")
    if auth_hdr.lower().startswith("bearer "):
        raise HTTPException(status_code=403, detail="Admin endpoints require an interactive session")
    return scope.require_admin()
