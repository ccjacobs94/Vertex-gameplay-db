"""
Central System SQLite Database layer for Vertex multi-tenancy.
Stores accounts, encrypted platform credentials, web sessions, OAuth tokens,
and the shared cross-user game catalog metadata cache.
"""

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

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

            CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user_id ON oauth_tokens(user_id);

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

