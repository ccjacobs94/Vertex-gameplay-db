"""
Central System SQLite Database layer for Vertex multi-tenancy.
Stores accounts, encrypted platform credentials, web sessions, OAuth tokens,
and the shared cross-user game catalog metadata cache.
"""

import hashlib
import json
import logging
import os
import re
import base64
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, MultiFernet

import passwords

logger = logging.getLogger("gamehub.system_db")

DEFAULT_SYSTEM_DB_PATH = (
    os.environ.get("VERTEX_SYSTEM_DB_PATH")
    or os.environ.get("SYSTEM_DB_PATH")
    or str(Path(__file__).resolve().parent / "data" / "system.db")
)


def _fernet_from_secret(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


# Legacy key kept ONLY so rows written before GAMEHUB_SECRET_KEY was configured can
# still be decrypted (and rotated to the primary key by migrate_secrets_at_rest()).
_LEGACY_FALLBACK_SECRET = "gamehub-envelope-key-fallback"


def _get_primary_fernet() -> Optional[Fernet]:
    sec = os.environ.get("GAMEHUB_SECRET_KEY", "").strip()
    return _fernet_from_secret(sec) if sec else None


def _get_encryption_cipher() -> MultiFernet:
    # MultiFernet encrypts with the first key and decrypts with any of them.
    keys = []
    sec = os.environ.get("GAMEHUB_SECRET_KEY", "").strip()
    if sec:
        keys.append(_fernet_from_secret(sec))
    pw = os.environ.get("VERTEX_PASSWORD", "").strip()
    if pw and pw != sec:
        keys.append(_fernet_from_secret(pw))
    fallback = _LEGACY_FALLBACK_SECRET
    if fallback != sec and fallback != pw:
        keys.append(_fernet_from_secret(fallback))
    return MultiFernet(keys)


def warn_if_secret_key_missing() -> bool:
    """Log a CRITICAL warning when GAMEHUB_SECRET_KEY is unset. Returns True if configured."""
    if os.environ.get("GAMEHUB_SECRET_KEY", "").strip():
        return True
    logger.critical(
        "GAMEHUB_SECRET_KEY is not set: platform credentials are encrypted with a legacy/"
        "derived key. Set a strong GAMEHUB_SECRET_KEY in .env and restart to re-encrypt them."
    )
    return False


def encrypt_credential(value: Optional[str]) -> str:
    if not value:
        return ""
    if value.startswith("gAAAAA"):
        return value
    cipher = _get_encryption_cipher()
    return cipher.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_credential(value: Optional[str]) -> str:
    if not value:
        return ""
    if not value.startswith("gAAAAA"):
        return value
    try:
        cipher = _get_encryption_cipher()
        return cipher.decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


ENCRYPTED_INSTANCE_SETTINGS = ("vapid_private_key",)


def _reencrypt_to_primary(value: str, primary: Fernet, cipher: MultiFernet) -> Optional[str]:
    """Return value re-encrypted under the primary key, or None if no change is needed/possible."""
    if not value:
        return None
    if not value.startswith("gAAAAA"):
        return primary.encrypt(value.encode("utf-8")).decode("ascii")
    try:
        primary.decrypt(value.encode("ascii"))
        return None  # already under the primary key
    except Exception:
        pass
    try:
        return cipher.rotate(value.encode("ascii")).decode("ascii")
    except Exception:
        return None  # undecryptable with any known key: leave untouched


def migrate_secrets_at_rest(db_path: Optional[str] = None) -> int:
    """Idempotent startup migration: re-encrypt user_connections credentials and
    encrypted instance settings under GAMEHUB_SECRET_KEY. No-op when it is unset.
    Returns the number of values rewritten. Never logs secret values."""
    primary = _get_primary_fernet()
    if primary is None:
        return 0
    cipher = _get_encryption_cipher()
    target = db_path or DEFAULT_SYSTEM_DB_PATH
    changed = 0
    with get_system_connection(target) as conn:
        rows = conn.execute(
            "SELECT user_id, xbl_api_key_encrypted, psn_npsso_encrypted FROM user_connections"
        ).fetchall()
        for r in rows:
            for col in ("xbl_api_key_encrypted", "psn_npsso_encrypted"):
                new_val = _reencrypt_to_primary(str(r[col] or ""), primary, cipher)
                if new_val is not None:
                    conn.execute(
                        f"UPDATE user_connections SET {col} = ? WHERE user_id = ?",
                        (new_val, r["user_id"]),
                    )
                    changed += 1
        for key in ENCRYPTED_INSTANCE_SETTINGS:
            row = conn.execute("SELECT value FROM instance_settings WHERE key = ?", (key,)).fetchone()
            if not row:
                continue
            new_val = _reencrypt_to_primary(str(row["value"] or ""), primary, cipher)
            if new_val is not None:
                conn.execute("UPDATE instance_settings SET value = ? WHERE key = ?", (new_val, key))
                changed += 1
    if changed:
        logger.info("Re-encrypted %d secret value(s) under GAMEHUB_SECRET_KEY", changed)
    return changed



def get_system_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path or DEFAULT_SYSTEM_DB_PATH
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_system_db(db_path: Optional[str] = None) -> None:
    target = db_path or DEFAULT_SYSTEM_DB_PATH
    with get_system_connection(target) as conn:
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

        # Additive migrations for users table (account lifecycle & roles)
        user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "status" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        if "status_reason" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN status_reason TEXT")
        if "role" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        if "updated_at" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN updated_at TEXT")
        if "last_login_at" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        if "timezone" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'auto'")
        if "timezone_eras" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN timezone_eras TEXT DEFAULT '[]'")

        # Additive migration for web_sessions table
        sess_cols = {r["name"] for r in conn.execute("PRAGMA table_info(web_sessions)")}
        if "username" not in sess_cols:
            conn.execute("ALTER TABLE web_sessions ADD COLUMN username TEXT")

        # Additive migration for user_connections table
        conn_cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_connections)")}
        if "onboarding_completed" not in conn_cols:
            conn.execute("ALTER TABLE user_connections ADD COLUMN onboarding_completed INTEGER NOT NULL DEFAULT 0")
        if "xbox_gamertag" not in conn_cols:
            conn.execute("ALTER TABLE user_connections ADD COLUMN xbox_gamertag TEXT")
        if "xbox_xuid" not in conn_cols:
            conn.execute("ALTER TABLE user_connections ADD COLUMN xbox_xuid TEXT")

        # Migrate legacy plaintext platform credentials to Fernet-encrypted format
        try:
            uc_rows = conn.execute("SELECT user_id, xbl_api_key_encrypted, psn_npsso_encrypted FROM user_connections").fetchall()
            for r in uc_rows:
                u_id = r["user_id"]
                xbl = r["xbl_api_key_encrypted"] or ""
                psn = r["psn_npsso_encrypted"] or ""
                needs_update = False
                if xbl and not xbl.startswith("gAAAAA"):
                    xbl = encrypt_credential(xbl)
                    needs_update = True
                if psn and not psn.startswith("gAAAAA"):
                    psn = encrypt_credential(psn)
                    needs_update = True
                if needs_update:
                    conn.execute(
                        "UPDATE user_connections SET xbl_api_key_encrypted = ?, psn_npsso_encrypted = ? WHERE user_id = ?",
                        (xbl, psn, u_id),
                    )
        except Exception:
            pass

        # New tables for account auditing and instance settings
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS account_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                actor_id TEXT,
                event TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account_events_user ON account_events(user_id);

            CREATE TABLE IF NOT EXISTS instance_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invites (
                code_hash TEXT PRIMARY KEY,
                created_by TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                used_by TEXT,
                used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_invites_used ON invites(used_by);

            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                customer_id TEXT,
                subscription_id TEXT,
                status TEXT NOT NULL,
                current_period_end TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(customer_id);

            CREATE TABLE IF NOT EXISTS billing_events_seen (
                event_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL
            );
            """
        )





def get_user_id_by_username(username: str, db_path: Optional[str] = None) -> Optional[str]:
    if not username:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username.strip(),)
        ).fetchone()
        return str(row["id"]) if row else None


def get_user(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not username:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_timezone_settings(user_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    user = get_user(user_id, db_path=db_path)
    if not user:
        return {"timezone": "auto", "timezone_eras": []}
    tz = user.get("timezone") or "auto"
    eras_raw = user.get("timezone_eras") or "[]"
    try:
        eras = json.loads(eras_raw) if isinstance(eras_raw, str) else (eras_raw or [])
    except Exception:
        eras = []
    return {"timezone": tz, "timezone_eras": eras}


def update_user_timezone_settings(
    user_id: str,
    tz_name: str,
    timezone_eras: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    tz_val = (tz_name or "auto").strip()
    eras_json = json.dumps(timezone_eras or [])
    now = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        conn.execute(
            "UPDATE users SET timezone = ?, timezone_eras = ?, updated_at = ? WHERE id = ?",
            (tz_val, eras_json, now, user_id),
        )
    return {"timezone": tz_val, "timezone_eras": timezone_eras or []}


def validate_username(username: str) -> None:
    """Validate username syntax for safety and directory name compatibility."""
    if not username:
        raise ValueError("Username cannot be empty")
    cleaned = username.strip()
    if len(cleaned) < 3 or len(cleaned) > 32:
        raise ValueError("Username must be between 3 and 32 characters long")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", cleaned):
        raise ValueError(
            "Username can only contain alphanumeric characters, underscores, hyphens, and periods"
        )
    if ".." in cleaned or cleaned.startswith(".") or cleaned.startswith("-"):
        raise ValueError("Invalid username format")


def create_user(
    username: str,
    password: str,
    email: Optional[str] = None,
    role: str = "user",
    status: str = "active",
    status_reason: Optional[str] = None,
    actor_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    validate_username(username)
    if role not in ("admin", "user"):
        raise ValueError(f"Invalid role: {role}")
    if status not in ("active", "pending", "inactive"):
        raise ValueError(f"Invalid status: {status}")

    pw_err = passwords.validate_password_policy(password)
    if pw_err:
        raise ValueError(pw_err)

    clean_user = username.strip()
    clean_id = clean_user.lower()
    clean_email = email.strip() if email else None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)

    hashed = passwords.hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    with get_system_connection(target_path) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (clean_user,)
        ).fetchone()
        if existing:
            raise ValueError(f"Username '{clean_user}' is already taken")

        conn.execute(
            """
            INSERT INTO users (id, username, email, password_hash, role, status, status_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (clean_id, clean_user, clean_email, hashed, role, status, status_reason, now, now),
        )
        conn.execute(
            """
            INSERT INTO account_events (user_id, actor_id, event, detail, created_at)
            VALUES (?, ?, 'user_created', ?, ?)
            """,
            (clean_id, actor_id, f"role={role},status={status}", now),
        )

    # Initialize the user's personal games.db so it's ready for immediate use
    try:
        import db as gdb
        user_db_file = Path(gdb.USERS_DB_DIR) / clean_id / "games.db"
        gdb.init_db(str(user_db_file))
    except Exception:
        pass

    return clean_id


def set_user_password(
    user_id: str,
    password: str,
    actor_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    pw_err = passwords.validate_password_policy(password)
    if pw_err:
        raise ValueError(pw_err)

    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    hashed = passwords.hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    with get_system_connection(target_path) as conn:
        res = conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hashed, now, user_id),
        )
        if res.rowcount == 0:
            raise ValueError(f"User '{user_id}' not found")
        conn.execute(
            """
            INSERT INTO account_events (user_id, actor_id, event, detail, created_at)
            VALUES (?, ?, 'password_changed', '', ?)
            """,
            (user_id, actor_id, now),
        )


