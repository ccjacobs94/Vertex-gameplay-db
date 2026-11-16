"""Domain models for play observations, sessions, baselines, and ledger entries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlaySession:
    id: int
    game_id: int
    platform: str
    started_at: str
    ended_at: str
    duration_minutes: int
    source: str
    precision_level: str
    notes: str | None
    achievements_unlocked: int
    is_locked: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    status: str
    delta_minutes: int
    baseline_minutes: int
    session: PlaySession | None


@dataclass(frozen=True, slots=True)
class DailyLedgerEntry:
    date: str
    game_id: int
    platform_family: str
    minutes_played: float
    sessions_count: int
    first_session_hour: int | None


@dataclass(frozen=True, slots=True)
class PlaytimePrompt:
    id: int
    game_id: int
    title: str
    platform: str
    cover_url: str | None
    current_status: str
    previous_hours: float
    new_hours: float
    delta_hours: float
    total_playtime_hours: float
    detected_at: str
    status: str


@dataclass(frozen=True, slots=True)
class PlaytimePromptResolution:
    prompt_id: int
    action: str
    game_id: int
    completion_status: str
    status_locked: bool
    playtime_prompt_muted: bool
