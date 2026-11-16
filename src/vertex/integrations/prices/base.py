"""Normalized storefront price adapter contract and structured error types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from vertex.integrations.platforms.base import sanitize_error_message


@dataclass(frozen=True, slots=True)
class PriceAdapterCapabilities:
    storefront: str
    display_name: str
    platform_families: tuple[str, ...]
    requires_api_key: bool
    supports_editions: bool
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class NormalizedPriceQuote:
    storefront: str
    store_sku_id: str
    platform_family: str
    platform_label: str
    edition_name: str
    edition_tier: str
    current_price_usd: float
    regular_price_usd: float | None = None
    discount_pct: int = 0
    historical_low_usd: float | None = None
    store_url: str = ""
    is_authorized_seller: bool = True
    is_drm_free: bool = False
    currency: str = "USD"
    retrieved_at: str = ""


class PriceAdapterError(Exception):
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


class PriceAdapter(Protocol):
    def capabilities(self) -> PriceAdapterCapabilities: ...

    async def fetch_prices(
        self,
        *,
        store_sku_ids: list[str],
        client: httpx.AsyncClient,
        api_key: str = "",
    ) -> list[NormalizedPriceQuote]: ...
