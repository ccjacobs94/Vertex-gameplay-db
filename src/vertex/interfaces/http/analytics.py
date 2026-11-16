"""Read-only library and session analytics HTTP router."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import AppServices, get_services, require_read_scope
from vertex.interfaces.http.schemas import (
    LibraryAnalyticsResponse,
    LibraryStatsResponse,
    SessionAnalyticsResponse,
    SessionWindowStatsResponse,
)

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/stats", response_model=LibraryStatsResponse)
def get_stats(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> LibraryStatsResponse:
    return LibraryStatsResponse(**asdict(services.analytics.get_summary_stats(scope)))


@router.get("/analytics", response_model=LibraryAnalyticsResponse)
def get_library_analytics(
    search: str = Query(default=""),
    platform: str = Query(default=""),
    status: str = Query(default=""),
    genre: str = Query(default=""),
    tag: str = Query(default=""),
    ownership: str = Query(default=""),
    is_game_pass: bool | None = Query(default=None),
    is_play_anywhere: bool | None = Query(default=None),
    favorite_only: bool = Query(default=False),
    client_timezone: str | None = Query(default=None),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> LibraryAnalyticsResponse:
    data = services.analytics.get_library_analytics(
        scope,
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        ownership=ownership,
        is_game_pass=is_game_pass,
        is_play_anywhere=is_play_anywhere,
        favorite_only=favorite_only,
        client_timezone=client_timezone,
    )
    return LibraryAnalyticsResponse(**asdict(data))


@router.get("/analytics/sessions", response_model=SessionAnalyticsResponse)
def get_session_analytics(
    days: int = Query(default=365, ge=0, le=3650),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    title: str | None = Query(default=None),
    game_id: int | None = Query(default=None),
    platform: str | None = Query(default=None),
    client_timezone: str | None = Query(default=None),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> SessionAnalyticsResponse:
    data = services.analytics.get_session_analytics(
        scope,
        days=days,
        game_id=game_id,
        title=title,
        platform=platform,
        client_timezone=client_timezone,
        limit=limit,
        offset=offset,
    )
    return SessionAnalyticsResponse(**asdict(data))


@router.get("/sessions/stats", response_model=SessionWindowStatsResponse)
def get_session_window_stats(
    days: int = Query(default=30, ge=0, le=3650),
    client_timezone: str | None = Query(default=None),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> SessionWindowStatsResponse:
    data = services.analytics.get_session_stats(
        scope,
        days=days,
        client_timezone=client_timezone,
    )
    return SessionWindowStatsResponse(**asdict(data))
