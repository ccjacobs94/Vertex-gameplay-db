"""Live presence telemetry status, manual poll, and daily sweep HTTP endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_sync_scope,
)
from vertex.interfaces.http.schemas import (
    ActiveTelemetrySessionResponse,
    TelemetryPollResponse,
    TelemetryProvidersResponse,
    TelemetryStatusResponse,
    TelemetrySweepResponse,
)

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.get("/status", response_model=TelemetryStatusResponse)
def telemetry_status(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> TelemetryStatusResponse:
    raw = services.telemetry.get_status(scope)
    return TelemetryStatusResponse(
        telemetry_enabled=raw["telemetry_enabled"],
        outbound_sync_enabled=raw["outbound_sync_enabled"],
        poll_interval_seconds=raw["poll_interval_seconds"],
        daily_sweep_interval_seconds=raw["daily_sweep_interval_seconds"],
        flush_delay_seconds=raw["flush_delay_seconds"],
        providers=TelemetryProvidersResponse(**raw["providers"]),
        active_sessions=[ActiveTelemetrySessionResponse(**asdict(s)) for s in raw["active_sessions"]],
    )


@router.post("/poll", response_model=TelemetryPollResponse)
async def poll_telemetry(
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> TelemetryPollResponse:
    raw = await services.telemetry.poll_user(scope, sleep_before_flush=False)
    return TelemetryPollResponse(
        status=raw["status"],
        events=raw["events"],
        active_sessions=[ActiveTelemetrySessionResponse(**asdict(s)) for s in raw["active_sessions"]],
        errors=raw["errors"],
    )


@router.post("/sweep", response_model=TelemetrySweepResponse)
async def run_daily_sweep(
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> TelemetrySweepResponse:
    raw = await services.telemetry.run_daily_sweep_for_user(scope)
    return TelemetrySweepResponse(**raw)
