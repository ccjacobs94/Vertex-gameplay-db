"""Normalized platform adapter contract and structured error types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

_SECRET_PATTERN = re.compile(
    r"(?i)(key|api_key|token|authorization|npsso|password|secret)=([^&\s]+)"
)
_HEADER_SECRET_PATTERN = re.compile(
    r"(?i)(authorization:\s*bearer\s+|bearer\s+|x-api-key:\s*|authorization:\s*)([^\s,;]+)"
)


def sanitize_error_message(message: str) -> str:
    cleaned = _SECRET_PATTERN.sub(r"\1=[REDACTED]", str(message or ""))
    cleaned = _HEADER_SECRET_PATTERN.sub(r"\1[REDACTED]", cleaned)
    return cleaned[:500]


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    provider: str
    display_name: str
    supports_playtime: bool
    supports_achievements: bool
    supports_game_pass: bool
    auth_modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlatformGameSnapshot:
    external_id: str
    title: str
    platform: str
    source: str
    playtime_minutes: int
    last_played: str | None = None
    cover_url: str | None = None
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    is_game_pass: bool | None = None
    is_play_anywhere: bool | None = None


class PlatformAdapterError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        throttle_seconds: int = 0,
    ) -> None:
        super().__init__(sanitize_error_message(message))
        self.code = code
        self.safe_message = sanitize_error_message(message)
        self.retryable = retryable
        self.throttle_seconds = max(0, throttle_seconds)


class PlatformAdapter(Protocol):
    def capabilities(self) -> AdapterCapabilities: ...

    async def fetch_library(
        self,
        *,
        user_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> list[PlatformGameSnapshot]: ...
