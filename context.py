"""
Request and task-scoped multi-tenancy context for Vertex (Game Hub).
"""

from contextvars import ContextVar, Token
from typing import Optional

current_user_id: ContextVar[Optional[str]] = ContextVar("current_user_id", default=None)


def get_current_user_id() -> Optional[str]:
    """Return the active user_id in the current execution context, or None."""
    return current_user_id.get()


def set_current_user_id(user_id: Optional[str]) -> Token:
    """Set the active user_id in the current execution context and return the reset token."""
    return current_user_id.set(user_id)


def reset_current_user_id(token: Token) -> None:
    """Restore current_user_id to its previous state using a Token."""
    current_user_id.reset(token)
