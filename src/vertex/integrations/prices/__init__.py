"""Storefront price adapters and automated wishlist sync orchestrator."""

from vertex.integrations.prices.base import (
    NormalizedPriceQuote,
    PriceAdapter,
    PriceAdapterCapabilities,
    PriceAdapterError,
)
from vertex.integrations.prices.cheapshark import CheapSharkAdapter
from vertex.integrations.prices.gog import GOGDirectAdapter
from vertex.integrations.prices.itad import ITADAdapter
from vertex.integrations.prices.psn import PSNStoreGraphQLAdapter
from vertex.integrations.prices.steam import SteamStoreAdapter
from vertex.integrations.prices.sync_service import WishlistSyncService
from vertex.integrations.prices.xbox import XboxCatalogAdapter

__all__ = [
    "CheapSharkAdapter",
    "GOGDirectAdapter",
    "ITADAdapter",
    "NormalizedPriceQuote",
    "PSNStoreGraphQLAdapter",
    "PriceAdapter",
    "PriceAdapterCapabilities",
    "PriceAdapterError",
    "SteamStoreAdapter",
    "WishlistSyncService",
    "XboxCatalogAdapter",
]
