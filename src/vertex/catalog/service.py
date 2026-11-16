"""Catalog metadata service managing shared public game records, search, and taxonomy-safe enrichment."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from vertex.catalog.models import (
    CatalogEnrichmentReport,
    CatalogMetadataEntry,
    CatalogSearchResult,
    normalize_title,
)
from vertex.catalog.repository import row_to_catalog_entry
from vertex.catalog.taxonomy import merge_lists, validate_genres, validate_tags
from vertex.infrastructure.errors import ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager

if TYPE_CHECKING:
    from vertex.library.service import LibraryService

logger = logging.getLogger("vertex.catalog")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CatalogLookupProvider(Protocol):
    """Protocol implemented by optional outbound catalog lookup integrations (keeps domain httpx-free)."""

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]: ...

    async def lookup_game(
        self,
        *,
        title: str,
        external_id: str | None = None,
    ) -> dict[str, Any] | None: ...


class CatalogService:
    """Stores public game metadata in system.db catalog_cache with provenance."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        outbound_sync_enabled: bool | None = None,
        lookup_provider: CatalogLookupProvider | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.outbound_sync_enabled = (
            db_manager.settings.outbound_sync_enabled
            if outbound_sync_enabled is None
            else bool(outbound_sync_enabled)
        )
        self.lookup_provider = lookup_provider

    def get_by_title(self, title: str) -> CatalogMetadataEntry | None:
        norm = normalize_title(title)
        if not norm:
            return None
        with self.db_manager.system_connection() as conn:
            row = conn.execute(
                "SELECT * FROM catalog_cache WHERE normalized_title = ?",
                (norm,),
            ).fetchone()
            return row_to_catalog_entry(row) if row else None

    def search_cache(self, query: str, *, limit: int = 20) -> list[CatalogMetadataEntry]:
        cleaned = (query or "").strip()
        norm = normalize_title(cleaned)
        if not cleaned and not norm:
            return []
        capped = max(1, min(100, int(limit)))
        like_norm = f"%{norm or cleaned.lower()}%"
        like_raw = f"%{cleaned.lower()}%"
        with self.db_manager.system_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM catalog_cache
                WHERE normalized_title LIKE ? OR LOWER(title) LIKE ?
                ORDER BY CASE WHEN normalized_title = ? THEN 0 ELSE 1 END, updated_at DESC
                LIMIT ?
                """,
                (like_norm, like_raw, norm, capped),
            ).fetchall()
            return [row_to_catalog_entry(r) for r in rows]

    def upsert_public_metadata(
        self,
        *,
        title: str,
        source: str,
        steam_appid: str | None = None,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        release_year: int | None = None,
        critic_score: int | None = None,
        cover_url: str | None = None,
        retrieved_at: str | None = None,
    ) -> CatalogMetadataEntry | None:
        norm = normalize_title(title)
        if not norm:
            return None
        now_iso = _utc_now()
        fetched_iso = retrieved_at or now_iso
        clean_genres = validate_genres(genres)
        clean_tags = validate_tags(tags)

        with self.db_manager.system_connection(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM catalog_cache WHERE normalized_title = ?",
                (norm,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO catalog_cache (
                        normalized_title, title, steam_appid, genres_json, tags_json,
                        release_year, critic_score, cover_url, source, retrieved_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        norm,
                        title.strip(),
                        steam_appid,
                        json.dumps(clean_genres),
                        json.dumps(clean_tags),
                        release_year,
                        critic_score,
                        cover_url,
                        source,
                        fetched_iso,
                        now_iso,
                    ),
                )
            else:
                merged_genres = merge_lists(json.loads(existing["genres_json"] or "[]"), clean_genres)
                merged_tags = merge_lists(json.loads(existing["tags_json"] or "[]"), clean_tags)
                conn.execute(
                    """
                    UPDATE catalog_cache
                    SET title = COALESCE(NULLIF(?, ''), title),
                        steam_appid = COALESCE(?, steam_appid),
                        genres_json = ?,
                        tags_json = ?,
                        release_year = COALESCE(?, release_year),
                        critic_score = COALESCE(?, critic_score),
                        cover_url = COALESCE(?, cover_url),
                        source = ?,
                        retrieved_at = ?,
                        updated_at = ?
                    WHERE normalized_title = ?
                    """,
                    (
                        title.strip(),
                        steam_appid,
                        json.dumps(merged_genres),
                        json.dumps(merged_tags),
                        release_year,
                        critic_score,
                        cover_url,
                        source,
                        fetched_iso,
                        now_iso,
                        norm,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM catalog_cache WHERE normalized_title = ?",
                (norm,),
            ).fetchone()
            return row_to_catalog_entry(row)

    async def search_catalog(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[CatalogSearchResult]:
        cleaned = (query or "").strip()
        if not cleaned:
            raise ValidationError("Search query 'q' is required")
        capped = max(1, min(50, int(limit)))

        cached_entries = self.search_cache(cleaned, limit=capped)
        by_norm: dict[str, CatalogSearchResult] = {
            e.normalized_title: CatalogSearchResult(
                normalized_title=e.normalized_title,
                title=e.title or e.normalized_title,
                steam_appid=e.steam_appid,
                genres=e.genres,
                tags=e.tags,
                release_year=e.release_year,
                critic_score=e.critic_score,
                cover_url=e.cover_url,
                source=e.source,
                retrieved_at=e.retrieved_at,
                cached=True,
            )
            for e in cached_entries
        }

        if self.outbound_sync_enabled and self.lookup_provider is not None and len(by_norm) < capped:
            try:
                remote_items = await self.lookup_provider.search(cleaned, limit=capped)
                now_iso = _utc_now()
                for item in remote_items:
                    title = str(item.get("title") or "").strip()
                    norm = normalize_title(title)
                    if not norm:
                        continue
                    stored = self.upsert_public_metadata(
                        title=title,
                        source=str(item.get("source") or "external"),
                        steam_appid=str(item["steam_appid"]) if item.get("steam_appid") else None,
                        genres=list(item.get("genres") or []),
                        tags=list(item.get("tags") or []),
                        release_year=item.get("release_year"),
                        critic_score=item.get("critic_score"),
                        cover_url=item.get("cover_url"),
                        retrieved_at=now_iso,
                    )
                    if stored is not None and norm not in by_norm:
                        by_norm[norm] = CatalogSearchResult(
                            normalized_title=stored.normalized_title,
                            title=stored.title,
                            steam_appid=stored.steam_appid,
                            genres=stored.genres,
                            tags=stored.tags,
                            release_year=stored.release_year,
                            critic_score=stored.critic_score,
                            cover_url=stored.cover_url,
                            source=stored.source,
                            retrieved_at=stored.retrieved_at,
                            cached=False,
                        )
            except Exception as exc:
                logger.warning("Catalog external search failed (falling back to cache): %s", exc)

        return list(by_norm.values())[:capped]

    async def enrich_library(
        self,
        scope: UserScope,
        library_service: LibraryService,
        *,
        limit: int = 500,
        force: bool = False,
    ) -> CatalogEnrichmentReport:
        """Enrich personal library titles via shared catalog_cache and optional external lookup (merge-only)."""
        scope.require_active()
        games = library_service.list_games(scope, sort_by="playtime")
        capped_limit = max(1, min(2000, int(limit)))

        # Build sibling metadata map from games in the user's library that already have genres.
        sibling_meta: dict[str, dict[str, Any]] = {}
        for g in games:
            norm = normalize_title(g.title)
            if norm and (g.genres or g.tags or g.cover_url or g.release_year or g.critic_score):
                existing = sibling_meta.get(norm)
                if existing is None:
                    sibling_meta[norm] = {
                        "genres": list(g.genres),
                        "tags": list(g.tags),
                        "release_year": g.release_year,
                        "critic_score": g.critic_score,
                        "cover_url": g.cover_url,
                    }
                else:
                    existing["genres"] = merge_lists(existing["genres"], list(g.genres))
                    existing["tags"] = merge_lists(existing["tags"], list(g.tags))
                    if existing["release_year"] is None:
                        existing["release_year"] = g.release_year
                    if existing["critic_score"] is None:
                        existing["critic_score"] = g.critic_score
                    if not existing["cover_url"]:
                        existing["cover_url"] = g.cover_url

        targets = [
            g
            for g in games
            if force or not g.genres or not (g.cover_url and g.cover_url.strip())
        ][:capped_limit]

        enriched_count = 0
        covers_filled = 0
        used_network = False
        now_iso = _utc_now()

        for g in targets:
            norm = normalize_title(g.title)
            meta: dict[str, Any] | None = None

            cached = self.get_by_title(g.title)
            if cached is not None and (cached.genres or cached.tags or cached.cover_url or cached.release_year or cached.critic_score):
                meta = {
                    "genres": list(cached.genres),
                    "tags": list(cached.tags),
                    "release_year": cached.release_year,
                    "critic_score": cached.critic_score,
                    "cover_url": cached.cover_url,
                }
            elif norm in sibling_meta:
                meta = sibling_meta[norm]

            need_remote = (
                self.outbound_sync_enabled
                and self.lookup_provider is not None
                and (force or meta is None or not meta.get("genres") or not meta.get("cover_url"))
            )
            if need_remote and self.lookup_provider is not None:
                try:
                    used_network = True
                    remote = await self.lookup_provider.lookup_game(
                        title=g.title,
                        external_id=g.external_id,
                    )
                    if remote:
                        stored = self.upsert_public_metadata(
                            title=str(remote.get("title") or g.title),
                            source=str(remote.get("source") or "external"),
                            steam_appid=str(remote["steam_appid"]) if remote.get("steam_appid") else None,
                            genres=list(remote.get("genres") or []),
                            tags=list(remote.get("tags") or []),
                            release_year=remote.get("release_year"),
                            critic_score=remote.get("critic_score"),
                            cover_url=remote.get("cover_url"),
                            retrieved_at=now_iso,
                        )
                        if stored is not None:
                            meta = {
                                "genres": list(stored.genres),
                                "tags": list(stored.tags),
                                "release_year": stored.release_year,
                                "critic_score": stored.critic_score,
                                "cover_url": stored.cover_url,
                            }
                except Exception as exc:
                    logger.warning("External catalog enrichment failed for '%s': %s", g.title, exc)

            if not meta:
                continue

            meta_changed, cover_changed = library_service.apply_catalog_enrichment(
                scope,
                g.id,
                genres=list(meta.get("genres") or []),
                tags=list(meta.get("tags") or []),
                release_year=meta.get("release_year"),
                critic_score=meta.get("critic_score"),
                cover_url=meta.get("cover_url"),
            )
            if meta_changed:
                enriched_count += 1
            if cover_changed:
                covers_filled += 1

        refreshed = library_service.list_games(scope)
        remaining = sum(1 for item in refreshed if not item.genres)
        mode_note = "cache+network" if used_network else "cache-only (offline-safe)"
        msg = (
            f"Enriched metadata for {enriched_count} games, filled {covers_filled} covers "
            f"({remaining} remaining without genres) [{mode_note}]"
        )
        library_service.record_sync_log(
            scope,
            source="Metadata Enricher",
            status="success",
            games_synced=enriched_count + covers_filled,
            message=msg,
        )
        return CatalogEnrichmentReport(
            games_enriched=enriched_count,
            covers_filled=covers_filled,
            remaining_without_genres=remaining,
            used_network=used_network,
            message=msg,
        )
