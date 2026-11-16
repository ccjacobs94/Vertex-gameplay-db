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
    timezone_eras: list[dict[str, Any]]
    onboarding_completed: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None


class AuthResponse(BaseModel):
    account: UserAccountResponse


class TimezoneUpdateRequest(BaseModel):
    timezone: str
    timezone_eras: list[dict[str, Any]] | None = None


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


class ResetDismissalsResponse(BaseModel):
    cleared: int


class AchievementPlatformStatusResponse(BaseModel):
    game_id: int
    platform: str
    short_platform: str
    source: str
    unlocked: bool
    unlock_time: str | None = None
    reward_type: str | None = None
    reward_value: str | None = None
    rarity_pct: float | None = None


class GroupedAchievementResponse(BaseModel):
    key: str
    name: str
    description: str
    icon_url: str | None = None
    icon_gray_url: str | None = None
    is_hidden: bool
    unlocked: bool
    unlock_time: str | None = None
    reward_type: str | None = None
    reward_value: str | None = None
    rarity_pct: float | None = None
    unlocked_platforms: list[str]
    platforms: list[AchievementPlatformStatusResponse]


class PlatformAchievementSummaryResponse(BaseModel):
    game_id: int
    title: str
    platform: str
    short_platform: str
    source: str
    unlocked: int
    total: int
    percentage: float
    achievements_synced_at: str | None = None


class UnifiedAchievementSummaryResponse(BaseModel):
    unlocked: int
    total: int
    percentage: float
    overlap_count: int


class GameAchievementsResponse(BaseModel):
    game_id: int
    unified_summary: UnifiedAchievementSummaryResponse
    per_platform_summary: list[PlatformAchievementSummaryResponse]
    achievements: list[GroupedAchievementResponse]


class PlaytimePromptResponse(BaseModel):
    id: int
    game_id: int
    title: str
    platform: str
    cover_url: str | None = None
    current_status: str
    previous_hours: float
    new_hours: float
    delta_hours: float
    total_playtime_hours: float
    detected_at: str
    status: str


class ResolvePlaytimePromptRequest(BaseModel):
    action: str = Field(pattern="^(accept|dismiss|mute)$")


class PlaytimePromptResolutionResponse(BaseModel):
    prompt_id: int
    action: str
    game_id: int
    completion_status: str
    status_locked: bool
    playtime_prompt_muted: bool


class CatalogSearchResultResponse(BaseModel):
    normalized_title: str
    title: str
    steam_appid: str | None = None
    genres: list[str]
    tags: list[str]
    release_year: int | None = None
    critic_score: int | None = None
    cover_url: str | None = None
    source: str
    retrieved_at: str
    cached: bool


class CatalogEnrichRequest(BaseModel):
    limit: int = Field(default=500, ge=1, le=2000)
    force: bool = False


class CatalogEnrichmentReportResponse(BaseModel):
    games_enriched: int
    covers_filled: int
    remaining_without_genres: int
    used_network: bool
    message: str


class AnalyticsSummaryResponse(BaseModel):
    total_entries: int
    unique_titles: int
    total_hours: float
    played_games: int
    beaten_count: int
    dropped_count: int
    playing_count: int
    shelf_of_shame_count: int
    favorites_count: int
    completion_rate: float
    drop_rate: float
    avg_hours: float
    avg_played_hours: float
    avg_beaten_hours: float
    avg_dropped_hours: float
    dominant_system: str
    dominant_genre: str
    achievements_unlocked: int
    achievements_total: int
    achievement_rate: float
    perfect_games_count: int


class PlatformAnalyticsItemResponse(BaseModel):
    name: str
    count: int
    hours: float
    beaten_count: int
    playtime_pct: float
    library_pct: float
    completion_rate: float
    top_game_title: str | None = None
    top_game_hours: float


class GenreAnalyticsItemResponse(BaseModel):
    genre: str
    count: int
    hours: float
    beaten_count: int
    playing_count: int
    backlog_count: int
    dropped_count: int
    playtime_pct: float
    completion_rate: float


class TagAnalyticsItemResponse(BaseModel):
    tag: str
    count: int
    hours: float
    dropped_count: int
    drop_rate: float
    completion_rate: float


class StatusAnalyticsItemResponse(BaseModel):
    name: str
    count: int
    count_pct: float
    hours: float
    hours_pct: float


