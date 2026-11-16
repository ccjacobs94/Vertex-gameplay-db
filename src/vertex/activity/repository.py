"""SQLite repository helpers for play_sessions and daily_playtime_ledger."""

from __future__ import annotations

import sqlite3

from vertex.activity.models import DailyLedgerEntry, PlaySession


def row_to_session(row: sqlite3.Row) -> PlaySession:
    return PlaySession(
        id=int(row["id"]),
        game_id=int(row["game_id"]),
        platform=row["platform"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_minutes=int(row["duration_minutes"]),
        source=row["source"],
        precision_level=row["precision_level"],
        notes=row["notes"],
        achievements_unlocked=int(row["achievements_unlocked"] or 0),
        is_locked=bool(row["is_locked"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_ledger(row: sqlite3.Row) -> DailyLedgerEntry:
    return DailyLedgerEntry(
        date=row["date"],
        game_id=int(row["game_id"]),
        platform_family=row["platform_family"],
        minutes_played=float(row["minutes_played"]),
        sessions_count=int(row["sessions_count"]),
        first_session_hour=row["first_session_hour"],
    )
