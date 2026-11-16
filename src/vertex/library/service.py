"""Personal library service enforcing UserScope and user-edit preservation invariants."""

from __future__ import annotations

import json
import re
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
    AchievementPlatformStatus,
    GameAchievementsDetail,
    GameRecord,
    GroupedAchievement,
    LibraryStats,
    PlatformAchievementSummary,
    ReconcileCandidate,
    UnifiedAchievementSummary,
)
from vertex.library.repository import row_to_game, upsert_platform_copy


_TRADEMARK_RE = re.compile(r"[™®©]")
_NON_ALNUM_ACH_RE = re.compile(r"[^a-z0-9]+")


def _normalize_achievement_text(text: str) -> str:
    cleaned = _TRADEMARK_RE.sub(" ", (text or "").lower())
    return _NON_ALNUM_ACH_RE.sub(" ", cleaned).strip()


def _short_platform_label(platform: str) -> str:
    p = (platform or "").strip()
    mapping = {
        "PC (Steam)": "Steam",
        "PC (GOG)": "GOG",
        "GOG": "GOG",
        "PC (Xbox)": "PC (Xbox)",
        "Xbox / PC": "Xbox/PC",
        "Xbox": "Xbox",
        "PlayStation 4": "PS4",
        "PlayStation 5": "PS5",
        "Nintendo Switch": "Switch",
        "PC (Other)": "PC",
    }
    return mapping.get(p, p)


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
        plat_q = platform.strip().lower()
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
            games = [row_to_game(r) for r in rows]
        if plat_q:
            from vertex.activity.service import _platform_family

            games = [
                g
                for g in games
                if plat_q in g.platform.lower() or _platform_family(g.platform).lower() == plat_q
            ]
        return games

    def get_game(self, scope: UserScope, game_id: int) -> GameRecord:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Game {game_id} not found")
            return row_to_game(row)

    def find_game(self, scope: UserScope, game_id: int) -> GameRecord | None:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            return row_to_game(row) if row is not None else None

    def find_game_by_external_id(self, scope: UserScope, external_id: str) -> GameRecord | None:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute("SELECT * FROM games WHERE external_id = ?", (external_id,)).fetchone()
            return row_to_game(row) if row is not None else None

    def get_linked_games(self, scope: UserScope, game_id: int) -> list[GameRecord]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            target = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if target is None:
                raise NotFoundError(f"Game {game_id} not found")
            cid = target["canonical_id"]
            if cid is not None:
                rows = conn.execute(
                    "SELECT * FROM games WHERE canonical_id = ? ORDER BY playtime_hours DESC, id ASC",
                    (cid,),
                ).fetchall()
                return [row_to_game(r) for r in rows]
            return [row_to_game(target)]

    def find_or_create_by_title(
        self,
        scope: UserScope,
        *,
        title: str,
        platform: str = "PC",
        source: str = "Manual",
        completion_status: str = "Playing",
        status_locked: bool = False,
    ) -> tuple[GameRecord, bool]:
        scope.require_active()
        cleaned_title = (title or "").strip()
        if not cleaned_title:
            raise ValidationError("Game title is required")
        cleaned_platform = (platform or "").strip() or "PC"
        norm_target = normalize_title(cleaned_title)

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            exact = conn.execute(
                "SELECT * FROM games WHERE LOWER(title) = LOWER(?) AND LOWER(platform) = LOWER(?) ORDER BY id ASC LIMIT 1",
                (cleaned_title, cleaned_platform),
            ).fetchone()
            if exact is not None:
                return row_to_game(exact), False

            by_title = conn.execute(
                "SELECT * FROM games WHERE LOWER(title) = LOWER(?) ORDER BY id ASC LIMIT 1",
                (cleaned_title,),
            ).fetchone()
            if by_title is not None:
                return row_to_game(by_title), False

            if norm_target:
                for cand in conn.execute("SELECT * FROM games ORDER BY id ASC").fetchall():
                    if normalize_title(cand["title"]) == norm_target:
                        return row_to_game(cand), False

            now_iso = _utc_now_iso()
            ext_id = f"manual:{uuid.uuid4()}"
            norm_status = _normalize_completion_status(completion_status)
            cur = conn.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours, playtime_minutes,
                    completion_status, status_locked, ownership_status, ownership_locked,
                    is_game_pass, is_play_anywhere, genres_json, tags_json,
                    notes, favorite, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0.0, 0, ?, ?, 'Owned', 0, 0, 0, '[]', '[]', '', 0, ?, ?)
                """,
                (
                    ext_id,
                    cleaned_title,
                    cleaned_platform,
                    (source or "Manual").strip() or "Manual",
                    norm_status,
                    1 if status_locked else 0,
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
                ownership_status="Owned",
                ownership_locked=False,
                is_game_pass=False,
                is_play_anywhere=False,
                playtime_minutes=0,
                last_played=None,
                now_iso=now_iso,
            )
            created_row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            return row_to_game(created_row), True

    def create_or_get_game_by_external_id(
        self,
        scope: UserScope,
        *,
        external_id: str,
        title: str,
        platform: str,
        source: str = "Manual",
        completion_status: str = "Plan to Play",
        status_locked: bool | None = None,
        ownership_status: str = "Owned",
        genres: list[str] | None = None,
        release_year: int | None = None,
        critic_score: int | None = None,
        cover_url: str | None = None,
        notes: str = "",
    ) -> tuple[GameRecord, bool]:
        """Idempotently create or fetch a library entry keyed by external_id (e.g., wishlist purchase)."""
        scope.require_active()
        cleaned_ext = (external_id or "").strip()
        cleaned_title = (title or "").strip()
        cleaned_platform = (platform or "").strip()
        if not cleaned_ext or not cleaned_title or not cleaned_platform:
            raise ValidationError("external_id, title, and platform are required")
        norm_status = _normalize_completion_status(completion_status)
        norm_ownership, forced_gp = _normalize_ownership_status(ownership_status)
        clean_genres = validate_genres(genres)
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM games WHERE external_id = ?",
                (cleaned_ext,),
            ).fetchone()
            if existing is not None:
                return row_to_game(existing), False

            cur = conn.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours, playtime_minutes,
                    completion_status, status_locked, ownership_status, ownership_locked,
                    is_game_pass, is_play_anywhere, genres_json, tags_json,
                    critic_score, release_year, cover_url, notes, favorite, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0.0, 0, ?, ?, ?, 1, ?, 0, ?, '[]', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    cleaned_ext,
                    cleaned_title,
                    cleaned_platform,
                    (source or "Manual").strip() or "Manual",
                    norm_status,
                    (1 if status_locked else 0) if status_locked is not None else (1 if norm_status != "Not Played" else 0),
                    norm_ownership,
                    1 if forced_gp else 0,
                    json.dumps(clean_genres),
                    critic_score,
                    release_year,
                    cover_url,
                    notes or "",
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
                external_id=cleaned_ext,
                ownership_status=norm_ownership,
                ownership_locked=True,
                is_game_pass=bool(forced_gp),
                is_play_anywhere=False,
                playtime_minutes=0,
                last_played=None,
                now_iso=now_iso,
            )
            row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            return row_to_game(row), True

    def add_manual_game(
        self,
        scope: UserScope,
        *,
        title: str,
        platform: str,
        source: str = "Manual",
        completion_status: str = "Not Played",
        status_locked: bool | None = None,
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
                    (1 if status_locked else 0) if status_locked is not None else (1 if norm_status != "Not Played" else 0),
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
        allow_cover_upgrade: bool = False,
        release_year: int | None = None,
        critic_score: int | None = None,
        is_game_pass: bool | None = None,
        is_play_anywhere: bool | None = None,
        achievements_unlocked: int | None = None,
        achievements_total: int | None = None,
    ) -> tuple[GameRecord, bool]:
        """Apply external snapshot safely: never clobbers locks, ownership, notes, rating, favorite, or labels."""
        scope.require_active()
        now_iso = _utc_now_iso()
        obs_mins = max(0, int(playtime_minutes))
        valid_g = validate_genres(genres)
        valid_t = validate_tags(tags)
        clean_cover = (cover_url or "").strip() or None

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM games WHERE external_id = ?",
                (external_id,),
            ).fetchone()

            if existing is None:
                init_status = "Playing" if obs_mins > 0 else "Not Played"
                gp = 1 if is_game_pass else 0
                pa = 1 if is_play_anywhere else 0
                ach_u = max(0, int(achievements_unlocked or 0))
                ach_t = max(0, int(achievements_total or 0))
                cur = conn.execute(
                    """
                    INSERT INTO games (
                        external_id, title, platform, source, playtime_hours, playtime_minutes,
                        last_synced_playtime_minutes, last_played, completion_status, status_locked,
                        ownership_status, ownership_locked, is_game_pass, is_play_anywhere,
                        achievements_unlocked, achievements_total,
                        genres_json, tags_json, critic_score, release_year, cover_url,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'Owned', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        ach_u,
                        ach_t,
                        json.dumps(valid_g),
                        json.dumps(valid_t),
                        critic_score,
                        release_year,
                        clean_cover,
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

                prev_ach_u = int(existing["achievements_unlocked"] or 0)
                prev_ach_t = int(existing["achievements_total"] or 0)
                if achievements_total is not None and int(achievements_total) > 0:
                    next_ach_t = max(prev_ach_t, int(achievements_total))
                    next_ach_u = (
                        max(prev_ach_u, int(achievements_unlocked or 0))
                        if achievements_unlocked is not None
                        else prev_ach_u
                    )
                else:
                    next_ach_u = prev_ach_u
                    next_ach_t = prev_ach_t

                if allow_cover_upgrade and clean_cover:
                    next_cover = clean_cover
                else:
                    existing_cov = (existing["cover_url"] or "").strip() if existing["cover_url"] else ""
                    next_cover = existing_cov or clean_cover

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
                        achievements_unlocked = ?,
                        achievements_total = ?,
                        genres_json = ?,
                        tags_json = ?,
                        critic_score = COALESCE(critic_score, ?),
                        release_year = COALESCE(release_year, ?),
                        cover_url = ?,
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
                        next_ach_u,
                        next_ach_t,
                        json.dumps(merged_g),
                        json.dumps(merged_t),
                        critic_score,
                        release_year,
                        next_cover,
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

    def reset_reconcile_dismissals(self, scope: UserScope) -> int:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            cur = conn.execute("DELETE FROM reconciliation_dismissals")
            return int(cur.rowcount)

    def apply_catalog_enrichment(
        self,
        scope: UserScope,
        game_id: int,
        *,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        release_year: int | None = None,
        critic_score: int | None = None,
        cover_url: str | None = None,
    ) -> tuple[bool, bool]:
        """Merge taxonomy labels and fill missing metadata across reconciled copies without overwriting user edits.

        Returns (genres_or_meta_updated, cover_filled).
        """
        scope.require_active()
        valid_g = validate_genres(genres)
        valid_t = validate_tags(tags)
        clean_cover = (cover_url or "").strip() or None
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            target = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if target is None:
                raise NotFoundError(f"Game {game_id} not found")

            cid = target["canonical_id"]
            if cid is not None:
                members = conn.execute(
                    "SELECT * FROM games WHERE canonical_id = ? ORDER BY id ASC",
                    (cid,),
                ).fetchall()
            else:
                members = [target]

            meta_changed = False
            cover_changed = False
            for row in members:
                existing_g = json.loads(row["genres_json"] or "[]")
                existing_t = json.loads(row["tags_json"] or "[]")
                merged_g = merge_lists(existing_g, valid_g)
                merged_t = merge_lists(existing_t, valid_t)
                needs_cover = bool(clean_cover) and not (row["cover_url"] and str(row["cover_url"]).strip())
                needs_year = release_year is not None and row["release_year"] is None
                needs_score = critic_score is not None and row["critic_score"] is None
                labels_changed = merged_g != existing_g or merged_t != existing_t
                if not (labels_changed or needs_cover or needs_year or needs_score):
                    continue

                conn.execute(
                    """
                    UPDATE games
                    SET genres_json = ?,
                        tags_json = ?,
                        release_year = COALESCE(release_year, ?),
                        critic_score = COALESCE(critic_score, ?),
                        cover_url = CASE
                            WHEN cover_url IS NULL OR TRIM(cover_url) = '' THEN COALESCE(?, cover_url)
                            ELSE cover_url
                        END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(merged_g),
                        json.dumps(merged_t),
                        release_year,
                        critic_score,
                        clean_cover,
                        now_iso,
                        row["id"],
                    ),
                )
                if labels_changed or needs_year or needs_score:
                    meta_changed = True
                if needs_cover:
                    cover_changed = True

            return meta_changed, cover_changed

    def upsert_game_achievements(
        self,
        scope: UserScope,
        *,
        game_id: int,
        source: str,
        platform: str,
        achievements: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Insert or update achievements for a game and refresh summary counts safely."""
        scope.require_active()
        now_iso = _utc_now_iso()
        unlocked_count = 0
        total_count = len(achievements)

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            game_row = conn.execute(
                "SELECT id, status_locked, completion_status FROM games WHERE id = ?",
                (game_id,),
            ).fetchone()
            if game_row is None:
                raise NotFoundError(f"Game {game_id} not found")

            for ach in achievements:
                ext_id = str(
                    ach.get("external_ach_id") or ach.get("id") or ach.get("apiname") or ""
                ).strip()
                if not ext_id:
                    continue
                name = str(ach.get("name") or ext_id).strip()
                desc = str(ach.get("description") or "").strip()
                icon_url = ach.get("icon_url")
                icon_gray_url = ach.get("icon_gray_url")
                unlocked = 1 if ach.get("unlocked") else 0
                if unlocked:
                    unlocked_count += 1
                unlock_time = ach.get("unlock_time")
                reward_type = ach.get("reward_type")
                reward_value = str(ach["reward_value"]) if ach.get("reward_value") is not None else None
                rarity_pct = float(ach["rarity_pct"]) if ach.get("rarity_pct") is not None else None
                is_hidden = 1 if ach.get("is_hidden") else 0

                conn.execute(
                    """
                    INSERT INTO game_achievements (
                        game_id, external_ach_id, source, platform, name, description,
                        icon_url, icon_gray_url, unlocked, unlock_time, reward_type,
                        reward_value, rarity_pct, is_hidden, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(game_id, external_ach_id) DO UPDATE SET
                        name = excluded.name,
                        description = CASE
                            WHEN excluded.description != '' THEN excluded.description
                            ELSE game_achievements.description
                        END,
                        icon_url = COALESCE(excluded.icon_url, game_achievements.icon_url),
                        icon_gray_url = COALESCE(excluded.icon_gray_url, game_achievements.icon_gray_url),
                        unlocked = MAX(game_achievements.unlocked, excluded.unlocked),
                        unlock_time = COALESCE(excluded.unlock_time, game_achievements.unlock_time),
                        reward_type = COALESCE(excluded.reward_type, game_achievements.reward_type),
                        reward_value = COALESCE(excluded.reward_value, game_achievements.reward_value),
                        rarity_pct = COALESCE(excluded.rarity_pct, game_achievements.rarity_pct),
                        is_hidden = excluded.is_hidden,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game_id,
                        ext_id,
                        source,
                        platform,
                        name,
                        desc,
                        icon_url,
                        icon_gray_url,
                        unlocked,
                        unlock_time,
                        reward_type,
                        reward_value,
                        rarity_pct,
                        is_hidden,
                        now_iso,
                    ),
                )

            agg = conn.execute(
                "SELECT COUNT(*) AS tot, COALESCE(SUM(unlocked), 0) AS unl FROM game_achievements WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            final_total = int(agg["tot"]) if agg else total_count
            final_unlocked = int(agg["unl"]) if agg else unlocked_count

            inferred_completion = None
            if (
                not game_row["status_locked"]
                and final_total > 0
                and final_unlocked >= final_total
            ):
                inferred_completion = "Completed"

            if inferred_completion:
                conn.execute(
                    """
                    UPDATE games
                    SET achievements_unlocked = ?,
                        achievements_total = ?,
                        achievements_synced_at = ?,
                        completion_status = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (final_unlocked, final_total, now_iso, inferred_completion, now_iso, game_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE games
                    SET achievements_unlocked = ?,
                        achievements_total = ?,
                        achievements_synced_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (final_unlocked, final_total, now_iso, now_iso, game_id),
                )
        return {"unlocked": final_unlocked, "total": final_total}

    def get_game_achievements(self, scope: UserScope, game_id: int) -> GameAchievementsDetail:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            target = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if target is None:
                raise NotFoundError(f"Game {game_id} not found")

            cid = target["canonical_id"]
            if cid is not None:
                members = conn.execute(
                    "SELECT * FROM games WHERE canonical_id = ? ORDER BY playtime_hours DESC, id ASC",
                    (cid,),
                ).fetchall()
            else:
                members = [target]

            member_ids = [int(m["id"]) for m in members]
            placeholders = ",".join("?" for _ in member_ids)
            ach_rows = conn.execute(
                f"""
                SELECT * FROM game_achievements
                WHERE game_id IN ({placeholders})
                ORDER BY unlocked DESC, unlock_time DESC, name ASC
                """,
                member_ids,
            ).fetchall()

        per_platform: list[PlatformAchievementSummary] = []
        for m in members:
            mid = int(m["id"])
            m_rows = [r for r in ach_rows if int(r["game_id"]) == mid]
            if m_rows:
                unl = sum(1 for r in m_rows if r["unlocked"])
                tot = len(m_rows)
            else:
                unl = int(m["achievements_unlocked"] or 0)
                tot = int(m["achievements_total"] or 0)
            pct = round((unl / max(tot, 1)) * 100, 1) if tot > 0 else 0.0
            per_platform.append(
                PlatformAchievementSummary(
                    game_id=mid,
                    title=m["title"],
                    platform=m["platform"],
                    short_platform=_short_platform_label(m["platform"]),
                    source=m["source"],
                    unlocked=unl,
                    total=tot,
                    percentage=pct,
                    achievements_synced_at=m["achievements_synced_at"],
                )
            )

        grouped_map: dict[str, dict[str, Any]] = {}
        desc_to_key: dict[str, str] = {}
        for row in ach_rows:
            name_key = _normalize_achievement_text(row["name"] or "")
            desc_key = _normalize_achievement_text(row["description"] or "")
            match_key: str | None = None
            if name_key:
                match_key = f"n:{name_key}"
            elif desc_key and len(desc_key) >= 10:
                match_key = f"d:{desc_key}"
            if desc_key and len(desc_key) >= 15 and desc_key in desc_to_key:
                match_key = desc_to_key[desc_key]
            if not match_key:
                match_key = f"raw:{row['id']}"
            if desc_key and len(desc_key) >= 15 and desc_key not in desc_to_key:
                desc_to_key[desc_key] = match_key

            m_plat = row["platform"] or ""
            plat_entry = AchievementPlatformStatus(
                game_id=int(row["game_id"]),
                platform=m_plat,
                short_platform=_short_platform_label(m_plat),
                source=row["source"] or "",
                unlocked=bool(row["unlocked"]),
                unlock_time=row["unlock_time"],
                reward_type=row["reward_type"],
                reward_value=row["reward_value"],
                rarity_pct=row["rarity_pct"],
            )

            if match_key not in grouped_map:
                grouped_map[match_key] = {
                    "key": match_key,
                    "name": row["name"],
                    "description": row["description"] or "",
                    "icon_url": row["icon_url"],
                    "icon_gray_url": row["icon_gray_url"],
                    "is_hidden": bool(row["is_hidden"]),
                    "reward_type": row["reward_type"],
                    "reward_value": row["reward_value"],
                    "rarity_pct": row["rarity_pct"],
                    "platforms": [plat_entry],
                }
            else:
                entry = grouped_map[match_key]
                if not entry["description"] and row["description"]:
                    entry["description"] = row["description"]
                if not entry["icon_url"] and row["icon_url"]:
                    entry["icon_url"] = row["icon_url"]
                if not entry["icon_gray_url"] and row["icon_gray_url"]:
                    entry["icon_gray_url"] = row["icon_gray_url"]
                if not entry["reward_type"] and row["reward_type"]:
                    entry["reward_type"] = row["reward_type"]
                    entry["reward_value"] = row["reward_value"]
                if entry["rarity_pct"] is None and row["rarity_pct"] is not None:
                    entry["rarity_pct"] = row["rarity_pct"]
                entry["platforms"].append(plat_entry)

        grouped_list: list[GroupedAchievement] = []
        overlap_count = 0
        for entry in grouped_map.values():
            plats: list[AchievementPlatformStatus] = entry["platforms"]
            unlocked_plats = tuple(p.short_platform for p in plats if p.unlocked)
            if len(unlocked_plats) > 1:
                overlap_count += 1
            unlock_times = [p.unlock_time for p in plats if p.unlock_time]
            grouped_list.append(
                GroupedAchievement(
                    key=entry["key"],
                    name=entry["name"],
                    description=entry["description"],
                    icon_url=entry["icon_url"],
                    icon_gray_url=entry["icon_gray_url"],
                    is_hidden=entry["is_hidden"],
                    unlocked=len(unlocked_plats) > 0,
                    unlock_time=min(unlock_times) if unlock_times else None,
                    reward_type=entry["reward_type"],
                    reward_value=entry["reward_value"],
                    rarity_pct=entry["rarity_pct"],
                    unlocked_platforms=unlocked_plats,
                    platforms=tuple(plats),
                )
            )

        grouped_list.sort(
            key=lambda a: (
                1 if a.unlocked else 0,
                a.unlock_time or "",
                a.name.lower(),
            ),
            reverse=True,
        )
        unique_unlocked = sum(1 for a in grouped_list if a.unlocked)
        unique_total = len(grouped_list)
        pct = round((unique_unlocked / max(unique_total, 1)) * 100, 1) if unique_total > 0 else 0.0

        return GameAchievementsDetail(
            game_id=game_id,
            unified_summary=UnifiedAchievementSummary(
                unlocked=unique_unlocked,
                total=unique_total,
                percentage=pct,
                overlap_count=overlap_count,
            ),
            per_platform_summary=tuple(per_platform),
            achievements=tuple(grouped_list),
        )
