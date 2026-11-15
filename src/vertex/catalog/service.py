"""Catalog metadata service managing shared public game records and taxonomy validation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from vertex.catalog.models import CatalogMetadataEntry, normalize_title
from vertex.catalog.repository import row_to_catalog_entry
from vertex.catalog.taxonomy import merge_lists, validate_genres, validate_tags
from vertex.infrastructure.sqlite import DatabaseManager


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CatalogService:
    """Stores public game metadata in system.db catalog_cache with provenance."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db_manager = db_manager

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
