"""
SQLite Database layer for Unified Game Hub (Web UI + Auto-Sync + MCP Server).
Stores cross-platform game library data for Steam, Xbox, PlayStation, and Nintendo Switch.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_DB_PATH = os.environ.get(
    "GAMEHUB_DB_PATH",
    str(Path(__file__).resolve().parent / "data" / "games.db"),
)


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                platform TEXT NOT NULL,
                source TEXT NOT NULL,
                playtime_hours REAL DEFAULT 0.0,
                last_played TEXT,
                completion_status TEXT DEFAULT 'Not Played',
                status_locked INTEGER DEFAULT 0,
                achievements_unlocked INTEGER DEFAULT 0,
                achievements_total INTEGER DEFAULT 0,
                genres TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                critic_score INTEGER,
                user_rating INTEGER,
                release_year INTEGER,
                cover_url TEXT,
                notes TEXT DEFAULT '',
                favorite INTEGER DEFAULT 0,
                canonical_id INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_games_platform ON games(platform);
            CREATE INDEX IF NOT EXISTS idx_games_status ON games(completion_status);
            CREATE INDEX IF NOT EXISTS idx_games_playtime ON games(playtime_hours DESC);

            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                games_synced INTEGER DEFAULT 0,
                message TEXT,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reconcile_dismissals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair_key TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS game_achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                external_ach_id TEXT NOT NULL,
                source TEXT NOT NULL,
                platform TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                icon_url TEXT,
                icon_gray_url TEXT,
                unlocked INTEGER DEFAULT 0,
                unlock_time TEXT,
                reward_type TEXT,
                reward_value TEXT,
                rarity_pct REAL,
                is_hidden INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(game_id, external_ach_id),
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_game_achievements_game ON game_achievements(game_id);
            """
        )
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(games)").fetchall()
        }
        if "canonical_id" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN canonical_id INTEGER DEFAULT NULL;")
        if "tags" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN tags TEXT DEFAULT '[]';")
        if "achievements_synced_at" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN achievements_synced_at TEXT DEFAULT NULL;")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_canonical ON games(canonical_id);")


_ROMAN_MAP = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}

_STATUS_PRIORITY = {
    "Completed": 6,
    "Beaten": 5,
    "Playing": 4,
    "Plan to Play": 3,
    "Dropped": 2,
    "Not Played": 1,
}


def normalize_game_title(title: str) -> str:
    """
    Normalize a game title for cross-platform reconciliation matching.
    Strips trademark symbols, edition/remaster/platform suffixes, quotes, and normalizes Roman numerals.
    """
    t = (title or "").lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[™®©]", " ", t)
    t = re.sub(r"\bwaw\b", "world at war", t)
    t = re.sub(r"\btc's\b|\btcs\b", "tom clancy s", t)
    t = re.sub(r"\brainbowsix\b", "rainbow six", t)
    t = re.sub(
        r"\((?:classic|19\d\d|20\d\d|xbox.*?|pc.*?|windows.*?|steam.*?|mac.*?)\)",
        " ",
        t,
    )
    t = re.sub(
        r"\b(the complete first season|a telltale games series|full clip edition|system redux|"
        r"campaign remastered|enhanced edition|definitive edition|ultimate edition|complete edition|"
        r"collector s edition|collectors edition|anniversary edition|deluxe edition|standard edition|"
        r"game of the year edition|goty edition|director s cut|directors cut|edition|definitive|"
        r"remastered|rebrushed|redux|goty|game of the year|windows 10|for windows 10|xbox one|"
        r"xbox series x\|s|xbox series x s|pc|demo|trial)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s+-\s+multiplayer\b", " ", t)
    words = re.sub(r"[^a-z0-9]+", " ", t).strip().split()
    words = [_ROMAN_MAP.get(w, w) for w in words]
    return " ".join(words)


