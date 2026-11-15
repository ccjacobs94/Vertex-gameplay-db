"""Application services for identity, local auth, invites, PATs, and encrypted connections."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from vertex.identity.models import (
    ALLOWED_PAT_SCOPES,
    ALLOWED_ROLES,
    ConnectionSummary,
    CreatedInvite,
    CreatedPat,
    DecryptedConnections,
    InviteRecord,
    LoginRateLimiter,
    PersonalAccessToken,
    UserAccount,
)
from vertex.identity.passwords import (
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    validate_password_policy,
    verify_password,
)
from vertex.identity.repository import (
    count_active_admins,
    get_connection_row,
    list_instance_settings,
    log_account_event,
    revoke_user_credentials,
    row_to_invite,
    row_to_pat,
    row_to_user,
)
from vertex.infrastructure.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    MissingSecretKeyError,
    NotFoundError,
    RateLimitedError,
    SecretDecryptionError,
    ValidationError,
)
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.scope import UserScope, validate_user_id
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,31}$")
_SAFE_SETTING_KEYS = frozenset({"instance_name", "allow_lan_access", "default_timezone"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _validate_username(username: str) -> tuple[str, str]:
    cleaned = (username or "").strip()
    if not _USERNAME_RE.match(cleaned):
        raise ValidationError(
            "Username must be 2–32 characters using letters, digits, dots, underscores, or hyphens"
        )
    user_id = validate_user_id(cleaned.lower())
    return cleaned, user_id


def _validate_timezone(tz_name: str) -> str:
    cleaned = (tz_name or "").strip()
    if cleaned in ("", "auto"):
        return "auto"
    try:
        ZoneInfo(cleaned)
    except Exception as exc:
        raise ValidationError(f"Unknown IANA timezone '{cleaned}'") from exc
    return cleaned


class IdentityService:
    """Manages local users, bootstrap, sessions, invites, PATs, and encrypted connections."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        secret_box: SecretBox,
        job_queue: JobQueue | None = None,
        *,
        rate_limiter: LoginRateLimiter | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.secret_box = secret_box
        self.job_queue = job_queue
        self.rate_limiter = rate_limiter or LoginRateLimiter(
            max_attempts=db_manager.settings.login_max_attempts,
            window_seconds=db_manager.settings.login_window_seconds,
        )

    def is_bootstrap_open(self) -> bool:
        with self.db_manager.system_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            return int(row["cnt"]) == 0

    def bootstrap_admin(
        self,
        *,
        username: str,
        password: str,
        user_agent: str = "bootstrap",
    ) -> tuple[UserAccount, str]:
        cleaned_name, user_id = _validate_username(username)
        ok, msg = validate_password_policy(password)
        if not ok:
            raise ValidationError(msg)

        now = _utc_now()
        now_iso = _iso(now)
        pw_hash = hash_password(password)
        raw_session = generate_opaque_token("vtxs_")
        session_hash = hash_opaque_token(raw_session)

        with self.db_manager.system_connection(immediate=True) as conn:
            existing = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            if int(existing["cnt"]) > 0:
                raise ConflictError("Initial bootstrap is already closed")

            conn.execute(
                """
                INSERT INTO users (
                    id, username, role, status, status_reason, password_hash,
                    timezone, timezone_eras_json, onboarding_completed, created_at, updated_at, last_login_at
                ) VALUES (?, ?, 'admin', 'pending_provisioning', 'bootstrap', ?, 'auto', '[]', 0, ?, ?, ?)
                """,
                (user_id, cleaned_name, pw_hash, now_iso, now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO user_connections (user_id, updated_at) VALUES (?, ?)",
                (user_id, now_iso),
            )
            log_account_event(
                conn,
                user_id=user_id,
                actor_id=user_id,
                event="bootstrap_admin_created",
                created_at=now_iso,
            )

        scope = UserScope(user_id=user_id, username=cleaned_name, role="admin", status="pending_provisioning")
        try:
            self.db_manager.ensure_user_library(scope, require_active=False)
        except Exception:
            with self.db_manager.system_connection(immediate=True) as conn:
                conn.execute("DELETE FROM users WHERE id = ? AND status = 'pending_provisioning'", (user_id,))
            raise

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                "UPDATE users SET status = 'active', status_reason = NULL, updated_at = ? WHERE id = ?",
                (now_iso, user_id),
            )
            created_ts = time.time()
            expires_ts = created_ts + self.db_manager.settings.session_ttl_seconds
            conn.execute(
                """
                INSERT INTO web_sessions (session_id, user_id, created_at, expires_at, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_hash, user_id, created_ts, expires_ts, user_agent[:256]),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return row_to_user(row), raw_session

    def login(
        self,
        *,
        username: str,
        password: str,
        client_ip: str = "127.0.0.1",
        user_agent: str = "",
    ) -> tuple[UserScope, str]:
        cleaned_user = (username or "").strip()
        allowed, retry_after = self.rate_limiter.check_allowed(client_ip, cleaned_user)
        if not allowed:
            raise RateLimitedError(f"Too many failed login attempts. Retry after {retry_after}s")

        with self.db_manager.system_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                (cleaned_user,),
            ).fetchone()

        if row is None or not verify_password(password, row["password_hash"]):
            self.rate_limiter.record_failure(client_ip, cleaned_user)
            raise AuthenticationError("Invalid username or password")

        user = row_to_user(row)
        if not user.is_active:
            self.rate_limiter.record_failure(client_ip, cleaned_user)
            raise AuthorizationError(f"Account is {user.status}")

        self.rate_limiter.record_success(client_ip, cleaned_user)
        raw_session = generate_opaque_token("vtxs_")
        session_hash = hash_opaque_token(raw_session)
        now_ts = time.time()
        expires_ts = now_ts + self.db_manager.settings.session_ttl_seconds
        now_iso = _iso(_utc_now())

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO web_sessions (session_id, user_id, created_at, expires_at, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_hash, user.id, now_ts, expires_ts, user_agent[:256]),
            )
            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now_iso, now_iso, user.id),
            )
        return user.to_scope(), raw_session

    def resolve_session(self, raw_session_token: str | None) -> UserScope | None:
        if not raw_session_token:
            return None
        session_hash = hash_opaque_token(raw_session_token)
        now_ts = time.time()
        with self.db_manager.system_connection() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.username, u.role, u.status, ws.expires_at
                FROM web_sessions ws
                JOIN users u ON u.id = ws.user_id
                WHERE ws.session_id = ?
                """,
                (session_hash,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now_ts or row["status"] != "active":
                conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_hash,))
                return None
            return UserScope(
                user_id=row["id"],
                username=row["username"],
                role=row["role"],
                status=row["status"],
            )

    def logout_session(self, raw_session_token: str | None) -> None:
        if not raw_session_token:
            return
        session_hash = hash_opaque_token(raw_session_token)
        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_hash,))

    def get_account(self, scope: UserScope) -> UserAccount:
        scope.require_active()
        with self.db_manager.system_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (scope.user_id,)).fetchone()
            if row is None:
                raise NotFoundError("Account not found")
            return row_to_user(row)

    def complete_onboarding(self, scope: UserScope) -> UserAccount:
        scope.require_active()
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                "UPDATE users SET onboarding_completed = 1, updated_at = ? WHERE id = ?",
                (now_iso, scope.user_id),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (scope.user_id,)).fetchone()
            return row_to_user(row)

    def update_timezone(
        self,
        scope: UserScope,
        *,
        timezone_name: str,
        timezone_eras: list[dict[str, str]] | None = None,
    ) -> UserAccount:
        scope.require_active()
        tz = _validate_timezone(timezone_name)
        now_iso = _iso(_utc_now())

        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (scope.user_id,)).fetchone()
            if row is None:
                raise NotFoundError("Account not found")
            if timezone_eras is not None:
                validated_eras = []
                for era in timezone_eras:
                    era_tz = _validate_timezone(str(era.get("tz") or ""))
                    eff = str(era.get("effective_from") or "").strip()
                    if not eff:
                        raise ValidationError("Each timezone era requires effective_from")
                    validated_eras.append({"tz": era_tz, "effective_from": eff})
                eras_json = json.dumps(validated_eras)
            else:
                eras = json.loads(row["timezone_eras_json"] or "[]")
                if not eras or eras[-1].get("tz") != tz:
                    eras.append({"tz": tz, "effective_from": now_iso})
                eras_json = json.dumps(eras)

            conn.execute(
                "UPDATE users SET timezone = ?, timezone_eras_json = ?, updated_at = ? WHERE id = ?",
                (tz, eras_json, now_iso, scope.user_id),
            )
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (scope.user_id,)).fetchone()
            return row_to_user(updated)

    def change_own_password(
        self,
        scope: UserScope,
        *,
        current_password: str,
        new_password: str,
        current_session_token: str | None = None,
    ) -> None:
        scope.require_active()
        ok, msg = validate_password_policy(new_password)
        if not ok:
            raise ValidationError(msg)

        now_iso = _iso(_utc_now())
        keep_hash = hash_opaque_token(current_session_token) if current_session_token else None
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (scope.user_id,)).fetchone()
            if row is None or not verify_password(current_password, row["password_hash"]):
                raise AuthenticationError("Current password is incorrect")
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now_iso, scope.user_id),
            )
            revoke_user_credentials(
                conn,
                user_id=scope.user_id,
                now_iso=now_iso,
                keep_session_hash=keep_hash,
                revoke_pats=True,
            )
            log_account_event(
                conn,
                user_id=scope.user_id,
                actor_id=scope.user_id,
                event="password_changed",
                created_at=now_iso,
            )

    def cli_reset_password(self, username: str, new_password: str) -> UserAccount:
        ok, msg = validate_password_policy(new_password)
        if not ok:
            raise ValidationError(msg)
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                (username.strip(),),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"User '{username}' not found")
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now_iso, row["id"]),
            )
            revoke_user_credentials(conn, user_id=row["id"], now_iso=now_iso, revoke_pats=True)
            log_account_event(
                conn,
                user_id=row["id"],
                actor_id="cli",
                event="cli_password_reset",
                created_at=now_iso,
            )
            return row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone())

    # ── Invites ─────────────────────────────────────────────────────────────

    def create_invite(
        self,
        admin_scope: UserScope,
        *,
        role: str = "member",
        note: str = "",
        ttl_hours: int = 72,
    ) -> CreatedInvite:
        admin_scope.require_admin()
        if role not in ALLOWED_ROLES:
            raise ValidationError(f"Invalid role '{role}'")
        if ttl_hours < 1 or ttl_hours > 24 * 30:
            raise ValidationError("Invite TTL must be between 1 and 720 hours")

        raw_code = generate_opaque_token("vtxi_")
        code_hash = hash_opaque_token(raw_code)
        now = _utc_now()
        now_iso = _iso(now)
        expires_iso = _iso(now + timedelta(hours=ttl_hours))

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO invites (code_hash, created_by, role, note, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (code_hash, admin_scope.user_id, role, note[:200], now_iso, expires_iso),
            )
            row = conn.execute("SELECT * FROM invites WHERE code_hash = ?", (code_hash,)).fetchone()
            return CreatedInvite(code=raw_code, record=row_to_invite(row))

    def list_invites(self, admin_scope: UserScope) -> list[InviteRecord]:
        admin_scope.require_admin()
        with self.db_manager.system_connection() as conn:
            rows = conn.execute("SELECT * FROM invites ORDER BY created_at DESC").fetchall()
            return [row_to_invite(r) for r in rows]

    def revoke_invite(self, admin_scope: UserScope, code_hash: str) -> None:
        admin_scope.require_admin()
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE invites SET revoked_at = ? WHERE code_hash = ? AND used_by IS NULL AND revoked_at IS NULL",
                (now_iso, code_hash),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Active unused invite not found")

    def accept_invite(
        self,
        *,
        invite_code: str,
        username: str,
        password: str,
        user_agent: str = "",
    ) -> tuple[UserAccount, str]:
        cleaned_name, user_id = _validate_username(username)
        ok, msg = validate_password_policy(password)
        if not ok:
            raise ValidationError(msg)

        code_hash = hash_opaque_token((invite_code or "").strip())
        now = _utc_now()
        now_iso = _iso(now)
        pw_hash = hash_password(password)
        raw_session = generate_opaque_token("vtxs_")
        session_hash = hash_opaque_token(raw_session)

        with self.db_manager.system_connection(immediate=True) as conn:
            inv = conn.execute("SELECT * FROM invites WHERE code_hash = ?", (code_hash,)).fetchone()
            if inv is None or inv["revoked_at"] is not None or inv["used_by"] is not None:
                raise AuthenticationError("Invitation is invalid or already used")
            if inv["expires_at"] and datetime.fromisoformat(inv["expires_at"]) <= now:
                raise AuthenticationError("Invitation has expired")

            existing = conn.execute(
                "SELECT id FROM users WHERE id = ? OR LOWER(username) = LOWER(?)",
                (user_id, cleaned_name),
            ).fetchone()
            if existing is not None:
                raise ConflictError("Username is already registered")

            role = inv["role"] if inv["role"] in ALLOWED_ROLES else "member"
            cur = conn.execute(
                """
                UPDATE invites
                SET used_by = ?, used_at = ?
                WHERE code_hash = ? AND used_by IS NULL AND revoked_at IS NULL
                """,
                (user_id, now_iso, code_hash),
            )
            if cur.rowcount != 1:
                raise ConflictError("Invitation was already consumed concurrently")

            conn.execute(
                """
                INSERT INTO users (
                    id, username, role, status, status_reason, password_hash,
                    timezone, timezone_eras_json, onboarding_completed, created_at, updated_at, last_login_at
                ) VALUES (?, ?, ?, 'pending_provisioning', 'invite_accept', ?, 'auto', '[]', 0, ?, ?, ?)
                """,
                (user_id, cleaned_name, role, pw_hash, now_iso, now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO user_connections (user_id, updated_at) VALUES (?, ?)",
                (user_id, now_iso),
            )
            log_account_event(
                conn,
                user_id=user_id,
                actor_id=inv["created_by"],
                event="invite_accepted",
                detail=f"role={role}",
                created_at=now_iso,
            )

        scope = UserScope(user_id=user_id, username=cleaned_name, role=role, status="pending_provisioning")
        try:
            self.db_manager.ensure_user_library(scope, require_active=False)
        except Exception:
            with self.db_manager.system_connection(immediate=True) as conn:
                conn.execute("DELETE FROM users WHERE id = ? AND status = 'pending_provisioning'", (user_id,))
                conn.execute(
                    "UPDATE invites SET used_by = NULL, used_at = NULL WHERE code_hash = ? AND used_by = ?",
                    (code_hash, user_id),
                )
            raise

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                "UPDATE users SET status = 'active', status_reason = NULL, updated_at = ? WHERE id = ?",
                (now_iso, user_id),
            )
            now_ts = time.time()
            conn.execute(
                """
                INSERT INTO web_sessions (session_id, user_id, created_at, expires_at, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_hash,
                    user_id,
                    now_ts,
                    now_ts + self.db_manager.settings.session_ttl_seconds,
                    user_agent[:256],
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return row_to_user(row), raw_session

    # ── Administration ──────────────────────────────────────────────────────

    def list_users(self, admin_scope: UserScope) -> list[UserAccount]:
        admin_scope.require_admin()
        with self.db_manager.system_connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
            return [row_to_user(r) for r in rows]

    def admin_create_user(
        self,
        admin_scope: UserScope,
        *,
        username: str,
        password: str,
        role: str = "member",
    ) -> UserAccount:
        admin_scope.require_admin()
        cleaned_name, user_id = _validate_username(username)
        if role not in ALLOWED_ROLES:
            raise ValidationError(f"Invalid role '{role}'")
        ok, msg = validate_password_policy(password)
        if not ok:
            raise ValidationError(msg)

        now_iso = _iso(_utc_now())
        pw_hash = hash_password(password)
        with self.db_manager.system_connection(immediate=True) as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE id = ? OR LOWER(username) = LOWER(?)",
                (user_id, cleaned_name),
            ).fetchone()
            if existing is not None:
                raise ConflictError("Username already exists")

            conn.execute(
                """
                INSERT INTO users (
                    id, username, role, status, status_reason, password_hash,
                    timezone, timezone_eras_json, onboarding_completed, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending_provisioning', 'admin_create', ?, 'auto', '[]', 0, ?, ?)
                """,
                (user_id, cleaned_name, role, pw_hash, now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO user_connections (user_id, updated_at) VALUES (?, ?)",
                (user_id, now_iso),
            )

        scope = UserScope(user_id=user_id, username=cleaned_name, role=role, status="pending_provisioning")
        try:
            self.db_manager.ensure_user_library(scope, require_active=False)
        except Exception:
            with self.db_manager.system_connection(immediate=True) as conn:
                conn.execute("DELETE FROM users WHERE id = ? AND status = 'pending_provisioning'", (user_id,))
            raise

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                "UPDATE users SET status = 'active', status_reason = NULL, updated_at = ? WHERE id = ?",
                (now_iso, user_id),
            )
            log_account_event(
                conn,
                user_id=user_id,
                actor_id=admin_scope.user_id,
                event="admin_user_created",
                detail=f"role={role}",
                created_at=now_iso,
            )
            return row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def admin_update_user(
        self,
        admin_scope: UserScope,
        target_user_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
        status_reason: str | None = None,
    ) -> UserAccount:
        admin_scope.require_admin()
        target_id = validate_user_id(target_user_id)
        if role is not None and role not in ALLOWED_ROLES:
            raise ValidationError(f"Invalid role '{role}'")
        if status is not None and status not in ("active", "suspended"):
            raise ValidationError(f"Invalid target status '{status}'")

        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise NotFoundError("Target user not found")

            new_role = role if role is not None else row["role"]
            new_status = status if status is not None else row["status"]
            if target_id == admin_scope.user_id:
                if new_role != "admin":
                    raise ValidationError("Administrators cannot demote their own role")
                if new_status != "active":
                    raise ValidationError("Administrators cannot suspend themselves")

            was_active_admin = row["role"] == "admin" and row["status"] == "active"
            will_be_active_admin = new_role == "admin" and new_status == "active"
            if was_active_admin and not will_be_active_admin and count_active_admins(conn) <= 1:
                raise ConflictError("Cannot demote or suspend the last active administrator")

            reason = status_reason if status_reason is not None else row["status_reason"]
            if new_status == "active" and status == "active":
                reason = status_reason

            conn.execute(
                """
                UPDATE users
                SET role = ?, status = ?, status_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_role, new_status, reason, now_iso, target_id),
            )
            if new_status != "active":
                revoke_user_credentials(conn, user_id=target_id, now_iso=now_iso, revoke_pats=True)

            log_account_event(
                conn,
                user_id=target_id,
                actor_id=admin_scope.user_id,
                event="admin_user_updated",
                detail=f"role={new_role},status={new_status}",
                created_at=now_iso,
            )
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()

        if new_status != "active" and self.job_queue is not None:
            self.job_queue.cancel_user_jobs(target_id, f"Account transitioned to {new_status}")

        return row_to_user(updated)

    def admin_reset_password(
        self,
        admin_scope: UserScope,
        target_user_id: str,
        *,
        new_password: str,
    ) -> UserAccount:
        admin_scope.require_admin()
        target_id = validate_user_id(target_user_id)
        ok, msg = validate_password_policy(new_password)
        if not ok:
            raise ValidationError(msg)

        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise NotFoundError("Target user not found")
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now_iso, target_id),
            )
            revoke_user_credentials(conn, user_id=target_id, now_iso=now_iso, revoke_pats=True)
            log_account_event(
                conn,
                user_id=target_id,
                actor_id=admin_scope.user_id,
                event="admin_password_reset",
                created_at=now_iso,
            )
            return row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone())

    def admin_delete_user(self, admin_scope: UserScope, target_user_id: str) -> None:
        admin_scope.require_admin()
        target_id = validate_user_id(target_user_id)
        if target_id == admin_scope.user_id:
            raise ValidationError("Administrators cannot delete their own active account")

        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise NotFoundError("Target user not found")
            if row["role"] == "admin" and row["status"] == "active" and count_active_admins(conn) <= 1:
                raise ConflictError("Cannot delete the last active administrator")

            conn.execute(
                "UPDATE users SET status = 'deleting', status_reason = 'admin_deleted', updated_at = ? WHERE id = ?",
                (now_iso, target_id),
            )
            revoke_user_credentials(conn, user_id=target_id, now_iso=now_iso, revoke_pats=True)

        if self.job_queue is not None:
            self.job_queue.cancel_user_jobs(target_id, "Account deleted")

        user_dir = self.db_manager.users_dir / target_id
        self.db_manager.invalidate_user_cache(target_id)
        if user_dir.exists():
            for suffix in ("library.db", "library.db-wal", "library.db-shm"):
                p = user_dir / suffix
                if p.exists():
                    p.unlink()
            try:
                user_dir.rmdir()
            except OSError:
                pass

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (target_id,))
            log_account_event(
                conn,
                user_id=target_id,
                actor_id=admin_scope.user_id,
                event="admin_user_deleted",
                created_at=now_iso,
            )

    def get_instance_settings(self, admin_scope: UserScope) -> dict[str, str]:
        admin_scope.require_admin()
        with self.db_manager.system_connection() as conn:
            return list_instance_settings(conn)

    def update_instance_settings(
        self,
        admin_scope: UserScope,
        updates: dict[str, str],
    ) -> dict[str, str]:
        admin_scope.require_admin()
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            for key, val in updates.items():
                if key not in _SAFE_SETTING_KEYS:
                    raise ValidationError(f"Unsupported setting '{key}'")
                conn.execute(
                    """
                    INSERT INTO instance_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, str(val), now_iso),
                )
            return list_instance_settings(conn)

    # ── Personal Access Tokens (PATs) ───────────────────────────────────────

    def create_pat(
        self,
        scope: UserScope,
        *,
        name: str,
        scopes: list[str] | tuple[str, ...],
        ttl_days: int | None = None,
    ) -> CreatedPat:
        scope.require_active()
        cleaned_name = (name or "").strip()
        if not cleaned_name or len(cleaned_name) > 64:
            raise ValidationError("Token name must be 1–64 characters")
        requested_scopes = tuple(sorted({s.strip() for s in scopes if s and s.strip()}))
        if not requested_scopes or not set(requested_scopes).issubset(ALLOWED_PAT_SCOPES):
            raise ValidationError(f"Scopes must be a non-empty subset of {sorted(ALLOWED_PAT_SCOPES)}")

        raw_token = generate_opaque_token("vtx_pat_")
        token_hash = hash_opaque_token(raw_token)
        token_id = generate_opaque_token("pat_")[:20]
        token_prefix = raw_token[:14]
        now = _utc_now()
        now_iso = _iso(now)
        expires_iso = _iso(now + timedelta(days=ttl_days)) if ttl_days and ttl_days > 0 else None

        with self.db_manager.system_connection(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO personal_access_tokens (
                    id, user_id, name, token_prefix, token_hash, scopes_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    scope.user_id,
                    cleaned_name,
                    token_prefix,
                    token_hash,
                    json.dumps(list(requested_scopes)),
                    now_iso,
                    expires_iso,
                ),
            )
            row = conn.execute("SELECT * FROM personal_access_tokens WHERE id = ?", (token_id,)).fetchone()
            return CreatedPat(token=raw_token, record=row_to_pat(row))

    def list_pats(self, scope: UserScope) -> list[PersonalAccessToken]:
        scope.require_active()
        with self.db_manager.system_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM personal_access_tokens WHERE user_id = ? ORDER BY created_at DESC",
                (scope.user_id,),
            ).fetchall()
            return [row_to_pat(r) for r in rows]

    def revoke_pat(self, scope: UserScope, token_id: str) -> None:
        scope.require_active()
        now_iso = _iso(_utc_now())
        with self.db_manager.system_connection(immediate=True) as conn:
            cur = conn.execute(
                """
                UPDATE personal_access_tokens
                SET revoked_at = ?
                WHERE id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (now_iso, token_id, scope.user_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("Active token not found for user")

    def resolve_pat(
        self,
        raw_token: str,
        *,
        required_scope: str | None = None,
    ) -> tuple[UserScope, PersonalAccessToken]:
        if not raw_token:
            raise AuthenticationError("Missing bearer token")
        token_hash = hash_opaque_token(raw_token.strip())
        now = _utc_now()
        now_iso = _iso(now)

        with self.db_manager.system_connection(immediate=True) as conn:
            row = conn.execute(
                """
                SELECT p.*, u.username, u.role, u.status
                FROM personal_access_tokens p
                JOIN users u ON u.id = p.user_id
                WHERE p.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise AuthenticationError("Invalid or revoked token")
            if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= now:
                raise AuthenticationError("Token has expired")
            if row["status"] != "active":
                raise AuthorizationError(f"Token owner account is {row['status']}")

            pat = row_to_pat(row)
            if required_scope and required_scope not in pat.scopes:
                raise AuthorizationError(f"Token lacks required scope '{required_scope}'")

            conn.execute(
                "UPDATE personal_access_tokens SET last_used_at = ? WHERE id = ?",
                (now_iso, pat.id),
            )
            scope = UserScope(
                user_id=row["user_id"],
                username=row["username"],
                role=row["role"],
                status=row["status"],
            )
            return scope, pat

    # ── Per-User Connections & Encrypted Credentials ────────────────────────

    def get_connections_summary(self, scope: UserScope) -> ConnectionSummary:
        scope.require_active()
        with self.db_manager.system_connection() as conn:
            row = get_connection_row(conn, scope.user_id)
            if row is None:
                return ConnectionSummary(
                    steam_id=None,
                    steam_api_key_configured=False,
                    xbox_gamertag=None,
                    xbox_xuid=None,
                    xbl_api_key_configured=False,
                    xbl_watcher_api_key_configured=False,
                    psn_configured=False,
                    ha_url=None,
                    ha_token_configured=False,
                    ha_entity_id=None,
                )

            decrypt_err: str | None = None
            for col in (
                "steam_api_key_encrypted",
                "xbl_api_key_encrypted",
                "xbl_watcher_api_key_encrypted",
                "psn_npsso_encrypted",
                "ha_token_encrypted",
            ):
                val = row[col]
                if val:
                    try:
                        self.secret_box.decrypt(val)
                    except (MissingSecretKeyError, SecretDecryptionError) as exc:
                        decrypt_err = str(exc)
                        break

            return ConnectionSummary(
                steam_id=row["steam_id"],
                steam_api_key_configured=bool(row["steam_api_key_encrypted"]),
                xbox_gamertag=row["xbox_gamertag"],
                xbox_xuid=row["xbox_xuid"],
                xbl_api_key_configured=bool(row["xbl_api_key_encrypted"]),
                xbl_watcher_api_key_configured=bool(row["xbl_watcher_api_key_encrypted"]),
                psn_configured=bool(row["psn_npsso_encrypted"]),
                ha_url=row["ha_url"],
                ha_token_configured=bool(row["ha_token_encrypted"]),
                ha_entity_id=row["ha_entity_id"],
                credential_decryption_error=decrypt_err,
                updated_at=row["updated_at"],
            )

    def update_connections(
        self,
        scope: UserScope,
        *,
        steam_id: str | None = None,
        steam_api_key: str | None = None,
        clear_steam_api_key: bool = False,
        xbox_gamertag: str | None = None,
        xbox_xuid: str | None = None,
        xbl_api_key: str | None = None,
        clear_xbl_api_key: bool = False,
        psn_npsso: str | None = None,
        clear_psn_npsso: bool = False,
        ha_url: str | None = None,
        ha_token: str | None = None,
        clear_ha_token: bool = False,
        ha_entity_id: str | None = None,
    ) -> ConnectionSummary:
        scope.require_active()
        now_iso = _iso(_utc_now())

        with self.db_manager.system_connection(immediate=True) as conn:
            row = get_connection_row(conn, scope.user_id)
            if row is None:
                conn.execute(
                    "INSERT INTO user_connections (user_id, updated_at) VALUES (?, ?)",
                    (scope.user_id, now_iso),
                )
                row = get_connection_row(conn, scope.user_id)
                assert row is not None

            updates: dict[str, Any] = {"updated_at": now_iso}
            if steam_id is not None:
                updates["steam_id"] = steam_id.strip() or None
            if clear_steam_api_key:
                updates["steam_api_key_encrypted"] = None
            elif steam_api_key is not None and steam_api_key.strip():
                updates["steam_api_key_encrypted"] = self.secret_box.encrypt(steam_api_key.strip())

            if xbox_gamertag is not None:
                updates["xbox_gamertag"] = xbox_gamertag.strip() or None
            if xbox_xuid is not None:
                updates["xbox_xuid"] = xbox_xuid.strip() or None
            if clear_xbl_api_key:
                updates["xbl_api_key_encrypted"] = None
            elif xbl_api_key is not None and xbl_api_key.strip():
                updates["xbl_api_key_encrypted"] = self.secret_box.encrypt(xbl_api_key.strip())

            if clear_psn_npsso:
                updates["psn_npsso_encrypted"] = None
            elif psn_npsso is not None and psn_npsso.strip():
                updates["psn_npsso_encrypted"] = self.secret_box.encrypt(psn_npsso.strip())

            if ha_url is not None:
                updates["ha_url"] = ha_url.strip() or None
            if ha_entity_id is not None:
                updates["ha_entity_id"] = ha_entity_id.strip() or None
            if clear_ha_token:
                updates["ha_token_encrypted"] = None
            elif ha_token is not None and ha_token.strip():
                updates["ha_token_encrypted"] = self.secret_box.encrypt(ha_token.strip())

            assigns = ", ".join(f"{col} = ?" for col in updates)
            conn.execute(
                f"UPDATE user_connections SET {assigns} WHERE user_id = ?",
                (*updates.values(), scope.user_id),
            )
            log_account_event(
                conn,
                user_id=scope.user_id,
                actor_id=scope.user_id,
                event="connections_updated",
                created_at=now_iso,
            )

        return self.get_connections_summary(scope)

    def get_decrypted_connections(self, scope: UserScope) -> DecryptedConnections:
        """Internal credential resolver for sync adapters; never falls back to another user."""
        scope.require_active()
        with self.db_manager.system_connection() as conn:
            row = get_connection_row(conn, scope.user_id)
            if row is None:
                return DecryptedConnections(
                    user_id=scope.user_id,
                    steam_id=None,
                    steam_api_key=self.db_manager.settings.steam_api_key or None,
                    xbox_gamertag=None,
                    xbox_xuid=None,
                    xbl_api_key=None,
                    xbl_watcher_api_key=None,
                    psn_npsso=None,
                    ha_url=None,
                    ha_token=None,
                    ha_entity_id=None,
                )

            user_steam_key = (
                self.secret_box.decrypt(row["steam_api_key_encrypted"])
                if row["steam_api_key_encrypted"]
                else (self.db_manager.settings.steam_api_key or None)
            )
            return DecryptedConnections(
                user_id=scope.user_id,
                steam_id=row["steam_id"],
                steam_api_key=user_steam_key,
                xbox_gamertag=row["xbox_gamertag"],
                xbox_xuid=row["xbox_xuid"],
                xbl_api_key=self.secret_box.decrypt(row["xbl_api_key_encrypted"])
                if row["xbl_api_key_encrypted"]
                else None,
                xbl_watcher_api_key=self.secret_box.decrypt(row["xbl_watcher_api_key_encrypted"])
                if row["xbl_watcher_api_key_encrypted"]
                else None,
                psn_npsso=self.secret_box.decrypt(row["psn_npsso_encrypted"])
                if row["psn_npsso_encrypted"]
                else None,
                ha_url=row["ha_url"],
                ha_token=self.secret_box.decrypt(row["ha_token_encrypted"])
                if row["ha_token_encrypted"]
                else None,
                ha_entity_id=row["ha_entity_id"],
            )
