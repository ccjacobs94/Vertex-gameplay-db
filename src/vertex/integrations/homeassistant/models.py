"""Domain models for Home Assistant state transitions, CSV import, and webhook events."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HomeAssistantSpan:
    title: str
    started_at: str
    ended_at: str
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class HomeAssistantImportResult:
    status: str
    imported_count: int
    skipped_count: int
    total_spans: int


@dataclass(frozen=True, slots=True)
class HomeAssistantEventResult:
    status: str
    game_id: int
    session_id: int | None
    duration_minutes: int
    idempotency_key: str
