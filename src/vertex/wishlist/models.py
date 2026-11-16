"""Domain models and normalization rules for wishlist items, editions, SKUs, and deals."""

from __future__ import annotations

from dataclasses import dataclass

VALID_WISHLIST_STATUSES = frozenset({"active", "purchased", "archived"})

EDITION_TIER_RANKS = {
    "base": 1,
    "standard": 1,
    "deluxe": 2,
    "special": 2,
    "gold": 3,
    "complete": 3,
    "definitive": 3,
    "ultimate": 4,
    "goty": 4,
    "legendary": 4,
    "collector": 5,
}


def normalize_platform_family(platform_str: str | None) -> str:
    if not platform_str:
        return "PC"
    p = platform_str.strip().lower()
    if "xbox" in p:
        return "Xbox"
    if any(k in p for k in ("playstation", "ps4", "ps5", "psn")):
        return "PlayStation"
    if any(k in p for k in ("switch", "nintendo")):
        return "Nintendo Switch"
    if any(k in p for k in ("steam", "gog", "epic", "pc", "windows", "fanatical", "humble", "gmg")):
        return "PC"
    return platform_str.strip() or "PC"


def infer_edition_tier(edition_name: str) -> tuple[str, int]:
    name = (edition_name or "").lower()
    if "collector" in name:
        return ("collector", 5)
    if any(k in name for k in ("ultimate", "legendary", "premium")):
        return ("ultimate", 4)
    if "goty" in name or "game of the year" in name:
        return ("goty", 4)
    if any(k in name for k in ("complete", "definitive", "voidheart", "legacy", "anniversary")):
        return ("complete", 3)
    if "gold" in name:
        return ("gold", 3)
    if any(k in name for k in ("deluxe", "director", "enhanced", "special")):
        return ("deluxe", 2)
    return ("base", 1)


@dataclass(frozen=True, slots=True)
class CanonicalGame:
    id: int
    normalized_title: str
    title: str
    release_year: int | None
    developer: str
    publisher: str
    cover_url: str | None
    genres: tuple[str, ...]
    critic_score: int | None
    library_game_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class EditionPlatformSku:
    id: int
    edition_id: int
    platform_family: str
    platform_label: str
    storefront: str
    store_sku_id: str
    itad_plain: str | None
    store_url: str
    msrp_usd: float | None
    current_price_usd: float | None
    regular_price_usd: float | None
    discount_pct: int
    historical_low_usd: float | None
    is_authorized_seller: bool
    is_drm_free: bool
    last_checked_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class GameEdition:
    id: int
    canonical_game_id: int
    edition_name: str
    edition_tier: str
    tier_rank: int
    is_base_edition: bool
    included_dlc_notes: str
    cover_url: str | None
    skus: tuple[EditionPlatformSku, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WishlistPlatformTarget:
    id: int
    wishlist_item_id: int
    platform_family: str
    allowed_storefronts: tuple[str, ...]
    target_price_usd: float
    is_active: bool
    muted_owned_conflict: bool
    last_alerted_at: str | None
    last_alerted_price_usd: float | None
    last_alerted_edition_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class OwnershipConflict:
    already_in_library: bool
    conflicted_platforms: tuple[str, ...]
    conflict_warning: str | None


@dataclass(frozen=True, slots=True)
class PriceHistoryEntry:
    id: int
    sku_id: int
    storefront: str
    price_usd: float
    regular_price_usd: float | None
    discount_pct: int
    is_all_time_low: bool
    source: str
    recorded_at: str


@dataclass(frozen=True, slots=True)
class WishlistItemDetail:
    id: int
    canonical_game_id: int
    preferred_edition_id: int | None
    allow_smart_upgrade: bool
    status: str
    notes: str
    added_via: str
    created_at: str
    updated_at: str
    canonical_game: CanonicalGame
    targets: tuple[WishlistPlatformTarget, ...]
    editions: tuple[GameEdition, ...]
    skus: tuple[EditionPlatformSku, ...]
    conflict: OwnershipConflict


@dataclass(frozen=True, slots=True)
class WishlistDeal:
    wishlist_item_id: int
    canonical_game_id: int
    game_title: str
    cover_url: str | None
    edition_id: int
    edition_name: str
    edition_tier: str
    tier_rank: int
    sku_id: int
    platform_family: str
    platform_label: str
    storefront: str
    store_url: str
    current_price_usd: float
    regular_price_usd: float | None
    discount_pct: int
    target_price_usd: float
    savings_below_target_usd: float
    current_base_edition_price_usd: float | None
    is_smart_upgrade: bool
    is_all_time_low: bool
    is_drm_free: bool
    deal_headline: str


@dataclass(frozen=True, slots=True)
class WishlistDealEvaluation:
    total_deals: int
    smart_upgrades_count: int
    all_time_lows_count: int
    deals: tuple[WishlistDeal, ...]


@dataclass(frozen=True, slots=True)
class WishlistPriceRecordResult:
    sku_id: int
    price_usd: float
    is_all_time_low: bool
    historical_low_usd: float
    item: WishlistItemDetail


@dataclass(frozen=True, slots=True)
class WishlistPurchaseResult:
    item: WishlistItemDetail
    library_game_id: int
    already_purchased: bool


@dataclass(frozen=True, slots=True)
class WishlistHistoryDetail:
    item: WishlistItemDetail
    price_history_by_sku: dict[int, list[PriceHistoryEntry]]
