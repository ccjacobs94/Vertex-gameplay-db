"""Normalized storefront price adapter contract and structured error types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from vertex.integrations.platforms.base import sanitize_error_message

DEFAULT_USER_AGENT = "Vertex-GameHub/0.2 (HomeServer Backlog Tracker)"
BATCH_CHUNK_SIZE = 25
XBOX_BATCH_CHUNK_SIZE = 20


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_http_error(storefront: str, resp: httpx.Response) -> None:
    if resp.status_code == 429:
        retry_after = 60
        raw_ra = resp.headers.get("Retry-After")
        if raw_ra and raw_ra.strip().isdigit():
            retry_after = max(5, min(3600, int(raw_ra.strip())))
        raise PriceAdapterError(
            code="rate_limited",
            message=f"{storefront} rate limited (HTTP 429)",
            retryable=True,
            throttle_seconds=retry_after,
        )
    if resp.status_code in (401, 403):
        raise PriceAdapterError(
            code="auth_failed",
            message=f"{storefront} authorization failed (HTTP {resp.status_code})",
            retryable=False,
        )
    if resp.status_code >= 500:
        raise PriceAdapterError(
            code="upstream_error",
            message=f"{storefront} upstream error (HTTP {resp.status_code})",
            retryable=True,
        )
    if resp.status_code >= 400:
        raise PriceAdapterError(
            code="http_error",
            message=f"{storefront} returned HTTP {resp.status_code}",
            retryable=False,
        )


def parse_json_response(storefront: str, resp: httpx.Response) -> Any:
    """Decode JSON safely, wrapping malformed responses in a retryable PriceAdapterError."""
    try:
        return resp.json()
    except ValueError as exc:
        raise PriceAdapterError(
            code="invalid_response",
            message=f"{storefront} returned invalid JSON",
            retryable=True,
        ) from exc


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
    region: str = "US"
    retrieved_at: str = ""
    image_url: str | None = None


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
