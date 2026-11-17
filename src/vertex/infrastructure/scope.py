"""Explicit tenant scope passed across personal services and repositories."""

from __future__ import annotations

import re
from dataclasses import dataclass

from vertex.infrastructure.errors import ScopeViolationError

_USER_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str):
        raise ScopeViolationError("User scope ID must be a string")
    cleaned = user_id.strip()
    if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise ScopeViolationError("Invalid user scope identifier")
    if not _USER_ID_RE.match(cleaned):
        raise ScopeViolationError("User scope identifier contains disallowed characters")
    return cleaned


@dataclass(frozen=True, slots=True)
class UserScope:
    """Resolved authenticated actor identity required by all personal operations."""

    user_id: str
    username: str
    role: str
    status: str

    def __post_init__(self) -> None:
        validate_user_id(self.user_id)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def require_active(self) -> "UserScope":
        if not self.is_active:
            raise ScopeViolationError(f"Account '{self.username}' is {self.status}")
        return self

    def require_admin(self) -> "UserScope":
        self.require_active()
        if not self.is_admin:
            raise ScopeViolationError("Administrator role required")
        return self
