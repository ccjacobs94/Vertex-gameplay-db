"""Read-only SQLite data access for analytics read models (never mutates user history)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def fetch_library_snapshot_rows(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    """Fetch read-only library rows needed to compute analytics."""
    return {
        "games": conn.execute("SELECT * FROM games ORDER BY id ASC").fetchall(),
        "achievements": conn.execute("SELECT * FROM game_achievements").fetchall(),
        "sessions": conn.execute("SELECT * FROM play_sessions ORDER BY started_at DESC, id DESC").fetchall(),
        "prompts": conn.execute("SELECT game_id, detected_at FROM playtime_prompts").fetchall(),
        "ledger": conn.execute("SELECT * FROM daily_playtime_ledger ORDER BY date ASC").fetchall(),
    }


def parse_json_str_list(raw: Any) -> list[str]:
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