class BucketAnalyticsItemResponse(BaseModel):
    key: str
    label: str
    count: int
    count_pct: float
    hours: float
    hours_pct: float


class CircadianRhythmResponse(BaseModel):
    grid: list[list[int]]
    time_buckets: dict[str, int]
    total_events: int
    peak_day: str
    peak_hour: str
    archetype: str
    eras_count: int


class LibraryAnalyticsResponse(BaseModel):
    summary: AnalyticsSummaryResponse
    platforms: list[PlatformAnalyticsItemResponse]
    genres: list[GenreAnalyticsItemResponse]
    tags: list[TagAnalyticsItemResponse]
    statuses: dict[str, StatusAnalyticsItemResponse]
    commitment_tiers: list[BucketAnalyticsItemResponse]
    eras: list[BucketAnalyticsItemResponse]
    avg_user_rating: float | None = None
    avg_critic_score: float | None = None
    backlog_gems: list[dict[str, Any]]
    hall_of_fame: list[dict[str, Any]]
    circadian_rhythm: CircadianRhythmResponse
    subscription_roi: dict[str, Any]
    cross_platform: dict[str, Any]
    trophy_pyramid: dict[str, Any]
    almost_perfect: list[dict[str, Any]]


class SessionAnalyticsResponse(BaseModel):
    kpis: dict[str, Any]
    heatmap_365: list[dict[str, Any]]
    circadian_clock: dict[str, Any]
    snack_vs_binge: dict[str, Any]
    rotation_gantt_30d: dict[str, Any]
    recent_sessions: list[dict[str, Any]]
    total_sessions: int
    limit: int
    offset: int


class SessionWindowStatsResponse(BaseModel):
    total_sessions: int
    total_minutes: int
    average_duration_minutes: float
    daily_playtime_series: list[dict[str, Any]]
    platform_distribution: dict[str, int]


class HomeAssistantEventRequest(BaseModel):
    event: str = Field(pattern="^(start|stop|record)$")
    title: str | None = None
    platform: str | None = None
    external_id: str | None = None
    source: str = Field(default="homeassistant", max_length=60)
    started_at: str | None = None
    ended_at: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    active_minutes: float | None = Field(default=None, ge=0.0)
    idle_minutes: float | None = Field(default=None, ge=0.0)
    wall_clock_minutes: float | None = Field(default=None, ge=0.0)
    achievements_unlocked: int = Field(default=0, ge=0)
    notes: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class HomeAssistantEventResponse(BaseModel):
    status: str
    game_id: int
    session_id: int | None = None
    duration_minutes: int
    idempotency_key: str


class HomeAssistantImportCsvRequest(BaseModel):
    csv_content: str = Field(min_length=1)
    platform: str = Field(default="Xbox", max_length=80)
    max_gap_seconds: int = Field(default=1800, ge=0, le=86400)


class HomeAssistantImportResponse(BaseModel):
    status: str
    imported_count: int
    skipped_count: int
    total_spans: int


class WishlistTargetInput(BaseModel):
    platform: str = Field(min_length=1, max_length=60)
    target_price_usd: float = Field(default=19.99, ge=0.0)
    allowed_storefronts: list[str] = Field(default_factory=list)
    is_active: bool = True


class WishlistCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    platform_targets: list[WishlistTargetInput | str] = Field(default_factory=list)
    release_year: int | None = None
    developer: str = ""
    publisher: str = ""
    cover_url: str | None = None
    genres: list[str] = Field(default_factory=list)
    critic_score: int | None = None
    allow_smart_upgrade: bool = True
    preferred_edition_id: int | None = None
    notes: str = ""
    force_track_owned: bool = False


class WishlistUpdateRequest(BaseModel):
    allow_smart_upgrade: bool | None = None
    preferred_edition_id: int | None = None
    status: str | None = None
    notes: str | None = None


class WishlistPlatformsUpdateRequest(BaseModel):
    platform_targets: list[WishlistTargetInput | str] = Field(default_factory=list)
    remove_platforms: list[str] = Field(default_factory=list)
    allow_smart_upgrade: bool | None = None
    force_track_owned: bool = False


