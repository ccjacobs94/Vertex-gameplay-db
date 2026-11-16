"""Read-only domain models for library summary, habits, and session analytics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnalyticsSummary:
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


@dataclass(frozen=True, slots=True)
class PlatformAnalyticsItem:
    name: str
    count: int
    hours: float
    beaten_count: int
    playtime_pct: float
    library_pct: float
    completion_rate: float
    top_game_title: str | None
    top_game_hours: float


@dataclass(frozen=True, slots=True)
class GenreAnalyticsItem:
    genre: str
    count: int
    hours: float
    beaten_count: int
    playing_count: int
    backlog_count: int
    dropped_count: int
    playtime_pct: float
    completion_rate: float


@dataclass(frozen=True, slots=True)
class TagAnalyticsItem:
    tag: str
    count: int
    hours: float
    dropped_count: int
    drop_rate: float
    completion_rate: float


@dataclass(frozen=True, slots=True)
class StatusAnalyticsItem:
    name: str
    count: int
    count_pct: float
    hours: float
    hours_pct: float


@dataclass(frozen=True, slots=True)
class BucketAnalyticsItem:
    key: str
    label: str
    count: int
    count_pct: float
    hours: float
    hours_pct: float


@dataclass(frozen=True, slots=True)
class CircadianRhythmAnalytics:
    grid: list[list[int]]
    time_buckets: dict[str, int]
    total_events: int
    peak_day: str
    peak_hour: str
    archetype: str
    eras_count: int


@dataclass(frozen=True, slots=True)
class LibraryAnalytics:
    summary: AnalyticsSummary
    platforms: list[PlatformAnalyticsItem]
    genres: list[GenreAnalyticsItem]
    tags: list[TagAnalyticsItem]
    statuses: dict[str, StatusAnalyticsItem]
    commitment_tiers: list[BucketAnalyticsItem]
    eras: list[BucketAnalyticsItem]
    avg_user_rating: float | None
    avg_critic_score: float | None
    backlog_gems: list[dict[str, Any]]
    hall_of_fame: list[dict[str, Any]]
    circadian_rhythm: CircadianRhythmAnalytics
    subscription_roi: dict[str, Any]
    cross_platform: dict[str, Any]
    trophy_pyramid: dict[str, Any]
    almost_perfect: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SessionAnalytics:
    kpis: dict[str, Any]
    heatmap_365: list[dict[str, Any]]
    circadian_clock: dict[str, Any]
    snack_vs_binge: dict[str, Any]
    rotation_gantt_30d: dict[str, Any]
    recent_sessions: list[dict[str, Any]]
    total_sessions: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class SessionWindowStats:
    total_sessions: int
    total_minutes: int
    average_duration_minutes: float
    daily_playtime_series: list[dict[str, Any]]
    platform_distribution: dict[str, int]
