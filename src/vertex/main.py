"""FastAPI application factory and clean lifecycle wiring."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from vertex import __version__
from vertex.activity.service import ActivityService
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
from vertex.integrations.platforms.sync_service import PlatformSyncService
from vertex.interfaces.http import (
    account,
    admin,
    auth,
    connections,
    health,
    jobs,
    library,
    portability,
)
from vertex.interfaces.http.deps import AppServices, validate_request_host_and_csrf
from vertex.library.portability import LibraryPortabilityService
from vertex.library.service import LibraryService

logger = logging.getLogger("vertex.app")


def create_app(
    settings: Settings | None = None,
    *,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    db_manager = DatabaseManager(cfg)
    secret_box = SecretBox(cfg.secret_key, cfg.previous_secret_keys)
    job_queue = JobQueue(db_manager)
    identity = IdentityService(db_manager, secret_box, job_queue)
    catalog = CatalogService(db_manager)
    library_svc = LibraryService(db_manager)
    portability_svc = LibraryPortabilityService(db_manager, identity)
    activity_svc = ActivityService(db_manager)
    platform_sync = PlatformSyncService(
        db_manager=db_manager,
        job_queue=job_queue,
        identity_service=identity,
        catalog_service=catalog,
        library_service=library_svc,
        activity_service=activity_svc,
        client_factory=client_factory,
    )

    services = AppServices(
        settings=cfg,
        db_manager=db_manager,
        secret_box=secret_box,
        job_queue=job_queue,
        identity=identity,
        catalog=catalog,
        library=library_svc,
        portability=portability_svc,
        activity=activity_svc,
        platform_sync=platform_sync,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db_manager.ensure_system_ready()
        worker_task: asyncio.Task[None] | None = None
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

        if cfg.worker_enabled:
            worker_task = asyncio.create_task(_worker_loop())

        try:
            yield
        finally:
            stop_event.set()
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="Vertex Self-Hosted API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.services = services

    @app.middleware("http")
    async def security_and_cache_middleware(request: Request, call_next: Any):
        try:
            validate_request_host_and_csrf(request, cfg)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"code": "security_blocked", "detail": exc.detail})

        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
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

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(connections.router)
    app.include_router(jobs.router)
    app.include_router(library.router)
    app.include_router(portability.router)

    dist_dir = Path(cfg.frontend_dist_dir)
    if dist_dir.exists() and (dist_dir / "index.html").exists():
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
