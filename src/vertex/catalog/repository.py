"""SQLite repository for shared public catalog_cache in system.db."""

from __future__ import annotations

import json
import sqlite3

from vertex.catalog.models import CatalogMetadataEntry


def row_to_catalog_entry(row: sqlite3.Row) -> CatalogMetadataEntry:
    return CatalogMetadataEntry(
        normalized_title=row["normalized_title"],
        title=row["title"] or "",
        steam_appid=row["steam_appid"],
        genres=tuple(json.loads(row["genres_json"] or "[]")),
        tags=tuple(json.loads(row["tags_json"] or "[]")),
        release_year=row["release_year"],
        critic_score=row["critic_score"],
        cover_url=row["cover_url"],
        source=row["source"] or "unknown",
        retrieved_at=row["retrieved_at"],
        updated_at=row["updated_at"],
    )
