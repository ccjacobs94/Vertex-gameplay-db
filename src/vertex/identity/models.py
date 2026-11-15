"""Domain models for identity, invitations, PATs, connections, and throttling."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from vertex.infrastructure.scope import UserScope

ALLOWED_PAT_SCOPES = frozenset({"read:library", "write:library", "sync:platforms"})
ALLOWED_ROLES = frozenset({"admin", "member"})
ALLOWED_USER_STATUSES = frozenset({"active", "invited", "suspended", "pending_provisioning", "deleting"})


@dataclass(frozen=True, slots=True)
class UserAccount:
    id: str
    username: str
    role: str
    status: str
    status_reason: str | None
    timezone: str
    timezone_eras: list[dict[str, str]]
    onboarding_completed: bool
    created_at: str
    updated_at: str
    last_login_at: str | None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_scope(self) -> UserScope:
        return UserScope(
            user_id=self.id,
            username=self.username,
            role=self.role,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class InviteRecord:
    code_hash: str
    created_by: str
    role: str
    note: str
    created_at: str
    expires_at: str | None
    used_by: str | None
    used_at: str | None
    revoked_at: str | None


@dataclass(frozen=True, slots=True)
class CreatedInvite:
    code: str
    record: InviteRecord


@dataclass(frozen=True, slots=True)
class PersonalAccessToken:
    id: str
    user_id: str
    name: str
    token_prefix: str
    scopes: tuple[str, ...]
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None


@dataclass(frozen=True, slots=True)
class CreatedPat:
    token: str
    record: PersonalAccessToken


@dataclass(frozen=True, slots=True)
class ConnectionSummary:
    steam_id: str | None
    steam_api_key_configured: bool
    xbox_gamertag: str | None
    xbox_xuid: str | None
    xbl_api_key_configured: bool
    xbl_watcher_api_key_configured: bool
    psn_configured: bool
    ha_url: str | None
    ha_token_configured: bool
    ha_entity_id: str | None
    credential_decryption_error: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class DecryptedConnections:
    user_id: str
    steam_id: str | None
    steam_api_key: str | None
    xbox_gamertag: str | None
    xbox_xuid: str | None
    xbl_api_key: str | None
    xbl_watcher_api_key: str | None
    psn_npsso: str | None
    ha_url: str | None
    ha_token: str | None
    ha_entity_id: str | None


class LoginRateLimiter:
    """In-memory sliding-window login rate limiter keyed by IP and IP+username."""

    def __init__(self, *, max_attempts: int = 8, window_seconds: int = 300) -> None:
        self.max_attempts = max(1, max_attempts)
        self.window_seconds = max(1, window_seconds)
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        entries = [ts for ts in self._attempts.get(key, []) if ts > cutoff]
        if entries:
            self._attempts[key] = entries
        else:
            self._attempts.pop(key, None)
        return entries

    def check_allowed(self, client_ip: str, username: str) -> tuple[bool, int]:
        now = time.monotonic()
        ip_key = f"ip:{client_ip or 'unknown'}"
        acct_key = f"acct:{client_ip or 'unknown'}:{username.strip().lower()}"
        with self._lock:
            for key, limit in ((acct_key, self.max_attempts), (ip_key, self.max_attempts * 3)):
                entries = self._prune(key, now)
                if len(entries) >= limit:
                    retry_after = max(1, int(self.window_seconds - (now - entries[0])) + 1)
                    return False, retry_after
        return True, 0

    def record_failure(self, client_ip: str, username: str) -> None:
        now = time.monotonic()
        ip_key = f"ip:{client_ip or 'unknown'}"
        acct_key = f"acct:{client_ip or 'unknown'}:{username.strip().lower()}"
        with self._lock:
            for key in (ip_key, acct_key):
                entries = self._prune(key, now)
                entries.append(now)
                self._attempts[key] = entries

    def record_success(self, client_ip: str, username: str) -> None:
        acct_key = f"acct:{client_ip or 'unknown'}:{username.strip().lower()}"
        with self._lock:
            self._attempts.pop(acct_key, None)