def normalize_achievement_text(text: str) -> str:
    """
    Normalize achievement titles or descriptions for cross-platform matching.
    Strips trademark/copyright symbols, quotes, punctuation, and collapses whitespace.
    """
    t = (text or "").lower()
    t = re.sub(r"[™®©]", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return re.sub(r"\s{2,}", " ", t)


def _clean_display_title(title: str) -> str:
    t = (title or "").strip().strip('"').strip("'")
    t = re.sub(r"[™®©]", "", t)
    t = re.sub(r"\s*-\s*Xbox Series X\|S\s*$", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip() or title


def _short_platform_label(platform: str) -> str:
    p = (platform or "").strip()
    mapping = {
        "PC (Steam)": "Steam",
        "PC (Xbox)": "PC (Xbox)",
        "Xbox / PC": "Xbox/PC",
        "Xbox": "Xbox",
        "PlayStation 4": "PS4",
        "PlayStation 5": "PS5",
        "Nintendo Switch": "Switch",
        "PC (Other)": "PC",
    }
    return mapping.get(p, p)


def _format_unified_platform(platforms: List[str]) -> str:
    if not platforms:
        return "Unknown"
    if len(platforms) == 1:
        return platforms[0]
    short_labels: List[str] = []
    for p in platforms:
        lbl = _short_platform_label(p)
        if lbl not in short_labels:
            short_labels.append(lbl)
    return " + ".join(short_labels)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["genres"] = json.loads(d.get("genres") or "[]")
    except Exception:
        d["genres"] = []
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except Exception:
        d["tags"] = []
    d["favorite"] = bool(d.get("favorite", 0))
    d["status_locked"] = bool(d.get("status_locked", 0))
    d["playtime_hours"] = round(float(d.get("playtime_hours") or 0.0), 1)
    d["canonical_id"] = d.get("canonical_id")
    d["achievements_synced_at"] = d.get("achievements_synced_at")
    d["is_reconciled"] = False
    d["linked_count"] = 1
    d["platforms"] = [d["platform"]]
    d["sources"] = [d["source"]]
    d["linked_games"] = [
        {
            "id": d["id"],
            "external_id": d["external_id"],
            "title": d["title"],
            "platform": d["platform"],
            "source": d["source"],
            "playtime_hours": d["playtime_hours"],
            "last_played": d.get("last_played"),
            "achievements_unlocked": d.get("achievements_unlocked") or 0,
            "achievements_total": d.get("achievements_total") or 0,
            "achievements_synced_at": d.get("achievements_synced_at"),
            "cover_url": d.get("cover_url"),
        }
    ]
    return d


def _consolidate_dicts(
    raw_games: List[Dict[str, Any]],
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """
    Consolidate raw game dicts sharing a canonical_id into unified cross-platform entries.
    Sums playtime_hours across all linked copies and aggregates platforms, achievements, and metadata.
    For achievements: computes non-overlapping deduplicated totals and unlocked counts across platforms.
    """
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for g in raw_games:
        cid = g.get("canonical_id")
        key = f"canon:{cid}" if cid is not None else f"single:{g['id']}"
        groups.setdefault(key, []).append(g)

    # Collect member IDs for multi-game groups to query individual achievements
    multi_member_ids = []
    for members in groups.values():
        if len(members) > 1:
            multi_member_ids.extend([m["id"] for m in members])

    ach_by_game: Dict[int, List[Dict[str, Any]]] = {}
    if multi_member_ids:
        try:
            with get_connection(db_path) as conn:
                placeholders = ",".join("?" for _ in multi_member_ids)
                rows = conn.execute(
                    f"SELECT game_id, name, description, unlocked, unlock_time FROM game_achievements WHERE game_id IN ({placeholders})",
                    tuple(multi_member_ids),
                ).fetchall()
                for r in rows:
                    ach_by_game.setdefault(r["game_id"], []).append(dict(r))
        except Exception:
            ach_by_game = {}

    consolidated: List[Dict[str, Any]] = []
    for key, members in groups.items():
        if len(members) == 1:
            single = dict(members[0])
            single["is_reconciled"] = False
            single["linked_count"] = 1
            single["platforms"] = [single["platform"]]
            single["sources"] = [single["source"]]
            single["linked_games"] = [
                {
                    "id": single["id"],
                    "external_id": single["external_id"],
                    "title": single["title"],
                    "platform": single["platform"],
                    "source": single["source"],
                    "playtime_hours": single["playtime_hours"],
                    "last_played": single.get("last_played"),
                    "achievements_unlocked": single.get("achievements_unlocked") or 0,
                    "achievements_total": single.get("achievements_total") or 0,
                    "achievements_synced_at": single.get("achievements_synced_at"),
                    "cover_url": single.get("cover_url"),
                }
            ]
            consolidated.append(single)
            continue

        # Sort members by playtime desc, then id asc
        members_sorted = sorted(
            members,
            key=lambda m: (float(m.get("playtime_hours") or 0.0), -(m.get("id") or 0)),
            reverse=True,
        )
        cid = members[0].get("canonical_id")
        primary = next((m for m in members if m["id"] == cid), members_sorted[0])
        unified = dict(primary)

        total_playtime = round(
            sum(float(m.get("playtime_hours") or 0.0) for m in members), 1
        )
        unified["playtime_hours"] = total_playtime

        platforms: List[str] = []
        sources: List[str] = []
        for m in members_sorted:
            if m["platform"] not in platforms:
                platforms.append(m["platform"])
            if m["source"] not in sources:
                sources.append(m["source"])

        unified["platforms"] = platforms
        unified["sources"] = sources
        unified["platform"] = _format_unified_platform(platforms)
        unified["source"] = " + ".join(sources)
        unified["is_reconciled"] = True
        unified["linked_count"] = len(members)

        # Last played: latest across all platforms
        last_played_vals = [m["last_played"] for m in members if m.get("last_played")]
        unified["last_played"] = max(last_played_vals) if last_played_vals else None

        # Achievements: compute cross-platform deduplicated union
        has_detailed_achs = any(m["id"] in ach_by_game for m in members)
        if has_detailed_achs:
            dedup_achs: Dict[str, Dict[str, Any]] = {}
            desc_to_key: Dict[str, str] = {}
            for m in members:
                for a in ach_by_game.get(m["id"], []):
                    name_key = normalize_achievement_text(a.get("name") or "")
                    desc_key = normalize_achievement_text(a.get("description") or "")
                    m_key = None
                    if name_key:
                        m_key = f"n:{name_key}"
                    elif desc_key and len(desc_key) >= 10:
                        m_key = f"d:{desc_key}"

                    if desc_key and len(desc_key) >= 15 and desc_key in desc_to_key:
                        m_key = desc_to_key[desc_key]
                    if not m_key:
                        continue
                    if desc_key and len(desc_key) >= 15 and desc_key not in desc_to_key:
                        desc_to_key[desc_key] = m_key

                    if m_key not in dedup_achs:
                        dedup_achs[m_key] = {
                            "unlocked": bool(a.get("unlocked")),
                            "unlock_time": a.get("unlock_time"),
                        }
                    else:
                        if a.get("unlocked"):
                            dedup_achs[m_key]["unlocked"] = True
                            if not dedup_achs[m_key]["unlock_time"] or (a.get("unlock_time") and a["unlock_time"] < dedup_achs[m_key]["unlock_time"]):
                                dedup_achs[m_key]["unlock_time"] = a["unlock_time"]

            unique_unlocked = sum(1 for a in dedup_achs.values() if a["unlocked"])
            unique_total = len(dedup_achs)
            summary_max_tot = max((int(m.get("achievements_total") or 0) for m in members), default=0)
            summary_max_unl = max((int(m.get("achievements_unlocked") or 0) for m in members), default=0)
            unified["achievements_unlocked"] = max(unique_unlocked, summary_max_unl)
            unified["achievements_total"] = max(unique_total, summary_max_tot)
        else:
            # Fallback when detailed achievement rows are not yet synced:
            # take the maximum across platforms so duplicate 80-achievement sets aren't doubled to 160
            unified["achievements_unlocked"] = max(
                (int(m.get("achievements_unlocked") or 0) for m in members), default=0
            )
            unified["achievements_total"] = max(
                (int(m.get("achievements_total") or 0) for m in members), default=0
            )

        # Cover URL: prefer primary, or Steam vertical cover, or any non-empty cover
        if not unified.get("cover_url"):
            steam_cover = next(
                (m["cover_url"] for m in members if m.get("cover_url") and "steam" in m["cover_url"]),
                None,
            )
            any_cover = next((m["cover_url"] for m in members if m.get("cover_url")), None)
            unified["cover_url"] = steam_cover or any_cover

        # Genres union
        merged_genres: List[str] = []
        for m in members:
            for gen in m.get("genres") or []:
                if gen not in merged_genres:
                    merged_genres.append(gen)
        unified["genres"] = merged_genres

        # Tags union
        merged_tags: List[str] = []
        for m in members:
            for tag in m.get("tags") or []:
                if tag not in merged_tags:
                    merged_tags.append(tag)
        unified["tags"] = merged_tags

        # Status, rating, favorite, critic_score, release_year
        unified["favorite"] = any(bool(m.get("favorite")) for m in members)
        unified["status_locked"] = any(bool(m.get("status_locked")) for m in members)
        ratings = [int(m["user_rating"]) for m in members if m.get("user_rating") is not None]
        if unified.get("user_rating") is None and ratings:
            unified["user_rating"] = max(ratings)
        scores = [int(m["critic_score"]) for m in members if m.get("critic_score") is not None]
        if unified.get("critic_score") is None and scores:
            unified["critic_score"] = max(scores)
        years = [int(m["release_year"]) for m in members if m.get("release_year") is not None]
        if unified.get("release_year") is None and years:
            unified["release_year"] = min(years)

        unified["linked_games"] = [
            {
                "id": m["id"],
                "external_id": m["external_id"],
                "title": m["title"],
                "platform": m["platform"],
                "source": m["source"],
                "playtime_hours": round(float(m.get("playtime_hours") or 0.0), 1),
                "last_played": m.get("last_played"),
                "achievements_unlocked": int(m.get("achievements_unlocked") or 0),
                "achievements_total": int(m.get("achievements_total") or 0),
                "achievements_synced_at": m.get("achievements_synced_at"),
                "cover_url": m.get("cover_url"),
            }
            for m in members_sorted
        ]
        consolidated.append(unified)

    return consolidated

    return consolidated


def upsert_synced_game(
    external_id: str,
    title: str,
    platform: str,
    source: str,
    playtime_hours: float = 0.0,
    last_played: Optional[str] = None,
    achievements_unlocked: int = 0,
    achievements_total: int = 0,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    critic_score: Optional[int] = None,
    release_year: Optional[int] = None,
    cover_url: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Insert or update a game from an automated sync source.
    Preserves user-modified fields (completion_status if status_locked=1, user_rating, notes, favorite).
    Syncs never overwrite genres or tags: a sync may fill them when
    empty, but manual genres/tags are preserved.
    """
    now = datetime.now(timezone.utc).isoformat()
    playtime_hours = round(float(playtime_hours or 0.0), 2)

    # Infer initial completion status when not manually locked by user
    inferred_status = "Not Played"
    if playtime_hours >= 0.5:
        inferred_status = "Playing"
    if achievements_total > 0 and achievements_unlocked >= achievements_total:
        inferred_status = "Completed"

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM games WHERE external_id = ?", (external_id,)
        ).fetchone()

        if existing is None:
            genres_json = json.dumps(genres or [])
            tags_json = json.dumps(tags or [])
            cursor = conn.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours,
                    last_played, completion_status, status_locked,
                    achievements_unlocked, achievements_total, genres, tags,
                    critic_score, release_year, cover_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_id,
                    title,
                    platform,
                    source,
                    playtime_hours,
                    last_played,
                    inferred_status,
                    achievements_unlocked,
                    achievements_total,
                    genres_json,
                    tags_json,
                    critic_score,
                    release_year,
                    cover_url,
                    now,
                    now,
                ),
            )
            game_id = cursor.lastrowid
        else:
            game_id = existing["id"]
            new_playtime = max(float(existing["playtime_hours"] or 0.0), playtime_hours)
            new_status = (
                existing["completion_status"]
                if existing["status_locked"]
                else (
                    inferred_status
                    if existing["completion_status"] == "Not Played" and new_playtime > 0
                    else existing["completion_status"]
                )
            )
            existing_genres = json.loads(existing["genres"] or "[]")
            # Merge, never replace: sync-discovered genres are added to
            # the manual list, existing entries are never dropped.
            merged_genres_list = list(existing_genres)
            for gen in genres or []:
                if gen not in merged_genres_list:
                    merged_genres_list.append(gen)
            merged_genres = json.dumps(merged_genres_list)

            existing_tags = json.loads(existing["tags"] or "[]")
            merged_tags_list = list(existing_tags)
            for tag in tags or []:
                if tag not in merged_tags_list:
                    merged_tags_list.append(tag)
            merged_tags = json.dumps(merged_tags_list)

            keep_custom_title = (
                existing["canonical_id"] is not None
                and existing["id"] == existing["canonical_id"]
            )
            effective_title = existing["title"] if keep_custom_title else title

            conn.execute(
                """
                UPDATE games SET
                    title = ?,
                    playtime_hours = ?,
                    last_played = COALESCE(?, last_played),
                    completion_status = ?,
                    achievements_unlocked = MAX(achievements_unlocked, ?),
                    achievements_total = MAX(achievements_total, ?),
                    genres = ?,
                    tags = ?,
                    critic_score = COALESCE(?, critic_score),
                    release_year = COALESCE(?, release_year),
                    cover_url = COALESCE(?, cover_url),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    effective_title,
                    new_playtime,
                    last_played,
                    new_status,
                    achievements_unlocked,
                    achievements_total,
                    merged_genres,
                    merged_tags,
                    critic_score,
                    release_year,
                    cover_url,
                    now,
                    game_id,
                ),
            )

        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return _row_to_dict(row)


def add_manual_game(
    title: str,
    platform: str,
    completion_status: str = "Not Played",
    playtime_hours: float = 0.0,
    user_rating: Optional[int] = None,
    critic_score: Optional[int] = None,
    release_year: Optional[int] = None,
    genres: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    cover_url: Optional[str] = None,
    notes: str = "",
    favorite: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
    plat_slug = "".join(c if c.isalnum() else "-" for c in platform.lower()).strip("-")
    external_id = f"manual:{plat_slug}:{slug}"

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO games (
                external_id, title, platform, source, playtime_hours,
                completion_status, status_locked, genres, tags, critic_score,
                user_rating, release_year, cover_url, notes, favorite,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'Manual', ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                title = excluded.title,
                playtime_hours = excluded.playtime_hours,
                completion_status = excluded.completion_status,
                status_locked = 1,
                genres = excluded.genres,
                tags = excluded.tags,
                critic_score = COALESCE(excluded.critic_score, games.critic_score),
                user_rating = COALESCE(excluded.user_rating, games.user_rating),
                release_year = COALESCE(excluded.release_year, games.release_year),
                cover_url = COALESCE(excluded.cover_url, games.cover_url),
                notes = excluded.notes,
                favorite = excluded.favorite,
                updated_at = excluded.updated_at
            """,
            (
                external_id,
                title,
                platform,
                round(float(playtime_hours or 0.0), 2),
                completion_status,
                json.dumps(genres or []),
                json.dumps(tags or []),
                critic_score,
                user_rating,
                release_year,
                cover_url,
                notes,
                1 if favorite else 0,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM games WHERE external_id = ?", (external_id,)
        ).fetchone()
        return _row_to_dict(row)


def update_game(
    game_id: int,
    updates: Dict[str, Any],
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    allowed = {
        "title",
        "platform",
        "playtime_hours",
        "completion_status",
        "achievements_unlocked",
        "achievements_total",
        "user_rating",
        "critic_score",
        "release_year",
        "cover_url",
        "notes",
        "favorite",
        "genres",
        "tags",
    }
    shared_group_keys = {
        "completion_status",
        "user_rating",
        "critic_score",
        "release_year",
        "notes",
        "favorite",
        "genres",
        "tags",
    }

    now = datetime.now(timezone.utc).isoformat()

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM games WHERE id = ?", (game_id,)
        ).fetchone()
        if not existing:
            return None

        cid = existing["canonical_id"]
        group_rows = (
            conn.execute(
                "SELECT * FROM games WHERE canonical_id = ?", (cid,)
            ).fetchall()
            if cid is not None
            else [existing]
        )
        is_multi = len(group_rows) > 1

        single_fields: List[str] = []
        single_values: List[Any] = []
        group_fields: List[str] = []
        group_values: List[Any] = []

        for k, v in updates.items():
            if k not in allowed:
                continue
            if k == "genres" and isinstance(v, list):
                v = json.dumps(v)
            elif k == "tags" and isinstance(v, list):
                v = json.dumps(v)
            elif k == "favorite":
                v = 1 if v else 0

            if is_multi and k == "playtime_hours":
                # Avoid inflating individual platform playtime when saving consolidated total
                other_sum = sum(
                    float(r["playtime_hours"] or 0.0)
                    for r in group_rows
                    if r["id"] != game_id
                )
                total_sum = other_sum + float(existing["playtime_hours"] or 0.0)
                new_val = float(v or 0.0)
                if abs(new_val - total_sum) >= 0.15:
                    adjusted = max(0.0, round(new_val - other_sum, 2))
                    single_fields.append("playtime_hours = ?")
                    single_values.append(adjusted)
                continue

            if is_multi and k in shared_group_keys:
                if k == "completion_status":
                    group_fields.append("status_locked = ?")
                    group_values.append(1)
                group_fields.append(f"{k} = ?")
                group_values.append(v)
            else:
                if k == "completion_status":
                    single_fields.append("status_locked = ?")
                    single_values.append(1)
                single_fields.append(f"{k} = ?")
                single_values.append(v)

        if single_fields:
            single_fields.append("updated_at = ?")
            single_values.append(now)
            single_values.append(game_id)
            conn.execute(
                f"UPDATE games SET {', '.join(single_fields)} WHERE id = ?",
                tuple(single_values),
            )

        if group_fields and cid is not None:
            group_fields.append("updated_at = ?")
            group_values.append(now)
            group_values.append(cid)
            conn.execute(
                f"UPDATE games SET {', '.join(group_fields)} WHERE canonical_id = ?",
                tuple(group_values),
            )

    return get_game_by_id(game_id, db_path)


def delete_game(game_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT canonical_id FROM games WHERE id = ?", (game_id,)).fetchone()
        cid = row["canonical_id"] if row else None
        cursor = conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
        deleted = cursor.rowcount > 0
        if deleted and cid is not None:
            remaining = conn.execute(
                "SELECT id FROM games WHERE canonical_id = ? ORDER BY playtime_hours DESC, id ASC",
                (cid,),
            ).fetchall()
            if len(remaining) == 1:
                conn.execute(
                    "UPDATE games SET canonical_id = NULL WHERE id = ?",
                    (remaining[0]["id"],),
                )
            elif len(remaining) > 1 and cid == game_id:
                new_cid = remaining[0]["id"]
                conn.execute(
                    "UPDATE games SET canonical_id = ? WHERE canonical_id = ?",
                    (new_cid, cid),
                )
        return deleted


def get_game_by_id(game_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if not row:
            return None
        cid = row["canonical_id"]
        if cid is not None:
            rows = conn.execute(
                "SELECT * FROM games WHERE canonical_id = ?", (cid,)
            ).fetchall()
            consolidated = _consolidate_dicts([_row_to_dict(r) for r in rows], db_path=db_path)
            return consolidated[0] if consolidated else _row_to_dict(row)
        return _row_to_dict(row)


def query_games(
    search: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    favorite_only: bool = False,
    sort_by: str = "playtime_desc",
    limit: int = 500,
    include_unconsolidated: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM games").fetchall()
        raw_dicts = [_row_to_dict(r) for r in rows]

    items = raw_dicts if include_unconsolidated else _consolidate_dicts(raw_dicts, db_path=db_path)

    if search:
        q = search.lower().strip()
        items = [
            g
            for g in items
            if q in (g.get("title") or "").lower()
            or q in (g.get("notes") or "").lower()
            or any(q in (lg.get("title") or "").lower() for lg in g.get("linked_games", []))
        ]

    if platform:
        p_q = platform.lower().strip()
        items = [
            g
            for g in items
            if p_q in (g.get("platform") or "").lower()
            or any(p_q in p.lower() for p in g.get("platforms", []))
            or any(p_q in s.lower() for s in g.get("sources", []))
        ]

    if status:
        s_q = status.lower().strip()
        items = [
            g
            for g in items
            if (g.get("completion_status") or "Not Played").lower() == s_q
        ]

    if genre:
        g_q = genre.lower().strip()
        items = [
            g
            for g in items
            if any(g_q in gen.lower() for gen in (g.get("genres") or []))
        ]

    if tag:
        t_q = tag.lower().strip()
        items = [
            g
            for g in items
            if any(t_q in t.lower() for t in (g.get("tags") or []))
            or any(t_q in gen.lower() for gen in (g.get("genres") or []))
        ]

    if favorite_only:
        items = [g for g in items if g.get("favorite")]

    if sort_by == "last_played_desc":
        items.sort(
            key=lambda g: (
                1 if g.get("last_played") else 0,
                g.get("last_played") or "",
                float(g.get("playtime_hours") or 0.0),
            ),
            reverse=True,
        )
    elif sort_by == "title_asc":
        items.sort(key=lambda g: (g.get("title") or "").lower())
    elif sort_by == "rating_desc":
        items.sort(
            key=lambda g: (
                int(g.get("user_rating") or 0) * 10 or int(g.get("critic_score") or 0),
                float(g.get("playtime_hours") or 0.0),
            ),
            reverse=True,
        )
    elif sort_by == "recent_added":
        items.sort(key=lambda g: g.get("updated_at") or "", reverse=True)
    elif sort_by == "achievements_desc":
        items.sort(
            key=lambda g: (
                int(g.get("achievements_unlocked") or 0),
                round((int(g.get("achievements_unlocked") or 0) / max(int(g.get("achievements_total") or 0), 1)) * 100, 1) if int(g.get("achievements_total") or 0) > 0 else 0.0,
                float(g.get("playtime_hours") or 0.0),
            ),
            reverse=True,
        )
    else:
        # Default: playtime_desc, then title_asc
        items.sort(key=lambda g: (g.get("title") or "").lower())
        items.sort(key=lambda g: float(g.get("playtime_hours") or 0.0), reverse=True)

    return items[:limit]


def _make_pair_key(id1: int, id2: int) -> str:
    return f"{min(id1, id2)}:{max(id1, id2)}"


def get_reconcile_candidates(
    cross_platform_only: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """
    Detect candidate groups of games across platforms or editions that appear to be the same game.
    Excludes groups where all items are already reconciled together or dismissed by the user.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM games").fetchall()
        raw_games = [_row_to_dict(r) for r in rows]
        dismissed_rows = conn.execute(
            "SELECT pair_key FROM reconcile_dismissals"
        ).fetchall()
        dismissed_pairs: Set[str] = {r["pair_key"] for r in dismissed_rows}

    by_norm: Dict[str, List[Dict[str, Any]]] = {}
    for g in raw_games:
        norm = normalize_game_title(g["title"])
        if norm:
            by_norm.setdefault(norm, []).append(g)

    candidates: List[Dict[str, Any]] = []
    for norm_key, group in by_norm.items():
        if len(group) < 2:
            continue

        # Check if all items in this group already share the same non-null canonical_id
        canon_ids = {
            g["canonical_id"] if g.get("canonical_id") is not None else f"unlinked_{g['id']}"
            for g in group
        }
        if len(canon_ids) < 2:
            continue

        # Filter out items where every pair involving an item to other distinct entities was dismissed
        # Keep items that have at least one non-dismissed, unlinked pair in the group
        active_members: List[Dict[str, Any]] = []
        for g in group:
            g_entity = g["canonical_id"] if g.get("canonical_id") is not None else f"u_{g['id']}"
            has_valid_partner = False
            for other in group:
                if other["id"] == g["id"]:
                    continue
                o_entity = (
                    other["canonical_id"]
                    if other.get("canonical_id") is not None
                    else f"u_{other['id']}"
                )
                if g_entity == o_entity:
                    continue
                if _make_pair_key(g["id"], other["id"]) not in dismissed_pairs:
                    has_valid_partner = True
                    break
            if has_valid_partner:
                active_members.append(g)

        active_entities = {
            m["canonical_id"] if m.get("canonical_id") is not None else f"u_{m['id']}"
            for m in active_members
        }
        if len(active_entities) < 2:
            continue

        platforms = []
        sources = []
        for m in active_members:
            if m["platform"] not in platforms:
                platforms.append(m["platform"])
            if m["source"] not in sources:
                sources.append(m["source"])

        is_cross_platform = len(platforms) > 1 or len(sources) > 1
        if cross_platform_only and not is_cross_platform:
            continue

        # Sort members by playtime desc, preferring Steam cover/clean title on tie
        members_sorted = sorted(
            active_members,
            key=lambda m: (
                1 if m.get("canonical_id") == m["id"] else 0,
                float(m.get("playtime_hours") or 0.0),
                1 if m.get("source") == "Steam" else 0,
            ),
            reverse=True,
        )

        # Pick cleanest title as suggested title
        cleaned_titles = [_clean_display_title(m["title"]) for m in members_sorted]
        # Prefer shortest clean title that isn't empty (avoids trailing edition bloat)
        suggested_title = min(cleaned_titles, key=lambda t: (len(t), t))

        # Unified status preview
        locked_members = [m for m in members_sorted if m.get("status_locked")]
        status_pool = locked_members if locked_members else members_sorted
        suggested_status = max(
            (m.get("completion_status") or "Not Played" for m in status_pool),
            key=lambda s: _STATUS_PRIORITY.get(s, 0),
        )

        combined_hours = round(
            sum(float(m.get("playtime_hours") or 0.0) for m in members_sorted), 1
        )

        candidates.append(
            {
                "group_key": norm_key,
                "suggested_title": suggested_title,
                "suggested_primary_id": members_sorted[0]["id"],
                "suggested_status": suggested_status,
                "combined_playtime_hours": combined_hours,
                "platforms": platforms,
                "unified_platform": _format_unified_platform(platforms),
                "is_cross_platform": is_cross_platform,
                "games": members_sorted,
            }
        )

    # Sort candidates: cross-platform first, then highest combined playtime, then title
    candidates.sort(
        key=lambda c: (
            1 if c["is_cross_platform"] else 0,
            float(c["combined_playtime_hours"]),
            c["suggested_title"].lower(),
        ),
        reverse=True,
    )
    return candidates


def reconcile_games(
    game_ids: List[int],
    primary_id: Optional[int] = None,
    custom_title: Optional[str] = None,
    completion_status: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """
    Link 2 or more games as the same game across platforms/editions, consolidating their playtime
    and unifying their completion status, rating, favorite flag, genres, and notes.
    """
    unique_ids = list(dict.fromkeys(int(x) for x in game_ids if x))
    if len(unique_ids) < 2:
        raise ValueError("At least 2 game IDs are required to reconcile.")

    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        placeholders = ",".join("?" for _ in unique_ids)
        initial_rows = conn.execute(
            f"SELECT * FROM games WHERE id IN ({placeholders})",
            tuple(unique_ids),
        ).fetchall()
        if len(initial_rows) < 2:
            raise ValueError("Could not find at least 2 matching games to reconcile.")

        # Also pull in any games that already share a canonical_id with any of the selected games
        existing_cids = {
            r["canonical_id"] for r in initial_rows if r["canonical_id"] is not None
        }
        all_ids = {r["id"] for r in initial_rows}
        if existing_cids:
            cid_placeholders = ",".join("?" for _ in existing_cids)
            extra_rows = conn.execute(
                f"SELECT * FROM games WHERE canonical_id IN ({cid_placeholders})",
                tuple(existing_cids),
            ).fetchall()
            for er in extra_rows:
                all_ids.add(er["id"])

        all_placeholders = ",".join("?" for _ in all_ids)
        rows = conn.execute(
            f"SELECT * FROM games WHERE id IN ({all_placeholders})",
            tuple(all_ids),
        ).fetchall()
        members = [_row_to_dict(r) for r in rows]

        # Determine primary_id
        if primary_id and any(m["id"] == primary_id for m in members):
            chosen_primary_id = int(primary_id)
        else:
            members_sorted = sorted(
                members,
                key=lambda m: (
                    1 if m.get("canonical_id") == m["id"] else 0,
                    float(m.get("playtime_hours") or 0.0),
                    1 if m.get("source") == "Steam" else 0,
                ),
                reverse=True,
            )
            chosen_primary_id = int(members_sorted[0]["id"])

        # Determine unified completion status (respecting locked statuses and progression rank)
        if completion_status:
            unified_status = completion_status
        else:
            locked = [m for m in members if m.get("status_locked")]
            pool = locked if locked else members
            unified_status = max(
                (m.get("completion_status") or "Not Played" for m in pool),
                key=lambda s: _STATUS_PRIORITY.get(s, 0),
            )

        # Unified rating, favorite, notes, genres, cover_url
        primary_member = next(m for m in members if m["id"] == chosen_primary_id)
        ratings = [int(m["user_rating"]) for m in members if m.get("user_rating") is not None]
        unified_rating = (
            primary_member["user_rating"]
            if primary_member.get("user_rating") is not None
            else (max(ratings) if ratings else None)
        )
        unified_fav = 1 if any(m.get("favorite") for m in members) else 0

        notes_parts: List[str] = []
        for m in members:
            n = (m.get("notes") or "").strip()
            if n and n not in notes_parts:
                notes_parts.append(n)
        unified_notes = " | ".join(notes_parts)

        merged_genres: List[str] = []
        for m in members:
            for g in m.get("genres") or []:
                if g not in merged_genres:
                    merged_genres.append(g)

        merged_tags: List[str] = []
        for m in members:
            for t in m.get("tags") or []:
                if t not in merged_tags:
                    merged_tags.append(t)

        steam_cover = next(
            (m["cover_url"] for m in members if m.get("cover_url") and "steam" in m["cover_url"]),
            None,
        )
        unified_cover = (
            primary_member.get("cover_url")
            or steam_cover
            or next((m["cover_url"] for m in members if m.get("cover_url")), None)
        )

        # Update all members in the group
        conn.execute(
            f"""
            UPDATE games SET
                canonical_id = ?,
                completion_status = ?,
                status_locked = 1,
                user_rating = COALESCE(?, user_rating),
                favorite = ?,
                notes = ?,
                genres = ?,
                tags = ?,
                updated_at = ?
            WHERE id IN ({all_placeholders})
            """,
            (
                chosen_primary_id,
                unified_status,
                unified_rating,
                unified_fav,
                unified_notes,
                json.dumps(merged_genres),
                json.dumps(merged_tags),
                now,
                *tuple(all_ids),
            ),
        )

        final_title = (
            custom_title.strip()
            if custom_title and custom_title.strip()
            else _clean_display_title(primary_member["title"])
        )
        conn.execute(
            "UPDATE games SET title = ?, cover_url = COALESCE(?, cover_url) WHERE id = ?",
            (final_title, unified_cover, chosen_primary_id),
        )

    return get_game_by_id(chosen_primary_id, db_path)


def unlink_game(game_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """
    Remove a single platform entry from its reconciled group so its playtime is tracked separately again.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if not row:
            return None
        cid = row["canonical_id"]
        if cid is None:
            return _row_to_dict(row)

        conn.execute(
            "UPDATE games SET canonical_id = NULL, updated_at = ? WHERE id = ?",
            (now, game_id),
        )

        remaining = conn.execute(
            "SELECT id FROM games WHERE canonical_id = ? ORDER BY playtime_hours DESC, id ASC",
            (cid,),
        ).fetchall()
        if len(remaining) == 1:
            conn.execute(
                "UPDATE games SET canonical_id = NULL, updated_at = ? WHERE id = ?",
                (now, remaining[0]["id"]),
            )
        elif len(remaining) > 1 and cid == game_id:
            new_cid = remaining[0]["id"]
            conn.execute(
                "UPDATE games SET canonical_id = ?, updated_at = ? WHERE canonical_id = ?",
                (new_cid, now, cid),
            )

    return get_game_by_id(game_id, db_path)


def dismiss_reconcile_group(game_ids: List[int], db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Record that a set of candidate games are NOT the same game so they won't be suggested together again.
    """
    unique_ids = sorted({int(x) for x in game_ids if x})
    if len(unique_ids) < 2:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with get_connection(db_path) as conn:
        for i in range(len(unique_ids)):
            for j in range(i + 1, len(unique_ids)):
                pkey = _make_pair_key(unique_ids[i], unique_ids[j])
                conn.execute(
                    "INSERT OR IGNORE INTO reconcile_dismissals (pair_key, created_at) VALUES (?, ?)",
                    (pkey, now),
                )
                count += 1
    return count


def reset_reconcile_dismissals(db_path: str = DEFAULT_DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM reconcile_dismissals")
        return cursor.rowcount


def log_sync_event(
    source: str,
    status: str,
    games_synced: int = 0,
    message: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_logs (source, status, games_synced, message, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source, status, games_synced, message, now),
        )


def get_library_stats(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    games = query_games(limit=10000, db_path=db_path)
    total_games = len(games)
    total_hours = round(sum(g["playtime_hours"] for g in games), 1)
    reconciled_groups_count = sum(1 for g in games if g.get("is_reconciled"))
    reconcile_candidates_count = len(get_reconcile_candidates(db_path=db_path))

    by_platform: Dict[str, Dict[str, Any]] = {}
    by_status: Dict[str, int] = {}
    genre_hours: Dict[str, float] = {}
    tag_hours: Dict[str, float] = {}

    for g in games:
        for lg in g.get("linked_games") or [g]:
            plat = lg["platform"]
            if plat not in by_platform:
                by_platform[plat] = {"count": 0, "hours": 0.0}
            by_platform[plat]["count"] += 1
            by_platform[plat]["hours"] = round(
                by_platform[plat]["hours"] + float(lg.get("playtime_hours") or 0.0), 1
            )

        st = g["completion_status"] or "Not Played"
        by_status[st] = by_status.get(st, 0) + 1

        for gen in g.get("genres") or []:
            genre_hours[gen] = round(genre_hours.get(gen, 0.0) + max(g["playtime_hours"], 0.5), 1)

        for tag in g.get("tags") or []:
            tag_hours[tag] = round(tag_hours.get(tag, 0.0) + max(g["playtime_hours"], 0.5), 1)

    top_genres = [
        {"genre": k, "weighted_hours": v}
        for k, v in sorted(genre_hours.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    all_genres = sorted(genre_hours.keys(), key=lambda g_name: g_name.lower())
    missing_genres_count = sum(1 for g in games if not (g.get("genres") or []))
    top_tags = [
        {"tag": k, "weighted_hours": v}
        for k, v in sorted(tag_hours.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    all_tags = sorted(tag_hours.keys(), key=lambda t_name: t_name.lower())

    top_played = sorted(games, key=lambda x: x["playtime_hours"], reverse=True)[:10]
    recently_played = [
        g for g in sorted(
            games,
            key=lambda x: x.get("last_played") or "",
            reverse=True,
        )
        if g.get("last_played")
    ][:8]

    with get_connection(db_path) as conn:
        sync_rows = conn.execute(
            """
            SELECT source, status, games_synced, message, synced_at
            FROM sync_logs
            WHERE id IN (SELECT MAX(id) FROM sync_logs GROUP BY source)
            ORDER BY synced_at DESC
            """
        ).fetchall()
        last_syncs = [dict(r) for r in sync_rows]

    backlog_count = sum(
        1 for g in games if g["completion_status"] in ("Not Played", "Plan to Play")
    )
    completed_count = sum(
        1 for g in games if g["completion_status"] in ("Beaten", "Completed")
    )

    return {
        "total_games": total_games,
        "total_playtime_hours": total_hours,
        "backlog_count": backlog_count,
        "completed_count": completed_count,
        "playing_count": by_status.get("Playing", 0),
        "reconciled_groups_count": reconciled_groups_count,
        "reconcile_candidates_count": reconcile_candidates_count,
        "missing_genres_count": missing_genres_count,
        "all_genres": all_genres,
        "all_tags": all_tags,
        "top_tags": top_tags,
        "platforms": by_platform,
        "statuses": by_status,
        "top_genres": top_genres,
        "top_played": [
            {
                "id": g["id"],
                "title": g["title"],
                "platform": g["platform"],
                "playtime_hours": g["playtime_hours"],
                "completion_status": g["completion_status"],
            }
            for g in top_played
        ],
        "recently_played": [
            {
                "id": g["id"],
                "title": g["title"],
                "platform": g["platform"],
                "playtime_hours": g["playtime_hours"],
                "last_played": g["last_played"],
            }
            for g in recently_played
        ],
        "last_syncs": last_syncs,
    }


def _platform_family(platform: str) -> str:
    p = (platform or "").lower()
    if "steam" in p:
        return "Steam"
    if "xbox" in p:
        return "Xbox"
    if "playstation" in p or "ps4" in p or "ps5" in p or "psn" in p:
        return "PlayStation"
    if "switch" in p or "nintendo" in p:
        return "Nintendo Switch"
    return platform or "Other"


def get_habits_analytics(
    search: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    genre: Optional[str] = None,
    tag: Optional[str] = None,
    favorite_only: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Compute comprehensive gaming habits analytics, system distributions, genre breakdowns,
    completion health, eras, and playtime commitment tiers for filtered library subsets.
    """
    games = query_games(
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        favorite_only=favorite_only,
        limit=10000,
        db_path=db_path,
    )
    total_games = len(games)
    total_hours = round(sum(float(g.get("playtime_hours") or 0.0) for g in games), 1)

    played_games = [g for g in games if float(g.get("playtime_hours") or 0.0) > 0]
    played_count = len(played_games)

    beaten_games = [g for g in games if g.get("completion_status") in ("Beaten", "Completed")]
    beaten_count = len(beaten_games)
    dropped_games = [g for g in games if g.get("completion_status") == "Dropped"]
    dropped_count = len(dropped_games)
    shelf_of_shame = [g for g in games if g.get("completion_status") in ("Not Played", "Plan to Play")]

    completion_rate = round((beaten_count / max(played_count, 1)) * 100, 1) if played_count > 0 else 0.0
    drop_rate = round((dropped_count / max(played_count, 1)) * 100, 1) if played_count > 0 else 0.0

    avg_hours = round(total_hours / max(total_games, 1), 1)
    avg_played_hours = round(total_hours / max(played_count, 1), 1) if played_count > 0 else 0.0

    beaten_hours_sum = sum(float(g.get("playtime_hours") or 0.0) for g in beaten_games)
    avg_beaten_hours = round(beaten_hours_sum / max(beaten_count, 1), 1) if beaten_count > 0 else 0.0

    dropped_hours_sum = sum(float(g.get("playtime_hours") or 0.0) for g in dropped_games)
    avg_dropped_hours = round(dropped_hours_sum / max(dropped_count, 1), 1) if dropped_count > 0 else 0.0

    # Platform family breakdown
    plat_stats: Dict[str, Dict[str, Any]] = {}
    for g in games:
        linked = g.get("linked_games") or [g]
        seen_families = set()
        for lg in linked:
            fam = _platform_family(lg.get("platform") or "")
            if fam not in plat_stats:
                plat_stats[fam] = {
                    "name": fam,
                    "hours": 0.0,
                    "count": 0,
                    "beaten_count": 0,
                    "top_game": None,
                    "top_game_hours": -1.0,
                }
            lg_hours = float(lg.get("playtime_hours") or 0.0)
            plat_stats[fam]["hours"] = round(plat_stats[fam]["hours"] + lg_hours, 1)
            if fam not in seen_families:
                plat_stats[fam]["count"] += 1
                seen_families.add(fam)
                if g.get("completion_status") in ("Beaten", "Completed"):
                    plat_stats[fam]["beaten_count"] += 1
            if lg_hours > plat_stats[fam]["top_game_hours"]:
                plat_stats[fam]["top_game_hours"] = lg_hours
                plat_stats[fam]["top_game"] = {
                    "id": g["id"],
                    "title": g["title"],
                    "hours": lg_hours,
                    "cover_url": g.get("cover_url"),
                }

    platform_list: List[Dict[str, Any]] = []
    for fam, st in sorted(plat_stats.items(), key=lambda x: x[1]["hours"], reverse=True):
        st["playtime_pct"] = round((st["hours"] / max(total_hours, 0.1)) * 100, 1)
        st["library_pct"] = round((st["count"] / max(total_games, 1)) * 100, 1)
        st["completion_rate"] = round((st["beaten_count"] / max(st["count"], 1)) * 100, 1)
        del st["top_game_hours"]
        platform_list.append(st)

    # Genre breakdown
    genre_data: Dict[str, Dict[str, Any]] = {}
    for g in games:
        g_hours = float(g.get("playtime_hours") or 0.0)
        st = g.get("completion_status") or "Not Played"
        for gen in (g.get("genres") or []):
            if gen not in genre_data:
                genre_data[gen] = {
                    "genre": gen,
                    "hours": 0.0,
                    "count": 0,
                    "beaten_count": 0,
                    "playing_count": 0,
                    "backlog_count": 0,
                    "dropped_count": 0,
                }
            genre_data[gen]["hours"] = round(genre_data[gen]["hours"] + g_hours, 1)
            genre_data[gen]["count"] += 1
            if st in ("Beaten", "Completed"):
                genre_data[gen]["beaten_count"] += 1
            elif st == "Playing":
                genre_data[gen]["playing_count"] += 1
            elif st == "Dropped":
                genre_data[gen]["dropped_count"] += 1
            else:
                genre_data[gen]["backlog_count"] += 1

    sorted_genres = sorted(genre_data.values(), key=lambda item: item["hours"], reverse=True)
    for gd in sorted_genres:
        gd["playtime_pct"] = round((gd["hours"] / max(total_hours, 0.1)) * 100, 1)
        gd["completion_rate"] = round((gd["beaten_count"] / max(gd["count"], 1)) * 100, 1)

    # Status breakdown
    statuses_data: Dict[str, Dict[str, Any]] = {}
    for st_name in ("Completed", "Beaten", "Playing", "Plan to Play", "Not Played", "Dropped"):
        matching = [g for g in games if (g.get("completion_status") or "Not Played") == st_name]
        st_hours = round(sum(float(g.get("playtime_hours") or 0.0) for g in matching), 1)
        statuses_data[st_name] = {
            "name": st_name,
            "count": len(matching),
            "count_pct": round((len(matching) / max(total_games, 1)) * 100, 1),
            "hours": st_hours,
            "hours_pct": round((st_hours / max(total_hours, 0.1)) * 100, 1),
        }

    # Commitment Tiers
    tiers: List[Dict[str, Any]] = [
        {"key": "bite", "label": "Bite-Sized (< 5 hrs)", "min": 0.0, "max": 5.0, "count": 0, "hours": 0.0},
        {"key": "medium", "label": "Medium (5–20 hrs)", "min": 5.0, "max": 20.0, "count": 0, "hours": 0.0},
        {"key": "deep", "label": "Deep Dives (20–50 hrs)", "min": 20.0, "max": 50.0, "count": 0, "hours": 0.0},
        {"key": "marathon", "label": "Marathons (50+ hrs)", "min": 50.0, "max": 999999.0, "count": 0, "hours": 0.0},
    ]
    for g in games:
        h = float(g.get("playtime_hours") or 0.0)
        for t in tiers:
            if t["min"] <= h < t["max"]:
                t["count"] += 1
                t["hours"] = round(t["hours"] + h, 1)
                break
    for t in tiers:
        t["hours_pct"] = round((t["hours"] / max(total_hours, 0.1)) * 100, 1)
        t["count_pct"] = round((t["count"] / max(total_games, 1)) * 100, 1)

    # Eras & Decades
    eras: List[Dict[str, Any]] = [
        {"key": "modern", "label": "Modern (2020+)", "count": 0, "hours": 0.0},
        {"key": "golden", "label": "2010s (2010–2019)", "count": 0, "hours": 0.0},
        {"key": "classics", "label": "2000s (2000–2009)", "count": 0, "hours": 0.0},
        {"key": "retro", "label": "Retro (Pre-2000)", "count": 0, "hours": 0.0},
        {"key": "unknown", "label": "Unknown Era", "count": 0, "hours": 0.0},
    ]
    era_map = {e["key"]: e for e in eras}
    for g in games:
        yr = g.get("release_year")
        h = float(g.get("playtime_hours") or 0.0)
        if not yr:
            era_map["unknown"]["count"] += 1
            era_map["unknown"]["hours"] = round(era_map["unknown"]["hours"] + h, 1)
        elif yr >= 2020:
            era_map["modern"]["count"] += 1
            era_map["modern"]["hours"] = round(era_map["modern"]["hours"] + h, 1)
        elif yr >= 2010:
            era_map["golden"]["count"] += 1
            era_map["golden"]["hours"] = round(era_map["golden"]["hours"] + h, 1)
        elif yr >= 2000:
            era_map["classics"]["count"] += 1
            era_map["classics"]["hours"] = round(era_map["classics"]["hours"] + h, 1)
        else:
            era_map["retro"]["count"] += 1
            era_map["retro"]["hours"] = round(era_map["retro"]["hours"] + h, 1)
    for e in eras:
        e["hours_pct"] = round((e["hours"] / max(total_hours, 0.1)) * 100, 1)
        e["count_pct"] = round((e["count"] / max(total_games, 1)) * 100, 1)

    # Ratings & gems
    ratings_with_val = [int(g["user_rating"]) for g in games if g.get("user_rating")]
    avg_user_rating = round(sum(ratings_with_val) / len(ratings_with_val), 1) if ratings_with_val else None

    critics_with_val = [int(g["critic_score"]) for g in games if g.get("critic_score")]
    avg_critic_score = round(sum(critics_with_val) / len(critics_with_val), 1) if critics_with_val else None

    backlog_candidates = [
        {
            "id": g["id"],
            "title": g["title"],
            "critic_score": g["critic_score"],
            "platform": g["platform"],
            "cover_url": g.get("cover_url"),
            "completion_status": g["completion_status"],
            "release_year": g.get("release_year"),
        }
        for g in games
        if g.get("completion_status") in ("Plan to Play", "Not Played") and g.get("critic_score")
    ]
    backlog_gems = sorted(backlog_candidates, key=lambda x: x["critic_score"], reverse=True)[:5]

    # Achievements
    ach_unlocked = sum(int(g.get("achievements_unlocked") or 0) for g in games)
    ach_total = sum(int(g.get("achievements_total") or 0) for g in games)
    perfect_count = sum(
        1
        for g in games
        if int(g.get("achievements_total") or 0) > 0
        and int(g.get("achievements_unlocked") or 0) >= int(g.get("achievements_total") or 0)
    )
    ach_rate = round((ach_unlocked / max(ach_total, 1)) * 100, 1) if ach_total > 0 else 0.0

    # Hall of fame (top played)
    hall_of_fame = [
        {
            "id": g["id"],
            "title": g["title"],
            "platform": g["platform"],
            "playtime_hours": float(g.get("playtime_hours") or 0.0),
            "completion_status": g["completion_status"],
            "cover_url": g.get("cover_url"),
            "release_year": g.get("release_year"),
            "user_rating": g.get("user_rating"),
        }
        for g in sorted(games, key=lambda x: float(x.get("playtime_hours") or 0.0), reverse=True)[:6]
    ]

    dominant_system = platform_list[0]["name"] if platform_list else "None"
    dominant_genre = sorted_genres[0]["genre"] if sorted_genres else "None"

    return {
        "summary": {
            "total_games": total_games,
            "total_hours": total_hours,
            "played_games": played_count,
            "average_hours_per_game": avg_hours,
            "average_hours_per_played_game": avg_played_hours,
            "completion_rate": completion_rate,
            "drop_rate": drop_rate,
            "beaten_count": beaten_count,
            "dropped_count": dropped_count,
            "shelf_of_shame_count": len(shelf_of_shame),
            "avg_beaten_hours": avg_beaten_hours,
            "avg_dropped_hours": avg_dropped_hours,
            "dominant_system": dominant_system,
            "dominant_genre": dominant_genre,
            "gaming_days": round(total_hours / 24.0, 1),
        },
        "platforms": platform_list,
        "genres": sorted_genres[:12],
        "all_genres_count": len(sorted_genres),
        "statuses": statuses_data,
        "commitments": tiers,
        "eras": eras,
        "achievements": {
            "unlocked": ach_unlocked,
            "total": ach_total,
            "rate": ach_rate,
            "perfect_games_count": perfect_count,
        },
        "ratings": {
            "avg_user_rating": avg_user_rating,
            "avg_critic_score": avg_critic_score,
            "rated_games_count": len(ratings_with_val),
            "backlog_gems": backlog_gems,
        },
        "hall_of_fame": hall_of_fame,
    }


def upsert_game_achievements(
    game_id: int,
    source: str,
    platform: str,
    achievements: List[Dict[str, Any]],
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Insert or update individual achievements for a game and update the summary counts on games.
    Preserves completion_status if status_locked = 1.
    """
    now = datetime.now(timezone.utc).isoformat()
    unlocked_count = 0
    total_count = len(achievements)

    with get_connection(db_path) as conn:
        for a in achievements:
            ext_id = str(a.get("external_ach_id") or a.get("id") or a.get("apiname") or "")
            name = str(a.get("name") or "").strip()
            desc = str(a.get("description") or "").strip()
            icon_url = a.get("icon_url")
            icon_gray_url = a.get("icon_gray_url")
            unlocked = 1 if a.get("unlocked") else 0
            if unlocked:
                unlocked_count += 1
            unlock_time = a.get("unlock_time")
            reward_type = a.get("reward_type")
            reward_value = str(a.get("reward_value")) if a.get("reward_value") is not None else None
            rarity_pct = float(a["rarity_pct"]) if a.get("rarity_pct") is not None else None
            is_hidden = 1 if a.get("is_hidden") else 0

            conn.execute(
                """
                INSERT INTO game_achievements (
                    game_id, external_ach_id, source, platform, name, description,
                    icon_url, icon_gray_url, unlocked, unlock_time, reward_type,
                    reward_value, rarity_pct, is_hidden, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, external_ach_id) DO UPDATE SET
                    name = excluded.name,
                    description = CASE WHEN excluded.description != '' THEN excluded.description ELSE game_achievements.description END,
                    icon_url = COALESCE(excluded.icon_url, game_achievements.icon_url),
                    icon_gray_url = COALESCE(excluded.icon_gray_url, game_achievements.icon_gray_url),
                    unlocked = excluded.unlocked,
                    unlock_time = COALESCE(excluded.unlock_time, game_achievements.unlock_time),
                    reward_type = COALESCE(excluded.reward_type, game_achievements.reward_type),
                    reward_value = COALESCE(excluded.reward_value, game_achievements.reward_value),
                    rarity_pct = COALESCE(excluded.rarity_pct, game_achievements.rarity_pct),
                    is_hidden = excluded.is_hidden,
                    updated_at = excluded.updated_at
                """,
                (
                    game_id, ext_id, source, platform, name, desc,
                    icon_url, icon_gray_url, unlocked, unlock_time, reward_type,
                    reward_value, rarity_pct, is_hidden, now,
                ),
            )

        existing = conn.execute("SELECT status_locked, completion_status FROM games WHERE id = ?", (game_id,)).fetchone()
        inferred_completion = None
        if existing and not existing["status_locked"]:
            if total_count > 0 and unlocked_count >= total_count:
                inferred_completion = "Completed"

        if inferred_completion:
            conn.execute(
                """
                UPDATE games SET
                    achievements_unlocked = ?,
                    achievements_total = ?,
                    achievements_synced_at = ?,
                    completion_status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (unlocked_count, total_count, now, inferred_completion, now, game_id),
            )
        else:
            conn.execute(
                """
                UPDATE games SET
                    achievements_unlocked = ?,
                    achievements_total = ?,
                    achievements_synced_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (unlocked_count, total_count, now, now, game_id),
            )

    return {"unlocked": unlocked_count, "total": total_count}


def get_game_achievements(game_id: int, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """
    Retrieve achievements for a game, merging across cross-platform linked copies if reconciled.
    Returns:
      - unified_summary: {"unlocked": int, "total": int, "percentage": float, "overlap_count": int}
      - per_platform_summary: list of {"game_id": int, "platform": str, "short_platform": str, "source": str, "unlocked": int, "total": int, "percentage": float}
      - achievements: list of deduplicated achievement items
    """
    with get_connection(db_path) as conn:
        target = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if not target:
            return {
                "unified_summary": {"unlocked": 0, "total": 0, "percentage": 0.0, "overlap_count": 0},
                "per_platform_summary": [],
                "achievements": [],
            }

        cid = target["canonical_id"]
        if cid is not None:
            members = [dict(r) for r in conn.execute(
                "SELECT * FROM games WHERE canonical_id = ? ORDER BY playtime_hours DESC, id ASC",
                (cid,)
            ).fetchall()]
        else:
            members = [dict(target)]

        member_ids = [m["id"] for m in members]
        placeholders = ",".join("?" for _ in member_ids)
        ach_rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM game_achievements WHERE game_id IN ({placeholders}) ORDER BY unlocked DESC, unlock_time DESC, name ASC",
            tuple(member_ids)
        ).fetchall()]

    # Build per_platform_summary
    per_platform_summary = []
    for m in members:
        unl = int(m.get("achievements_unlocked") or 0)
        tot = int(m.get("achievements_total") or 0)
        m_rows = [a for a in ach_rows if a["game_id"] == m["id"]]
        if m_rows:
            unl = sum(1 for a in m_rows if a["unlocked"])
            tot = len(m_rows)
        pct = round((unl / max(tot, 1)) * 100, 1) if tot > 0 else 0.0
        per_platform_summary.append({
            "game_id": m["id"],
            "title": m["title"],
            "platform": m["platform"],
            "short_platform": _short_platform_label(m["platform"]),
            "source": m["source"],
            "unlocked": unl,
            "total": tot,
            "percentage": pct,
            "achievements_synced_at": m.get("achievements_synced_at"),
        })

    # Group achievements across copies
    grouped_map: Dict[str, Dict[str, Any]] = {}
    desc_to_key: Dict[str, str] = {}

    for row in ach_rows:
        name_key = normalize_achievement_text(row.get("name") or "")
        desc_key = normalize_achievement_text(row.get("description") or "")

        match_key = None
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

        m_plat = row.get("platform") or ""
        short_plat = _short_platform_label(m_plat)

        plat_entry = {
            "game_id": row["game_id"],
            "platform": m_plat,
            "short_platform": short_plat,
            "source": row.get("source"),
            "unlocked": bool(row["unlocked"]),
            "unlock_time": row.get("unlock_time"),
            "reward_type": row.get("reward_type"),
            "reward_value": row.get("reward_value"),
            "rarity_pct": row.get("rarity_pct"),
        }

        if match_key not in grouped_map:
            grouped_map[match_key] = {
                "key": match_key,
                "name": row["name"],
                "description": row.get("description") or "",
                "icon_url": row.get("icon_url"),
                "icon_gray_url": row.get("icon_gray_url"),
                "is_hidden": bool(row.get("is_hidden")),
                "reward_type": row.get("reward_type"),
                "reward_value": row.get("reward_value"),
                "rarity_pct": row.get("rarity_pct"),
                "platforms": [plat_entry],
            }
        else:
            entry = grouped_map[match_key]
            if not entry["description"] and row.get("description"):
                entry["description"] = row["description"]
            if not entry["icon_url"] and row.get("icon_url"):
                entry["icon_url"] = row["icon_url"]
            if not entry["icon_gray_url"] and row.get("icon_gray_url"):
                entry["icon_gray_url"] = row["icon_gray_url"]
            if not entry["reward_type"] and row.get("reward_type"):
                entry["reward_type"] = row["reward_type"]
                entry["reward_value"] = row.get("reward_value")
            if entry["rarity_pct"] is None and row.get("rarity_pct") is not None:
                entry["rarity_pct"] = row["rarity_pct"]
            entry["platforms"].append(plat_entry)

    achievements_list = []
    overlap_count = 0
    for entry in grouped_map.values():
        unlocked_plats = [p["short_platform"] for p in entry["platforms"] if p["unlocked"]]
        is_unlocked = len(unlocked_plats) > 0
        if len(unlocked_plats) > 1:
            overlap_count += 1

        unlock_times = [p["unlock_time"] for p in entry["platforms"] if p.get("unlock_time")]
        earliest_unlock = min(unlock_times) if unlock_times else None

        entry["unlocked"] = is_unlocked
        entry["unlock_time"] = earliest_unlock
        entry["unlocked_platforms"] = unlocked_plats
        achievements_list.append(entry)

    achievements_list.sort(
        key=lambda a: (
            1 if a["unlocked"] else 0,
            a["unlock_time"] or "",
            (a["name"] or "").lower(),
        ),
        reverse=True,
    )

    unique_unlocked = sum(1 for a in achievements_list if a["unlocked"])
    unique_total = len(achievements_list)
    percentage = round((unique_unlocked / max(unique_total, 1)) * 100, 1) if unique_total > 0 else 0.0

    return {
        "unified_summary": {
            "unlocked": unique_unlocked,
            "total": unique_total,
            "percentage": percentage,
            "overlap_count": overlap_count,
        },
        "per_platform_summary": per_platform_summary,
        "achievements": achievements_list,
    }
