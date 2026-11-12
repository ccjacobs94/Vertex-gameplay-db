"""
Home Assistant History Puller and State Transition Parser.

Extracts granular video game play sessions from Home Assistant history
(e.g., sensor.<gamertag>_now_playing) and ingests them into Vertex's play_sessions table.
"""

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

import db

logger = logging.getLogger("gamehub.homeassistant_sync")

IGNORED_STATES = {"unknown", "unavailable", "none", "off", "idle", ""}


def _parse_iso(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        clean = ts_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_ha_state_entries(
    entries: List[Dict[str, Any]],
    max_gap_seconds: int = 1800,
) -> List[Dict[str, Any]]:
    """
    Parse a sequence of Home Assistant state changes (ordered chronologically)
    into contiguous play sessions, filtering out transient OAuth token refreshes
    ('unavailable' blips) and short dashboard pauses ('unknown' gaps <= max_gap_seconds).
    """
    spans: List[Dict[str, Any]] = []
    if not entries:
        return spans

    # Sort chronologically
    valid_entries = []
    for e in entries:
        st = str(e.get("state") or "").strip()
        ts = _parse_iso(str(e.get("last_changed") or e.get("last_updated") or ""))
        if ts:
            valid_entries.append({"state": st, "timestamp": ts})
    valid_entries.sort(key=lambda x: x["timestamp"])

    current_game: Optional[str] = None
    session_start: Optional[datetime] = None
    last_active_end: Optional[datetime] = None

    for i, item in enumerate(valid_entries):
        st = item["state"]
        ts = item["timestamp"]
        is_ignored = st.lower() in IGNORED_STATES

        if not is_ignored:
            if current_game is None:
                # Started playing a game
                current_game = st
                session_start = ts
                last_active_end = ts
            elif current_game.lower() == st.lower():
                # Still playing the same game (e.g. after a blip)
                last_active_end = ts
            else:
                # Switched directly to a different game!
                # Close the previous game session
                end_ts = ts
                dur_mins = max(1, int(round((end_ts - session_start).total_seconds() / 60.0)))
                spans.append({
                    "title": current_game,
                    "started_at": session_start.isoformat(),
                    "ended_at": end_ts.isoformat(),
                    "duration_minutes": dur_mins,
                })
                # Start new game session
                current_game = st
                session_start = ts
                last_active_end = ts
        else:
            # Ignored state (e.g. unknown, unavailable)
            if current_game is not None:
                # Check how long this inactive gap lasts
                # Peek at the next non-ignored state
                next_active = None
                for nxt in valid_entries[i + 1 :]:
                    if nxt["state"].lower() not in IGNORED_STATES:
                        next_active = nxt
                        break

                if next_active and next_active["state"].lower() == current_game.lower():
                    # Same game resumes later. Check gap duration.
                    gap_seconds = (next_active["timestamp"] - ts).total_seconds()
                    if gap_seconds <= max_gap_seconds:
                        # Short gap (e.g. 1-second token refresh or 5-minute dashboard break): keep session open!
                        continue

                # Game session has ended
                end_ts = ts
                dur_mins = max(1, int(round((end_ts - session_start).total_seconds() / 60.0)))
                spans.append({
                    "title": current_game,
                    "started_at": session_start.isoformat(),
                    "ended_at": end_ts.isoformat(),
                    "duration_minutes": dur_mins,
                })
                current_game = None
                session_start = None
                last_active_end = None

    # If still active at the end of the history stream
    if current_game is not None and session_start is not None and last_active_end is not None:
        end_ts = last_active_end
        dur_mins = max(1, int(round((end_ts - session_start).total_seconds() / 60.0)))
        if dur_mins >= 1:
            spans.append({
                "title": current_game,
                "started_at": session_start.isoformat(),
                "ended_at": end_ts.isoformat(),
                "duration_minutes": dur_mins,
            })

    return spans


def parse_ha_csv_to_spans(csv_content: str, max_gap_seconds: int = 1800) -> List[Dict[str, Any]]:
    """Parse CSV text exported from Home Assistant history into play session spans."""
    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    entries = []
    for row in reader:
        st = row.get("state")
        lc = row.get("last_changed") or row.get("last_updated")
        if st is not None and lc:
            entries.append({"state": st, "last_changed": lc})
    return parse_ha_state_entries(entries, max_gap_seconds=max_gap_seconds)


def ingest_ha_spans(
    spans: List[Dict[str, Any]],
    platform: str = "Xbox",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest parsed Home Assistant session spans into the user's games.db database."""
    imported_count = 0
    skipped_count = 0

    for span in spans:
        title = (span.get("title") or "").strip()
        if not title:
            continue

        start_iso = span["started_at"]
        end_iso = span["ended_at"]
        dur = int(span["duration_minutes"])

        with db.get_connection(db_path) as conn:
            game_row = conn.execute(
                "SELECT id, platform FROM games WHERE LOWER(title) = LOWER(?) ORDER BY id ASC LIMIT 1",
                (title,),
            ).fetchone()

        if game_row:
            game_id = game_row["id"]
            effective_platform = game_row["platform"] or platform
        else:
            new_game = db.add_manual_game(
                title=title,
                platform=platform,
                completion_status="Playing",
                db_path=db_path,
            )
            game_id = new_game["id"]
            effective_platform = platform

        with db.get_connection(db_path) as conn:
            # Check for existing duplicate session (same game, overlapping start/end within 2 minutes)
            dup = conn.execute(
                """
                SELECT id FROM play_sessions
                WHERE game_id = ?
                  AND (
                    started_at = ?
                    OR (started_at <= ? AND ended_at >= ?)
                  )
                LIMIT 1
                """,
                (game_id, start_iso, end_iso, start_iso),
            ).fetchone()

            if dup:
                skipped_count += 1
                continue

            conn.execute(
                """
                INSERT INTO play_sessions (
                    game_id, platform, started_at, ended_at, duration_minutes,
                    source, notes, achievements_unlocked, is_locked
                ) VALUES (?, ?, ?, ?, ?, 'home_assistant', NULL, 0, 0)
                """,
                (game_id, effective_platform, start_iso, end_iso, dur),
            )
            imported_count += 1

    return {
        "status": "ok",
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "total_spans": len(spans),
    }


async def fetch_ha_history(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Query Home Assistant REST API for entity state history."""
    clean_url = ha_url.rstrip("/")
    clean_token = ha_token.strip()
    clean_entity = entity_id.strip()

    if start_time is None:
        start_time = datetime.now(timezone.utc) - timedelta(days=7)

    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{clean_url}/api/history/period/{start_iso}"
    params = {"filter_entity_id": clean_entity}
    if end_time:
        params["end_time"] = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            # Home Assistant returns a list of lists: [[{state1}, {state2}...]]
            if isinstance(data[0], list):
                return data[0]
            return data
    return []


async def sync_homeassistant_history(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    platform: str = "Xbox",
    days_back: int = 7,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch and ingest Home Assistant history directly via REST API."""
    start_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
    entries = await fetch_ha_history(ha_url, ha_token, entity_id, start_time=start_dt)
    spans = parse_ha_state_entries(entries)
    res = ingest_ha_spans(spans, platform=platform, db_path=db_path)
    res["states_retrieved"] = len(entries)
    return res
