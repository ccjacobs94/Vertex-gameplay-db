"""SQLite persistence for identity, sessions, invites, PATs, and encrypted connections."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from vertex.identity.models import InviteRecord, PersonalAccessToken, UserAccount


def row_to_user(row: sqlite3.Row) -> UserAccount:
    return UserAccount(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        status=row["status"],
        status_reason=row["status_reason"],
        timezone=row["timezone"] or "auto",
        timezone_eras=json.loads(row["timezone_eras_json"] or "[]"),
        onboarding_completed=bool(row["onboarding_completed"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )


def row_to_invite(row: sqlite3.Row) -> InviteRecord:
    return InviteRecord(
        code_hash=row["code_hash"],
        created_by=row["created_by"],
        role=row["role"],
        note=row["note"] or "",
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        used_by=row["used_by"],
        used_at=row["used_at"],
        revoked_at=row["revoked_at"],
    )


def row_to_pat(row: sqlite3.Row) -> PersonalAccessToken:
    return PersonalAccessToken(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        token_prefix=row["token_prefix"],
        scopes=tuple(json.loads(row["scopes_json"] or "[]")),
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )


def log_account_event(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    actor_id: str | None,
    event: str,
    detail: str = "",
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO account_events (user_id, actor_id, event, detail, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, actor_id, event, detail, created_at),
    )


def revoke_user_credentials(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    now_iso: str,
    keep_session_hash: str | None = None,
    revoke_pats: bool = True,
) -> None:
    if keep_session_hash:
        conn.execute(
            "DELETE FROM web_sessions WHERE user_id = ? AND session_id != ?",
            (user_id, keep_session_hash),
        )
    else:
        conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
    if revoke_pats:
        conn.execute(
            "UPDATE personal_access_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now_iso, user_id),
        )


def count_active_admins(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin' AND status = 'active'"
    ).fetchone()
    return int(row["cnt"]) if row else 0


def get_connection_row(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM user_connections WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def list_instance_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM instance_settings ORDER BY key ASC").fetchall()
    return {r["key"]: r["value"] for r in rows}
