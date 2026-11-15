"""Authoritative play session, baseline, coalescing, and timezone-aware daily ledger service."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from vertex.activity.models import DailyLedgerEntry, ObservationOutcome, PlaySession
from vertex.activity.repository import row_to_ledger, row_to_session
from vertex.infrastructure.errors import NotFoundError, ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_family(platform: str) -> str:
    p = (platform or "").lower()
    if "steam" in p or "pc" in p:
        return "PC"
    if "xbox" in p:
        return "Xbox"
    if "ps" in p or "playstation" in p:
        return "PlayStation"
    if "switch" in p or "nintendo" in p:
        return "Nintendo"
    return platform or "Other"


def _resolve_zoneinfo(tz_name: str, timezone_eras: list[dict[str, str]], at_utc: datetime) -> ZoneInfo:
    selected = tz_name
    for era in timezone_eras:
        eff = era.get("effective_from")
        era_tz = era.get("tz")
        if eff and era_tz and _parse_iso(eff) <= at_utc:
            selected = era_tz
    if not selected or selected == "auto":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(selected)
    except Exception:
        return ZoneInfo("UTC")


def allocate_minutes_by_local_day(
    started_at: str,
    ended_at: str,
    duration_minutes: int,
    tz: ZoneInfo,
) -> list[tuple[str, float, int]]:
    """Slice a session cleanly across local calendar days (DST-safe), returning (YYYY-MM-DD, mins, start_hour)."""
    dur = max(0, int(duration_minutes))
    if dur <= 0:
        return []
    start_utc = _parse_iso(started_at)
    end_utc = _parse_iso(ended_at)
    if end_utc <= start_utc:
        local_start = start_utc.astimezone(tz)
        return [(local_start.date().isoformat(), float(dur), local_start.hour)]

    total_span = (end_utc - start_utc).total_seconds()
    slices: list[tuple[str, float, int]] = []
    cursor_utc = start_utc
    allocated = 0.0

    while cursor_utc < end_utc:
        local_cur = cursor_utc.astimezone(tz)
        local_day = local_cur.date()
        next_local_midnight = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz)
        next_utc_boundary = min(end_utc, next_local_midnight.astimezone(timezone.utc))
        if next_utc_boundary <= cursor_utc:
            # Guard edge case right on DST fold
            next_utc_boundary = end_utc

        frac = (next_utc_boundary - cursor_utc).total_seconds() / total_span
        if next_utc_boundary >= end_utc:
            mins_here = round(max(0.0, float(dur) - allocated), 2)
        else:
            mins_here = round(float(dur) * frac, 2)
            allocated += mins_here

        slices.append((local_day.isoformat(), mins_here, local_cur.hour))
        cursor_utc = next_utc_boundary

    return slices


def rebuild_daily_ledger_for_conn(
    conn: sqlite3.Connection,
    *,
    tz_name: str = "UTC",
    timezone_eras: list[dict[str, str]] | None = None,
) -> None:
    eras = timezone_eras or []
    conn.execute("DELETE FROM daily_playtime_ledger")
    rows = conn.execute(
        """
        SELECT ps.game_id, ps.platform, ps.started_at, ps.ended_at, ps.duration_minutes, ps.achievements_unlocked
        FROM play_sessions ps
        ORDER BY ps.started_at ASC, ps.id ASC
        """
    ).fetchall()

    aggregates: dict[tuple[str, int], dict[str, object]] = {}
    for r in rows:
        start_utc = _parse_iso(r["started_at"])
        tz = _resolve_zoneinfo(tz_name, eras, start_utc)
        slices = allocate_minutes_by_local_day(
            r["started_at"],
            r["ended_at"],
            int(r["duration_minutes"]),
            tz,
        )
        family = _platform_family(r["platform"])
        for idx, (day_str, mins_here, first_hour) in enumerate(slices):
            key = (day_str, int(r["game_id"]))
            entry = aggregates.get(key)
            if entry is None:
                aggregates[key] = {
                    "platform_family": family,
                    "minutes_played": mins_here,
                    "sessions_count": 1,
                    "achievements_unlocked": int(r["achievements_unlocked"] or 0) if idx == 0 else 0,
                    "first_session_hour": first_hour,
                }
            else:
                entry["minutes_played"] = round(float(entry["minutes_played"]) + mins_here, 2)
                entry["sessions_count"] = int(entry["sessions_count"]) + 1
                if idx == 0:
                    entry["achievements_unlocked"] = int(entry["achievements_unlocked"]) + int(
                        r["achievements_unlocked"] or 0
                    )
                if entry["first_session_hour"] is None or first_hour < int(entry["first_session_hour"]):
                    entry["first_session_hour"] = first_hour

    for (day_str, game_id), data in sorted(aggregates.items()):
        conn.execute(
            """
            INSERT INTO daily_playtime_ledger (
                date, game_id, platform_family, minutes_played, sessions_count,
                achievements_unlocked, first_session_hour
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day_str,
                game_id,
                data["platform_family"],
                data["minutes_played"],
                data["sessions_count"],
                data["achievements_unlocked"],
                data["first_session_hour"],
            ),
        )


