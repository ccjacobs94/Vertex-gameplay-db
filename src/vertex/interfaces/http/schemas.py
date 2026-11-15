"""Pydantic v2 HTTP request/response schemas driving FastAPI OpenAPI contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    version: str
    system_db: str
    applied_system_migrations: list[str]
    worker_enabled: bool
    secret_key_configured: bool
    outbound_sync_enabled: bool


class BootstrapStatusResponse(BaseModel):
    bootstrap_open: bool


class AuthCredentialsRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class AcceptInviteRequest(BaseModel):
    invite_code: str = Field(min_length=6, max_length=128)
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class UserAccountResponse(BaseModel):
    id: str
    username: str
    role: str
    status: str
    status_reason: str | None = None
    timezone: str
    timezone_eras: list[dict[str, str]]
    onboarding_completed: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None


class AuthResponse(BaseModel):
    account: UserAccountResponse


class TimezoneUpdateRequest(BaseModel):
    timezone: str
    timezone_eras: list[dict[str, str]] | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class CreatePatRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(min_length=1)
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


class PatResponse(BaseModel):
    id: str
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None


class CreatedPatResponse(BaseModel):
    token: str
    record: PatResponse


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="member")


class AdminUpdateUserRequest(BaseModel):
    role: str | None = None
    status: str | None = None
    status_reason: str | None = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class CreateInviteRequest(BaseModel):
    role: str = Field(default="member")
    note: str = Field(default="", max_length=200)
    ttl_hours: int = Field(default=72, ge=1, le=720)


class InviteResponse(BaseModel):
    code_hash: str
    created_by: str
    role: str
    note: str
    created_at: str
    expires_at: str | None = None
    used_by: str | None = None
    used_at: str | None = None
    revoked_at: str | None = None


class CreatedInviteResponse(BaseModel):
    code: str
    record: InviteResponse


class ConnectionsSummaryResponse(BaseModel):
    steam_id: str | None = None
    steam_api_key_configured: bool
    xbox_gamertag: str | None = None
    xbox_xuid: str | None = None
    xbl_api_key_configured: bool
    xbl_watcher_api_key_configured: bool
    psn_configured: bool
    ha_url: str | None = None
    ha_token_configured: bool
    ha_entity_id: str | None = None
    credential_decryption_error: str | None = None
    updated_at: str | None = None


class UpdateConnectionsRequest(BaseModel):
    steam_id: str | None = None
    steam_api_key: str | None = None
    clear_steam_api_key: bool = False
    xbox_gamertag: str | None = None
    xbox_xuid: str | None = None
    xbl_api_key: str | None = None
    clear_xbl_api_key: bool = False
    psn_npsso: str | None = None
    clear_psn_npsso: bool = False
    ha_url: str | None = None
    ha_token: str | None = None
    clear_ha_token: bool = False
    ha_entity_id: str | None = None


class GameCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=80)
    source: str = Field(default="Manual", max_length=60)
    completion_status: str = Field(default="Not Played")
    ownership_status: str = Field(default="Owned")
    is_game_pass: bool = False
    is_play_anywhere: bool = False
    playtime_hours: float = Field(default=0.0, ge=0.0)
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    release_year: int | None = None
    critic_score: int | None = None
    user_rating: int | None = Field(default=None, ge=1, le=10)
    cover_url: str | None = None
    notes: str = ""
    favorite: bool = False


class GameUpdateRequest(BaseModel):
    title: str | None = None
    platform: str | None = None
    completion_status: str | None = None
    status_locked: bool | None = None
    ownership_status: str | None = None
    ownership_locked: bool | None = None
    is_game_pass: bool | None = None
    is_play_anywhere: bool | None = None
    playtime_hours: float | None = Field(default=None, ge=0.0)
    genres: list[str] | None = None
    tags: list[str] | None = None
    replace_labels: bool = False
    user_rating: int | None = Field(default=None, ge=1, le=10)
    clear_rating: bool = False
    release_year: int | None = None
    cover_url: str | None = None
    notes: str | None = None
    favorite: bool | None = None
    playtime_prompt_muted: bool | None = None


class GameResponse(BaseModel):
    id: int
    external_id: str
    title: str
    platform: str
    source: str
    playtime_hours: float
    playtime_minutes: int
    last_synced_playtime_minutes: int | None = None
    last_played: str | None = None
    completion_status: str
    status_locked: bool
    ownership_status: str
    ownership_locked: bool
    is_game_pass: bool
    is_play_anywhere: bool
    achievements_unlocked: int
    achievements_total: int
    genres: list[str]
    tags: list[str]
    critic_score: int | None = None
    user_rating: int | None = None
    release_year: int | None = None
    cover_url: str | None = None
    notes: str
    favorite: bool
    canonical_id: int | None = None
    created_at: str
    updated_at: str


class LibraryStatsResponse(BaseModel):
    total_entries: int
    unique_titles: int
    playing: int
    completed: int
    not_played: int
    favorites: int
    total_playtime_hours: float
    platforms: dict[str, int]


class ReconcileIdsRequest(BaseModel):
    game_ids: list[int] = Field(min_length=2)


class ReconcileCandidateResponse(BaseModel):
    canonical_title: str
    games: list[GameResponse]


class PlaySessionResponse(BaseModel):
    id: int
    game_id: int
    platform: str
    started_at: str
    ended_at: str
    duration_minutes: int
    source: str
    precision_level: str
    notes: str | None = None
    achievements_unlocked: int
    is_locked: bool
    created_at: str
    updated_at: str


class UpdateSessionRequest(BaseModel):
    started_at: str | None = None
    ended_at: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    notes: str | None = None
    is_locked: bool | None = True


class JobResponse(BaseModel):
    id: int
    kind: str
    provider: str
    state: str
    attempts: int
    max_attempts: int
    scheduled_at: str
    available_at: str
    started_at: str | None = None
    finished_at: str | None = None
    last_error_code: str | None = None
    last_error: str | None = None
    result: dict[str, Any] | None = None


class ProviderStatusResponse(BaseModel):
    provider: str
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error_code: str | None = None
    last_error: str | None = None
    next_attempt_at: str | None = None
    active_state: str | None = None
    credential_expired: bool


class JobsOverviewResponse(BaseModel):
    providers: list[ProviderStatusResponse]
    recent_jobs: list[JobResponse]


class JsonImportRequest(BaseModel):
    payload: dict[str, Any]
    mode: str = Field(default="merge", pattern="^(merge|replace)$")
    dry_run: bool = False


class ImportReportResponse(BaseModel):
    dry_run: bool
    mode: str
    format_source: str
    games_added: int
    games_updated: int
    games_skipped: int
    sessions_added: int
    warnings: list[str]
