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

from context import current_user_id

DEFAULT_DB_PATH = os.environ.get(
    "GAMEHUB_DB_PATH",
    str(Path(__file__).resolve().parent / "data" / "games.db"),
)
USERS_DB_DIR = os.environ.get("GAMEHUB_USERS_DIR", "/app/data/users")


def is_multiuser_mode() -> bool:
    try:
        import system_db
        edition = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
        if edition == "hosted":
            return True
        reg_policy = system_db.get_setting(
            "registration_policy", os.environ.get("VERTEX_REGISTRATION", "disabled")
        )
        if reg_policy != "disabled":
            return True
        return system_db.count_active_users() > 1
    except Exception:
        return False


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    if db_path is not None:
        resolved_path = db_path
    else:
        user_id = current_user_id.get()
        if user_id:
            resolved_path = str(Path(USERS_DB_DIR) / str(user_id) / "games.db")
        else:
            if is_multiuser_mode():
                raise RuntimeError("Database access denied: user context is required in multi-user mode")
            resolved_path = DEFAULT_DB_PATH
    path = Path(resolved_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
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

            CREATE TABLE IF NOT EXISTS playtime_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL UNIQUE,
                previous_hours REAL NOT NULL,
                new_hours REAL NOT NULL,
                delta_hours REAL NOT NULL,
                current_status TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS canonical_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_title TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                release_year INTEGER,
                developer TEXT DEFAULT '',
                publisher TEXT DEFAULT '',
                cover_url TEXT,
                genres TEXT DEFAULT '[]',
                critic_score INTEGER,
                library_game_id INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(library_game_id) REFERENCES games(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_canonical_games_norm ON canonical_games(normalized_title);
            CREATE INDEX IF NOT EXISTS idx_canonical_games_lib ON canonical_games(library_game_id);

            CREATE TABLE IF NOT EXISTS game_editions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_game_id INTEGER NOT NULL,
                edition_name TEXT NOT NULL,
                edition_tier TEXT NOT NULL DEFAULT 'base',
                tier_rank INTEGER NOT NULL DEFAULT 1,
                is_base_edition INTEGER NOT NULL DEFAULT 0,
                included_dlc_notes TEXT DEFAULT '',
                cover_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(canonical_game_id, edition_name),
                FOREIGN KEY(canonical_game_id) REFERENCES canonical_games(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_game_editions_canonical ON game_editions(canonical_game_id);

            CREATE TABLE IF NOT EXISTS edition_platform_skus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edition_id INTEGER NOT NULL,
                platform_family TEXT NOT NULL,
                platform_label TEXT NOT NULL,
                storefront TEXT NOT NULL,
                store_sku_id TEXT NOT NULL,
                itad_plain TEXT DEFAULT NULL,
                store_url TEXT DEFAULT '',
                msrp_usd REAL DEFAULT NULL,
                current_price_usd REAL DEFAULT NULL,
                regular_price_usd REAL DEFAULT NULL,
                discount_pct INTEGER DEFAULT 0,
                historical_low_usd REAL DEFAULT NULL,
                is_authorized_seller INTEGER NOT NULL DEFAULT 1,
                is_drm_free INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(edition_id, storefront, store_sku_id),
                FOREIGN KEY(edition_id) REFERENCES game_editions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_skus_edition ON edition_platform_skus(edition_id);
            CREATE INDEX IF NOT EXISTS idx_skus_platform_family ON edition_platform_skus(platform_family);
            CREATE INDEX IF NOT EXISTS idx_skus_storefront_sku ON edition_platform_skus(storefront, store_sku_id);

            CREATE TABLE IF NOT EXISTS wishlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_game_id INTEGER NOT NULL UNIQUE,
                preferred_edition_id INTEGER DEFAULT NULL,
                allow_smart_upgrade INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT DEFAULT '',
                added_via TEXT NOT NULL DEFAULT 'web',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(canonical_game_id) REFERENCES canonical_games(id) ON DELETE CASCADE,
                FOREIGN KEY(preferred_edition_id) REFERENCES game_editions(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wishlist_status ON wishlist_items(status);

            CREATE TABLE IF NOT EXISTS wishlist_platform_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wishlist_item_id INTEGER NOT NULL,
                platform_family TEXT NOT NULL,
                allowed_storefronts TEXT DEFAULT '[]',
                target_price_usd REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                muted_owned_conflict INTEGER NOT NULL DEFAULT 0,
                last_alerted_at TEXT DEFAULT NULL,
                last_alerted_price_usd REAL DEFAULT NULL,
                last_alerted_edition_id INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(wishlist_item_id, platform_family),
                FOREIGN KEY(wishlist_item_id) REFERENCES wishlist_items(id) ON DELETE CASCADE,
                FOREIGN KEY(last_alerted_edition_id) REFERENCES game_editions(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wpt_wishlist_item ON wishlist_platform_targets(wishlist_item_id);

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku_id INTEGER NOT NULL,
                storefront TEXT NOT NULL,
                price_usd REAL NOT NULL,
                regular_price_usd REAL DEFAULT NULL,
                discount_pct INTEGER DEFAULT 0,
                is_all_time_low INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'daily_sync',
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(sku_id) REFERENCES edition_platform_skus(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_price_history_sku_time ON price_history(sku_id, recorded_at DESC);

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh_key TEXT NOT NULL,
                auth_key TEXT NOT NULL,
                user_agent TEXT DEFAULT '',
                device_label TEXT DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used_at TEXT DEFAULT NULL
            );
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
        if "playtime_prompt_muted" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN playtime_prompt_muted INTEGER DEFAULT 0;")
        if "ownership_status" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN ownership_status TEXT DEFAULT 'Owned';")
        if "ownership_locked" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN ownership_locked INTEGER DEFAULT 0;")
        if "is_game_pass" not in cols:
            conn.execute("ALTER TABLE games ADD COLUMN is_game_pass INTEGER DEFAULT 0;")
            # Migrate any legacy 'Game Pass' ownership_status into is_game_pass = 1 and ownership_status = 'Subscription'
            conn.execute("UPDATE games SET is_game_pass = 1 WHERE ownership_status = 'Game Pass';")
            conn.execute("UPDATE games SET ownership_status = 'Subscription' WHERE ownership_status = 'Game Pass';")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_canonical ON games(canonical_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_ownership ON games(ownership_status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_is_game_pass ON games(is_game_pass);")


OWNERSHIP_STATUSES = ("Owned", "Subscription", "Borrowed", "None")

_OWNERSHIP_MAP = {
    "owned": "Owned",
    "own": "Owned",
    "purchased": "Owned",
    "bought": "Owned",
    "subscription": "Subscription",
    "sub": "Subscription",
    "game pass": "Subscription",
    "gamepass": "Subscription",
    "xbox game pass": "Subscription",
    "pc game pass": "Subscription",
    "xgp": "Subscription",
    "ps plus": "Subscription",
    "ps+": "Subscription",
    "playstation plus": "Subscription",
    "ea play": "Subscription",
    "ubisoft+": "Subscription",
    "borrowed": "Borrowed",
    "borrow": "Borrowed",
    "loaned": "Borrowed",
    "none": "None",
    "unowned": "None",
    "not owned": "None",
    "other": "None",
}


def normalize_ownership_status(val: Optional[str]) -> str:
    if not val or not isinstance(val, str):
        return "Owned"
    cleaned = val.strip()
    if cleaned in OWNERSHIP_STATUSES:
        return cleaned
    return _OWNERSHIP_MAP.get(cleaned.lower(), "Owned")


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
    d["ownership_status"] = normalize_ownership_status(d.get("ownership_status"))
    d["ownership_locked"] = bool(d.get("ownership_locked", 0))
    d["is_game_pass"] = bool(d.get("is_game_pass", 0))
    d["ownership_statuses"] = [d["ownership_status"]]
    d["has_game_pass_copy"] = d["is_game_pass"]
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
            "ownership_status": d["ownership_status"],
            "ownership_locked": d["ownership_locked"],
            "is_game_pass": d["is_game_pass"],
            "cover_url": d.get("cover_url"),
        }
    ]
    return d


def _consolidate_dicts(
    raw_games: List[Dict[str, Any]],
    db_path: Optional[str] = None,
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
            own_st = normalize_ownership_status(single.get("ownership_status"))
            single["ownership_status"] = own_st
            single["ownership_locked"] = bool(single.get("ownership_locked", 0))
            single["ownership_statuses"] = [own_st]
            single["has_game_pass_copy"] = own_st == "Game Pass"
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
                    "ownership_status": own_st,
                    "ownership_locked": single["ownership_locked"],
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
        if not unified.get("cover_url"):
            unified["cover_url"] = next(
                (m["cover_url"] for m in members_sorted if m.get("cover_url")),
                None,
            )

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

        # Game Pass: true if ANY linked copy is on Game Pass
        unified["is_game_pass"] = any(bool(m.get("is_game_pass")) for m in members)
        unified["has_game_pass_copy"] = unified["is_game_pass"]

        # Ownership: if owned on at least one linked platform, unified game counts as 'Owned'
        member_ownerships: List[str] = []
        for m in members_sorted:
            ost = normalize_ownership_status(m.get("ownership_status"))
            if ost not in member_ownerships:
                member_ownerships.append(ost)
        if "Owned" in member_ownerships:
            unified["ownership_status"] = "Owned"
        elif "Subscription" in member_ownerships:
            unified["ownership_status"] = "Subscription"
        elif "Borrowed" in member_ownerships:
            unified["ownership_status"] = "Borrowed"
        else:
            unified["ownership_status"] = member_ownerships[0] if member_ownerships else "Owned"
        unified["ownership_statuses"] = member_ownerships
        unified["ownership_locked"] = any(bool(m.get("ownership_locked")) for m in members)

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
                "ownership_status": normalize_ownership_status(m.get("ownership_status")),
                "ownership_locked": bool(m.get("ownership_locked", 0)),
                "is_game_pass": bool(m.get("is_game_pass", 0)),
                "cover_url": m.get("cover_url"),
            }
            for m in members_sorted
        ]
        consolidated.append(unified)

    return consolidated


PLAYTIME_PROMPT_THRESHOLD_HOURS = 0.5


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
    ownership_status: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
    db_path: Optional[str] = None,
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

        norm_incoming_ownership = (
            normalize_ownership_status(ownership_status)
            if ownership_status is not None
            else "Owned"
        )
        is_gp_val = 1 if is_game_pass else 0

        if existing is None:
            genres_json = json.dumps(genres or [])
            tags_json = json.dumps(tags or [])
            cursor = conn.execute(
                """
                INSERT INTO games (
                    external_id, title, platform, source, playtime_hours,
                    last_played, completion_status, status_locked,
                    ownership_status, ownership_locked, is_game_pass,
                    achievements_unlocked, achievements_total, genres, tags,
                    critic_score, release_year, cover_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_id,
                    title,
                    platform,
                    source,
                    playtime_hours,
                    last_played,
                    inferred_status,
                    norm_incoming_ownership,
                    is_gp_val,
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
            old_playtime = round(float(existing["playtime_hours"] or 0.0), 2)
            new_playtime = max(old_playtime, playtime_hours)
            delta_hours = round(new_playtime - old_playtime, 2)

            # Preserve existing status on sync updates; prompt the user when playtime increases >= 0.5h
            new_status = existing["completion_status"]
            if (
                not existing["status_locked"]
                and achievements_total > 0
                and achievements_unlocked >= achievements_total
            ):
                new_status = "Completed"

            # Self-reported ownership rule: sync never overwrites existing ownership_status
            # unless an explicit non-None ownership_status is provided and ownership is not locked.
            existing_ownership = normalize_ownership_status(existing["ownership_status"])
            if bool(existing["ownership_locked"]):
                new_ownership = existing_ownership
            elif ownership_status is not None:
                new_ownership = norm_incoming_ownership
            else:
                new_ownership = existing_ownership

            # Game Pass catalog status updates dynamically with sync unless None
            if is_game_pass is not None:
                new_is_gp = 1 if is_game_pass else 0
            else:
                new_is_gp = int(existing["is_game_pass"] or 0)

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
                    ownership_status = ?,
                    is_game_pass = ?,
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
                    new_ownership,
                    new_is_gp,
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

            if delta_hours >= PLAYTIME_PROMPT_THRESHOLD_HOURS and not bool(existing["playtime_prompt_muted"]):
                target_id = existing["canonical_id"] if existing["canonical_id"] is not None else game_id
                target_row = conn.execute(
                    "SELECT id, completion_status, playtime_prompt_muted FROM games WHERE id = ?",
                    (target_id,),
                ).fetchone()
                if (
                    target_row
                    and (target_row["completion_status"] or "Not Played") != "Playing"
                    and not bool(target_row["playtime_prompt_muted"])
                ):
                    conn.execute(
                        """
                        INSERT INTO playtime_prompts (
                            game_id, previous_hours, new_hours, delta_hours, current_status, detected_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(game_id) DO UPDATE SET
                            new_hours = excluded.new_hours,
                            delta_hours = ROUND(playtime_prompts.delta_hours + excluded.delta_hours, 2),
                            current_status = excluded.current_status,
                            detected_at = excluded.detected_at
                        """,
                        (
                            target_id,
                            old_playtime,
                            new_playtime,
                            delta_hours,
                            target_row["completion_status"] or "Not Played",
                            now,
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
    ownership_status: str = "Owned",
    is_game_pass: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
    plat_slug = "".join(c if c.isalnum() else "-" for c in platform.lower()).strip("-")
    external_id = f"manual:{plat_slug}:{slug}"
    norm_ownership = normalize_ownership_status(ownership_status)
    is_gp_val = 1 if is_game_pass else 0

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO games (
                external_id, title, platform, source, playtime_hours,
                completion_status, status_locked, ownership_status, ownership_locked,
                is_game_pass,
                genres, tags, critic_score,
                user_rating, release_year, cover_url, notes, favorite,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'Manual', ?, ?, 1, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                title = excluded.title,
                playtime_hours = excluded.playtime_hours,
                completion_status = excluded.completion_status,
                status_locked = 1,
                ownership_status = excluded.ownership_status,
                ownership_locked = 1,
                is_game_pass = excluded.is_game_pass,
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
                norm_ownership,
                is_gp_val,
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
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    allowed = {
        "title",
        "platform",
        "playtime_hours",
        "completion_status",
        "ownership_status",
        "is_game_pass",
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
            elif k == "is_game_pass":
                v = 1 if v else 0
            elif k == "ownership_status":
                v = normalize_ownership_status(v)

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
                elif k == "ownership_status":
                    single_fields.append("ownership_locked = ?")
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

        if "completion_status" in updates:
            ids_to_clear = [r["id"] for r in group_rows]
            if cid is not None and cid not in ids_to_clear:
                ids_to_clear.append(cid)
            placeholders = ",".join("?" for _ in ids_to_clear)
            conn.execute(
                f"DELETE FROM playtime_prompts WHERE game_id IN ({placeholders})",
                tuple(ids_to_clear),
            )

    return get_game_by_id(game_id, db_path)


def delete_game(game_id: int, db_path: Optional[str] = None) -> bool:
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


def get_game_by_id(game_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
    ownership: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
    favorite_only: bool = False,
    sort_by: str = "playtime_desc",
    limit: int = 500,
    include_unconsolidated: bool = False,
    db_path: Optional[str] = None,
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

    if is_game_pass is not None:
        target_gp = bool(is_game_pass)
        items = [
            g
            for g in items
            if bool(g.get("is_game_pass")) == target_gp
            or any(bool(lg.get("is_game_pass")) == target_gp for lg in g.get("linked_games", []))
        ]

    if ownership:
        raw_o = ownership.strip().lower()
        if raw_o in ("game pass", "gamepass"):
            items = [
                g
                for g in items
                if bool(g.get("is_game_pass"))
                or (g.get("ownership_status") or "").lower() in ("subscription", "game pass")
                or any(bool(lg.get("is_game_pass")) for lg in g.get("linked_games", []))
            ]
        else:
            o_q = normalize_ownership_status(ownership).lower()
            items = [
                g
                for g in items
                if (g.get("ownership_status") or "Owned").lower() == o_q
                or (o_q in [s.lower() for s in (g.get("ownership_statuses") or [])])
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
    db_path: Optional[str] = None,
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
    db_path: Optional[str] = None,
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


def unlink_game(game_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
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


def dismiss_reconcile_group(game_ids: List[int], db_path: Optional[str] = None) -> int:
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


def reset_reconcile_dismissals(db_path: Optional[str] = None) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM reconcile_dismissals")
        return cursor.rowcount


def get_pending_playtime_prompts(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return all pending playtime increase prompts for games whose status is not 'Playing'
    and that have not been muted. Automatically prunes stale prompts.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM playtime_prompts ORDER BY detected_at DESC, id DESC"
        ).fetchall()

    prompts: List[Dict[str, Any]] = []
    stale_ids: List[int] = []

    for r in rows:
        p_id = r["id"]
        g_id = r["game_id"]
        game = get_game_by_id(g_id, db_path=db_path)
        if not game or game.get("completion_status") == "Playing":
            stale_ids.append(p_id)
            continue

        prompts.append(
            {
                "prompt_id": p_id,
                "game_id": game["id"],
                "title": game["title"],
                "platform": game["platform"],
                "cover_url": game.get("cover_url"),
                "current_status": game.get("completion_status") or "Not Played",
                "previous_hours": round(float(r["previous_hours"] or 0.0), 1),
                "new_hours": round(float(r["new_hours"] or 0.0), 1),
                "delta_hours": round(float(r["delta_hours"] or 0.0), 1),
                "total_playtime_hours": game.get("playtime_hours") or 0.0,
                "detected_at": r["detected_at"],
            }
        )

    if stale_ids:
        with get_connection(db_path) as conn:
            placeholders = ",".join("?" for _ in stale_ids)
            conn.execute(
                f"DELETE FROM playtime_prompts WHERE id IN ({placeholders})",
                tuple(stale_ids),
            )

    return prompts


def resolve_playtime_prompt(
    prompt_id: int,
    action: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Resolve a playtime prompt:
      - 'accept': mark game (and any reconciled group) as 'Playing' (status_locked=1) and delete prompt
      - 'dismiss': delete prompt for now (will ask again on future +0.5h sync increases)
      - 'mute': set playtime_prompt_muted=1 on the game (and any reconciled group) and delete prompt
    """
    if action not in ("accept", "dismiss", "mute"):
        raise ValueError("action must be 'accept', 'dismiss', or 'mute'")

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM playtime_prompts WHERE id = ?", (prompt_id,)
        ).fetchone()
        if not row:
            return None
        game_id = int(row["game_id"])

        if action == "dismiss":
            conn.execute("DELETE FROM playtime_prompts WHERE id = ?", (prompt_id,))
        elif action == "mute":
            game_row = conn.execute(
                "SELECT id, canonical_id FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            if game_row:
                cid = game_row["canonical_id"]
                if cid is not None:
                    conn.execute(
                        "UPDATE games SET playtime_prompt_muted = 1 WHERE canonical_id = ?",
                        (cid,),
                    )
                else:
                    conn.execute(
                        "UPDATE games SET playtime_prompt_muted = 1 WHERE id = ?",
                        (game_id,),
                    )
            conn.execute("DELETE FROM playtime_prompts WHERE id = ?", (prompt_id,))

    if action == "accept":
        updated_game = update_game(
            game_id, {"completion_status": "Playing"}, db_path=db_path
        )
        with get_connection(db_path) as conn:
            conn.execute("DELETE FROM playtime_prompts WHERE id = ?", (prompt_id,))
        return {"action": "accept", "game_id": game_id, "game": updated_game}

    return {
        "action": action,
        "game_id": game_id,
        "game": get_game_by_id(game_id, db_path=db_path),
    }


def sanitize_sync_message(msg: str) -> str:
    if not msg:
        return ""
    # Scrub sensitive API keys, tokens, and secrets from URLs, headers, or error text
    cleaned = re.sub(
        r"([\?&;]|\b)(key|api_key|token|client_secret|npsso)\s*=\s*[^&\s;'\"]+",
        r"\1\2=[REDACTED]",
        msg,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(Bearer\s+)[a-zA-Z0-9._~+/-]+",
        r"\1[REDACTED]",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def log_sync_event(
    source: str,
    status: str,
    games_synced: int = 0,
    message: str = "",
    db_path: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    clean_message = sanitize_sync_message(message)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_logs (source, status, games_synced, message, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source, status, games_synced, clean_message, now),
        )


def get_library_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    games = query_games(limit=10000, db_path=db_path)
    total_games = len(games)
    total_hours = round(sum(g["playtime_hours"] for g in games), 1)
    reconciled_groups_count = sum(1 for g in games if g.get("is_reconciled"))
    reconcile_candidates_count = len(get_reconcile_candidates(db_path=db_path))
    game_pass_count = sum(1 for g in games if g.get("is_game_pass"))
    game_pass_hours = round(sum(g["playtime_hours"] for g in games if g.get("is_game_pass")), 1)

    by_platform: Dict[str, Dict[str, Any]] = {}
    by_status: Dict[str, int] = {}
    by_ownership: Dict[str, Dict[str, Any]] = {}
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

        own = g.get("ownership_status") or "Owned"
        if own not in by_ownership:
            by_ownership[own] = {"count": 0, "hours": 0.0}
        by_ownership[own]["count"] += 1
        by_ownership[own]["hours"] = round(
            by_ownership[own]["hours"] + float(g.get("playtime_hours") or 0.0), 1
        )

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
    pending_prompts = get_pending_playtime_prompts(db_path=db_path)

    return {
        "total_games": total_games,
        "total_playtime_hours": total_hours,
        "backlog_count": backlog_count,
        "completed_count": completed_count,
        "playing_count": by_status.get("Playing", 0),
        "reconciled_groups_count": reconciled_groups_count,
        "reconcile_candidates_count": reconcile_candidates_count,
        "game_pass_count": game_pass_count,
        "game_pass_hours": game_pass_hours,
        "playtime_prompts_count": len(pending_prompts),
        "playtime_prompts": pending_prompts,
        "missing_genres_count": missing_genres_count,
        "all_genres": all_genres,
        "all_tags": all_tags,
        "top_tags": top_tags,
        "platforms": by_platform,
        "statuses": by_status,
        "ownership": by_ownership,
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
    ownership: Optional[str] = None,
    is_game_pass: Optional[bool] = None,
    favorite_only: bool = False,
    db_path: Optional[str] = None,
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
        ownership=ownership,
        is_game_pass=is_game_pass,
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
    db_path: Optional[str] = None,
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


def get_game_achievements(game_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
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


# ==============================================================================
# Multi-Platform Wishlist, Edition Hierarchy, Price Monitoring & Smart Upgrades
# ==============================================================================

EDITION_TIER_RANKS = {
    "base": 1,
    "standard": 1,
    "deluxe": 2,
    "special": 2,
    "gold": 3,
    "complete": 3,
    "definitive": 3,
    "ultimate": 4,
    "goty": 4,
    "legendary": 4,
    "collector": 5,
}


def normalize_platform_family(platform_str: Optional[str]) -> str:
    """Normalize a platform string or storefront into a standard platform family."""
    if not platform_str:
        return "PC"
    p = platform_str.strip().lower()
    if "xbox" in p:
        return "Xbox"
    if any(k in p for k in ("playstation", "ps4", "ps5", "psn")):
        return "PlayStation"
    if any(k in p for k in ("switch", "nintendo")):
        return "Nintendo Switch"
    if any(k in p for k in ("steam", "gog", "epic", "pc", "windows", "fanatical", "humble", "gmg")):
        return "PC"
    return platform_str.strip()


def infer_edition_tier(edition_name: str) -> Tuple[str, int]:
    """Infer edition tier and rank (1-5) from edition name."""
    name = (edition_name or "").lower()
    if "collector" in name:
        return ("collector", 5)
    if "ultimate" in name:
        return ("ultimate", 4)
    if "legendary" in name:
        return ("ultimate", 4)
    if "premium" in name:
        return ("ultimate", 4)
    if "goty" in name or "game of the year" in name:
        return ("goty", 4)
    if "complete" in name:
        return ("complete", 3)
    if "definitive" in name:
        return ("complete", 3)
    if "voidheart" in name:
        return ("complete", 3)
    if "legacy" in name:
        return ("complete", 3)
    if "gold" in name:
        return ("gold", 3)
    if "anniversary" in name:
        return ("complete", 3)
    if "deluxe" in name:
        return ("deluxe", 2)
    if "director" in name:
        return ("deluxe", 2)
    if "enhanced" in name:
        return ("deluxe", 2)
    if "special" in name:
        return ("deluxe", 2)
    return ("base", 1)


def check_library_ownership_conflicts(
    title: str,
    canonical_game_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check if a game (by title or canonical_game_id) already exists in the user's
    owned library or play history. Returns conflict status and platforms.
    """
    norm_title = normalize_game_title(title)
    with get_connection(db_path) as conn:
        lib_game_id = None
        if canonical_game_id is not None:
            c_row = conn.execute(
                "SELECT library_game_id FROM canonical_games WHERE id = ?",
                (canonical_game_id,),
            ).fetchone()
            if c_row and c_row["library_game_id"]:
                lib_game_id = c_row["library_game_id"]

        all_games = conn.execute(
            """
            SELECT id, title, platform, source, playtime_hours, ownership_status,
                   is_game_pass, achievements_unlocked, canonical_id
            FROM games
            """
        ).fetchall()

        matched_rows = []
        for g in all_games:
            is_match = False
            if lib_game_id is not None and (g["id"] == lib_game_id or g["canonical_id"] == lib_game_id):
                is_match = True
            elif normalize_game_title(g["title"]) == norm_title:
                is_match = True

            if is_match:
                matched_rows.append(g)

    conflicted_platforms = set()
    library_games = []
    for g in matched_rows:
        has_ownership = (
            g["ownership_status"] in ("Owned", "Subscription")
            or bool(g["is_game_pass"])
            or float(g["playtime_hours"] or 0.0) > 0.0
            or int(g["achievements_unlocked"] or 0) > 0
        )
        fam = normalize_platform_family(g["platform"])
        if has_ownership:
            conflicted_platforms.add(fam)

        library_games.append({
            "id": g["id"],
            "title": g["title"],
            "platform": g["platform"],
            "platform_family": fam,
            "ownership_status": g["ownership_status"],
            "is_game_pass": bool(g["is_game_pass"]),
            "playtime_hours": float(g["playtime_hours"] or 0.0),
            "achievements_unlocked": int(g["achievements_unlocked"] or 0),
        })

    warning = None
    if conflicted_platforms:
        parts = []
        for fam in sorted(conflicted_platforms):
            fam_games = [g for g in library_games if g["platform_family"] == fam]
            playtime = max((g["playtime_hours"] for g in fam_games), default=0.0)
            status = fam_games[0]["ownership_status"] if fam_games else "Owned"
            if playtime > 0:
                parts.append(f"{status} on {fam} ({playtime:.1f} hrs played)")
            else:
                parts.append(f"{status} on {fam}")
        warning = f"Already in Library: {', '.join(parts)}"

    return {
        "already_in_library": len(conflicted_platforms) > 0,
        "conflicted_platforms": sorted(list(conflicted_platforms)),
        "library_games": library_games,
        "conflict_warning": warning,
    }


def upsert_canonical_game(
    title: str,
    release_year: Optional[int] = None,
    developer: str = "",
    publisher: str = "",
    cover_url: Optional[str] = None,
    genres: Optional[List[str]] = None,
    critic_score: Optional[int] = None,
    library_game_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    norm = normalize_game_title(title)
    now = datetime.now(timezone.utc).isoformat()
    genres_json = json.dumps(genres or [])
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO canonical_games (
                normalized_title, title, release_year, developer, publisher,
                cover_url, genres, critic_score, library_game_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_title) DO UPDATE SET
                title = excluded.title,
                release_year = COALESCE(excluded.release_year, canonical_games.release_year),
                developer = CASE WHEN excluded.developer != '' THEN excluded.developer ELSE canonical_games.developer END,
                publisher = CASE WHEN excluded.publisher != '' THEN excluded.publisher ELSE canonical_games.publisher END,
                cover_url = COALESCE(excluded.cover_url, canonical_games.cover_url),
                genres = CASE WHEN excluded.genres != '[]' THEN excluded.genres ELSE canonical_games.genres END,
                critic_score = COALESCE(excluded.critic_score, canonical_games.critic_score),
                library_game_id = COALESCE(excluded.library_game_id, canonical_games.library_game_id),
                updated_at = excluded.updated_at
            """,
            (
                norm, title, release_year, developer, publisher,
                cover_url, genres_json, critic_score, library_game_id, now, now
            ),
        )
        row = conn.execute("SELECT * FROM canonical_games WHERE normalized_title = ?", (norm,)).fetchone()
        res = dict(row)
        res["genres"] = json.loads(res.get("genres") or "[]")
        return res


def get_canonical_game(canonical_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM canonical_games WHERE id = ?", (canonical_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        res["genres"] = json.loads(res.get("genres") or "[]")
        return res


def get_canonical_game_by_title(title: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    norm = normalize_game_title(title)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM canonical_games WHERE normalized_title = ?", (norm,)).fetchone()
        if not row:
            return None
        res = dict(row)
        res["genres"] = json.loads(res.get("genres") or "[]")
        return res


def upsert_game_edition(
    canonical_game_id: int,
    edition_name: str = "Standard Edition",
    edition_tier: Optional[str] = None,
    tier_rank: Optional[int] = None,
    is_base_edition: Optional[bool] = None,
    included_dlc_notes: str = "",
    cover_url: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    if edition_tier is None or tier_rank is None:
        inferred_tier, inferred_rank = infer_edition_tier(edition_name)
        edition_tier = edition_tier or inferred_tier
        tier_rank = tier_rank or inferred_rank

    if is_base_edition is None:
        is_base_edition = (tier_rank == 1 or "standard" in edition_name.lower() or "base" in edition_name.lower())

    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO game_editions (
                canonical_game_id, edition_name, edition_tier, tier_rank,
                is_base_edition, included_dlc_notes, cover_url, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_game_id, edition_name) DO UPDATE SET
                edition_tier = excluded.edition_tier,
                tier_rank = excluded.tier_rank,
                is_base_edition = excluded.is_base_edition,
                included_dlc_notes = CASE WHEN excluded.included_dlc_notes != '' THEN excluded.included_dlc_notes ELSE game_editions.included_dlc_notes END,
                cover_url = COALESCE(excluded.cover_url, game_editions.cover_url),
                updated_at = excluded.updated_at
            """,
            (
                canonical_game_id, edition_name, edition_tier, tier_rank,
                1 if is_base_edition else 0, included_dlc_notes, cover_url, now, now
            ),
        )
        row = conn.execute(
            "SELECT * FROM game_editions WHERE canonical_game_id = ? AND edition_name = ?",
            (canonical_game_id, edition_name),
        ).fetchone()
        res = dict(row)
        res["is_base_edition"] = bool(res["is_base_edition"])
        return res


def list_game_editions(canonical_game_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM game_editions WHERE canonical_game_id = ? ORDER BY tier_rank ASC, id ASC",
            (canonical_game_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["is_base_edition"] = bool(d["is_base_edition"])
            result.append(d)
        return result


def _append_price_history_internal(
    conn: sqlite3.Connection,
    sku_id: int,
    storefront: str,
    price_usd: float,
    regular_price_usd: Optional[float],
    discount_pct: int,
    is_all_time_low: bool,
    source: str,
    recorded_at: str,
) -> None:
    last_row = conn.execute(
        "SELECT * FROM price_history WHERE sku_id = ? ORDER BY recorded_at DESC LIMIT 1",
        (sku_id,),
    ).fetchone()
    today_prefix = recorded_at[:10]
    if last_row and last_row["recorded_at"].startswith(today_prefix) and abs(last_row["price_usd"] - price_usd) < 0.001:
        return

    conn.execute(
        """
        INSERT INTO price_history (
            sku_id, storefront, price_usd, regular_price_usd, discount_pct,
            is_all_time_low, source, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sku_id, storefront, price_usd, regular_price_usd, discount_pct,
            1 if is_all_time_low else 0, source, recorded_at
        ),
    )


def upsert_edition_sku(
    edition_id: int,
    platform_family: str,
    platform_label: str,
    storefront: str,
    store_sku_id: str,
    itad_plain: Optional[str] = None,
    store_url: str = "",
    msrp_usd: Optional[float] = None,
    current_price_usd: Optional[float] = None,
    regular_price_usd: Optional[float] = None,
    discount_pct: Optional[int] = None,
    historical_low_usd: Optional[float] = None,
    is_authorized_seller: bool = True,
    is_drm_free: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    norm_family = normalize_platform_family(platform_family)

    if discount_pct is None and current_price_usd is not None and regular_price_usd and regular_price_usd > 0:
        discount_pct = max(0, int(round((1.0 - (current_price_usd / regular_price_usd)) * 100)))

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM edition_platform_skus WHERE edition_id = ? AND storefront = ? AND store_sku_id = ?",
            (edition_id, storefront, store_sku_id),
        ).fetchone()

        hist_low = current_price_usd
        if existing and existing["historical_low_usd"] is not None:
            hist_low = existing["historical_low_usd"]
            if current_price_usd is not None:
                hist_low = min(hist_low, current_price_usd)
            if historical_low_usd is not None:
                hist_low = min(hist_low, historical_low_usd)
        elif historical_low_usd is not None:
            hist_low = min(historical_low_usd, current_price_usd) if current_price_usd is not None else historical_low_usd

        conn.execute(
            """
            INSERT INTO edition_platform_skus (
                edition_id, platform_family, platform_label, storefront, store_sku_id,
                itad_plain, store_url, msrp_usd, current_price_usd, regular_price_usd,
                discount_pct, historical_low_usd, is_authorized_seller, is_drm_free,
                last_checked_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edition_id, storefront, store_sku_id) DO UPDATE SET
                platform_family = excluded.platform_family,
                platform_label = excluded.platform_label,
                itad_plain = COALESCE(excluded.itad_plain, edition_platform_skus.itad_plain),
                store_url = CASE WHEN excluded.store_url != '' THEN excluded.store_url ELSE edition_platform_skus.store_url END,
                msrp_usd = COALESCE(excluded.msrp_usd, edition_platform_skus.msrp_usd),
                current_price_usd = COALESCE(excluded.current_price_usd, edition_platform_skus.current_price_usd),
                regular_price_usd = COALESCE(excluded.regular_price_usd, edition_platform_skus.regular_price_usd),
                discount_pct = COALESCE(excluded.discount_pct, edition_platform_skus.discount_pct),
                historical_low_usd = excluded.historical_low_usd,
                is_authorized_seller = excluded.is_authorized_seller,
                is_drm_free = excluded.is_drm_free,
                last_checked_at = excluded.last_checked_at,
                updated_at = excluded.updated_at
            """,
            (
                edition_id, norm_family, platform_label, storefront, store_sku_id,
                itad_plain, store_url, msrp_usd, current_price_usd, regular_price_usd,
                discount_pct or 0, hist_low, 1 if is_authorized_seller else 0,
                1 if is_drm_free else 0, now if current_price_usd is not None else None, now, now
            ),
        )
        row = conn.execute(
            "SELECT * FROM edition_platform_skus WHERE edition_id = ? AND storefront = ? AND store_sku_id = ?",
            (edition_id, storefront, store_sku_id),
        ).fetchone()
        res = dict(row)
        res["is_authorized_seller"] = bool(res["is_authorized_seller"])
        res["is_drm_free"] = bool(res["is_drm_free"])

        if current_price_usd is not None:
            _append_price_history_internal(
                conn=conn,
                sku_id=res["id"],
                storefront=storefront,
                price_usd=current_price_usd,
                regular_price_usd=regular_price_usd,
                discount_pct=discount_pct or 0,
                is_all_time_low=bool(hist_low is not None and current_price_usd <= hist_low + 0.001),
                source="sync",
                recorded_at=now,
            )
        return res


def record_sku_price(
    sku_id: int,
    price_usd: float,
    regular_price_usd: Optional[float] = None,
    discount_pct: Optional[int] = None,
    source: str = "daily_sync",
    recorded_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    now = recorded_at or datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        sku = conn.execute("SELECT * FROM edition_platform_skus WHERE id = ?", (sku_id,)).fetchone()
        if not sku:
            raise ValueError(f"SKU {sku_id} not found")

        cur_low = sku["historical_low_usd"]
        is_atl = (cur_low is None or price_usd <= cur_low + 0.001)
        new_hist_low = min(cur_low, price_usd) if cur_low is not None else price_usd

        if discount_pct is None and regular_price_usd and regular_price_usd > 0:
            discount_pct = max(0, int(round((1.0 - (price_usd / regular_price_usd)) * 100)))

        conn.execute(
            """
            UPDATE edition_platform_skus
            SET current_price_usd = ?,
                regular_price_usd = COALESCE(?, regular_price_usd),
                discount_pct = COALESCE(?, discount_pct),
                historical_low_usd = ?,
                last_checked_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (price_usd, regular_price_usd, discount_pct, new_hist_low, now, now, sku_id),
        )

        _append_price_history_internal(
            conn=conn,
            sku_id=sku_id,
            storefront=sku["storefront"],
            price_usd=price_usd,
            regular_price_usd=regular_price_usd,
            discount_pct=discount_pct or 0,
            is_all_time_low=is_atl,
            source=source,
            recorded_at=now,
        )

        return {
            "sku_id": sku_id,
            "price_usd": price_usd,
            "is_all_time_low": is_atl,
            "historical_low_usd": new_hist_low,
        }


def get_sku_price_history(sku_id: int, limit: int = 100, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE sku_id = ? ORDER BY recorded_at DESC LIMIT ?",
            (sku_id, limit),
        ).fetchall()
        return [
            {
                **dict(r),
                "is_all_time_low": bool(r["is_all_time_low"]),
            }
            for r in rows
        ]


def get_wishlist_item(item_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["allow_smart_upgrade"] = bool(item["allow_smart_upgrade"])

        canonical_row = conn.execute(
            "SELECT * FROM canonical_games WHERE id = ?", (item["canonical_game_id"],)
        ).fetchone()
        if canonical_row:
            c = dict(canonical_row)
            c["genres"] = json.loads(c.get("genres") or "[]")
            item["canonical_game"] = c
        else:
            item["canonical_game"] = None

        targets = conn.execute(
            "SELECT * FROM wishlist_platform_targets WHERE wishlist_item_id = ?",
            (item_id,),
        ).fetchall()
        item["targets"] = []
        for t in targets:
            td = dict(t)
            td["allowed_storefronts"] = json.loads(td.get("allowed_storefronts") or "[]")
            td["is_active"] = bool(td["is_active"])
            td["muted_owned_conflict"] = bool(td["muted_owned_conflict"])
            item["targets"].append(td)

        # Fetch editions and skus
        editions = conn.execute(
            "SELECT * FROM game_editions WHERE canonical_game_id = ? ORDER BY tier_rank ASC, id ASC",
            (item["canonical_game_id"],),
        ).fetchall()
        ed_list = []
        all_skus = []
        for ed in editions:
            e_dict = dict(ed)
            e_dict["is_base_edition"] = bool(e_dict["is_base_edition"])
            skus = conn.execute(
                "SELECT * FROM edition_platform_skus WHERE edition_id = ?", (e_dict["id"],)
            ).fetchall()
            e_dict["skus"] = [
                {
                    **dict(s),
                    "is_authorized_seller": bool(s["is_authorized_seller"]),
                    "is_drm_free": bool(s["is_drm_free"]),
                }
                for s in skus
            ]
            all_skus.extend(e_dict["skus"])
            ed_list.append(e_dict)
        item["editions"] = ed_list
        item["skus"] = all_skus

        # Conflict info
        if item["canonical_game"]:
            item["conflict"] = check_library_ownership_conflicts(
                title=item["canonical_game"]["title"],
                canonical_game_id=item["canonical_game_id"],
                db_path=db_path,
            )
        else:
            item["conflict"] = None

        return item


def get_wishlist_item_by_title(title: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    norm = normalize_game_title(title)
    with get_connection(db_path) as conn:
        c_row = conn.execute("SELECT id FROM canonical_games WHERE normalized_title = ?", (norm,)).fetchone()
        if not c_row:
            return None
        w_row = conn.execute("SELECT id FROM wishlist_items WHERE canonical_game_id = ?", (c_row["id"],)).fetchone()
        if not w_row:
            return None
        return get_wishlist_item(w_row["id"], db_path=db_path)


def add_wishlist_item(
    title: str,
    platform_targets: Optional[List[Dict[str, Any]]] = None,
    release_year: Optional[int] = None,
    developer: str = "",
    publisher: str = "",
    cover_url: Optional[str] = None,
    genres: Optional[List[str]] = None,
    critic_score: Optional[int] = None,
    allow_smart_upgrade: bool = True,
    preferred_edition_id: Optional[int] = None,
    notes: str = "",
    added_via: str = "web",
    force_track_owned: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    canonical = upsert_canonical_game(
        title=title,
        release_year=release_year,
        developer=developer,
        publisher=publisher,
        cover_url=cover_url,
        genres=genres,
        critic_score=critic_score,
        db_path=db_path,
    )
    upsert_game_edition(
        canonical_game_id=canonical["id"],
        edition_name="Standard Edition",
        edition_tier="base",
        tier_rank=1,
        is_base_edition=True,
        cover_url=cover_url,
        db_path=db_path,
    )

    conflict = check_library_ownership_conflicts(
        title=title,
        canonical_game_id=canonical["id"],
        db_path=db_path,
    )

    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO wishlist_items (
                canonical_game_id, preferred_edition_id, allow_smart_upgrade,
                status, notes, added_via, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(canonical_game_id) DO UPDATE SET
                preferred_edition_id = COALESCE(excluded.preferred_edition_id, wishlist_items.preferred_edition_id),
                allow_smart_upgrade = excluded.allow_smart_upgrade,
                status = 'active',
                notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE wishlist_items.notes END,
                updated_at = excluded.updated_at
            """,
            (
                canonical["id"], preferred_edition_id, 1 if allow_smart_upgrade else 0,
                notes, added_via, now, now
            ),
        )
        item_row = conn.execute(
            "SELECT * FROM wishlist_items WHERE canonical_game_id = ?",
            (canonical["id"],),
        ).fetchone()
        wishlist_item_id = item_row["id"]

        if platform_targets:
            for pt in platform_targets:
                if isinstance(pt, str):
                    fam = normalize_platform_family(pt)
                    price = 19.99
                    allowed_sf = json.dumps([])
                    is_conflicted = fam in conflict["conflicted_platforms"]
                    is_active = 0 if (is_conflicted and not force_track_owned) else 1
                    muted = 1 if (is_conflicted and not force_track_owned) else 0
                elif isinstance(pt, dict):
                    fam = normalize_platform_family(pt.get("platform") or pt.get("platform_family") or "PC")
                    price = float(pt.get("target_price_usd", 19.99))
                    allowed_sf = json.dumps(pt.get("storefronts") or pt.get("allowed_storefronts") or [])
                    is_conflicted = fam in conflict["conflicted_platforms"]
                    is_active = 1
                    muted = 0
                    if is_conflicted and not force_track_owned:
                        is_active = 0
                        muted = 1
                    elif "is_active" in pt:
                        is_active = 1 if pt["is_active"] else 0
                        muted = 1 if (is_conflicted and not is_active) else 0
                else:
                    continue

                conn.execute(
                    """
                    INSERT INTO wishlist_platform_targets (
                        wishlist_item_id, platform_family, allowed_storefronts,
                        target_price_usd, is_active, muted_owned_conflict,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wishlist_item_id, platform_family) DO UPDATE SET
                        allowed_storefronts = excluded.allowed_storefronts,
                        target_price_usd = excluded.target_price_usd,
                        is_active = excluded.is_active,
                        muted_owned_conflict = excluded.muted_owned_conflict,
                        updated_at = excluded.updated_at
                    """,
                    (wishlist_item_id, fam, allowed_sf, price, is_active, muted, now, now),
                )

    return get_wishlist_item(wishlist_item_id, db_path=db_path)


def update_wishlist_item(
    item_id: int,
    allow_smart_upgrade: Optional[bool] = None,
    preferred_edition_id: Optional[int] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        fields = []
        vals = []
        if allow_smart_upgrade is not None:
            fields.append("allow_smart_upgrade = ?")
            vals.append(1 if allow_smart_upgrade else 0)
        if preferred_edition_id is not None:
            fields.append("preferred_edition_id = ?")
            vals.append(preferred_edition_id)
        if status is not None:
            fields.append("status = ?")
            vals.append(status)
        if notes is not None:
            fields.append("notes = ?")
            vals.append(notes)
        if not fields:
            return get_wishlist_item(item_id, db_path=db_path)

        fields.append("updated_at = ?")
        vals.append(now)
        vals.append(item_id)

        conn.execute(f"UPDATE wishlist_items SET {', '.join(fields)} WHERE id = ?", tuple(vals))
    return get_wishlist_item(item_id, db_path=db_path)


def update_wishlist_platforms(
    wishlist_item_id: int,
    platform_targets: Optional[List[Dict[str, Any]]] = None,
    remove_platforms: Optional[List[str]] = None,
    allow_smart_upgrade: Optional[bool] = None,
    force_track_owned: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    item = get_wishlist_item(wishlist_item_id, db_path=db_path)
    if not item:
        raise ValueError(f"Wishlist item {wishlist_item_id} not found")

    if allow_smart_upgrade is not None:
        update_wishlist_item(wishlist_item_id, allow_smart_upgrade=allow_smart_upgrade, db_path=db_path)

    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        if remove_platforms:
            for p in remove_platforms:
                fam = normalize_platform_family(p)
                conn.execute(
                    "DELETE FROM wishlist_platform_targets WHERE wishlist_item_id = ? AND platform_family = ?",
                    (wishlist_item_id, fam),
                )

        if platform_targets:
            conflict = item.get("conflict") or check_library_ownership_conflicts(
                title=item["canonical_game"]["title"],
                canonical_game_id=item["canonical_game_id"],
                db_path=db_path,
            )
            for pt in platform_targets:
                if isinstance(pt, str):
                    fam = normalize_platform_family(pt)
                    price = 19.99
                    allowed_sf = json.dumps([])
                    is_conflicted = fam in conflict["conflicted_platforms"]
                    is_active = 0 if (is_conflicted and not force_track_owned) else 1
                    muted = 1 if (is_conflicted and not force_track_owned) else 0
                elif isinstance(pt, dict):
                    fam = normalize_platform_family(pt.get("platform") or pt.get("platform_family") or "PC")
                    price = float(pt.get("target_price_usd", 19.99))
                    allowed_sf = json.dumps(pt.get("storefronts") or pt.get("allowed_storefronts") or [])
                    is_conflicted = fam in conflict["conflicted_platforms"]
                    is_active = 1
                    muted = 0
                    if is_conflicted and not force_track_owned:
                        is_active = 0
                        muted = 1
                    elif "is_active" in pt:
                        is_active = 1 if pt["is_active"] else 0
                        muted = 1 if (is_conflicted and not is_active) else 0
                else:
                    continue

                conn.execute(
                    """
                    INSERT INTO wishlist_platform_targets (
                        wishlist_item_id, platform_family, allowed_storefronts,
                        target_price_usd, is_active, muted_owned_conflict,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wishlist_item_id, platform_family) DO UPDATE SET
                        allowed_storefronts = excluded.allowed_storefronts,
                        target_price_usd = excluded.target_price_usd,
                        is_active = excluded.is_active,
                        muted_owned_conflict = excluded.muted_owned_conflict,
                        updated_at = excluded.updated_at
                    """,
                    (wishlist_item_id, fam, allowed_sf, price, is_active, muted, now, now),
                )

    return get_wishlist_item(wishlist_item_id, db_path=db_path)


def delete_wishlist_item(item_id: int, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM wishlist_items WHERE id = ?", (item_id,))
        return cursor.rowcount > 0


def list_wishlist_items(status: str = "active", db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        if status == "all":
            rows = conn.execute("SELECT id FROM wishlist_items ORDER BY updated_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT id FROM wishlist_items WHERE status = ? ORDER BY updated_at DESC", (status,)).fetchall()
        return [get_wishlist_item(r["id"], db_path=db_path) for r in rows if r["id"]]


def _matches_platform_target(sku: Dict[str, Any], target: Dict[str, Any]) -> bool:
    target_fam = target.get("platform_family")
    sku_fam = sku.get("platform_family")
    if target_fam and sku_fam and target_fam != sku_fam:
        return False
    allowed = target.get("allowed_storefronts") or []
    if allowed and sku.get("storefront") not in allowed:
        return False
    return True


def evaluate_wishlist_deals(
    wishlist_item_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluates deals for active wishlist items against their active platform targets.
    Detects both standard target deals and Smart Upgrade deals.
    """
    if wishlist_item_id is not None:
        item = get_wishlist_item(wishlist_item_id, db_path=db_path)
        items = [item] if item and item.get("status") == "active" else []
    else:
        items = list_wishlist_items(status="active", db_path=db_path)

    all_deals: List[Dict[str, Any]] = []

    for item in items:
        if not item or not item.get("targets") or not item.get("skus"):
            continue
        allow_upgrade = bool(item.get("allow_smart_upgrade", True))
        editions = item.get("editions") or []
        edition_by_id = {e["id"]: e for e in editions}
        skus = item.get("skus") or []

        for target in item["targets"]:
            if not target.get("is_active") or target.get("muted_owned_conflict"):
                continue

            base_target_usd = float(target["target_price_usd"])

            base_prices = [
                float(s["current_price_usd"])
                for s in skus
                if s.get("current_price_usd") is not None
                and _matches_platform_target(s, target)
                and edition_by_id.get(s["edition_id"], {}).get("tier_rank", 1) == 1
            ]
            current_base_price = min(base_prices) if base_prices else None

            for sku in skus:
                price = sku.get("current_price_usd")
                if price is None or not _matches_platform_target(sku, target):
                    continue
                price_f = float(price)
                if price_f > base_target_usd:
                    continue

                ed = edition_by_id.get(sku["edition_id"], {})
                tier_rank = int(ed.get("tier_rank") or 1)
                is_smart_upgrade = bool(allow_upgrade and tier_rank > 1)

                if not allow_upgrade and tier_rank > 1 and item.get("preferred_edition_id") != ed.get("id"):
                    continue

                atl = sku.get("historical_low_usd")
                is_atl = bool(atl is not None and price_f <= float(atl) + 0.01)

                canonical = item.get("canonical_game") or {}

                all_deals.append({
                    "wishlist_item_id": item["id"],
                    "canonical_game_id": item["canonical_game_id"],
                    "game_title": canonical.get("title", ""),
                    "cover_url": canonical.get("cover_url"),
                    "edition_id": ed.get("id"),
                    "edition_name": ed.get("edition_name", "Standard Edition"),
                    "edition_tier": ed.get("edition_tier", "base"),
                    "tier_rank": tier_rank,
                    "sku_id": sku["id"],
                    "platform_family": sku["platform_family"],
                    "platform_label": sku["platform_label"],
                    "storefront": sku["storefront"],
                    "store_url": sku.get("store_url") or "",
                    "current_price_usd": round(price_f, 2),
                    "regular_price_usd": sku.get("regular_price_usd"),
                    "discount_pct": int(sku.get("discount_pct") or 0),
                    "target_price_usd": round(base_target_usd, 2),
                    "savings_below_target_usd": round(base_target_usd - price_f, 2),
                    "current_base_edition_price_usd": current_base_price,
                    "is_smart_upgrade": is_smart_upgrade,
                    "is_all_time_low": is_atl,
                    "is_drm_free": bool(sku.get("is_drm_free")),
                    "deal_headline": (
                        f"SMART UPGRADE: {ed.get('edition_name')} is ${price_f:.2f} "
                        f"(meets your ${base_target_usd:.2f} Base target!)"
                        if is_smart_upgrade
                        else f"TARGET MET: {ed.get('edition_name')} is ${price_f:.2f} "
                             f"(target ${base_target_usd:.2f})"
                    ),
                })

    all_deals.sort(
        key=lambda d: (-int(d["is_smart_upgrade"]), -d["tier_rank"], d["current_price_usd"])
    )
    return all_deals


def mark_wishlist_item_purchased(
    wishlist_item_id: int,
    platform: str,
    edition_name: Optional[str] = None,
    price_paid_usd: Optional[float] = None,
    completion_status: str = "Plan to Play",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        item = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (wishlist_item_id,)).fetchone()
        if not item:
            raise ValueError(f"Wishlist item {wishlist_item_id} not found")

        canonical = conn.execute("SELECT * FROM canonical_games WHERE id = ?", (item["canonical_game_id"],)).fetchone()
        if not canonical:
            raise ValueError("Canonical game not found")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE wishlist_items SET status = 'purchased', updated_at = ? WHERE id = ?",
            (now, wishlist_item_id),
        )

    ed_str = f" {edition_name}" if edition_name else ""
    price_str = f" for ${price_paid_usd:.2f}" if price_paid_usd is not None else ""
    purchase_note = f"Purchased{ed_str}{price_str}".strip()

    notes = purchase_note
    if canonical["developer"] or canonical["publisher"]:
        notes += f" | Dev: {canonical['developer']} Pub: {canonical['publisher']}"

    raw_genres = canonical["genres"]
    canonical_genres = json.loads(raw_genres) if raw_genres else []
    added = add_manual_game(
        title=canonical["title"],
        platform=platform,
        completion_status=completion_status,
        playtime_hours=0.0,
        critic_score=canonical["critic_score"],
        release_year=canonical["release_year"],
        genres=canonical_genres,
        cover_url=canonical["cover_url"],
        notes=notes,
        ownership_status="Owned",
        db_path=db_path,
    )

    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE canonical_games SET library_game_id = ?, updated_at = ? WHERE id = ?",
            (added["id"], now, canonical["id"]),
        )

    updated_item = get_wishlist_item(wishlist_item_id, db_path=db_path)
    return {
        **updated_item,
        "library_game_id": added["id"],
    }


def upsert_push_subscription(
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
    user_agent: str = "",
    device_label: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO push_subscriptions (
                endpoint, p256dh_key, auth_key, user_agent, device_label,
                is_active, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                p256dh_key = excluded.p256dh_key,
                auth_key = excluded.auth_key,
                user_agent = excluded.user_agent,
                device_label = excluded.device_label,
                is_active = 1,
                last_used_at = excluded.last_used_at
            """,
            (endpoint, p256dh_key, auth_key, user_agent, device_label, now, now),
        )
        row = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        return dict(row)


def delete_push_subscription(endpoint: str, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        return cursor.rowcount > 0


def list_active_push_subscriptions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM push_subscriptions WHERE is_active = 1").fetchall()
        return [dict(r) for r in rows]

