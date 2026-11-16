"""Authenticated Home Assistant event and CSV ingestion HTTP router."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_sync_scope,
    require_write_scope,
)
from vertex.interfaces.http.schemas import (
    HomeAssistantEventRequest,
    HomeAssistantEventResponse,
    HomeAssistantImportCsvRequest,
    HomeAssistantImportResponse,
    HomeAssistantSyncRequest,
    HomeAssistantSyncResponse,
)

router = APIRouter(prefix="/api/integrations/homeassistant", tags=["homeassistant"])


@router.post("/events", response_model=HomeAssistantEventResponse)
def ingest_ha_event(
    body: HomeAssistantEventRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> HomeAssistantEventResponse:
    res = services.homeassistant.ingest_event(
        scope,
        event=body.event,
        title=body.title,
        platform=body.platform,
        external_id=body.external_id,
        source=body.source,
        started_at=body.started_at,
        ended_at=body.ended_at,
        duration_minutes=body.duration_minutes,
        active_minutes=body.active_minutes,
        idle_minutes=body.idle_minutes,
        wall_clock_minutes=body.wall_clock_minutes,
        achievements_unlocked=body.achievements_unlocked,
        notes=body.notes,
        idempotency_key=body.idempotency_key,
    )
    return HomeAssistantEventResponse(**asdict(res))


@router.post("/import-csv", response_model=HomeAssistantImportResponse)
def import_ha_csv(
    body: HomeAssistantImportCsvRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> HomeAssistantImportResponse:
    res = services.homeassistant.import_csv(
        scope,
        csv_content=body.csv_content,
        platform=body.platform,
        max_gap_seconds=body.max_gap_seconds,
    )
    return HomeAssistantImportResponse(**asdict(res))


@router.post("/sync", response_model=HomeAssistantSyncResponse)
async def sync_ha_history(
    body: HomeAssistantSyncRequest | None = None,
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> HomeAssistantSyncResponse:
    req = body or HomeAssistantSyncRequest()
    creds = services.identity.get_decrypted_connections(scope)
    res = await services.homeassistant.sync_history(
        scope,
        ha_url=req.ha_url,
        ha_token=req.ha_token,
        entity_id=req.entity_id,
        platform=req.platform,
        days_back=req.days_back,
        max_gap_seconds=req.max_gap_seconds,
        creds=creds,
    )
    return HomeAssistantSyncResponse(**asdict(res))
