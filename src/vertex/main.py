"""FastAPI application factory and clean lifecycle wiring."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from vertex import __version__
from vertex.activity.service import ActivityService
from vertex.analytics.service import AnalyticsService
from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import (
    AuthenticationError,
    AuthorizationError,
    BackupError,
    ConflictError,
    MigrationError,
    MissingSecretKeyError,
    NotFoundError,
    RateLimitedError,
    SecretDecryptionError,
    ValidationError,
    VertexError,
)
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.catalog_lookup import HttpCatalogLookupProvider
from vertex.integrations.homeassistant.client import HttpHomeAssistantHistoryProvider
from vertex.integrations.homeassistant.service import HomeAssistantService
from vertex.integrations.platforms.base import PlatformAdapterError
from vertex.integrations.platforms.sync_service import PlatformSyncService
from vertex.integrations.platforms.telemetry import TelemetryService, XboxRtaManager
from vertex.integrations.prices.sync_service import WishlistSyncService
from vertex.integrations.push.web_push import WebPushService
from vertex.interfaces.http import (
    account,
    admin,
    analytics,
    auth,
    catalog as catalog_http,
    connections,
    health,
    homeassistant,
    jobs,
    legacy_ui,
    library,
    portability,
    push as push_http,
    telemetry as telemetry_http,
    wishlist as wishlist_http,
)
from vertex.interfaces.http.deps import AppServices, validate_request_host_and_csrf
from vertex.interfaces.mcp.server import build_mcp_server, build_mcp_streamable_app
from vertex.library.portability import LibraryPortabilityService
from vertex.library.service import LibraryService
from vertex.wishlist.service import WishlistService

logger = logging.getLogger("vertex.app")


def build_app_services(
    settings: Settings | None = None,
    *,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> AppServices:
    """Assemble shared infrastructure, domain services, and external adapters."""
    cfg = settings or Settings.from_env()
    db_manager = DatabaseManager(cfg)
    secret_box = SecretBox(cfg.secret_key, cfg.previous_secret_keys)
    job_queue = JobQueue(db_manager)
    identity = IdentityService(db_manager, secret_box, job_queue)
    catalog_lookup = HttpCatalogLookupProvider(client_factory=client_factory)
    catalog = CatalogService(
        db_manager,
        outbound_sync_enabled=cfg.outbound_sync_enabled,
        lookup_provider=catalog_lookup,
    )
    library_svc = LibraryService(db_manager)
    portability_svc = LibraryPortabilityService(db_manager, identity)
    activity_svc = ActivityService(db_manager)
    analytics_svc = AnalyticsService(db_manager)
    wishlist_svc = WishlistService(db_manager, library_svc)
    web_push = WebPushService(
        settings=cfg,
        db_manager=db_manager,
        secret_box=secret_box,
        wishlist_service=wishlist_svc,
    )
    wishlist_sync = WishlistSyncService(
        settings=cfg,
        job_queue=job_queue,
        identity_service=identity,
        catalog_service=catalog,
        wishlist_service=wishlist_svc,
        web_push_service=web_push,
        client_factory=client_factory,
    )
    ha_history_provider = HttpHomeAssistantHistoryProvider(client_factory=client_factory)
    ha_svc = HomeAssistantService(
        db_manager,
        library_svc,
        activity_svc,
        history_provider=ha_history_provider,
    )
    platform_sync = PlatformSyncService(
        db_manager=db_manager,
        job_queue=job_queue,
        identity_service=identity,
        catalog_service=catalog,
        library_service=library_svc,
        activity_service=activity_svc,
        wishlist_sync=wishlist_sync,
        client_factory=client_factory,
    )
    telemetry_svc = TelemetryService(
        db_manager=db_manager,
        identity_service=identity,
        library_service=library_svc,
        activity_service=activity_svc,
        xbox_adapter=platform_sync.xbox_adapter,
        gog_adapter=platform_sync.gog_adapter,
        client_factory=client_factory,
    )

    return AppServices(
        settings=cfg,
        db_manager=db_manager,
        secret_box=secret_box,
        job_queue=job_queue,
        identity=identity,
        catalog=catalog,
        library=library_svc,
        portability=portability_svc,
        activity=activity_svc,
        analytics=analytics_svc,
        wishlist=wishlist_svc,
        wishlist_sync=wishlist_sync,
        web_push=web_push,
        homeassistant=ha_svc,
        platform_sync=platform_sync,
        telemetry=telemetry_svc,
    )


def create_app(
    settings: Settings | None = None,
    *,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
    services = build_app_services(settings, client_factory=client_factory)
    cfg = services.settings
    db_manager = services.db_manager
    identity = services.identity
    wishlist_sync = services.wishlist_sync
    platform_sync = services.platform_sync
    telemetry_svc = services.telemetry
    mcp_server = build_mcp_server(services) if cfg.mcp_enabled else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db_manager.ensure_system_ready()
        background_tasks: list[asyncio.Task[None]] = []
        rta_manager = XboxRtaManager(
            identity_service=identity,
            telemetry_service=telemetry_svc,
        )
        stop_event = asyncio.Event()

        async def _worker_loop() -> None:
            while not stop_event.is_set():
                try:
                    processed = await platform_sync.process_next_job(worker_id="embedded-worker")
                    if processed is not None:
                        continue
                except Exception as exc:
                    logger.warning("Background worker error: %s", exc)
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=max(0.1, cfg.worker_poll_interval_seconds),
                    )
                except TimeoutError:
                    pass

        async def _scheduled_sync_loop() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=max(0.2, cfg.scheduled_sync_interval_seconds),
                    )
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                try:
                    platform_sync.enqueue_scheduled_sync_iteration()
                except Exception as exc:
                    logger.warning("Scheduled sync enqueue error: %s", exc)

        async def _telemetry_loop() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=max(0.2, cfg.telemetry_poll_interval_seconds),
                    )
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                try:
                    await telemetry_svc.poll_all_users(sleep_before_flush=True)
                except Exception as exc:
                    logger.warning("Telemetry poll loop error: %s", exc)
                if cfg.worker_enabled:
                    try:
                        await rta_manager.reconcile()
                    except Exception as exc:
                        logger.warning("Xbox RTA reconcile error: %s", exc)

        async def _daily_sweep_loop() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=max(0.2, cfg.daily_sweep_interval_seconds),
                    )
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                try:
                    await telemetry_svc.run_daily_sweep_all_users()
                except Exception as exc:
                    logger.warning("Daily sweep loop error: %s", exc)

        async def _wishlist_sync_loop() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=max(1.0, float(cfg.wishlist_sync_interval_seconds)),
                    )
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                try:
                    wishlist_sync.enqueue_scheduled_sync_iteration()
                except Exception as exc:
                    logger.warning("Scheduled wishlist sync loop error: %s", exc)

        if cfg.worker_enabled:
            background_tasks.append(asyncio.create_task(_worker_loop()))
            if cfg.scheduled_sync_enabled and cfg.outbound_sync_enabled:
                background_tasks.append(asyncio.create_task(_scheduled_sync_loop()))
            if cfg.wishlist_sync_enabled and cfg.outbound_sync_enabled:
                background_tasks.append(asyncio.create_task(_wishlist_sync_loop()))
        if cfg.telemetry_enabled and cfg.outbound_sync_enabled:
            if cfg.worker_enabled:
                try:
                    await rta_manager.reconcile()
                except Exception as exc:
                    logger.warning("Initial Xbox RTA reconcile error: %s", exc)
            background_tasks.append(asyncio.create_task(_telemetry_loop()))
            background_tasks.append(asyncio.create_task(_daily_sweep_loop()))

        async with AsyncExitStack() as stack:
            if mcp_server is not None:
                await stack.enter_async_context(mcp_server.session_manager.run())
            try:
                yield
            finally:
                stop_event.set()
                await rta_manager.shutdown()
                for task in background_tasks:
                    task.cancel()
                for task in background_tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(
        title="Vertex Self-Hosted API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.services = services

    _DIRECT_LEGACY_PREFIXES = (
        "/api/ui/",
        "/api/user/",
        "/api/search-external",
        "/api/enrich-metadata",
        "/api/auth/steam/",
        "/api/auth/xbox/",
    )

    @app.middleware("http")
    async def security_and_cache_middleware(request: Request, call_next: Any):
        is_mcp_path = request.url.path == "/mcp" or request.url.path.startswith("/mcp/")
        if not is_mcp_path:
            try:
                validate_request_host_and_csrf(request, cfg)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"code": "security_blocked", "detail": exc.detail})

        path = request.scope.get("path", "")
        if (
            request.headers.get("x-vertex-ui") == "1"
            and path.startswith("/api/")
            and not path.startswith(_DIRECT_LEGACY_PREFIXES)
        ):
            request.scope["path"] = "/api/ui/" + path[len("/api/") :]

        response = await call_next(request)
        if request.url.path.startswith("/api/") or is_mcp_path:
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
        elif request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    _STATUS_MAP: list[tuple[type[VertexError], int]] = [
        (RateLimitedError, 429),
        (AuthenticationError, 401),
        (AuthorizationError, 403),
        (NotFoundError, 404),
        (ConflictError, 409),
        (ValidationError, 422),
        (MissingSecretKeyError, 503),
        (SecretDecryptionError, 503),
        (MigrationError, 500),
        (BackupError, 400),
    ]

    @app.exception_handler(VertexError)
    async def vertex_error_handler(_request: Request, exc: VertexError) -> JSONResponse:
        status_code = 400
        for cls, code in _STATUS_MAP:
            if isinstance(exc, cls):
                status_code = code
                break
        return JSONResponse(status_code=status_code, content={"code": exc.code, "detail": str(exc)})

    @app.exception_handler(PlatformAdapterError)
    async def platform_adapter_error_handler(
        _request: Request, exc: PlatformAdapterError
    ) -> JSONResponse:
        if exc.throttle_seconds > 0:
            status_code = 429
        elif exc.code in {
            "missing_credentials",
            "invalid_credentials",
            "auth_failed",
            "outbound_sync_disabled",
        }:
            status_code = 400
        elif exc.retryable:
            status_code = 502
        else:
            status_code = 400
        return JSONResponse(
            status_code=status_code,
            content={"code": exc.code, "detail": exc.safe_message},
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(connections.router)
    app.include_router(jobs.router)
    app.include_router(library.router)
    app.include_router(analytics.router)
    app.include_router(catalog_http.router)
    app.include_router(homeassistant.router)
    app.include_router(telemetry_http.router)
    app.include_router(wishlist_http.router)
    app.include_router(push_http.router)
    app.include_router(portability.router)
    app.include_router(legacy_ui.router)

    if mcp_server is not None:
        secured_mcp_app = build_mcp_streamable_app(mcp_server, services)
        mcp_methods = ["GET", "POST", "DELETE", "OPTIONS"]
        app.router.routes.append(Route("/mcp", endpoint=secured_mcp_app, methods=mcp_methods))
        app.router.routes.append(Route("/mcp/", endpoint=secured_mcp_app, methods=mcp_methods))

    repo_root = Path(__file__).resolve().parents[2]
    static_dir = repo_root / "static"
    if static_dir.exists() and static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="legacy-static")

    dist_dir = Path(cfg.frontend_dist_dir)
    default_dist_dir = (repo_root / "frontend" / "dist").resolve()
    use_legacy_static = (
        static_dir.exists()
        and (static_dir / "index.html").exists()
        and dist_dir.resolve() == default_dist_dir
    )

    if use_legacy_static:
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(
                static_dir / "index.html",
                headers={"Cache-Control": "no-cache, private"},
            )

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def serve_manifest() -> FileResponse:
            manifest_path = static_dir / "manifest.webmanifest"
            if not manifest_path.exists():
                manifest_path = dist_dir / "manifest.webmanifest"
            return FileResponse(
                manifest_path,
                media_type="application/manifest+json",
            )

        @app.get("/sw.js", include_in_schema=False)
        def serve_sw() -> FileResponse:
            sw_path = static_dir / "sw.js"
            if not sw_path.exists():
                sw_path = dist_dir / "sw.js"
            return FileResponse(
                sw_path,
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )
    elif dist_dir.exists() and (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(
                dist_dir / "index.html",
                headers={"Cache-Control": "no-cache, private"},
            )

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def serve_manifest() -> FileResponse:
            return FileResponse(
                dist_dir / "manifest.webmanifest",
                media_type="application/manifest+json",
            )

        @app.get("/sw.js", include_in_schema=False)
        def serve_sw() -> FileResponse:
            return FileResponse(
                dist_dir / "sw.js",
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )
    else:

        @app.get("/", include_in_schema=False)
        def fallback_index() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><title>Vertex</title><body><p>Vertex API running. Build frontend assets with scripts/build_frontend.py.</p></body>",
                headers={"Cache-Control": "no-cache"},
            )

    return app
