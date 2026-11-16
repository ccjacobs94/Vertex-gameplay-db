"""Library, reconciliation, activity sessions, and stats HTTP router."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query

from vertex.catalog.taxonomy import GENRES, RETIRED_GENRES, TAGS
from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_write_scope,
)
from vertex.interfaces.http.schemas import (
    GameAchievementsResponse,
    GameCreateRequest,
    GameResponse,
    GameUpdateRequest,
    PlaySessionResponse,
    PlaytimePromptResolutionResponse,
    PlaytimePromptResponse,
    ReconcileCandidateResponse,
    ReconcileIdsRequest,
    ResetDismissalsResponse,
    ResolvePlaytimePromptRequest,
    UpdateSessionRequest,
)

router = APIRouter(prefix="/api", tags=["library"])


def _game_to_resp(g: Any) -> GameResponse:
    d = asdict(g)
    d["genres"] = list(g.genres)
    d["tags"] = list(g.tags)
    d.pop("playtime_prompt_muted", None)
    d.pop("achievements_synced_at", None)
    return GameResponse(**d)


@router.get("/taxonomy")
def get_taxonomy(
    scope: UserScope = Depends(require_read_scope),
) -> dict[str, list[str]]:
    scope.require_active()
    return {"genres": GENRES, "tags": TAGS, "retired_genres": RETIRED_GENRES}


@router.get("/games", response_model=list[GameResponse])
def list_games(
    search: str = Query(default=""),
    platform: str = Query(default=""),
    status: str = Query(default=""),
    favorite_only: bool = Query(default=False),
    sort_by: str = Query(default="title"),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[GameResponse]:
    games = services.library.list_games(
        scope,
        search=search,
        platform=platform,
        status=status,
        favorite_only=favorite_only,
        sort_by=sort_by,
    )
    return [_game_to_resp(g) for g in games]


@router.post("/games", response_model=GameResponse, status_code=201)
def add_game(
    body: GameCreateRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> GameResponse:
    game = services.library.add_manual_game(
        scope,
        title=body.title,
        platform=body.platform,
        source=body.source,
        completion_status=body.completion_status,
        ownership_status=body.ownership_status,
        is_game_pass=body.is_game_pass,
        is_play_anywhere=body.is_play_anywhere,
        playtime_hours=body.playtime_hours,
        genres=body.genres,
        tags=body.tags,
        release_year=body.release_year,
        critic_score=body.critic_score,
        user_rating=body.user_rating,
        cover_url=body.cover_url,
        notes=body.notes,
        favorite=body.favorite,
    )
    return _game_to_resp(game)


@router.get("/games/{game_id}", response_model=GameResponse)
def get_game(
    game_id: int,
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> GameResponse:
    return _game_to_resp(services.library.get_game(scope, game_id))


@router.patch("/games/{game_id}", response_model=GameResponse)
def update_game(
    game_id: int,
    body: GameUpdateRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> GameResponse:
    game = services.library.update_game(
        scope,
        game_id,
        title=body.title,
        platform=body.platform,
        completion_status=body.completion_status,
        status_locked=body.status_locked,
        ownership_status=body.ownership_status,
        ownership_locked=body.ownership_locked,
        is_game_pass=body.is_game_pass,
        is_play_anywhere=body.is_play_anywhere,
        playtime_hours=body.playtime_hours,
        genres=body.genres,
        tags=body.tags,
        replace_labels=body.replace_labels,
        user_rating=body.user_rating,
        clear_rating=body.clear_rating,
        release_year=body.release_year,
        cover_url=body.cover_url,
        notes=body.notes,
        favorite=body.favorite,
        playtime_prompt_muted=body.playtime_prompt_muted,
    )
    return _game_to_resp(game)


@router.delete("/games/{game_id}", status_code=204)
def delete_game(
    game_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.library.delete_game(scope, game_id)


@router.get("/games/{game_id}/achievements", response_model=GameAchievementsResponse)
def get_game_achievements(
    game_id: int,
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> GameAchievementsResponse:
    detail = services.library.get_game_achievements(scope, game_id)
    return GameAchievementsResponse(**asdict(detail))


@router.get("/reconcile/candidates", response_model=list[ReconcileCandidateResponse])
def reconcile_candidates(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[ReconcileCandidateResponse]:
    cands = services.library.get_reconcile_candidates(scope)
    return [
        ReconcileCandidateResponse(
            canonical_title=c.canonical_title,
            games=[_game_to_resp(g) for g in c.games],
        )
        for c in cands
    ]


@router.post("/reconcile/confirm")
def confirm_reconcile(
    body: ReconcileIdsRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, int]:
    canon_id = services.library.merge_games(scope, body.game_ids)
    return {"canonical_id": canon_id}


@router.post("/reconcile/unlink/{game_id}", status_code=204)
def unlink_reconcile(
    game_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.library.unlink_game(scope, game_id)


@router.post("/reconcile/dismiss", status_code=204)
def dismiss_reconcile(
    body: ReconcileIdsRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.library.dismiss_reconcile_candidate(scope, body.game_ids)


@router.post("/reconcile/reset-dismissals", response_model=ResetDismissalsResponse)
def reset_reconcile_dismissals(
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> ResetDismissalsResponse:
    cleared = services.library.reset_reconcile_dismissals(scope)
    return ResetDismissalsResponse(cleared=cleared)


@router.get("/playtime-prompts", response_model=list[PlaytimePromptResponse])
def list_playtime_prompts(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[PlaytimePromptResponse]:
    return [PlaytimePromptResponse(**asdict(p)) for p in services.activity.list_playtime_prompts(scope)]


@router.post("/playtime-prompts/{prompt_id}/resolve", response_model=PlaytimePromptResolutionResponse)
def resolve_playtime_prompt(
    prompt_id: int,
    body: ResolvePlaytimePromptRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> PlaytimePromptResolutionResponse:
    res = services.activity.resolve_playtime_prompt(scope, prompt_id, body.action)
    return PlaytimePromptResolutionResponse(**asdict(res))


@router.get("/sessions", response_model=list[PlaySessionResponse])
def list_sessions(
    game_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[PlaySessionResponse]:
    return [
        PlaySessionResponse(**asdict(s))
        for s in services.activity.list_sessions(scope, game_id=game_id, limit=limit)
    ]


@router.patch("/sessions/{session_id}", response_model=PlaySessionResponse)
def update_session(
    session_id: int,
    body: UpdateSessionRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> PlaySessionResponse:
    sess = services.activity.update_session(
        scope,
        session_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        duration_minutes=body.duration_minutes,
        notes=body.notes,
        is_locked=body.is_locked,
    )
    return PlaySessionResponse(**asdict(sess))


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.activity.delete_session(scope, session_id)


@router.get("/ledger")
def get_daily_ledger(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[dict[str, Any]]:
    return [asdict(entry) for entry in services.activity.get_daily_ledger(scope)]


@router.post("/ledger/rebuild")
def rebuild_daily_ledger(
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> list[dict[str, Any]]:
    return [asdict(entry) for entry in services.activity.rebuild_daily_ledger(scope)]