class ActivityService:
    """Manages play observations, provider baselines, sessions, locks, and daily ledger projection."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

    def _get_user_timezone_config(self, scope: UserScope) -> tuple[str, list[dict[str, str]]]:
        with self.db_manager.system_connection() as conn:
            row = conn.execute(
                "SELECT timezone, timezone_eras_json FROM users WHERE id = ?",
                (scope.user_id,),
            ).fetchone()
            if row is None:
                return "UTC", []
            return row["timezone"] or "UTC", json.loads(row["timezone_eras_json"] or "[]")

    def record_cumulative_observation(
        self,
        scope: UserScope,
        *,
        game_id: int,
        source: str,
        platform: str,
        observed_minutes: int,
        observed_at: str,
        idempotency_key: str,
        precision_level: str = "estimated",
    ) -> ObservationOutcome:
        scope.require_active()
        obs_mins = max(0, int(observed_minutes))
        obs_dt = _parse_iso(observed_at)
        obs_iso = obs_dt.isoformat()
        now_iso = _utc_now_iso()
        tz_name, tz_eras = self._get_user_timezone_config(scope)

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if game is None:
                raise NotFoundError(f"Game {game_id} not found")

            dup = conn.execute(
                "SELECT id FROM play_observations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            baseline_row = conn.execute(
                "SELECT * FROM provider_baselines WHERE game_id = ? AND source = ?",
                (game_id, source),
            ).fetchone()

            if dup is not None:
                current_base = int(baseline_row["baseline_minutes"]) if baseline_row else obs_mins
                return ObservationOutcome(
                    status="duplicate_ignored",
                    delta_minutes=0,
                    baseline_minutes=current_base,
                    session=None,
                )

            if baseline_row is None:
                # Baseline initialization: record anchor WITHOUT inventing retroactive sessions.
                conn.execute(
                    """
                    INSERT INTO play_observations (
                        idempotency_key, game_id, source, platform, observation_kind,
                        observed_at, cumulative_minutes, precision_level, created_at
                    ) VALUES (?, ?, ?, ?, 'baseline_init', ?, ?, ?, ?)
                    """,
                    (idempotency_key, game_id, source, platform, obs_iso, obs_mins, precision_level, now_iso),
                )
                conn.execute(
                    """
                    INSERT INTO provider_baselines (
                        game_id, source, baseline_minutes, last_observed_at, reset_count, updated_at
                    ) VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (game_id, source, obs_mins, obs_iso, now_iso),
                )
                conn.execute(
                    "UPDATE games SET last_synced_playtime_minutes = ?, updated_at = ? WHERE id = ?",
                    (obs_mins, now_iso, game_id),
                )
                return ObservationOutcome(
                    status="baseline_initialized",
                    delta_minutes=0,
                    baseline_minutes=obs_mins,
                    session=None,
                )

            prev_baseline = int(baseline_row["baseline_minutes"])
            delta = obs_mins - prev_baseline

            if delta < 0:
                # Provider playtime decreased/regressed: never emit negative session, never drop baseline/total.
                conn.execute(
                    """
                    INSERT INTO play_observations (
                        idempotency_key, game_id, source, platform, observation_kind,
                        observed_at, cumulative_minutes, precision_level, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, 'baseline_reset', ?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        game_id,
                        source,
                        platform,
                        obs_iso,
                        obs_mins,
                        precision_level,
                        json.dumps({"previous_baseline": prev_baseline, "observed": obs_mins}),
                        now_iso,
                    ),
                )
                conn.execute(
                    """
                    UPDATE provider_baselines
                    SET reset_count = reset_count + 1,
                        last_observed_at = ?,
                        updated_at = ?
                    WHERE game_id = ? AND source = ?
                    """,
                    (obs_iso, now_iso, game_id, source),
                )
                return ObservationOutcome(
                    status="regression_ignored",
                    delta_minutes=0,
                    baseline_minutes=prev_baseline,
                    session=None,
                )

            conn.execute(
                """
                INSERT INTO play_observations (
                    idempotency_key, game_id, source, platform, observation_kind,
                    observed_at, cumulative_minutes, precision_level, created_at
                ) VALUES (?, ?, ?, ?, 'cumulative_total', ?, ?, ?, ?)
                """,
                (idempotency_key, game_id, source, platform, obs_iso, obs_mins, precision_level, now_iso),
            )

            if delta == 0:
                conn.execute(
                    "UPDATE provider_baselines SET last_observed_at = ?, updated_at = ? WHERE game_id = ? AND source = ?",
                    (obs_iso, now_iso, game_id, source),
                )
                return ObservationOutcome(
                    status="no_change",
                    delta_minutes=0,
                    baseline_minutes=prev_baseline,
                    session=None,
                )

            # Positive delta -> update baseline and last_synced_playtime_minutes atomically.
            conn.execute(
                """
                UPDATE provider_baselines
                SET baseline_minutes = ?, last_observed_at = ?, updated_at = ?
                WHERE game_id = ? AND source = ?
                """,
                (obs_mins, obs_iso, now_iso, game_id, source),
            )
            conn.execute(
                "UPDATE games SET last_synced_playtime_minutes = ?, updated_at = ? WHERE id = ?",
                (obs_mins, now_iso, game_id),
            )

            # Transition unlocked backlog entries to Playing on observed playtime increase.
            if not game["status_locked"] and game["completion_status"] in (
                "Plan to Play",
                "Not Played",
                "Unplayed",
            ):
                conn.execute(
                    "UPDATE games SET completion_status = 'Playing', updated_at = ? WHERE id = ?",
                    (now_iso, game_id),
                )

            last_sess = conn.execute(
                """
                SELECT * FROM play_sessions
                WHERE game_id = ?
                ORDER BY ended_at DESC, id DESC
                LIMIT 1
                """,
                (game_id,),
            ).fetchone()

            coalesced = False
            session_id: int
            if last_sess is not None and not last_sess["is_locked"] and last_sess["source"] == source:
                prev_end = _parse_iso(last_sess["ended_at"])
                gap_sec = (obs_dt - prev_end).total_seconds()
                if 0 <= gap_sec <= 1800:
                    conn.execute(
                        """
                        UPDATE play_sessions
                        SET ended_at = ?, duration_minutes = duration_minutes + ?, updated_at = ?
                        WHERE id = ? AND is_locked = 0
                        """,
                        (obs_iso, delta, now_iso, last_sess["id"]),
                    )
                    session_id = int(last_sess["id"])
                    coalesced = True

            if not coalesced:
                started_iso = (obs_dt - timedelta(minutes=delta)).isoformat()
                cur = conn.execute(
                    """
                    INSERT INTO play_sessions (
                        game_id, platform, started_at, ended_at, duration_minutes,
                        source, precision_level, is_locked, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        game_id,
                        platform,
                        started_iso,
                        obs_iso,
                        delta,
                        source,
                        precision_level,
                        now_iso,
                        now_iso,
                    ),
                )
                session_id = int(cur.lastrowid)

            rebuild_daily_ledger_for_conn(conn, tz_name=tz_name, timezone_eras=tz_eras)
            sess_row = conn.execute("SELECT * FROM play_sessions WHERE id = ?", (session_id,)).fetchone()
            return ObservationOutcome(
                status="session_coalesced" if coalesced else "session_created",
                delta_minutes=delta,
                baseline_minutes=obs_mins,
                session=row_to_session(sess_row),
            )

    def list_sessions(self, scope: UserScope, *, game_id: int | None = None, limit: int = 50) -> list[PlaySession]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            if game_id is not None:
                rows = conn.execute(
                    "SELECT * FROM play_sessions WHERE game_id = ? ORDER BY ended_at DESC, id DESC LIMIT ?",
                    (game_id, max(1, min(200, limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM play_sessions ORDER BY ended_at DESC, id DESC LIMIT ?",
                    (max(1, min(200, limit))),
                ).fetchall()
            return [row_to_session(r) for r in rows]

    def update_session(
        self,
        scope: UserScope,
        session_id: int,
        *,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_minutes: int | None = None,
        notes: str | None = None,
        is_locked: bool | None = True,
    ) -> PlaySession:
        scope.require_active()
        tz_name, tz_eras = self._get_user_timezone_config(scope)
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            row = conn.execute("SELECT * FROM play_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Play session {session_id} not found")

            new_start = _parse_iso(started_at).isoformat() if started_at else row["started_at"]
            new_end = _parse_iso(ended_at).isoformat() if ended_at else row["ended_at"]
            new_dur = int(duration_minutes) if duration_minutes is not None else int(row["duration_minutes"])
            if new_dur <= 0:
                raise ValidationError("Session duration_minutes must be positive")
            new_notes = notes if notes is not None else row["notes"]
            locked_flag = 1 if (True if is_locked is None else is_locked) else 0

            conn.execute(
                """
                UPDATE play_sessions
                SET started_at = ?, ended_at = ?, duration_minutes = ?, notes = ?, is_locked = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_start, new_end, new_dur, new_notes, locked_flag, now_iso, session_id),
            )
            rebuild_daily_ledger_for_conn(conn, tz_name=tz_name, timezone_eras=tz_eras)
            updated = conn.execute("SELECT * FROM play_sessions WHERE id = ?", (session_id,)).fetchone()
            return row_to_session(updated)

    def delete_session(self, scope: UserScope, session_id: int) -> None:
        """Delete a visible session while keeping provider_baselines and games.last_synced_playtime_minutes intact."""
        scope.require_active()
        tz_name, tz_eras = self._get_user_timezone_config(scope)
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            cur = conn.execute("DELETE FROM play_sessions WHERE id = ?", (session_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Play session {session_id} not found")
            rebuild_daily_ledger_for_conn(conn, tz_name=tz_name, timezone_eras=tz_eras)

    def get_daily_ledger(self, scope: UserScope) -> list[DailyLedgerEntry]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute(
                "SELECT * FROM daily_playtime_ledger ORDER BY date DESC, game_id ASC"
            ).fetchall()
            return [row_to_ledger(r) for r in rows]

    def rebuild_daily_ledger(self, scope: UserScope) -> list[DailyLedgerEntry]:
        scope.require_active()
        tz_name, tz_eras = self._get_user_timezone_config(scope)
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            rebuild_daily_ledger_for_conn(conn, tz_name=tz_name, timezone_eras=tz_eras)
            rows = conn.execute(
                "SELECT * FROM daily_playtime_ledger ORDER BY date DESC, game_id ASC"
            ).fetchall()
            return [row_to_ledger(r) for r in rows]
