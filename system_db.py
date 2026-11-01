"""
Central System SQLite Database layer for Vertex multi-tenancy.
Stores accounts, encrypted platform credentials, web sessions, OAuth tokens,
and the shared cross-user game catalog metadata cache.
"""

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SYSTEM_DB_PATH = (
    os.environ.get("VERTEX_SYSTEM_DB_PATH")
    or os.environ.get("SYSTEM_DB_PATH")
    or str(Path(__file__).resolve().parent / "data" / "system.db")
)


def get_system_connection(db_path: str = DEFAULT_SYSTEM_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_system_db(db_path: str = DEFAULT_SYSTEM_DB_PATH) -> None:
    with get_system_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_connections (
                user_id TEXT PRIMARY KEY,
                steam_id TEXT,
                xbl_api_key_encrypted TEXT,
                psn_npsso_encrypted TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS web_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                user_agent TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_web_sessions_user_id ON web_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_web_sessions_expires_at ON web_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS oauth_tokens (
                access_token TEXT PRIMARY KEY,
                refresh_token TEXT UNIQUE,
                client_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scope TEXT DEFAULT '',
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS catalog_cache (
                normalized_title TEXT PRIMARY KEY,
                steam_appid TEXT,
                genres_json TEXT DEFAULT '[]',
                release_year INTEGER,
                critic_score INTEGER,
                cover_url TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Additive, idempotent migration for system.db files created before oauth_tokens
        # carried a user_id. Legacy rows keep NULL and are never accepted as valid tokens.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(oauth_tokens)")}
        if "user_id" not in cols:
            conn.execute("ALTER TABLE oauth_tokens ADD COLUMN user_id TEXT")
        if "scope" not in cols:
            conn.execute("ALTER TABLE oauth_tokens ADD COLUMN scope TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user_id ON oauth_tokens(user_id)")


def get_user_id_by_username(username: str, db_path: Optional[str] = None) -> Optional[str]:
    if not username:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone()
        return str(row["id"]) if row else None


def get_username_by_user_id(user_id: str, db_path: Optional[str] = None) -> Optional[str]:
    if not user_id:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        return str(row["username"]) if row else None


def ensure_user(
    username: str,
    user_id: Optional[str] = None,
    password_hash: str = "managed_by_web_auth",
    email: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """Ensure a user exists in system.db, returning their user_id.

    If a user with this username (case-insensitive) already exists, returns their id.
    Otherwise inserts a new user record.
    """
    if not username:
        raise ValueError("username cannot be empty")
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone()
        if row:
            return str(row["id"])
        assigned_id = str(user_id or username.lower().strip())
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO users (id, username, email, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (assigned_id, username.strip(), email, password_hash, now),
        )
        return assigned_id


def get_catalog_cache_entry(
    normalized_title: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if not normalized_title:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM catalog_cache WHERE normalized_title = ?",
            (normalized_title,),
        ).fetchone()
        return dict(row) if row else None


def upsert_catalog_cache_entry(
    normalized_title: str,
    steam_appid: Optional[str] = None,
    genres_json: str = "[]",
    release_year: Optional[int] = None,
    critic_score: Optional[int] = None,
    cover_url: Optional[str] = None,
    updated_at: str = "",
    db_path: Optional[str] = None,
) -> None:
    if not normalized_title:
        return
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        conn.execute(
            """
            INSERT INTO catalog_cache (
                normalized_title, steam_appid, genres_json,
                release_year, critic_score, cover_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_title) DO UPDATE SET
                steam_appid = COALESCE(excluded.steam_appid, catalog_cache.steam_appid),
                genres_json = CASE
                    WHEN excluded.genres_json IS NOT NULL AND excluded.genres_json != '[]' AND excluded.genres_json != ''
                    THEN excluded.genres_json
                    ELSE catalog_cache.genres_json
                END,
                release_year = COALESCE(excluded.release_year, catalog_cache.release_year),
                critic_score = COALESCE(excluded.critic_score, catalog_cache.critic_score),
                cover_url = COALESCE(excluded.cover_url, catalog_cache.cover_url),
                updated_at = excluded.updated_at
            """,
            (
                normalized_title,
                steam_appid,
                genres_json,
                release_year,
                critic_score,
                cover_url,
                updated_at,
            ),
        )


def get_user_connections(user_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    if not user_id:
        return {"steam_id": "", "xbl_api_key": "", "psn_npsso": ""}
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT steam_id, xbl_api_key_encrypted, psn_npsso_encrypted FROM user_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {"steam_id": "", "xbl_api_key": "", "psn_npsso": ""}
        return {
            "steam_id": str(row["steam_id"] or ""),
            "xbl_api_key": str(row["xbl_api_key_encrypted"] or ""),
            "psn_npsso": str(row["psn_npsso_encrypted"] or ""),
        }


def set_user_connections(
    user_id: str,
    steam_id: Optional[str] = None,
    xbl_api_key: Optional[str] = None,
    psn_npsso: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required")
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        existing = conn.execute(
            "SELECT steam_id, xbl_api_key_encrypted, psn_npsso_encrypted FROM user_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        final_steam = steam_id.strip() if steam_id is not None else (existing["steam_id"] if existing else "")
        final_xbl = xbl_api_key.strip() if xbl_api_key is not None else (existing["xbl_api_key_encrypted"] if existing else "")
        final_psn = psn_npsso.strip() if psn_npsso is not None else (existing["psn_npsso_encrypted"] if existing else "")

        conn.execute(
            """
            INSERT INTO user_connections (user_id, steam_id, xbl_api_key_encrypted, psn_npsso_encrypted, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                steam_id = excluded.steam_id,
                xbl_api_key_encrypted = excluded.xbl_api_key_encrypted,
                psn_npsso_encrypted = excluded.psn_npsso_encrypted,
                updated_at = excluded.updated_at
            """,
            (user_id, final_steam, final_xbl, final_psn, now),
        )
    return {"steam_id": final_steam, "xbl_api_key": final_xbl, "psn_npsso": final_psn}


def list_user_oauth_tokens(user_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    if not user_id:
        return []
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        rows = conn.execute(
            """
            SELECT access_token, client_id, scope, created_at
            FROM oauth_tokens
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        result = []
        for r in rows:
            token = str(r["access_token"])
            preview = f"{token[:12]}..." if len(token) > 12 else token
            result.append(
                {
                    "access_token": token,
                    "token_preview": preview,
                    "client_id": str(r["client_id"] or ""),
                    "scope": str(r["scope"] or ""),
                    "created_at": float(r["created_at"] or 0),
                }
            )
        return result


def create_personal_access_token(
    user_id: str,
    label: str = "Personal Access Token",
    scope: str = "all",
    db_path: Optional[str] = None,
) -> str:
    if not user_id:
        raise ValueError("user_id is required")
    import secrets
    import time

    token = f"pat_{secrets.token_urlsafe(32)}"
    refresh_token = f"ref_{secrets.token_urlsafe(32)}"
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        conn.execute(
            """
            INSERT INTO oauth_tokens (access_token, refresh_token, client_id, user_id, scope, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token, refresh_token, label.strip() or "pat", user_id, scope, time.time()),
        )
    return token


def revoke_oauth_token(user_id: str, token: str, db_path: Optional[str] = None) -> bool:
    if not user_id or not token:
        return False
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        cur = conn.execute(
            "DELETE FROM oauth_tokens WHERE user_id = ? AND access_token = ?",
            (user_id, token),
        )
        return cur.rowcount > 0