class WishlistPriceRecordRequest(BaseModel):
    price_usd: float = Field(ge=0.0)
    platform: str = Field(default="PC", max_length=60)
    storefront: str = Field(default="manual", max_length=60)
    edition_name: str = Field(default="Standard Edition", max_length=120)
    edition_tier: str | None = None
    regular_price_usd: float | None = Field(default=None, ge=0.0)
    store_url: str = ""
    store_sku_id: str | None = None
    is_drm_free: bool = False
    is_authorized_seller: bool = True


class WishlistPurchaseRequest(BaseModel):
    platform: str = Field(default="PC", max_length=80)
    edition_name: str | None = None
    price_paid_usd: float | None = Field(default=None, ge=0.0)
    completion_status: str = Field(default="Plan to Play")


class CanonicalGameResponse(BaseModel):
    id: int
    normalized_title: str
    title: str
    release_year: int | None = None
    developer: str
    publisher: str
    cover_url: str | None = None
    genres: list[str]
    critic_score: int | None = None
    library_game_id: int | None = None
    created_at: str
    updated_at: str


class EditionPlatformSkuResponse(BaseModel):
    id: int
    edition_id: int
    platform_family: str
    platform_label: str
    storefront: str
    store_sku_id: str
    itad_plain: str | None = None
    store_url: str
    msrp_usd: float | None = None
    current_price_usd: float | None = None
    regular_price_usd: float | None = None
    discount_pct: int
    historical_low_usd: float | None = None
    is_authorized_seller: bool
    is_drm_free: bool
    last_checked_at: str | None = None
    created_at: str
    updated_at: str


class GameEditionResponse(BaseModel):
    id: int
    canonical_game_id: int
    edition_name: str
    edition_tier: str
    tier_rank: int
    is_base_edition: bool
    included_dlc_notes: str
    cover_url: str | None = None
    skus: list[EditionPlatformSkuResponse]
    created_at: str
    updated_at: str


class WishlistPlatformTargetResponse(BaseModel):
    id: int
    wishlist_item_id: int
    platform_family: str
    allowed_storefronts: list[str]
    target_price_usd: float
    is_active: bool
    muted_owned_conflict: bool
    last_alerted_at: str | None = None
    last_alerted_price_usd: float | None = None
    last_alerted_edition_id: int | None = None
    created_at: str
    updated_at: str


class OwnershipConflictResponse(BaseModel):
    already_in_library: bool
    conflicted_platforms: list[str]
    conflict_warning: str | None = None


class WishlistItemResponse(BaseModel):
    id: int
    canonical_game_id: int
    preferred_edition_id: int | None = None
    allow_smart_upgrade: bool
    status: str
    notes: str
    added_via: str
    created_at: str
    updated_at: str
    canonical_game: CanonicalGameResponse
    targets: list[WishlistPlatformTargetResponse]
    editions: list[GameEditionResponse]
    skus: list[EditionPlatformSkuResponse]
    conflict: OwnershipConflictResponse


class WishlistDealResponse(BaseModel):
    wishlist_item_id: int
    canonical_game_id: int
    game_title: str
    cover_url: str | None = None
    edition_id: int
    edition_name: str
    edition_tier: str
    tier_rank: int
    sku_id: int
    platform_family: str
    platform_label: str
    storefront: str
    store_url: str
    current_price_usd: float
    regular_price_usd: float | None = None
    discount_pct: int
    target_price_usd: float
    savings_below_target_usd: float
    current_base_edition_price_usd: float | None = None
    is_smart_upgrade: bool
    is_all_time_low: bool
    is_drm_free: bool
    deal_headline: str


class WishlistDealEvaluationResponse(BaseModel):
    total_deals: int
    smart_upgrades_count: int
    all_time_lows_count: int
    deals: list[WishlistDealResponse]


class WishlistPriceRecordResponse(BaseModel):
    sku_id: int
    price_usd: float
    is_all_time_low: bool
    historical_low_usd: float
    item: WishlistItemResponse


class WishlistPurchaseResponse(BaseModel):
    item: WishlistItemResponse
    library_game_id: int
    already_purchased: bool


class PriceHistoryEntryResponse(BaseModel):
    id: int
    sku_id: int
    storefront: str
    price_usd: float
    regular_price_usd: float | None = None
    discount_pct: int
    is_all_time_low: bool
    source: str
    recorded_at: str


class WishlistHistoryResponse(BaseModel):
    item: WishlistItemResponse
    price_history_by_sku: dict[str, list[PriceHistoryEntryResponse]]