def set_user_status(
    user_id: str,
    status: str,
    reason: Optional[str] = None,
    actor_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    if status == "disabled":
        status = "inactive"
        if not reason:
            reason = "admin_suspended"

    if status not in ("active", "pending", "inactive"):
        raise ValueError(f"Invalid status: {status}")

    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now = datetime.now(timezone.utc).isoformat()

    with get_system_connection(target_path) as conn:
        user = conn.execute("SELECT id, role, status FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise ValueError(f"User '{user_id}' not found")

        # Guardrails: cannot deactivate last admin or self
        if status != "active" and user["role"] == "admin" and user["status"] == "active":
            admins_count = conn.execute(
                "SELECT COUNT(*) as c FROM users WHERE role = 'admin' AND status = 'active'"
            ).fetchone()["c"]
            if admins_count <= 1:
                raise ValueError("Cannot deactivate the last active admin")

        if actor_id and actor_id == user_id and status != "active":
            raise ValueError("Cannot deactivate your own account")

        conn.execute(
            "UPDATE users SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
            (status, reason, now, user_id),
        )
        conn.execute(
            """
            INSERT INTO account_events (user_id, actor_id, event, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, actor_id, f"status_{status}", reason or "", now),
        )

        # On deactivation: revoke web sessions and OAuth/PAT tokens
        if status != "active":
            conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))


def delete_user(
    user_id: str,
    actor_id: Optional[str] = None,
    db_path: Optional[str] = None,
    users_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Permanently delete a user account and all associated data, including:
    - User directory and library database on disk
    - User connections and platform credentials
    - Active sessions and OAuth/PAT tokens
    - Subscriptions, invites, and audit logs
    """
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)

    with get_system_connection(target_path) as conn:
        user = conn.execute("SELECT id, username, role, status FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise ValueError(f"User '{user_id}' not found")

        # Guardrails: cannot delete self or last active admin
        if actor_id and actor_id == user_id:
            raise ValueError("Cannot delete your own account")

        if user["role"] == "admin" and user["status"] == "active":
            admins_count = conn.execute(
                "SELECT COUNT(*) as c FROM users WHERE role = 'admin' AND status = 'active'"
            ).fetchone()["c"]
            if admins_count <= 1:
                raise ValueError("Cannot delete the last active admin")

        username = user["username"]

        # Revoke sessions for username in memory/auth cache if web_auth is present
        try:
            import web_auth
            web_auth.revoke_sessions_for_username(username)
        except Exception:
            pass

        # Purge all user records in system.db
        conn.execute("DELETE FROM web_sessions WHERE user_id = ? OR LOWER(username) = LOWER(?)", (user_id, username))
        conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_connections WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM account_events WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM invites WHERE created_by = ? OR created_by = ?", (user_id, username))
        conn.execute("UPDATE invites SET used_by = NULL, used_at = NULL WHERE used_by = ? OR used_by = ?", (user_id, username))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # Erase user's library and files on disk
    try:
        if users_dir:
            base_dir = Path(users_dir)
        else:
            import db as gdb
            base_dir = Path(gdb.USERS_DB_DIR)

        for folder_name in {user_id, str(username).lower(), str(username)}:
            u_path = base_dir / folder_name
            if u_path.exists():
                if u_path.is_dir():
                    shutil.rmtree(str(u_path), ignore_errors=True)
                else:
                    u_path.unlink(missing_ok=True)
    except Exception:
        pass

    return {"deleted_user_id": user_id, "username": username}


def set_user_role(
    user_id: str,
    role: str,
    actor_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    if role not in ("admin", "user"):
        raise ValueError(f"Invalid role: {role}")

    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now = datetime.now(timezone.utc).isoformat()

    with get_system_connection(target_path) as conn:
        user = conn.execute("SELECT id, role, status FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise ValueError(f"User '{user_id}' not found")

        # Guardrail: cannot demote last active admin
        if user["role"] == "admin" and role != "admin" and user["status"] == "active":
            admins_count = conn.execute(
                "SELECT COUNT(*) as c FROM users WHERE role = 'admin' AND status = 'active'"
            ).fetchone()["c"]
            if admins_count <= 1:
                raise ValueError("Cannot demote the last active admin")

        conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now, user_id))
        conn.execute(
            """
            INSERT INTO account_events (user_id, actor_id, event, detail, created_at)
            VALUES (?, ?, ?, '', ?)
            """,
            (user_id, actor_id, f"role_{role}", now),
        )


def count_active_users(db_path: Optional[str] = None) -> int:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM users WHERE status = 'active'").fetchone()
        return int(row["c"]) if row else 0


def count_active_admins(db_path: Optional[str] = None) -> int:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM users WHERE role = 'admin' AND status = 'active'"
        ).fetchone()
        return int(row["c"]) if row else 0


def has_users_with_passwords(db_path: Optional[str] = None) -> bool:
    """Return True if any user in system.db has a configured password hash."""
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    try:
        init_system_db(target_path)
        with get_system_connection(target_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM users
                WHERE password_hash IS NOT NULL
                  AND TRIM(password_hash) != ''
                  AND LOWER(TRIM(password_hash)) != 'managed_by_web_auth'
                LIMIT 1
                """
            ).fetchone()
            return bool(row)
    except Exception:
        return False


def list_users(status: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        if status:
            rows = conn.execute(
                """
                SELECT id, username, email, role, status, status_reason, created_at, updated_at, last_login_at
                FROM users WHERE status = ? ORDER BY created_at ASC
                """,
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, username, email, role, status, status_reason, created_at, updated_at, last_login_at
                FROM users ORDER BY created_at ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]


def record_login(user_id: str, db_path: Optional[str] = None) -> None:
    if not user_id:
        return
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))


def get_setting(key: str, default: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute("SELECT value FROM instance_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str, db_path: Optional[str] = None) -> None:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        conn.execute(
            """
            INSERT INTO instance_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value), now),
        )


