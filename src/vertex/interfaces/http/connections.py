"""User platform connections HTTP router (masked secrets only)."""

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
from vertex.interfaces.http.schemas import ConnectionsSummaryResponse, UpdateConnectionsRequest

router = APIRouter(prefix="/api/connections", tags=["connections"])


@router.get("", response_model=ConnectionsSummaryResponse)
def get_connections(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> ConnectionsSummaryResponse:
    summary = services.identity.get_connections_summary(scope)
    return ConnectionsSummaryResponse(**asdict(summary))


@router.put("", response_model=ConnectionsSummaryResponse)
def update_connections(
    body: UpdateConnectionsRequest,
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> ConnectionsSummaryResponse:
    summary = services.identity.update_connections(
        scope,
        steam_id=body.steam_id,
        steam_api_key=body.steam_api_key,
        clear_steam_api_key=body.clear_steam_api_key,
        xbox_gamertag=body.xbox_gamertag,
        xbox_xuid=body.xbox_xuid,
        xbl_api_key=body.xbl_api_key,
        clear_xbl_api_key=body.clear_xbl_api_key,
        psn_npsso=body.psn_npsso,
        clear_psn_npsso=body.clear_psn_npsso,
        ha_url=body.ha_url,
        ha_token=body.ha_token,
        clear_ha_token=body.clear_ha_token,
        ha_entity_id=body.ha_entity_id,
    )
    return ConnectionsSummaryResponse(**asdict(summary))
