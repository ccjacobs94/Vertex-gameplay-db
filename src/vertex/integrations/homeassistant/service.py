"""Authenticated Home Assistant event and CSV ingestion service feeding ActivityService."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from vertex.activity.service import ActivityService
from vertex.infrastructure.errors import ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.homeassistant.models import (
    HomeAssistantEventResult,
    HomeAssistantImportResult,
    HomeAssistantSpan,
)
from vertex.library.service import LibraryService

IGNORED_STATES = frozenset({"unknown", "unavailable", "none", "off", "idle", ""})


def _parse_iso(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        clean = str(ts_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_ha_state_entries(
    entries: list[dict[str, Any]],
    *,
    max_gap_seconds: int = 1800,
) -> list[HomeAssistantSpan]:
    """Parse chronologically ordered HA state entries into contiguous game play spans."""
    if not entries:
        return []

    valid_entries: list[tuple[str, datetime]] = []
    for e in entries:
        st = str(e.get("state") or "").strip()
        ts = _parse_iso(e.get("last_changed") or e.get("last_updated"))
        if ts is not None:
            valid_entries.append((st, ts))
    valid_entries.sort(key=lambda pair: pair[1])

    spans: list[HomeAssistantSpan] = []
    current_game: str | None = None
    session_start: datetime | None = None
    last_active_end: datetime | None = None

    for i, (st, ts) in enumerate(valid_entries):
        is_ignored = st.lower() in IGNORED_STATES
        if not is_ignored:
            if current_game is None:
                current_game = st
                session_start = ts
                last_active_end = ts
            elif current_game.lower() == st.lower():
                last_active_end = ts
            else:
                assert session_start is not None
                dur_mins = max(1, int(round((ts - session_start).total_seconds() / 60.0)))
                spans.append(
                    HomeAssistantSpan(
                        title=current_game,
                        started_at=session_start.isoformat(),
                        ended_at=ts.isoformat(),
                        duration_minutes=dur_mins,
                    )
                )
                current_game = st
                session_start = ts
                last_active_end = ts
        else:
            if current_game is not None and session_start is not None:
                next_active: tuple[str, datetime] | None = None
                for nxt in valid_entries[i + 1 :]:
                    if nxt[0].lower() not in IGNORED_STATES:
                        next_active = nxt
                        break
                if (
                    next_active is not None
                    and next_active[0].lower() == current_game.lower()
                    and (next_active[1] - ts).total_seconds() <= max_gap_seconds
                ):
                    continue

                dur_mins = max(1, int(round((ts - session_start).total_seconds() / 60.0)))
                spans.append(
                    HomeAssistantSpan(
                        title=current_game,
                        started_at=session_start.isoformat(),
                        ended_at=ts.isoformat(),
                        duration_minutes=dur_mins,
                    )
                )
                current_game = None
                session_start = None
                last_active_end = None

    if current_game is not None and session_start is not None and last_active_end is not None:
        if last_active_end > session_start:
            dur_mins = max(1, int(round((last_active_end - session_start).total_seconds() / 60.0)))
            spans.append(
                HomeAssistantSpan(
                    title=current_game,
                    started_at=session_start.isoformat(),
                    ended_at=last_active_end.isoformat(),
                    duration_minutes=dur_mins,
                )
            )

    return spans


def parse_ha_csv_to_spans(
    csv_content: str,
    *,
    max_gap_seconds: int = 1800,
) -> list[HomeAssistantSpan]:
    cleaned = (csv_content or "").strip()
    if not cleaned:
        raise ValidationError("CSV content cannot be empty")
    reader = csv.DictReader(io.StringIO(cleaned))
    if not reader.fieldnames or "state" not in reader.fieldnames:
        raise ValidationError("CSV must contain a 'state' column and 'last_changed' or 'last_updated'")
    if "last_changed" not in reader.fieldnames and "last_updated" not in reader.fieldnames:
        raise ValidationError("CSV must contain a 'last_changed' or 'last_updated' timestamp column")

    entries: list[dict[str, Any]] = []
    for row in reader:
        st = row.get("state")
        lc = row.get("last_changed") or row.get("last_updated")
        if st is not None and lc:
            entries.append({"state": st, "last_changed": lc})
    return parse_ha_state_entries(entries, max_gap_seconds=max_gap_seconds)


class HomeAssistantService:
    """Ingests Home Assistant CSV exports and authenticated webhook transition events."""

    _ACTIVE_PREF_KEY = "ha_active_sessions"

    def __init__(
        self,
        db_manager: DatabaseManager,
        library_service: LibraryService,
        activity_service: ActivityService,
    ) -> None:
        self.db_manager = db_manager
        self.library_service = library_service
        self.activity_service = activity_service

    def import_csv(
        self,
        scope: UserScope,
        *,
        csv_content: str,
        platform: str = "Xbox",
        max_gap_seconds: int = 1800,
    ) -> HomeAssistantImportResult:
        scope.require_active()
        spans = parse_ha_csv_to_spans(csv_content, max_gap_seconds=max_gap_seconds)
        imported = 0
        skipped = 0

        for span in spans:
            game, _ = self.library_service.find_or_create_by_title(
                scope,
                title=span.title,
                platform=platform,
                source="Home Assistant",
                completion_status="Playing",
                status_locked=False,
            )
            digest = hashlib.sha256(
                f"{game.id}|{span.started_at}|{span.ended_at}|{span.duration_minutes}".encode("utf-8")
            ).hexdigest()[:24]
            idem_key = f"ha_csv:{digest}"
            outcome = self.activity_service.record_interval_observation(
                scope,
                game_id=game.id,
                source="homeassistant",
                platform=game.platform or platform,
                started_at=span.started_at,
                ended_at=span.ended_at,
                duration_minutes=span.duration_minutes,
                idempotency_key=idem_key,
                precision_level="exact",
                payload={"import_kind": "csv", "title": span.title},
            )
            if outcome.status == "session_created":
                imported += 1
            else:
                skipped += 1

        return HomeAssistantImportResult(
            status="ok",
            imported_count=imported,
            skipped_count=skipped,
            total_spans=len(spans),
        )

    def _load_active_sessions(self, scope: UserScope) -> dict[str, dict[str, Any]]:
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute(
                "SELECT value_json FROM user_preferences WHERE key = ?",
                (self._ACTIVE_PREF_KEY,),
            ).fetchone()
            if row is None:
                return {}
            try:
                loaded = json.loads(row["value_json"] or "{}")
                return loaded if isinstance(loaded, dict) else {}
            except Exception:
                return {}

    def _save_active_sessions(self, scope: UserScope, active_map: dict[str, dict[str, Any]]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (self._ACTIVE_PREF_KEY, json.dumps(active_map), now_iso),
            )

    def ingest_event(
        self,
        scope: UserScope,
        *,
        event: str,
        title: str | None = None,
        platform: str | None = None,
        external_id: str | None = None,
        source: str = "homeassistant",
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_minutes: int | None = None,
        active_minutes: float | None = None,
        idle_minutes: float | None = None,
        wall_clock_minutes: float | None = None,
        achievements_unlocked: int = 0,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> HomeAssistantEventResult:
        scope.require_active()
        ev = (event or "").strip().lower()
        if ev not in ("start", "stop", "record"):
            raise ValidationError("event must be 'start', 'stop', or 'record'")

        eff_platform = (platform or "").strip() or "PC"
        eff_source = (source or "").strip() or "homeassistant"
        active_map = self._load_active_sessions(scope)

        # Resolve game if title is provided, else match active session/external_id.
        game_id: int | None = None
        if title and title.strip():
            game, _ = self.library_service.find_or_create_by_title(
                scope,
                title=title.strip(),
                platform=eff_platform,
                source="Home Assistant",
                completion_status="Playing",
                status_locked=False,
            )
            game_id = game.id
            eff_platform = game.platform or eff_platform
        elif external_id and external_id.strip() in active_map:
            game_id = int(active_map[external_id.strip()]["game_id"])
            eff_platform = str(active_map[external_id.strip()].get("platform") or eff_platform)

        if game_id is None:
            raise ValidationError("Game title (or an active external_id) is required")

        session_key = (external_id or "").strip() or f"game:{game_id}"

        if ev == "start":
            start_dt = _parse_iso(started_at) if started_at else datetime.now(timezone.utc)
            if start_dt is None:
                raise ValidationError(f"Invalid started_at timestamp: '{started_at}'")
            start_iso = start_dt.isoformat()
            active_map[session_key] = {
                "game_id": game_id,
                "platform": eff_platform,
                "started_at": start_iso,
                "source": eff_source,
            }
            self._save_active_sessions(scope, active_map)
            return HomeAssistantEventResult(
                status="started",
                game_id=game_id,
                session_id=None,
                duration_minutes=0,
                idempotency_key=idempotency_key or f"ha_start:{session_key}:{start_iso}",
            )

        # stop or record
        matched_active = active_map.pop(session_key, None) if ev == "stop" else None
        if ev == "stop" and matched_active is None:
            for k, val in list(active_map.items()):
                if int(val.get("game_id", -1)) == game_id:
                    matched_active = active_map.pop(k)
                    break
        if matched_active is not None:
            self._save_active_sessions(scope, active_map)

        end_dt = _parse_iso(ended_at) if ended_at else datetime.now(timezone.utc)
        if end_dt is None:
            raise ValidationError(f"Invalid ended_at timestamp: '{ended_at}'")

        start_dt = _parse_iso(started_at) if started_at else None
        if start_dt is None and matched_active is not None:
            start_dt = _parse_iso(matched_active.get("started_at"))

        if duration_minutes is not None:
            dur = int(duration_minutes)
        elif active_minutes is not None:
            dur = max(1, int(round(float(active_minutes))))
        elif wall_clock_minutes is not None:
            dur = max(1, int(round(float(wall_clock_minutes))))
        elif start_dt is not None:
            dur = max(1, int(round((end_dt - start_dt).total_seconds() / 60.0)))
        else:
            raise ValidationError(
                "Provide started_at or duration_minutes/active_minutes/wall_clock_minutes for stop/record events"
            )

        if dur <= 0:
            raise ValidationError("Session duration must be positive")
        if start_dt is None:
            start_dt = end_dt - timedelta(minutes=dur)
        if end_dt < start_dt:
            raise ValidationError("ended_at cannot be earlier than started_at")

        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()
        act_mins = float(active_minutes) if active_minutes is not None else float(dur)
        idle_mins = (
            float(idle_minutes)
            if idle_minutes is not None
            else (
                max(0.0, float(wall_clock_minutes) - act_mins)
                if wall_clock_minutes is not None
                else 0.0
            )
        )

        idem = (idempotency_key or "").strip()
        if not idem:
            digest = hashlib.sha256(
                f"{ev}|{game_id}|{start_iso}|{end_iso}|{dur}".encode("utf-8")
            ).hexdigest()[:24]
            idem = f"ha_evt:{digest}"

        outcome = self.activity_service.record_interval_observation(
            scope,
            game_id=game_id,
            source=eff_source,
            platform=eff_platform,
            started_at=start_iso,
            ended_at=end_iso,
            duration_minutes=dur,
            idempotency_key=idem,
            precision_level="exact",
            notes=notes,
            achievements_unlocked=achievements_unlocked,
            active_minutes=act_mins,
            idle_minutes=idle_mins,
            external_session_ref=external_id,
            payload={"event": ev},
        )
        return HomeAssistantEventResult(
            status=outcome.status,
            game_id=game_id,
            session_id=outcome.session.id if outcome.session else None,
            duration_minutes=dur,
            idempotency_key=idem,
        )
