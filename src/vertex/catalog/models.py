"""Public catalog metadata models (no personal user fields)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return _NON_ALNUM_RE.sub(" ", (title or "").lower()).strip()


@dataclass(frozen=True, slots=True)
class CatalogMetadataEntry:
    normalized_title: str
    title: str
    steam_appid: str | None
    genres: tuple[str, ...]
    tags: tuple[str, ...]
    release_year: int | None
    critic_score: int | None
    cover_url: str | None
    source: str
    retrieved_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CatalogSearchResult:
    normalized_title: str
    title: str
    steam_appid: str | None
    genres: tuple[str, ...]
    tags: tuple[str, ...]
    release_year: int | None
    critic_score: int | None
    cover_url: str | None
    source: str
    retrieved_at: str
    cached: bool


@dataclass(frozen=True, slots=True)
class CatalogEnrichmentReport:
    games_enriched: int
    covers_filled: int
    remaining_without_genres: int
    used_network: bool
    message: str
