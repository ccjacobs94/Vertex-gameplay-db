"""SQLite repository for per-user personal library operations."""

from __future__ import annotations

import json
import sqlite3

from vertex.library.models import GameRecord


def row_to_game(row: sqlite3.Row) -> GameRecord:
    return GameRecord(
        id=int(row["id"]),
        external_id=row["external_id"],
        title=row["title"],
        platform=row["platform"],
        source=row["source"],
        playtime_hours=float(row["playtime_hours"] or 0.0),
        playtime_minutes=int(row["playtime_minutes"] or 0),
        last_synced_playtime_minutes=row["last_synced_playtime_minutes"],
        last_played=row["last_played"],
        completion_status=row["completion_status"],
        status_locked=bool(row["status_locked"]),
        ownership_status=row["ownership_status"],
        ownership_locked=bool(row["ownership_locked"]),
        is_game_pass=bool(row["is_game_pass"]),
        is_play_anywhere=bool(row["is_play_anywhere"]),
        achievements_unlocked=int(row["achievements_unlocked"] or 0),
        achievements_total=int(row["achievements_total"] or 0),
        achievements_synced_at=row["achievements_synced_at"],
        genres=tuple(json.loads(row["genres_json"] or "[]")),
        tags=tuple(json.loads(row["tags_json"] or "[]")),
        critic_score=row["critic_score"],
        user_rating=row["user_rating"],
        release_year=row["release_year"],
        cover_url=row["cover_url"],
        notes=row["notes"] or "",
        favorite=bool(row["favorite"]),
        playtime_prompt_muted=bool(row["playtime_prompt_muted"]),
        canonical_id=row["canonical_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_platform_copy(
    conn: sqlite3.Connection,
    *,
    game_id: int,
    platform: str,
    source: str,
    external_id: str,
    ownership_status: str,
    ownership_locked: bool,
    is_game_pass: bool,
    is_play_anywhere: bool,
    playtime_minutes: int,
    last_played: str | None,
    now_iso: str,
) -> None:
    conn.execute(
        """
        INSERT INTO platform_copies (
            game_id, platform, source, external_id, ownership_status,
            ownership_locked, is_game_pass, is_play_anywhere,
            playtime_minutes, last_played, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_id) DO UPDATE SET
            game_id = excluded.game_id,
            platform = excluded.platform,
            source = excluded.source,
            ownership_status = excluded.ownership_status,
            ownership_locked = excluded.ownership_locked,
            is_game_pass = excluded.is_game_pass,
            is_play_anywhere = excluded.is_play_anywhere,
            playtime_minutes = excluded.playtime_minutes,
            last_played = COALESCE(excluded.last_played, platform_copies.last_played),
            updated_at = excluded.updated_at
        """,
        (
            game_id,
            platform,
            source,
            external_id,
            ownership_status,
            1 if ownership_locked else 0,
            1 if is_game_pass else 0,
            1 if is_play_anywhere else 0,
            playtime_minutes,
            last_played,
            now_iso,
            now_iso,
        ),
    )
