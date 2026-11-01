"""
Central System SQLite Database layer for Vertex multi-tenancy.
Stores accounts, encrypted platform credentials, web sessions, OAuth tokens,
and the shared cross-user game catalog metadata cache.
"""

import os
import sqlite3
from pathlib import Path

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
