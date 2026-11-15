"""Versioned JSON portability export/import and validated legacy SQLite inspection/import."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vertex import __version__
from vertex.catalog.taxonomy import merge_lists, validate_genres, validate_tags
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.models import (
    VALID_COMPLETION_STATUSES,
    VALID_OWNERSHIP_STATUSES,
    ImportReport,
)

SQLITE_MAGIC_HEADER = b"SQLite format 3\x00"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return [str(x) for x in loaded if x]
        except Exception:
            pass
    return []


class LibraryPortabilityService:
    """Handles per-user JSON export/import (no secrets) and validated legacy SQLite import."""

    def __init__(self, db_manager: DatabaseManager, identity_service: IdentityService) -> None:
        self.db_manager = db_manager
        self.identity_service = identity_service

    def export_json(self, scope: UserScope) -> dict[str, Any]:
        scope.require_active()
        account = self.identity_service.get_account(scope)
        with self.db_manager.library_connection(scope) as conn:
            games = conn.execute("SELECT * FROM games ORDER BY id ASC").fetchall()
            sessions = conn.execute("SELECT * FROM play_sessions ORDER BY id ASC").fetchall()
            baselines = conn.execute("SELECT * FROM provider_baselines ORDER BY game_id, source").fetchall()
            dismissals = conn.execute("SELECT pair_key, created_at FROM reconciliation_dismissals").fetchall()

        game_ext_by_id = {int(g["id"]): g["external_id"] for g in games}

        return {
            "format_version": 1,
            "exported_at": _utc_now_iso(),
            "app_version": __version__,
            "account": {
                "username": account.username,
                "timezone": account.timezone,
                "timezone_eras": account.timezone_eras,
            },
            "games": [
                {
                    "external_id": g["external_id"],
                    "title": g["title"],
                    "platform": g["platform"],
                    "source": g["source"],
                    "playtime_hours": float(g["playtime_hours"] or 0.0),
                    "playtime_minutes": int(g["playtime_minutes"] or 0),
                    "last_synced_playtime_minutes": g["last_synced_playtime_minutes"],
                    "last_played": g["last_played"],
                    "completion_status": g["completion_status"],
                    "status_locked": bool(g["status_locked"]),
                    "ownership_status": g["ownership_status"],
                    "ownership_locked": bool(g["ownership_locked"]),
                    "is_game_pass": bool(g["is_game_pass"]),
                    "is_play_anywhere": bool(g["is_play_anywhere"]),
                    "achievements_unlocked": int(g["achievements_unlocked"] or 0),
                    "achievements_total": int(g["achievements_total"] or 0),
                    "genres": json.loads(g["genres_json"] or "[]"),
                    "tags": json.loads(g["tags_json"] or "[]"),
                    "critic_score": g["critic_score"],
                    "user_rating": g["user_rating"],
                    "release_year": g["release_year"],
                    "cover_url": g["cover_url"],
                    "notes": g["notes"] or "",
                    "favorite": bool(g["favorite"]),
                    "playtime_prompt_muted": bool(g["playtime_prompt_muted"]),
                    "canonical_id": g["canonical_id"],
                    "created_at": g["created_at"],
                    "updated_at": g["updated_at"],
                }
                for g in games
            ],
            "play_sessions": [
                {
                    "game_external_id": game_ext_by_id.get(int(s["game_id"])),
                    "platform": s["platform"],
                    "started_at": s["started_at"],
                    "ended_at": s["ended_at"],
                    "duration_minutes": int(s["duration_minutes"]),
                    "source": s["source"],
                    "precision_level": s["precision_level"],
                    "notes": s["notes"],
                    "achievements_unlocked": int(s["achievements_unlocked"] or 0),
                    "is_locked": bool(s["is_locked"]),
                }
                for s in sessions
                if int(s["game_id"]) in game_ext_by_id
            ],
            "provider_baselines": [
                {
                    "game_external_id": game_ext_by_id.get(int(b["game_id"])),
                    "source": b["source"],
                    "baseline_minutes": int(b["baseline_minutes"]),
                    "last_observed_at": b["last_observed_at"],
                    "reset_count": int(b["reset_count"]),
                }
                for b in baselines
                if int(b["game_id"]) in game_ext_by_id
            ],
            "reconciliation_dismissals": [
                {"pair_key": d["pair_key"], "created_at": d["created_at"]} for d in dismissals
            ],
        }

    def _validate_json_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValidationError("Import payload must be a JSON object")
        if payload.get("format_version") != 1:
            raise ValidationError("Unsupported or missing format_version (expected 1)")
        if not isinstance(payload.get("games"), list):
            raise ValidationError("Import payload must include a 'games' array")
        for idx, g in enumerate(payload["games"]):
            if not isinstance(g, dict):
                raise ValidationError(f"Game at index {idx} must be an object")
            if not (g.get("external_id") and g.get("title") and g.get("platform")):
                raise ValidationError(f"Game at index {idx} missing external_id, title, or platform")
            status = g.get("completion_status", "Not Played")
            if status not in VALID_COMPLETION_STATUSES:
                raise ValidationError(f"Game '{g.get('title')}' has invalid completion_status '{status}'")

    def import_json(
        self,
        scope: UserScope,
        payload: dict[str, Any],
        *,
        mode: str = "merge",
        dry_run: bool = False,
        format_source: str = "json_v1",
    ) -> ImportReport:
        scope.require_active()
        if mode not in ("merge", "replace"):
            raise ValidationError("Import mode must be 'merge' or 'replace'")
        self._validate_json_payload(payload)

        warnings: list[str] = []
        added = 0
        updated = 0
        skipped = 0
        sessions_added = 0
        now_iso = _utc_now_iso()
        canon_remap: dict[int, int] = {}

        with self.db_manager.library_connection(scope, immediate=not dry_run) as conn:
            if mode == "replace":
                if not dry_run:
                    conn.execute("DELETE FROM play_sessions")
                    conn.execute("DELETE FROM provider_baselines")
                    conn.execute("DELETE FROM games")
                    conn.execute("DELETE FROM reconciliation_dismissals")
                existing_by_ext: dict[str, sqlite3.Row] = {}
            else:
                existing_by_ext = {
                    r["external_id"]: r
                    for r in conn.execute("SELECT * FROM games").fetchall()
                }

            ext_to_game_id: dict[str, int] = {k: int(v["id"]) for k, v in existing_by_ext.items()}
            planned_ext_ids: set[str] = set(existing_by_ext.keys())

            for g in payload["games"]:
                ext_id = str(g["external_id"]).strip()
                title = str(g["title"]).strip()
                platform = str(g["platform"]).strip()
                source = str(g.get("source") or "Import").strip() or "Import"
                mins = max(0, int(g.get("playtime_minutes") or round(float(g.get("playtime_hours") or 0.0) * 60)))
                hours = round(mins / 60.0, 2)
                raw_own = str(g.get("ownership_status") or "Owned").strip()
                own_status = "Subscription" if raw_own == "Game Pass" else (
                    raw_own if raw_own in VALID_OWNERSHIP_STATUSES else "Owned"
                )
                gp = bool(g.get("is_game_pass") or raw_own == "Game Pass")
                genres = validate_genres(_parse_json_list(g.get("genres")))
                tags = validate_tags(_parse_json_list(g.get("tags")))

                existing = existing_by_ext.get(ext_id)
                if existing is not None:
                    # Merge preserves user locks, notes, ratings, favorites, and existing labels.
                    if existing["status_locked"] or existing["ownership_locked"] or existing["notes"]:
                        skipped += 1
                    else:
                        updated += 1
                    if not dry_run:
                        merged_g = merge_lists(json.loads(existing["genres_json"] or "[]"), genres)
                        merged_t = merge_lists(json.loads(existing["tags_json"] or "[]"), tags)
                        new_mins = max(int(existing["playtime_minutes"] or 0), mins)
                        conn.execute(
                            """
                            UPDATE games
                            SET playtime_minutes = ?,
                                playtime_hours = ?,
                                genres_json = ?,
                                tags_json = ?,
                                cover_url = COALESCE(cover_url, ?),
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                new_mins,
                                round(new_mins / 60.0, 2),
                                json.dumps(merged_g),
                                json.dumps(merged_t),
                                g.get("cover_url"),
                                now_iso,
                                existing["id"],
                            ),
                        )
                    ext_to_game_id[ext_id] = int(existing["id"])
                    continue

                added += 1
                planned_ext_ids.add(ext_id)
                if not dry_run:
                    cur = conn.execute(
                        """
                        INSERT INTO games (
                            external_id, title, platform, source, playtime_hours, playtime_minutes,
                            last_synced_playtime_minutes, last_played, completion_status, status_locked,
                            ownership_status, ownership_locked, is_game_pass, is_play_anywhere,
                            achievements_unlocked, achievements_total, genres_json, tags_json,
                            critic_score, user_rating, release_year, cover_url, notes, favorite,
                            playtime_prompt_muted, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ext_id,
                            title,
                            platform,
                            source,
                            hours,
                            mins,
                            g.get("last_synced_playtime_minutes"),
                            g.get("last_played"),
                            g.get("completion_status") or "Not Played",
                            1 if g.get("status_locked") else 0,
                            own_status,
                            1 if g.get("ownership_locked") else 0,
                            1 if gp else 0,
                            1 if g.get("is_play_anywhere") else 0,
                            int(g.get("achievements_unlocked") or 0),
                            int(g.get("achievements_total") or 0),
                            json.dumps(genres),
                            json.dumps(tags),
                            g.get("critic_score"),
                            g.get("user_rating"),
                            g.get("release_year"),
                            g.get("cover_url"),
                            str(g.get("notes") or ""),
                            1 if g.get("favorite") else 0,
                            1 if g.get("playtime_prompt_muted") else 0,
                            str(g.get("created_at") or now_iso),
                            now_iso,
                        ),
                    )
                    new_id = int(cur.lastrowid)
                    ext_to_game_id[ext_id] = new_id
                    if g.get("canonical_id") is not None:
                        old_c = int(g["canonical_id"])
                        mapped_c = canon_remap.setdefault(old_c, new_id)
                        conn.execute(
                            "UPDATE games SET canonical_id = ? WHERE id = ?",
                            (mapped_c, new_id),
                        )

            for s in payload.get("play_sessions") or []:
                ext = s.get("game_external_id")
                ext_str = str(ext) if ext else ""
                gid = ext_to_game_id.get(ext_str) if ext_str else None
                dur = int(s.get("duration_minutes") or 0)
                if dur <= 0:
                    continue
                if not ext_str or ext_str not in planned_ext_ids:
                    warnings.append(f"Skipped session for unresolved game external_id '{ext}'")
                    continue
                if not dry_run:
                    assert gid is not None
                    exists = conn.execute(
                        """
                        SELECT 1 FROM play_sessions
                        WHERE game_id = ? AND started_at = ? AND ended_at = ? AND duration_minutes = ?
                        """,
                        (gid, s["started_at"], s["ended_at"], dur),
                    ).fetchone()
                    if exists:
                        continue
                    conn.execute(
                        """
                        INSERT INTO play_sessions (
                            game_id, platform, started_at, ended_at, duration_minutes,
                            source, precision_level, notes, achievements_unlocked, is_locked,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            gid,
                            str(s.get("platform") or "Unknown"),
                            str(s["started_at"]),
                            str(s["ended_at"]),
                            dur,
                            str(s.get("source") or "import"),
                            str(s.get("precision_level") or "estimated"),
                            s.get("notes"),
                            int(s.get("achievements_unlocked") or 0),
                            1 if s.get("is_locked") else 0,
                            now_iso,
                            now_iso,
                        ),
                    )
                sessions_added += 1

            for b in payload.get("provider_baselines") or []:
                ext = b.get("game_external_id")
                gid = ext_to_game_id.get(str(ext)) if ext else None
                if gid is not None and not dry_run:
                    conn.execute(
                        """
                        INSERT INTO provider_baselines (
                            game_id, source, baseline_minutes, last_observed_at, reset_count, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(game_id, source) DO UPDATE SET
                            baseline_minutes = MAX(provider_baselines.baseline_minutes, excluded.baseline_minutes),
                            updated_at = excluded.updated_at
                        """,
                        (
                            gid,
                            str(b["source"]),
                            max(0, int(b["baseline_minutes"])),
                            str(b.get("last_observed_at") or now_iso),
                            int(b.get("reset_count") or 0),
                            now_iso,
                        ),
                    )

            if dry_run:
                conn.rollback()

        return ImportReport(
            dry_run=dry_run,
            mode=mode,
            format_source=format_source,
            games_added=added,
            games_updated=updated,
            games_skipped=skipped,
            sessions_added=sessions_added,
            warnings=tuple(warnings),
        )

    def import_legacy_sqlite_bytes(
        self,
        scope: UserScope,
        raw_bytes: bytes,
        *,
        mode: str = "merge",
        dry_run: bool = False,
    ) -> ImportReport:
        """Validate and import a legacy SQLite games.db snapshot without swapping files into live storage."""
        scope.require_active()
        max_bytes = self.db_manager.settings.max_import_bytes
        if not raw_bytes or len(raw_bytes) < len(SQLITE_MAGIC_HEADER):
            raise ValidationError("Uploaded file is empty or too small to be a SQLite database")
        if len(raw_bytes) > max_bytes:
            raise ValidationError(f"Uploaded SQLite snapshot exceeds limit ({max_bytes} bytes)")
        if raw_bytes[: len(SQLITE_MAGIC_HEADER)] != SQLITE_MAGIC_HEADER:
            raise ValidationError("Invalid SQLite file header")

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            tmp.write(raw_bytes)
            tmp.flush()
            conn = sqlite3.connect(f"file:{Path(tmp.name).as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                chk = conn.execute("PRAGMA integrity_check").fetchone()
                if not chk or str(chk[0]).lower() != "ok":
                    raise ValidationError("Uploaded SQLite snapshot failed PRAGMA integrity_check")
                tables = {
                    r["name"]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "games" not in tables:
                    raise ValidationError("Uploaded SQLite snapshot does not contain a 'games' table")
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(games)").fetchall()}
                required = {"id", "external_id", "title", "platform", "source"}
                if not required.issubset(cols):
                    raise ValidationError("Uploaded SQLite 'games' table is missing required columns")

                rows = conn.execute("SELECT * FROM games ORDER BY id ASC").fetchall()
                game_ext_by_id = {int(r["id"]): r["external_id"] for r in rows}
                games_payload = []
                for r in rows:
                    d = dict(r)
                    games_payload.append(
                        {
                            "external_id": d["external_id"],
                            "title": d["title"],
                            "platform": d["platform"],
                            "source": d["source"],
                            "playtime_hours": float(d.get("playtime_hours") or 0.0),
                            "playtime_minutes": int(d.get("playtime_minutes") or 0),
                            "last_synced_playtime_minutes": d.get("last_synced_playtime_minutes"),
                            "last_played": d.get("last_played"),
                            "completion_status": d.get("completion_status") or "Not Played",
                            "status_locked": bool(d.get("status_locked")),
                            "ownership_status": d.get("ownership_status") or "Owned",
                            "ownership_locked": bool(d.get("ownership_locked")),
                            "is_game_pass": bool(d.get("is_game_pass")),
                            "is_play_anywhere": bool(d.get("is_play_anywhere")),
                            "achievements_unlocked": int(d.get("achievements_unlocked") or 0),
                            "achievements_total": int(d.get("achievements_total") or 0),
                            "genres": _parse_json_list(d.get("genres")),
                            "tags": _parse_json_list(d.get("tags")),
                            "critic_score": d.get("critic_score"),
                            "user_rating": d.get("user_rating"),
                            "release_year": d.get("release_year"),
                            "cover_url": d.get("cover_url"),
                            "notes": d.get("notes") or "",
                            "favorite": bool(d.get("favorite")),
                            "playtime_prompt_muted": bool(d.get("playtime_prompt_muted")),
                            "canonical_id": d.get("canonical_id"),
                            "created_at": d.get("created_at"),
                        }
                    )

                sessions_payload = []
                if "play_sessions" in tables:
                    for s in conn.execute("SELECT * FROM play_sessions ORDER BY id ASC").fetchall():
                        sd = dict(s)
                        ext = game_ext_by_id.get(int(sd["game_id"]))
                        if not ext:
                            continue
                        sessions_payload.append(
                            {
                                "game_external_id": ext,
                                "platform": sd["platform"],
                                "started_at": sd["started_at"],
                                "ended_at": sd["ended_at"],
                                "duration_minutes": int(sd["duration_minutes"]),
                                "source": sd.get("source") or "sync",
                                "precision_level": "estimated",
                                "notes": sd.get("notes"),
                                "achievements_unlocked": int(sd.get("achievements_unlocked") or 0),
                                "is_locked": bool(sd.get("is_locked")),
                            }
                        )
            finally:
                conn.close()

        return self.import_json(
            scope,
            {
                "format_version": 1,
                "games": games_payload,
                "play_sessions": sessions_payload,
            },
            mode=mode,
            dry_run=dry_run,
            format_source="legacy_sqlite",
        )