def ensure_bootstrap_admin(username: str, db_path: Optional[str] = None) -> str:
    """Ensure bootstrap user exists and has role='admin' on initial setup."""
    clean_user = (username or "chameleon94").strip()
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT id, role, status FROM users WHERE LOWER(username) = LOWER(?)", (clean_user,)
        ).fetchone()
        if row:
            # Preserve existing user status; do not reactivate suspended admins
            return str(row["id"])
        uid = ensure_user(clean_user, db_path=target_path)
        conn.execute(
            "UPDATE users SET role = 'admin', status = 'active' WHERE id = ?",
            (uid,),
        )
        return uid


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


def list_active_user_ids(db_path: Optional[str] = None) -> List[str]:
    """Return all user IDs that are active. In instances before status columns, returns all user IDs."""
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "status" in cols:
            rows = conn.execute(
                "SELECT id FROM users WHERE status = 'active' ORDER BY created_at ASC"
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM users ORDER BY created_at ASC").fetchall()
        return [str(r["id"]) for r in rows]



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
        return {"steam_id": "", "xbl_api_key": "", "xbox_gamertag": "", "xbox_xuid": "", "psn_npsso": "", "onboarding_completed": False}
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM user_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {"steam_id": "", "xbl_api_key": "", "xbox_gamertag": "", "xbox_xuid": "", "psn_npsso": "", "onboarding_completed": False}
        keys = row.keys()
        return {
            "steam_id": str(row["steam_id"] or ""),
            "xbl_api_key": decrypt_credential(str(row["xbl_api_key_encrypted"] or "")),
            "xbox_gamertag": str(row["xbox_gamertag"] or "") if "xbox_gamertag" in keys else "",
            "xbox_xuid": str(row["xbox_xuid"] or "") if "xbox_xuid" in keys else "",
            "psn_npsso": decrypt_credential(str(row["psn_npsso_encrypted"] or "")),
            "onboarding_completed": bool(row["onboarding_completed"]),
        }


def set_user_connections(
    user_id: str,
    steam_id: Optional[str] = None,
    xbl_api_key: Optional[str] = None,
    xbox_gamertag: Optional[str] = None,
    xbox_xuid: Optional[str] = None,
    psn_npsso: Optional[str] = None,
    onboarding_completed: Optional[bool] = None,
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
            "SELECT * FROM user_connections WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        e_keys = existing.keys() if existing else []
        existing_xbl = decrypt_credential(existing["xbl_api_key_encrypted"]) if existing else ""
        existing_psn = decrypt_credential(existing["psn_npsso_encrypted"]) if existing else ""
        final_steam = steam_id.strip() if steam_id is not None else (existing["steam_id"] if existing else "")
        final_xbl = xbl_api_key.strip() if xbl_api_key is not None else existing_xbl
        final_gt = xbox_gamertag.strip() if xbox_gamertag is not None else (existing["xbox_gamertag"] if existing and "xbox_gamertag" in e_keys else "")
        final_xuid = xbox_xuid.strip() if xbox_xuid is not None else (existing["xbox_xuid"] if existing and "xbox_xuid" in e_keys else "")
        final_psn = psn_npsso.strip() if psn_npsso is not None else existing_psn
        final_onboarding = int(onboarding_completed) if onboarding_completed is not None else (existing["onboarding_completed"] if existing else 0)

        enc_xbl = encrypt_credential(final_xbl) if final_xbl else ""
        enc_psn = encrypt_credential(final_psn) if final_psn else ""

        conn.execute(
            """
            INSERT INTO user_connections (user_id, steam_id, xbl_api_key_encrypted, xbox_gamertag, xbox_xuid, psn_npsso_encrypted, updated_at, onboarding_completed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                steam_id = excluded.steam_id,
                xbl_api_key_encrypted = excluded.xbl_api_key_encrypted,
                xbox_gamertag = excluded.xbox_gamertag,
                xbox_xuid = excluded.xbox_xuid,
                psn_npsso_encrypted = excluded.psn_npsso_encrypted,
                updated_at = excluded.updated_at,
                onboarding_completed = excluded.onboarding_completed
            """,
            (user_id, final_steam, enc_xbl, final_gt, final_xuid, enc_psn, now, final_onboarding),
        )
    return {
        "steam_id": final_steam,
        "xbl_api_key": final_xbl,
        "xbox_gamertag": final_gt,
        "xbox_xuid": final_xuid,
        "psn_npsso": final_psn,
        "onboarding_completed": bool(final_onboarding),
    }


def set_user_onboarding_completed(user_id: str, completed: bool = True, db_path: Optional[str] = None) -> None:
    if not user_id:
        return
    set_user_connections(user_id, onboarding_completed=completed, db_path=db_path)


def is_user_onboarding_completed(user_id: str, db_path: Optional[str] = None) -> bool:
    if not user_id:
        return False
    conns = get_user_connections(user_id, db_path=db_path)
    return bool(conns.get("onboarding_completed"))


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


def get_registration_policy(db_path: Optional[str] = None) -> str:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    val = get_setting(
        "registration_policy", os.environ.get("VERTEX_REGISTRATION", "disabled"), db_path=target_path
    )
    val = (val or "disabled").strip().lower()
    return val if val in ("disabled", "invite", "approval", "open") else "disabled"


def set_registration_policy(policy: str, db_path: Optional[str] = None) -> None:
    pol = (policy or "").strip().lower()
    if pol not in ("disabled", "invite", "approval", "open"):
        raise ValueError(f"Invalid registration policy: {policy}")
    set_setting("registration_policy", pol, db_path=db_path)


def create_invite(
    created_by: str,
    note: str = "",
    ttl_days: int = 7,
    db_path: Optional[str] = None,
) -> str:
    if not created_by:
        raise ValueError("created_by is required")
    code = secrets.token_urlsafe(16)
    code_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
    now_dt = datetime.now(timezone.utc)
    now_str = now_dt.isoformat()
    expires_dt = now_dt + timedelta(days=ttl_days) if ttl_days > 0 else None
    expires_str = expires_dt.isoformat() if expires_dt else None

    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        conn.execute(
            """
            INSERT INTO invites (code_hash, created_by, note, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (code_hash, created_by, (note or "").strip(), now_str, expires_str),
        )
    return code


def list_invites(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now_str = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        rows = conn.execute(
            """
            SELECT code_hash, created_by, note, created_at, expires_at, used_by, used_at
            FROM invites ORDER BY created_at DESC
            """
        ).fetchall()
        result = []
        for r in rows:
            is_used = bool(r["used_by"])
            is_expired = bool(r["expires_at"] and r["expires_at"] < now_str)
            status = "used" if is_used else ("expired" if is_expired else "active")
            result.append(
                {
                    "code_hash": r["code_hash"],
                    "code_preview": f"{r['code_hash'][:8]}...",
                    "created_by": r["created_by"],
                    "note": r["note"] or "",
                    "created_at": r["created_at"],
                    "expires_at": r["expires_at"],
                    "used_by": r["used_by"],
                    "used_at": r["used_at"],
                    "status": status,
                }
            )
        return result


def revoke_invite(code_hash: str, db_path: Optional[str] = None) -> bool:
    if not code_hash:
        return False
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        cur = conn.execute(
            "DELETE FROM invites WHERE code_hash = ? AND used_by IS NULL", (code_hash,)
        )
        return cur.rowcount > 0


def consume_invite(code: str, user_id: str, db_path: Optional[str] = None) -> bool:
    if not code or not user_id:
        return False
    code_hash = hashlib.sha256(code.strip().encode("ascii")).hexdigest()
    now_str = datetime.now(timezone.utc).isoformat()
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        cur = conn.execute(
            """
            UPDATE invites
            SET used_by = ?, used_at = ?
            WHERE code_hash = ?
              AND used_by IS NULL
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (user_id, now_str, code_hash, now_str),
        )
        return cur.rowcount > 0


def get_user_subscription(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_customer_id(customer_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not customer_id:
        return None
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            """
            SELECT u.* FROM users u
            JOIN subscriptions s ON u.id = s.user_id
            WHERE s.customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_user_subscription(
    user_id: str,
    provider: str,
    status: str,
    customer_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
    current_period_end: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    if not user_id:
        raise ValueError("user_id is required")
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, provider, customer_id, subscription_id, status, current_period_end, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                provider = excluded.provider,
                customer_id = COALESCE(excluded.customer_id, subscriptions.customer_id),
                subscription_id = COALESCE(excluded.subscription_id, subscriptions.subscription_id),
                status = excluded.status,
                current_period_end = COALESCE(excluded.current_period_end, subscriptions.current_period_end),
                updated_at = excluded.updated_at
            """,
            (user_id, provider, customer_id, subscription_id, status, current_period_end, now),
        )


def is_billing_event_seen(event_id: str, db_path: Optional[str] = None) -> bool:
    if not event_id:
        return False
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    with get_system_connection(target_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM billing_events_seen WHERE event_id = ?", (event_id,)
        ).fetchone()
        return bool(row)


def record_billing_event_seen(event_id: str, db_path: Optional[str] = None) -> None:
    if not event_id:
        return
    target_path = db_path or DEFAULT_SYSTEM_DB_PATH
    init_system_db(target_path)
    now = datetime.now(timezone.utc).isoformat()
    with get_system_connection(target_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO billing_events_seen (event_id, received_at) VALUES (?, ?)",
            (event_id, now),
        )




