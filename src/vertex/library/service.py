"""Personal library service enforcing UserScope and user-edit preservation invariants."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from vertex.catalog.models import normalize_title
from vertex.catalog.taxonomy import merge_lists, validate_genres, validate_tags
from vertex.infrastructure.errors import NotFoundError, ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.models import (
    VALID_COMPLETION_STATUSES,
    VALID_OWNERSHIP_STATUSES,
    GameRecord,
    LibraryStats,
    ReconcileCandidate,
)
from vertex.library.repository import row_to_game, upsert_platform_copy


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_completion_status(status: str | None) -> str:
    cleaned = (status or "").strip() or "Not Played"
    if cleaned not in VALID_COMPLETION_STATUSES:
        raise ValidationError(f"Invalid completion status '{cleaned}'")
    return cleaned


_OWNERSHIP_SYNONYMS = {
    "owned": ("Owned", False),
    "own": ("Owned", False),
    "purchased": ("Owned", False),
    "bought": ("Owned", False),
    "subscription": ("Subscription", False),
    "sub": ("Subscription", False),
    "game pass": ("Subscription", True),
    "gamepass": ("Subscription", True),
    "xbox game pass": ("Subscription", True),
    "pc game pass": ("Subscription", True),
    "xgp": ("Subscription", True),
    "ps plus": ("Subscription", False),
    "ps+": ("Subscription", False),
    "playstation plus": ("Subscription", False),
    "ea play": ("Subscription", False),
    "ubisoft+": ("Subscription", False),
    "borrowed": ("Borrowed", False),
    "borrow": ("Borrowed", False),
    "loaned": ("Borrowed", False),
    "none": ("None", False),
    "unowned": ("None", False),
    "not owned": ("None", False),
}


def _normalize_ownership_status(status: str | None) -> tuple[str, bool]:
    cleaned = (status or "").strip() or "Owned"
    if cleaned in VALID_OWNERSHIP_STATUSES:
        return cleaned, False
    mapped = _OWNERSHIP_SYNONYMS.get(cleaned.lower())
    if mapped is not None:
        return mapped
    raise ValidationError(f"Invalid ownership status '{cleaned}'")


def _validate_rating(rating: int | None) -> int | None:
    if rating is None:
        return None
    if rating < 1 or rating > 10:
        raise ValidationError("user_rating must be between 1 and 10")
    return int(rating)


class LibraryService:
    """Scoped application service for personal games, reconciliation, and summary metrics."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    def list_games(
        self,
        scope: UserScope,
        *,
        search: str = "",
        platform: str = "",
        status: str = "",
        favorite_only: bool = False,
        sort_by: str = "title",
    ) -> list[GameRecord]:
        scope.require_active()
        clauses = ["1=1"]
        params: list[Any] = []
        if search.strip():
            clauses.append("LOWER(title) LIKE ?")
            params.append(f"%{search.strip().lower()}%")
        if platform.strip():
            clauses.append("platform = ?")
            params.append(platform.strip())
        if status.strip():
            clauses.append("completion_status = ?")
            params.append(status.strip())
        if favorite_only:
            clauses.append("favorite = 1")

        order_map = {
            "title": "LOWER(title) ASC, id ASC",
            "playtime": "playtime_minutes DESC, LOWER(title) ASC",
            "recent": "COALESCE(last_played, updated_at) DESC, id DESC",
            "rating": "COALESCE(user_rating, 0) DESC, LOWER(title) ASC",
        }
        order_sql = order_map.get(sort_by, order_map["title"])
        sql = f"SELECT * FROM games WHERE {' AND '.join(clauses)} ORDER BY {order_sql}"

        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [row_to_game(r) for r in rows]

    def get_game(self, scope: UserScope, game_id: int) -> GameRecord:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Game {game_id} not found")
            return row_to_game(row)

    def add_manual_game(
        self,
        scope: UserScope,
        *,
        title: str,
        platform: str,
        source: str = "Manual",
        completion_status: str = "Not Played",
        ownership_status: str = "Owned",
        is_game_pass: bool = False,
        is_play_anywhere: bool = False,
        playtime_hours: float = 0.0,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        release_year: int | None = None,
        critic_score: int | None = None,
        user_rating: int | None = None,
        cover_url: str | None = None,
        notes: str = "",
        favorite: bool = False,
    ) -> GameRecord:
        scope.require_active()
        cleaned_title = (title or "").strip()
        cleaned_platform = (platform or "").strip()
        if not cleaned_title:
            raise ValidationError("Game title is required")
        if not cleaned_platform:
            raise ValidationError("Platform is required")

        norm_status = _normalize_completion_status(completion_status)
        norm_ownership, forced_gp = _normalize_ownership_status(ownership_status)
        gp = bool(is_game_pass or forced_gp)
        rating = _validate_rating(user_rating)
        mins = max(0, int(round(max(0.0, float(playtime_hours)) * 60)))
        hours = round(mins / 60.0, 2)
        clean_genres = validate_genres(genres)
        clean_tags = validate_tags(tags)
        ext_id = f"manual:{uuid.uuid4()}"
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours, playtime_minutes,
                    completion_status, status_locked, ownership_status, ownership_locked,
                    is_game_pass, is_play_anywhere, genres_json, tags_json,
                    critic_score, user_rating, release_year, cover_url, notes, favorite,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ext_id,
                    cleaned_title,
                    cleaned_platform,
                    (source or "Manual").strip() or "Manual",
                    hours,
                    mins,
                    norm_status,
                    1 if norm_status != "Not Played" else 0,
                    norm_ownership,
                    1,
                    1 if gp else 0,
                    1 if is_play_anywhere else 0,
                    json.dumps(clean_genres),
                    json.dumps(clean_tags),
                    critic_score,
                    rating,
                    release_year,
                    cover_url,
                    notes or "",
                    1 if favorite else 0,
                    now_iso,
                    now_iso,
                ),
            )
            game_id = int(cur.lastrowid)
            upsert_platform_copy(
                conn,
                game_id=game_id,
                platform=cleaned_platform,
                source=(source or "Manual").strip() or "Manual",
                external_id=ext_id,
                ownership_status=norm_ownership,
                ownership_locked=True,
                is_game_pass=gp,
                is_play_anywhere=is_play_anywhere,
                playtime_minutes=mins,
                last_played=None,
                now_iso=now_iso,
            )
            row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            return row_to_game(row)

    def update_game(
        self,
        scope: UserScope,
        game_id: int,
        *,
        title: str | None = None,
        platform: str | None = None,
        completion_status: str | None = None,
        status_locked: bool | None = None,
        ownership_status: str | None = None,
        ownership_locked: bool | None = None,
        is_game_pass: bool | None = None,
        is_play_anywhere: bool | None = None,
        playtime_hours: float | None = None,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        replace_labels: bool = False,
        user_rating: int | None = None,
        clear_rating: bool = False,
        release_year: int | None = None,
        cover_url: str | None = None,
        notes: str | None = None,
        favorite: bool | None = None,
        playtime_prompt_muted: bool | None = None,
    ) -> GameRecord:
        scope.require_active()
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Game {game_id} not found")

            updates: dict[str, Any] = {"updated_at": now_iso}
            if title is not None:
                cleaned = title.strip()
                if not cleaned:
                    raise ValidationError("Title cannot be empty")
                updates["title"] = cleaned
            if platform is not None:
                cleaned_p = platform.strip()
                if not cleaned_p:
                    raise ValidationError("Platform cannot be empty")
                updates["platform"] = cleaned_p

            if completion_status is not None:
                updates["completion_status"] = _normalize_completion_status(completion_status)
                updates["status_locked"] = 1 if status_locked is None else (1 if status_locked else 0)
            elif status_locked is not None:
                updates["status_locked"] = 1 if status_locked else 0

            if ownership_status is not None:
                norm_own, forced_gp = _normalize_ownership_status(ownership_status)
                updates["ownership_status"] = norm_own
                updates["ownership_locked"] = 1 if ownership_locked is None else (1 if ownership_locked else 0)
                if forced_gp and is_game_pass is None:
                    updates["is_game_pass"] = 1
            elif ownership_locked is not None:
                updates["ownership_locked"] = 1 if ownership_locked else 0

            if is_game_pass is not None:
                updates["is_game_pass"] = 1 if is_game_pass else 0
            if is_play_anywhere is not None:
                updates["is_play_anywhere"] = 1 if is_play_anywhere else 0

            if playtime_hours is not None:
                mins = max(0, int(round(max(0.0, float(playtime_hours)) * 60)))
                updates["playtime_minutes"] = mins
                updates["playtime_hours"] = round(mins / 60.0, 2)

            if genres is not None:
                valid_g = validate_genres(genres)
                if replace_labels:
                    updates["genres_json"] = json.dumps(valid_g)
                else:
                    existing_g = json.loads(row["genres_json"] or "[]")
                    updates["genres_json"] = json.dumps(merge_lists(existing_g, valid_g))

            if tags is not None:
                valid_t = validate_tags(tags)
                if replace_labels:
                    updates["tags_json"] = json.dumps(valid_t)
                else:
                    existing_t = json.loads(row["tags_json"] or "[]")
                    updates["tags_json"] = json.dumps(merge_lists(existing_t, valid_t))

            if clear_rating:
                updates["user_rating"] = None
            elif user_rating is not None:
                updates["user_rating"] = _validate_rating(user_rating)

            if release_year is not None:
                updates["release_year"] = release_year
            if cover_url is not None:
                updates["cover_url"] = cover_url
            if notes is not None:
                updates["notes"] = notes
            if favorite is not None:
                updates["favorite"] = 1 if favorite else 0
            if playtime_prompt_muted is not None:
                updates["playtime_prompt_muted"] = 1 if playtime_prompt_muted else 0

            assigns = ", ".join(f"{col} = ?" for col in updates)
            conn.execute(
                f"UPDATE games SET {assigns} WHERE id = ?",
                (*updates.values(), game_id),
            )
            updated = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            upsert_platform_copy(
                conn,
                game_id=game_id,
                platform=updated["platform"],
                source=updated["source"],
                external_id=updated["external_id"],
                ownership_status=updated["ownership_status"],
                ownership_locked=bool(updated["ownership_locked"]),
                is_game_pass=bool(updated["is_game_pass"]),
                is_play_anywhere=bool(updated["is_play_anywhere"]),
                playtime_minutes=int(updated["playtime_minutes"]),
                last_played=updated["last_played"],
                now_iso=now_iso,
            )
            return row_to_game(updated)

    def delete_game(self, scope: UserScope, game_id: int) -> None:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            cur = conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Game {game_id} not found")

    def apply_platform_snapshot(
        self,
        scope: UserScope,
        *,
        external_id: str,
        title: str,
        platform: str,
        source: str,
        playtime_minutes: int,
        last_played: str | None = None,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        cover_url: str | None = None,
        release_year: int | None = None,
        critic_score: int | None = None,
        is_game_pass: bool | None = None,
        is_play_anywhere: bool | None = None,
    ) -> tuple[GameRecord, bool]:
        """Apply external snapshot safely: never clobbers locks, ownership, notes, rating, favorite, or labels."""
        scope.require_active()
        now_iso = _utc_now_iso()
        obs_mins = max(0, int(playtime_minutes))
        valid_g = validate_genres(genres)
        valid_t = validate_tags(tags)

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM games WHERE external_id = ?",
                (external_id,),
            ).fetchone()

            if existing is None:
                init_status = "Playing" if obs_mins > 0 else "Not Played"
                gp = 1 if is_game_pass else 0
                pa = 1 if is_play_anywhere else 0
                cur = conn.execute(
                    """
                    INSERT INTO games (
                        external_id, title, platform, source, playtime_hours, playtime_minutes,
                        last_synced_playtime_minutes, last_played, completion_status, status_locked,
                        ownership_status, ownership_locked, is_game_pass, is_play_anywhere,
                        genres_json, tags_json, critic_score, release_year, cover_url,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'Owned', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        external_id,
                        title.strip(),
                        platform.strip(),
                        source.strip(),
                        round(obs_mins / 60.0, 2),
                        obs_mins,
                        obs_mins,
                        last_played,
                        init_status,
                        gp,
                        pa,
                        json.dumps(valid_g),
                        json.dumps(valid_t),
                        critic_score,
                        release_year,
                        cover_url,
                        now_iso,
                        now_iso,
                    ),
                )
                game_id = int(cur.lastrowid)
                created = True
            else:
                game_id = int(existing["id"])
                created = False
                # Never lower preserved playtime on provider regressions!
                prev_mins = int(existing["playtime_minutes"] or 0)
                kept_mins = max(prev_mins, obs_mins)

                # Merge genres/tags cleanly (never replace existing user labels).
                merged_g = merge_lists(json.loads(existing["genres_json"] or "[]"), valid_g)
                merged_t = merge_lists(json.loads(existing["tags_json"] or "[]"), valid_t)

                # NEVER modify completion_status if status_locked = 1!
                new_status = existing["completion_status"]
                last_synced_ref = (
                    existing["last_synced_playtime_minutes"]
                    if existing["last_synced_playtime_minutes"] is not None
                    else prev_mins
                )
                if (
                    not existing["status_locked"]
                    and existing["completion_status"] in ("Plan to Play", "Not Played", "Unplayed")
                    and obs_mins > int(last_synced_ref)
                ):
                    new_status = "Playing"

                # NEVER overwrite ownership_status on existing games during automated syncs.
                # is_game_pass and is_play_anywhere remain independent catalog attributes.
                gp = int(existing["is_game_pass"]) if is_game_pass is None else (1 if is_game_pass else 0)
                pa = int(existing["is_play_anywhere"]) if is_play_anywhere is None else (1 if is_play_anywhere else 0)

                conn.execute(
                    """
                    UPDATE games
                    SET title = COALESCE(NULLIF(?, ''), title),
                        playtime_hours = ?,
                        playtime_minutes = ?,
                        last_synced_playtime_minutes = MAX(COALESCE(last_synced_playtime_minutes, 0), ?),
                        last_played = COALESCE(?, last_played),
                        completion_status = ?,
                        is_game_pass = ?,
                        is_play_anywhere = ?,
                        genres_json = ?,
                        tags_json = ?,
                        critic_score = COALESCE(critic_score, ?),
                        release_year = COALESCE(release_year, ?),
                        cover_url = COALESCE(cover_url, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        title.strip(),
                        round(kept_mins / 60.0, 2),
                        kept_mins,
                        obs_mins,
                        last_played,
                        new_status,
                        gp,
                        pa,
                        json.dumps(merged_g),
                        json.dumps(merged_t),
                        critic_score,
                        release_year,
                        cover_url,
                        now_iso,
                        game_id,
                    ),
                )

            saved = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            upsert_platform_copy(
                conn,
                game_id=game_id,
                platform=saved["platform"],
                source=saved["source"],
                external_id=saved["external_id"],
                ownership_status=saved["ownership_status"],
                ownership_locked=bool(saved["ownership_locked"]),
                is_game_pass=bool(saved["is_game_pass"]),
                is_play_anywhere=bool(saved["is_play_anywhere"]),
                playtime_minutes=int(saved["playtime_minutes"]),
                last_played=saved["last_played"],
                now_iso=now_iso,
            )
            return row_to_game(saved), created

    def record_sync_log(
        self,
        scope: UserScope,
        *,
        source: str,
        status: str,
        games_synced: int,
        message: str,
    ) -> None:
        scope.require_active()
        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO sync_logs (source, status, games_synced, message, synced_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source, status, games_synced, message[:500], now_iso),
            )

    def get_summary_stats(self, scope: UserScope) -> LibraryStats:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute("SELECT * FROM games").fetchall()

        games = [row_to_game(r) for r in rows]
        platforms: dict[str, int] = {}
        total_minutes = 0
        for g in games:
            platforms[g.platform] = platforms.get(g.platform, 0) + 1
            total_minutes += g.playtime_minutes

        # Group reconciled copies so unique title counts aren't inflated.
        groups: dict[str, list[GameRecord]] = {}
        for g in games:
            key = f"canon:{g.canonical_id}" if g.canonical_id is not None else f"id:{g.id}"
            groups.setdefault(key, []).append(g)

        playing = 0
        completed = 0
        not_played = 0
        favorites = 0
        for grp in groups.values():
            statuses = {g.completion_status for g in grp}
            if "Playing" in statuses:
                playing += 1
            elif statuses & {"Beaten", "Completed"}:
                completed += 1
            elif statuses & {"Not Played", "Unplayed", "Plan to Play"}:
                not_played += 1
            if any(g.favorite for g in grp):
                favorites += 1

        return LibraryStats(
            total_entries=len(games),
            unique_titles=len(groups),
            playing=playing,
            completed=completed,
            not_played=not_played,
            favorites=favorites,
            total_playtime_hours=round(total_minutes / 60.0, 2),
            platforms=dict(sorted(platforms.items())),
        )

    # ── Cross-Platform Reconciliation ───────────────────────────────────────

    def get_reconcile_candidates(self, scope: UserScope) -> list[ReconcileCandidate]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute("SELECT * FROM games ORDER BY LOWER(title) ASC, id ASC").fetchall()
            dismissed = {
                r["pair_key"]
                for r in conn.execute("SELECT pair_key FROM reconciliation_dismissals").fetchall()
            }

        by_norm: dict[str, list[GameRecord]] = {}
        for r in rows:
            game = row_to_game(r)
            norm = normalize_title(game.title)
            if norm:
                by_norm.setdefault(norm, []).append(game)

        candidates: list[ReconcileCandidate] = []
        for norm, grp in by_norm.items():
            if len(grp) < 2:
                continue
            canonical_ids = {g.canonical_id for g in grp if g.canonical_id is not None}
            if len(canonical_ids) == 1 and all(g.canonical_id is not None for g in grp):
                continue
            pair_key = "|".join(str(g.id) for g in sorted(grp, key=lambda x: x.id))
            if pair_key in dismissed:
                continue
            candidates.append(ReconcileCandidate(canonical_title=grp[0].title, games=tuple(grp)))
        return candidates

    def merge_games(self, scope: UserScope, game_ids: list[int]) -> int:
        scope.require_active()
        clean_ids = sorted({int(gid) for gid in game_ids})
        if len(clean_ids) < 2:
            raise ValidationError("At least two games are required to reconcile")
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            placeholders = ",".join("?" for _ in clean_ids)
            rows = conn.execute(
                f"SELECT id, canonical_id FROM games WHERE id IN ({placeholders})",
                clean_ids,
            ).fetchall()
            if len(rows) != len(clean_ids):
                raise NotFoundError("One or more games to merge were not found")

            existing_canons = [int(r["canonical_id"]) for r in rows if r["canonical_id"] is not None]
            canonical_id = min(existing_canons) if existing_canons else clean_ids[0]
            conn.execute(
                f"UPDATE games SET canonical_id = ?, updated_at = ? WHERE id IN ({placeholders})",
                (canonical_id, now_iso, *clean_ids),
            )
            return canonical_id

    def unlink_game(self, scope: UserScope, game_id: int) -> None:
        scope.require_active()
        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            row = conn.execute("SELECT id, canonical_id FROM games WHERE id = ?", (game_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Game {game_id} not found")
            canon_id = row["canonical_id"]
            conn.execute(
                "UPDATE games SET canonical_id = NULL, updated_at = ? WHERE id = ?",
                (now_iso, game_id),
            )
            if canon_id is not None:
                remaining = conn.execute(
                    "SELECT id FROM games WHERE canonical_id = ?",
                    (canon_id,),
                ).fetchall()
                if len(remaining) == 1:
                    conn.execute(
                        "UPDATE games SET canonical_id = NULL, updated_at = ? WHERE id = ?",
                        (now_iso, remaining[0]["id"]),
                    )

    def dismiss_reconcile_candidate(self, scope: UserScope, game_ids: list[int]) -> None:
        scope.require_active()
        clean_ids = sorted({int(gid) for gid in game_ids})
        if len(clean_ids) < 2:
            raise ValidationError("At least two game IDs are required")
        pair_key = "|".join(str(gid) for gid in clean_ids)
        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO reconciliation_dismissals (pair_key, created_at) VALUES (?, ?)",
                (pair_key, now_iso),
            )
