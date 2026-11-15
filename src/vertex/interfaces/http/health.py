"""Liveness and migration/database readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from vertex import __version__
from vertex.infrastructure.migrations import list_applied_migrations
from vertex.interfaces.http.deps import AppServices, get_services
from vertex.interfaces.http.schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadinessResponse)
def readyz(services: AppServices = Depends(get_services)) -> ReadinessResponse:
    with services.db_manager.system_connection() as conn:
        conn.execute("SELECT 1").fetchone()
        applied = [m["version"] for m in list_applied_migrations(conn)]

    return ReadinessResponse(
        status="ready",
        version=__version__,
        system_db="ok",
        applied_system_migrations=applied,
        worker_enabled=services.settings.worker_enabled,
        secret_key_configured=services.secret_box.is_configured,
        outbound_sync_enabled=services.settings.outbound_sync_enabled,
    )
