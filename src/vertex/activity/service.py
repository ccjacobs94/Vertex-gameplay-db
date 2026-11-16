"""Authoritative play session, baseline, coalescing, and timezone-aware daily ledger service."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from vertex.activity.models import (
    AchievementBackfillResult,
    ActiveTelemetrySession,
    DailyLedgerEntry,
    ObservationOutcome,
    PlaySession,
    PlaytimePrompt,
    PlaytimePromptResolution,
)
from vertex.activity.repository import row_to_ledger, row_to_session
from vertex.infrastructure.errors import NotFoundError, ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_user_iso(ts: str, field_name: str = "timestamp") -> datetime:
    try:
        return _parse_iso(str(ts or "").strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid ISO timestamp for {field_name}: '{ts}'") from exc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_family(platform: str) -> str:
    p = (platform or "").lower()
    if "xbox" in p:
        return "Xbox"
    if any(k in p for k in ("steam", "gog", "pc")):
        return "PC"
    if any(k in p for k in ("playstation", "psn", "ps1", "ps2", "ps3", "ps4", "ps5", "psp", "ps vita")):
        return "PlayStation"
    if "switch" in p or "nintendo" in p:
        return "Nintendo"
    return platform or "Other"


def _resolve_zoneinfo(tz_name: str, timezone_eras: list[dict[str, object]], at_utc: datetime) -> ZoneInfo:
    until_eras: list[tuple[datetime, str]] = []
    from_eras: list[tuple[datetime, str]] = []
    for era in timezone_eras:
        if not isinstance(era, dict):
            continue
        era_tz = str(era.get("timezone") or era.get("tz") or "").strip()
        if not era_tz:
            continue
        until_val = era.get("until")
        from_val = era.get("effective_from")
        if until_val:
            try:
                until_eras.append((_parse_iso(str(until_val)), era_tz))
            except ValueError:
                continue
        elif from_val:
            try:
                from_eras.append((_parse_iso(str(from_val)), era_tz))
            except ValueError:
                continue

    selected = tz_name
    if until_eras:
        until_eras.sort(key=lambda item: item[0])
        matched_until = False
        for until_dt, era_tz in until_eras:
            if at_utc < until_dt:
                selected = era_tz
                matched_until = True
                break
        if not matched_until and from_eras:
            from_eras.sort(key=lambda item: item[0])
            for eff_dt, era_tz in from_eras:
                if eff_dt <= at_utc:
                    selected = era_tz
    elif from_eras:
        from_eras.sort(key=lambda item: item[0])
        for eff_dt, era_tz in from_eras:
            if eff_dt <= at_utc:
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
    timezone_eras: list[dict[str, Any]] | None = None,
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

    def _get_user_timezone_config(self, scope: UserScope) -> tuple[str, list[dict[str, Any]]]:
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
        obs_dt = _parse_user_iso(observed_at, "observed_at")
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

            prev_obs_dt = _parse_iso(baseline_row["last_observed_at"])
            if delta == 0:
                if obs_dt > prev_obs_dt:
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
            next_obs_iso = obs_iso if obs_dt >= prev_obs_dt else baseline_row["last_observed_at"]
            conn.execute(
                """
                UPDATE provider_baselines
                SET baseline_minutes = ?, last_observed_at = ?, updated_at = ?
                WHERE game_id = ? AND source = ?
                """,
                (obs_mins, next_obs_iso, now_iso, game_id, source),
            )
            conn.execute(
                "UPDATE games SET last_synced_playtime_minutes = MAX(COALESCE(last_synced_playtime_minutes, 0), ?), updated_at = ? WHERE id = ?",
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
            elif (
                game["status_locked"]
                and (game["completion_status"] or "Not Played") != "Playing"
                and not game["playtime_prompt_muted"]
                and delta >= 30
            ):
                prev_hrs = round(prev_baseline / 60.0, 2)
                new_hrs = round(obs_mins / 60.0, 2)
                delta_hrs = round(delta / 60.0, 2)
                conn.execute(
                    """
                    INSERT INTO playtime_prompts (
                        game_id, previous_hours, new_hours, delta_hours, current_status,
                        detected_at, status, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL)
                    ON CONFLICT(game_id) DO UPDATE SET
                        new_hours = excluded.new_hours,
                        delta_hours = CASE
                            WHEN playtime_prompts.status = 'pending'
                            THEN ROUND(playtime_prompts.delta_hours + excluded.delta_hours, 2)
                            ELSE excluded.delta_hours
                        END,
                        previous_hours = CASE
                            WHEN playtime_prompts.status = 'pending'
                            THEN playtime_prompts.previous_hours
                            ELSE excluded.previous_hours
                        END,
                        current_status = excluded.current_status,
                        detected_at = excluded.detected_at,
                        status = 'pending',
                        resolved_at = NULL
                    """,
                    (
                        game_id,
                        prev_hrs,
                        new_hrs,
                        delta_hrs,
                        game["completion_status"] or "Not Played",
                        obs_iso,
                    ),
                )

            # Avoid double-counting time already recorded by exact interval sources (e.g. Home Assistant / Telemetry).
            exact_covered = 0
            exact_window_end = obs_dt + timedelta(minutes=5)
            exact_rows = conn.execute(
                """
                SELECT duration_minutes, started_at, ended_at, source
                FROM play_sessions
                WHERE game_id = ? AND precision_level = 'exact' AND source != ?
                """,
                (game_id, source),
            ).fetchall()
            for er in exact_rows:
                e_start = _parse_iso(er["started_at"])
                e_end = _parse_iso(er["ended_at"])
                if (prev_obs_dt < e_end <= exact_window_end) or (
                    e_start <= obs_dt <= e_end and e_end > prev_obs_dt
                ):
                    exact_covered += int(er["duration_minutes"] or 0)

            effective_delta = max(0, delta - exact_covered)
            if effective_delta == 0:
                return ObservationOutcome(
                    status="covered_by_exact_session",
                    delta_minutes=0,
                    baseline_minutes=obs_mins,
                    session=None,
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
                        (obs_iso, effective_delta, now_iso, last_sess["id"]),
                    )
                    session_id = int(last_sess["id"])
                    coalesced = True

            if not coalesced:
                started_iso = (obs_dt - timedelta(minutes=effective_delta)).isoformat()
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
                        effective_delta,
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
                delta_minutes=effective_delta,
                baseline_minutes=obs_mins,
                session=row_to_session(sess_row),
            )

    def record_interval_observation(
        self,
        scope: UserScope,
        *,
        game_id: int,
        source: str,
        platform: str,
        started_at: str,
        ended_at: str,
        idempotency_key: str,
        duration_minutes: int | None = None,
        precision_level: str = "exact",
        notes: str | None = None,
        achievements_unlocked: int = 0,
        active_minutes: float | None = None,
        idle_minutes: float | None = None,
        external_session_ref: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ObservationOutcome:
        scope.require_active()
        if precision_level not in ("exact", "estimated", "coarse"):
            raise ValidationError(f"Invalid precision_level '{precision_level}'")
        start_dt = _parse_user_iso(started_at, "started_at")
        end_dt = _parse_user_iso(ended_at, "ended_at")
        if end_dt < start_dt:
            raise ValidationError("Interval ended_at cannot be earlier than started_at")
        dur = (
            int(duration_minutes)
            if duration_minutes is not None
            else max(1, int(round((end_dt - start_dt).total_seconds() / 60.0)))
        )
        if dur <= 0:
            raise ValidationError("Interval duration_minutes must be positive")

        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()
        now_iso = _utc_now_iso()
        tz_name, tz_eras = self._get_user_timezone_config(scope)

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if game is None:
                raise NotFoundError(f"Game {game_id} not found")

            baseline_row = conn.execute(
                "SELECT COALESCE(MAX(baseline_minutes), 0) AS bm FROM provider_baselines WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            current_base = int(baseline_row["bm"]) if baseline_row else 0

            dup = conn.execute(
                "SELECT id FROM play_observations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if dup is not None:
                return ObservationOutcome(
                    status="duplicate_ignored",
                    delta_minutes=0,
                    baseline_minutes=current_base,
                    session=None,
                )

            existing_sessions = conn.execute(
                "SELECT * FROM play_sessions WHERE game_id = ? ORDER BY started_at ASC, id ASC",
                (game_id,),
            ).fetchall()

            precision_rank = {"coarse": 1, "estimated": 2, "exact": 3}
            incoming_rank = precision_rank[precision_level]
            overlap_blocking_id: int | None = None
            unlocked_estimated_overlaps: list[tuple[sqlite3.Row, int]] = []
            for es in existing_sessions:
                es_start = _parse_iso(es["started_at"])
                es_end = _parse_iso(es["ended_at"])
                # Strictly overlapping intervals (adjacent boundaries touching at exact second do not overlap)
                overlap_start = max(start_dt, es_start)
                overlap_end = min(end_dt, es_end)
                if overlap_end > overlap_start or (es_start == start_dt and es_end == end_dt):
                    existing_rank = precision_rank.get(es["precision_level"], 2)
                    if bool(es["is_locked"]) or existing_rank >= incoming_rank:
                        overlap_blocking_id = int(es["id"])
                        break
                    overlap_mins = max(1, int(round((overlap_end - overlap_start).total_seconds() / 60.0)))
                    unlocked_estimated_overlaps.append((es, overlap_mins))

            obs_payload = dict(payload or {})
            if overlap_blocking_id is not None:
                obs_payload["deduplicated_against_session_id"] = overlap_blocking_id
                conn.execute(
                    """
                    INSERT INTO play_observations (
                        idempotency_key, game_id, source, platform, observation_kind,
                        observed_at, interval_started_at, interval_ended_at, interval_minutes,
                        precision_level, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, 'interval', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        game_id,
                        source,
                        platform,
                        end_iso,
                        start_iso,
                        end_iso,
                        dur,
                        precision_level,
                        json.dumps(obs_payload),
                        now_iso,
                    ),
                )
                return ObservationOutcome(
                    status="overlap_ignored",
                    delta_minutes=0,
                    baseline_minutes=current_base,
                    session=None,
                )

            # Trim/supersede overlapping unlocked estimated sessions so ledger never double-counts.
            for es, overlap_mins in unlocked_estimated_overlaps:
                existing_dur = int(es["duration_minutes"])
                remaining = existing_dur - overlap_mins
                if remaining <= 0:
                    conn.execute("DELETE FROM play_sessions WHERE id = ? AND is_locked = 0", (es["id"],))
                else:
                    conn.execute(
                        "UPDATE play_sessions SET duration_minutes = ?, updated_at = ? WHERE id = ? AND is_locked = 0",
                        (remaining, now_iso, es["id"]),
                    )

            conn.execute(
                """
                INSERT INTO play_observations (
                    idempotency_key, game_id, source, platform, observation_kind,
                    observed_at, interval_started_at, interval_ended_at, interval_minutes,
                    precision_level, payload_json, created_at
                ) VALUES (?, ?, ?, ?, 'interval', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    game_id,
                    source,
                    platform,
                    end_iso,
                    start_iso,
                    end_iso,
                    dur,
                    precision_level,
                    json.dumps(obs_payload),
                    now_iso,
                ),
            )

            if not game["status_locked"] and game["completion_status"] in (
                "Plan to Play",
                "Not Played",
                "Unplayed",
            ):
                conn.execute(
                    "UPDATE games SET completion_status = 'Playing', updated_at = ? WHERE id = ?",
                    (now_iso, game_id),
                )

            cur = conn.execute(
                """
                INSERT INTO play_sessions (
                    game_id, platform, started_at, ended_at, duration_minutes,
                    source, precision_level, notes, achievements_unlocked, is_locked,
                    active_minutes, idle_minutes, external_session_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    platform,
                    start_iso,
                    end_iso,
                    dur,
                    source,
                    precision_level,
                    notes,
                    int(achievements_unlocked or 0),
                    active_minutes,
                    idle_minutes,
                    external_session_ref,
                    now_iso,
                    now_iso,
                ),
            )
            session_id = int(cur.lastrowid)
            rebuild_daily_ledger_for_conn(conn, tz_name=tz_name, timezone_eras=tz_eras)
            sess_row = conn.execute("SELECT * FROM play_sessions WHERE id = ?", (session_id,)).fetchone()
            return ObservationOutcome(
                status="session_created",
                delta_minutes=dur,
                baseline_minutes=current_base,
                session=row_to_session(sess_row),
            )

    def list_sessions(
        self,
        scope: UserScope,
        *,
        game_id: int | None = None,
        limit: int = 50,
        platform: str | None = None,
        source: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
    ) -> list[PlaySession]:
        scope.require_active()
        cap = max(1, min(200, int(limit)))
        plat_l = (platform or "").strip().lower()
        src_l = (source or "").strip().lower()

        after_dt: datetime | None = None
        if started_after and started_after.strip():
            raw_after = started_after.strip()
            if len(raw_after) == 10 and raw_after[4] == "-" and raw_after[7] == "-":
                raw_after = f"{raw_after}T00:00:00+00:00"
            after_dt = _parse_iso(raw_after)

        before_dt: datetime | None = None
        if started_before and started_before.strip():
            raw_before = started_before.strip()
            if len(raw_before) == 10 and raw_before[4] == "-" and raw_before[7] == "-":
                raw_before = f"{raw_before}T23:59:59.999999+00:00"
            before_dt = _parse_iso(raw_before)

        has_filters = bool(plat_l or src_l or after_dt is not None or before_dt is not None)
        with self.db_manager.library_connection(scope) as conn:
            if has_filters:
                if game_id is not None:
                    rows = conn.execute(
                        "SELECT * FROM play_sessions WHERE game_id = ? ORDER BY ended_at DESC, id DESC",
                        (game_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM play_sessions ORDER BY ended_at DESC, id DESC"
                    ).fetchall()
            elif game_id is not None:
                rows = conn.execute(
                    "SELECT * FROM play_sessions WHERE game_id = ? ORDER BY ended_at DESC, id DESC LIMIT ?",
                    (game_id, cap),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM play_sessions ORDER BY ended_at DESC, id DESC LIMIT ?",
                    (cap,),
                ).fetchall()

            if not has_filters:
                return [row_to_session(r) for r in rows]

            family_keys = {"pc", "xbox", "playstation", "nintendo"}
            filtered: list[PlaySession] = []
            for r in rows:
                sess = row_to_session(r)
                if plat_l:
                    s_plat = sess.platform.lower()
                    if s_plat != plat_l and not (
                        plat_l in family_keys and _platform_family(sess.platform).lower() == plat_l
                    ):
                        continue
                if src_l and sess.source.lower() != src_l:
                    continue
                if after_dt is not None or before_dt is not None:
                    start_dt = _parse_iso(sess.started_at)
                    if after_dt is not None and start_dt < after_dt:
                        continue
                    if before_dt is not None and start_dt > before_dt:
                        continue
                filtered.append(sess)
                if len(filtered) >= cap:
                    break
            return filtered

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

            new_start = _parse_user_iso(started_at, "started_at").isoformat() if started_at else row["started_at"]
            new_end = _parse_user_iso(ended_at, "ended_at").isoformat() if ended_at else row["ended_at"]
            if _parse_iso(new_end) < _parse_iso(new_start):
                raise ValidationError("Session ended_at cannot be earlier than started_at")
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

    # ── Playtime Prompts ────────────────────────────────────────────────────

    def list_playtime_prompts(self, scope: UserScope) -> list[PlaytimePrompt]:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            rows = conn.execute(
                """
                SELECT pp.*, g.title, g.platform, g.cover_url, g.completion_status AS game_status,
                       g.playtime_hours, g.playtime_prompt_muted
                FROM playtime_prompts pp
                LEFT JOIN games g ON g.id = pp.game_id
                WHERE pp.status = 'pending'
                ORDER BY pp.detected_at DESC, pp.id DESC
                """
            ).fetchall()

            prompts: list[PlaytimePrompt] = []
            stale_ids: list[int] = []
            for r in rows:
                if (
                    r["title"] is None
                    or r["game_status"] == "Playing"
                    or bool(r["playtime_prompt_muted"])
                ):
                    stale_ids.append(int(r["id"]))
                    continue
                prompts.append(
                    PlaytimePrompt(
                        id=int(r["id"]),
                        game_id=int(r["game_id"]),
                        title=r["title"],
                        platform=r["platform"],
                        cover_url=r["cover_url"],
                        current_status=r["game_status"] or r["current_status"],
                        previous_hours=round(float(r["previous_hours"] or 0.0), 1),
                        new_hours=round(float(r["new_hours"] or 0.0), 1),
                        delta_hours=round(float(r["delta_hours"] or 0.0), 1),
                        total_playtime_hours=float(r["playtime_hours"] or 0.0),
                        detected_at=r["detected_at"],
                        status=r["status"],
                    )
                )

            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                conn.execute(
                    f"DELETE FROM playtime_prompts WHERE id IN ({placeholders})",
                    stale_ids,
                )
            return prompts

    def resolve_playtime_prompt(
        self,
        scope: UserScope,
        prompt_id: int,
        action: str,
    ) -> PlaytimePromptResolution:
        scope.require_active()
        cleaned_action = (action or "").strip().lower()
        if cleaned_action not in ("accept", "dismiss", "mute"):
            raise ValidationError("action must be 'accept', 'dismiss', or 'mute'")

        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM playtime_prompts WHERE id = ? AND status = 'pending'",
                (prompt_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Playtime prompt {prompt_id} not found")
            game_id = int(row["game_id"])
            game_row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if game_row is None:
                conn.execute("DELETE FROM playtime_prompts WHERE id = ?", (prompt_id,))
                raise NotFoundError(f"Game {game_id} for playtime prompt not found")

            cid = game_row["canonical_id"]
            if cleaned_action == "accept":
                if cid is not None:
                    conn.execute(
                        "UPDATE games SET completion_status = 'Playing', status_locked = 1, updated_at = ? WHERE canonical_id = ?",
                        (now_iso, cid),
                    )
                else:
                    conn.execute(
                        "UPDATE games SET completion_status = 'Playing', status_locked = 1, updated_at = ? WHERE id = ?",
                        (now_iso, game_id),
                    )
                conn.execute(
                    "UPDATE playtime_prompts SET status = 'resolved', resolved_at = ? WHERE id = ?",
                    (now_iso, prompt_id),
                )
            elif cleaned_action == "mute":
                if cid is not None:
                    conn.execute(
                        "UPDATE games SET playtime_prompt_muted = 1, updated_at = ? WHERE canonical_id = ?",
                        (now_iso, cid),
                    )
                else:
                    conn.execute(
                        "UPDATE games SET playtime_prompt_muted = 1, updated_at = ? WHERE id = ?",
                        (now_iso, game_id),
                    )
                conn.execute(
                    "UPDATE playtime_prompts SET status = 'dismissed', resolved_at = ? WHERE id = ?",
                    (now_iso, prompt_id),
                )
            else:
                conn.execute(
                    "UPDATE playtime_prompts SET status = 'dismissed', resolved_at = ? WHERE id = ?",
                    (now_iso, prompt_id),
                )

            updated = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            return PlaytimePromptResolution(
                prompt_id=prompt_id,
                action=cleaned_action,
                game_id=game_id,
                completion_status=updated["completion_status"],
                status_locked=bool(updated["status_locked"]),
                playtime_prompt_muted=bool(updated["playtime_prompt_muted"]),
            )

    # ── Telemetry Active Sessions & Historical Backfill ─────────────────────

    def seed_baseline_if_absent(
        self,
        scope: UserScope,
        *,
        game_id: int,
        source: str,
        baseline_minutes: int,
        observed_at: str,
    ) -> bool:
        scope.require_active()
        obs_dt = _parse_user_iso(observed_at, "observed_at")
        obs_iso = obs_dt.isoformat()
        now_iso = _utc_now_iso()
        mins = max(0, int(baseline_minutes))
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            existing = conn.execute(
                "SELECT 1 FROM provider_baselines WHERE game_id = ? AND source = ?",
                (game_id, source),
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                """
                INSERT INTO provider_baselines (
                    game_id, source, baseline_minutes, last_observed_at, reset_count, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (game_id, source, mins, obs_iso, now_iso),
            )
            return True

    def get_provider_baseline_minutes(
        self,
        scope: UserScope,
        *,
        game_id: int,
        source: str,
    ) -> int | None:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute(
                "SELECT baseline_minutes FROM provider_baselines WHERE game_id = ? AND source = ?",
                (game_id, source),
            ).fetchone()
            return int(row["baseline_minutes"]) if row is not None else None

    def advance_baseline_from_telemetry(
        self,
        scope: UserScope,
        *,
        game_id: int,
        source: str,
        platform: str,
        observed_minutes: int,
        observed_at: str,
        idempotency_key: str,
    ) -> None:
        """Advance provider baseline after an exact telemetry close so deletions/sweeps never recreate or double-count."""
        scope.require_active()
        obs_mins = max(0, int(observed_minutes))
        obs_dt = _parse_user_iso(observed_at, "observed_at")
        obs_iso = obs_dt.isoformat()
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            dup = conn.execute(
                "SELECT 1 FROM play_observations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if dup is not None:
                return
            conn.execute(
                """
                INSERT INTO play_observations (
                    idempotency_key, game_id, source, platform, observation_kind,
                    observed_at, cumulative_minutes, precision_level, created_at
                ) VALUES (?, ?, ?, ?, 'cumulative_total', ?, ?, 'exact', ?)
                """,
                (idempotency_key, game_id, source, platform, obs_iso, obs_mins, now_iso),
            )
            existing = conn.execute(
                "SELECT baseline_minutes, last_observed_at FROM provider_baselines WHERE game_id = ? AND source = ?",
                (game_id, source),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO provider_baselines (
                        game_id, source, baseline_minutes, last_observed_at, reset_count, updated_at
                    ) VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (game_id, source, obs_mins, obs_iso, now_iso),
                )
            else:
                next_mins = max(int(existing["baseline_minutes"]), obs_mins)
                prev_dt = _parse_iso(existing["last_observed_at"])
                next_obs = obs_iso if obs_dt >= prev_dt else existing["last_observed_at"]
                conn.execute(
                    """
                    UPDATE provider_baselines
                    SET baseline_minutes = ?, last_observed_at = ?, updated_at = ?
                    WHERE game_id = ? AND source = ?
                    """,
                    (next_mins, next_obs, now_iso, game_id, source),
                )
            conn.execute(
                "UPDATE games SET last_synced_playtime_minutes = MAX(COALESCE(last_synced_playtime_minutes, 0), ?), updated_at = ? WHERE id = ?",
                (obs_mins, now_iso, game_id),
            )

    def get_active_telemetry_session(
        self,
        scope: UserScope,
        provider: str,
    ) -> ActiveTelemetrySession | None:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute(
                "SELECT * FROM telemetry_active_sessions WHERE provider = ?",
                (provider,),
            ).fetchone()
            if row is None:
                return None
            return ActiveTelemetrySession(
                provider=row["provider"],
                external_title_id=row["external_title_id"],
                game_id=int(row["game_id"]),
                title=row["title"],
                platform=row["platform"],
                source=row["source"],
                started_at=row["started_at"],
                last_seen_at=row["last_seen_at"],
                baseline_minutes=int(row["baseline_minutes"] or 0),
                baseline_achievements=int(row["baseline_achievements"] or 0),
                rich_presence=row["rich_presence"],
                detection_method=row["detection_method"] or "presence_poll",
            )

    def list_active_telemetry_sessions(self, scope: UserScope) -> list[ActiveTelemetrySession]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute(
                "SELECT * FROM telemetry_active_sessions ORDER BY started_at DESC"
            ).fetchall()
            return [
                ActiveTelemetrySession(
                    provider=r["provider"],
                    external_title_id=r["external_title_id"],
                    game_id=int(r["game_id"]),
                    title=r["title"],
                    platform=r["platform"],
                    source=r["source"],
                    started_at=r["started_at"],
                    last_seen_at=r["last_seen_at"],
                    baseline_minutes=int(r["baseline_minutes"] or 0),
                    baseline_achievements=int(r["baseline_achievements"] or 0),
                    rich_presence=r["rich_presence"],
                    detection_method=r["detection_method"] or "presence_poll",
                )
                for r in rows
            ]

    def upsert_active_telemetry_session(
        self,
        scope: UserScope,
        *,
        provider: str,
        external_title_id: str,
        game_id: int,
        title: str,
        platform: str,
        source: str,
        started_at: str,
        last_seen_at: str,
        baseline_minutes: int,
        baseline_achievements: int,
        rich_presence: str | None = None,
        detection_method: str = "presence_poll",
    ) -> ActiveTelemetrySession:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO telemetry_active_sessions (
                    provider, external_title_id, game_id, title, platform, source,
                    started_at, last_seen_at, baseline_minutes, baseline_achievements,
                    rich_presence, detection_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    external_title_id = excluded.external_title_id,
                    game_id = excluded.game_id,
                    title = excluded.title,
                    platform = excluded.platform,
                    source = excluded.source,
                    started_at = excluded.started_at,
                    last_seen_at = excluded.last_seen_at,
                    baseline_minutes = excluded.baseline_minutes,
                    baseline_achievements = excluded.baseline_achievements,
                    rich_presence = excluded.rich_presence,
                    detection_method = excluded.detection_method
                """,
                (
                    provider,
                    external_title_id,
                    game_id,
                    title,
                    platform,
                    source,
                    started_at,
                    last_seen_at,
                    max(0, int(baseline_minutes)),
                    max(0, int(baseline_achievements)),
                    rich_presence,
                    detection_method,
                ),
            )
        return ActiveTelemetrySession(
            provider=provider,
            external_title_id=external_title_id,
            game_id=game_id,
            title=title,
            platform=platform,
            source=source,
            started_at=started_at,
            last_seen_at=last_seen_at,
            baseline_minutes=max(0, int(baseline_minutes)),
            baseline_achievements=max(0, int(baseline_achievements)),
            rich_presence=rich_presence,
            detection_method=detection_method,
        )

    def touch_active_telemetry_session(
        self,
        scope: UserScope,
        provider: str,
        *,
        last_seen_at: str,
        rich_presence: str | None = None,
    ) -> None:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                UPDATE telemetry_active_sessions
                SET last_seen_at = ?, rich_presence = COALESCE(?, rich_presence)
                WHERE provider = ?
                """,
                (last_seen_at, rich_presence, provider),
            )

    def delete_active_telemetry_session(self, scope: UserScope, provider: str) -> None:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute("DELETE FROM telemetry_active_sessions WHERE provider = ?", (provider,))

    def backfill_historical_sessions_from_achievements(
        self,
        scope: UserScope,
    ) -> AchievementBackfillResult:
        """Cluster unlocked achievement timestamps (<=180m gap) into coarse historical sessions idempotently."""
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            already_games = {
                int(r["game_id"])
                for r in conn.execute(
                    """
                    SELECT DISTINCT game_id FROM play_sessions WHERE source = 'achievement_backfill'
                    UNION
                    SELECT DISTINCT game_id FROM play_observations WHERE source = 'achievement_backfill'
                    """
                ).fetchall()
            }
            rows = conn.execute(
                """
                SELECT ga.game_id, ga.unlock_time, g.platform
                FROM game_achievements ga
                JOIN games g ON g.id = ga.game_id
                WHERE ga.unlocked = 1 AND ga.unlock_time IS NOT NULL AND ga.unlock_time != ''
                ORDER BY ga.game_id ASC, ga.unlock_time ASC
                """
            ).fetchall()

        by_game: dict[int, tuple[str, list[datetime]]] = {}
        for r in rows:
            gid = int(r["game_id"])
            if gid in already_games:
                continue
            try:
                dt = _parse_iso(str(r["unlock_time"]))
            except Exception:
                continue
            plat = str(r["platform"] or "PC")
            entry = by_game.get(gid)
            if entry is None:
                by_game[gid] = (plat, [dt])
            else:
                entry[1].append(dt)

        sessions_created = 0
        backfilled_games: set[int] = set()
        for gid, (plat, dts) in by_game.items():
            if not dts:
                continue
            dts.sort()
            clusters: list[list[datetime]] = [[dts[0]]]
            for nxt in dts[1:]:
                if (nxt - clusters[-1][-1]).total_seconds() / 60.0 <= 180.0:
                    clusters[-1].append(nxt)
                else:
                    clusters.append([nxt])

            for cl in clusters:
                started_dt = cl[0] - timedelta(minutes=30)
                ended_dt = cl[-1] + timedelta(minutes=15)
                dur_mins = max(15, int(round((ended_dt - started_dt).total_seconds() / 60.0)))
                started_iso = started_dt.isoformat()
                ended_iso = ended_dt.isoformat()
                idem_key = f"ach_backfill:{gid}:{started_iso}:{ended_iso}:{len(cl)}"
                outcome = self.record_interval_observation(
                    scope,
                    game_id=gid,
                    source="achievement_backfill",
                    platform=plat,
                    started_at=started_iso,
                    ended_at=ended_iso,
                    duration_minutes=dur_mins,
                    idempotency_key=idem_key,
                    precision_level="coarse",
                    achievements_unlocked=len(cl),
                    payload={"cluster_size": len(cl)},
                )
                if outcome.status == "session_created":
                    sessions_created += 1
                    backfilled_games.add(gid)

        return AchievementBackfillResult(
            sessions_created=sessions_created,
            games_backfilled=len(backfilled_games),
        )
