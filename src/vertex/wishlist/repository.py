"""SQLite repository helpers for per-user wishlist, editions, SKUs, and price history."""

from __future__ import annotations

import json
import sqlite3

from vertex.wishlist.models import (
    CanonicalGame,
    EditionPlatformSku,
    GameEdition,
    PriceHistoryEntry,
    WishlistPlatformTarget,
)


def row_to_canonical(row: sqlite3.Row) -> CanonicalGame:
    return CanonicalGame(
        id=int(row["id"]),
        normalized_title=row["normalized_title"],
        title=row["title"],
        release_year=row["release_year"],
        developer=row["developer"] or "",
        publisher=row["publisher"] or "",
        cover_url=row["cover_url"],
        genres=tuple(json.loads(row["genres_json"] or "[]")),
        critic_score=row["critic_score"],
        library_game_id=row["library_game_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_sku(row: sqlite3.Row) -> EditionPlatformSku:
    return EditionPlatformSku(
        id=int(row["id"]),
        edition_id=int(row["edition_id"]),
        platform_family=row["platform_family"],
        platform_label=row["platform_label"],
        storefront=row["storefront"],
        store_sku_id=row["store_sku_id"],
        itad_plain=row["itad_plain"],
        store_url=row["store_url"] or "",
        msrp_usd=float(row["msrp_usd"]) if row["msrp_usd"] is not None else None,
        current_price_usd=float(row["current_price_usd"]) if row["current_price_usd"] is not None else None,
        regular_price_usd=float(row["regular_price_usd"]) if row["regular_price_usd"] is not None else None,
        discount_pct=int(row["discount_pct"] or 0),
        historical_low_usd=float(row["historical_low_usd"]) if row["historical_low_usd"] is not None else None,
        is_authorized_seller=bool(row["is_authorized_seller"]),
        is_drm_free=bool(row["is_drm_free"]),
        last_checked_at=row["last_checked_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_edition(row: sqlite3.Row, skus: tuple[EditionPlatformSku, ...]) -> GameEdition:
    return GameEdition(
        id=int(row["id"]),
        canonical_game_id=int(row["canonical_game_id"]),
        edition_name=row["edition_name"],
        edition_tier=row["edition_tier"],
        tier_rank=int(row["tier_rank"]),
        is_base_edition=bool(row["is_base_edition"]),
        included_dlc_notes=row["included_dlc_notes"] or "",
        cover_url=row["cover_url"],
        skus=skus,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_target(row: sqlite3.Row) -> WishlistPlatformTarget:
    return WishlistPlatformTarget(
        id=int(row["id"]),
        wishlist_item_id=int(row["wishlist_item_id"]),
        platform_family=row["platform_family"],
        allowed_storefronts=tuple(json.loads(row["allowed_storefronts"] or "[]")),
        target_price_usd=float(row["target_price_usd"]),
        is_active=bool(row["is_active"]),
        muted_owned_conflict=bool(row["muted_owned_conflict"]),
        last_alerted_at=row["last_alerted_at"],
        last_alerted_price_usd=(
            float(row["last_alerted_price_usd"]) if row["last_alerted_price_usd"] is not None else None
        ),
        last_alerted_edition_id=row["last_alerted_edition_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_price_history(row: sqlite3.Row) -> PriceHistoryEntry:
    return PriceHistoryEntry(
        id=int(row["id"]),
        sku_id=int(row["sku_id"]),
        storefront=row["storefront"],
        price_usd=float(row["price_usd"]),
        regular_price_usd=float(row["regular_price_usd"]) if row["regular_price_usd"] is not None else None,
        discount_pct=int(row["discount_pct"] or 0),
        is_all_time_low=bool(row["is_all_time_low"]),
        source=row["source"],
        recorded_at=row["recorded_at"],
    )
