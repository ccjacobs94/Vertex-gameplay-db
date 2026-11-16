"""Domain models for personal game library entries, copies, stats, and portability."""

from __future__ import annotations

from dataclasses import dataclass

VALID_COMPLETION_STATUSES = frozenset(
    {
        "Not Played",
        "Unplayed",
        "Plan to Play",
        "Playing",
        "Beaten",
        "Completed",
        "Dropped",
    }
)

VALID_OWNERSHIP_STATUSES = frozenset(
    {
        "Owned",
        "Subscription",
        "Borrowed",
        "None",
    }
)


@dataclass(frozen=True, slots=True)
class GameRecord:
    id: int
    external_id: str
    title: str
    platform: str
    source: str
    playtime_hours: float
    playtime_minutes: int
    last_synced_playtime_minutes: int | None
    last_played: str | None
    completion_status: str
    status_locked: bool
    ownership_status: str
    ownership_locked: bool
    is_game_pass: bool
    is_play_anywhere: bool
    achievements_unlocked: int
    achievements_total: int
    achievements_synced_at: str | None
    genres: tuple[str, ...]
    tags: tuple[str, ...]
    critic_score: int | None
    user_rating: int | None
    release_year: int | None
    cover_url: str | None
    notes: str
    favorite: bool
    playtime_prompt_muted: bool
    canonical_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LibraryStats:
    total_entries: int
    unique_titles: int
    playing: int
    completed: int
    not_played: int
    favorites: int
    total_playtime_hours: float
    platforms: dict[str, int]


@dataclass(frozen=True, slots=True)
class ReconcileCandidate:
    canonical_title: str
    games: tuple[GameRecord, ...]


@dataclass(frozen=True, slots=True)
class ImportReport:
    dry_run: bool
    mode: str
    format_source: str
    games_added: int
    games_updated: int
    games_skipped: int
    sessions_added: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AchievementPlatformStatus:
    game_id: int
    platform: str
    short_platform: str
    source: str
    unlocked: bool
    unlock_time: str | None
    reward_type: str | None
    reward_value: str | None
    rarity_pct: float | None


@dataclass(frozen=True, slots=True)
class GroupedAchievement:
    key: str
    name: str
    description: str
    icon_url: str | None
    icon_gray_url: str | None
    is_hidden: bool
    unlocked: bool
    unlock_time: str | None
    reward_type: str | None
    reward_value: str | None
    rarity_pct: float | None
    unlocked_platforms: tuple[str, ...]
    platforms: tuple[AchievementPlatformStatus, ...]


@dataclass(frozen=True, slots=True)
class PlatformAchievementSummary:
    game_id: int
    title: str
    platform: str
    short_platform: str
    source: str
    unlocked: int
    total: int
    percentage: float
    achievements_synced_at: str | None


@dataclass(frozen=True, slots=True)
class UnifiedAchievementSummary:
    unlocked: int
    total: int
    percentage: float
    overlap_count: int


@dataclass(frozen=True, slots=True)
class GameAchievementsDetail:
    game_id: int
    unified_summary: UnifiedAchievementSummary
    per_platform_summary: tuple[PlatformAchievementSummary, ...]
    achievements: tuple[GroupedAchievement, ...]
