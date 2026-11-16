"""GOG.com Direct public catalog and product price adapter (DRM-Free)."""

from __future__ import annotations

import re
from typing import Any

import httpx

from vertex.integrations.platforms.base import sanitize_error_message
from vertex.integrations.prices.base import (
    BATCH_CHUNK_SIZE,
    DEFAULT_USER_AGENT,
    NormalizedPriceQuote,
    PriceAdapterCapabilities,
    PriceAdapterError,
    check_http_error,
    parse_json_response,
    utc_now_iso,
)


def _parse_gog_amount(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "." in text:
        try:
            return round(float(re.sub(r"[^0-9.]", "", text)), 2)
        except ValueError:
            return None
    try:
        val = float(re.sub(r"[^0-9.]", "", text))
    except ValueError:
        return None
    return round(val / 100.0, 2) if val > 1000 else round(val, 2)


class GOGDirectAdapter:
    """GOG Direct Public Catalog & Price API adapter."""

    def __init__(self, *, country: str = "US", currency: str = "USD") -> None:
        self.country = (country or "US").strip().upper() or "US"
        self.currency = (currency or "USD").strip().upper() or "USD"

    def capabilities(self) -> PriceAdapterCapabilities:
        return PriceAdapterCapabilities(
            storefront="gog",
            display_name="GOG.com",
            platform_families=("PC",),
            requires_api_key=False,
            supports_editions=True,
            currency=self.currency,
        )

    async def search_catalog(
        self,
        query: str,
        *,
        client: httpx.AsyncClient,
        country: str | None = None,
        currency: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query or "").strip()
        if not clean_q:
            return []
        region = (country or self.country).strip().upper() or "US"
        curr = (currency or self.currency).strip().upper() or "USD"
        try:
            resp = await client.get(
                "https://catalog.gog.com/v1/catalog",
                params={
                    "query": clean_q,
                    "order": "desc:score",
                    "countryCode": region,
                    "locale": "en-US",
                    "currencyCode": curr,
                    "limit": str(max(1, min(20, limit))),
                },
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"GOG catalog search failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("GOG", resp)
        data = parse_json_response("GOG", resp)
        products = data.get("products") if isinstance(data, dict) else []
        return [p for p in (products or []) if isinstance(p, dict)]

    async def fetch_prices(
        self,
        *,
        store_sku_ids: list[str],
        client: httpx.AsyncClient,
        api_key: str = "",
        country: str | None = None,
        currency: str | None = None,
    ) -> list[NormalizedPriceQuote]:
        del api_key
        cleaned_ids = list(dict.fromkeys(s.strip() for s in store_sku_ids if s and s.strip()))
        if not cleaned_ids:
            return []

        region = (country or self.country).strip().upper() or "US"
        curr = (currency or self.currency).strip().upper() or "USD"
        quotes: list[NormalizedPriceQuote] = []

        for i in range(0, len(cleaned_ids), BATCH_CHUNK_SIZE):
            chunk = cleaned_ids[i : i + BATCH_CHUNK_SIZE]
            try:
                resp = await client.get(
                    "https://api.gog.com/products",
                    params={
                        "ids": ",".join(chunk),
                        "countryCode": region,
                        "currency": curr,
                    },
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                )
            except httpx.RequestError as exc:
                raise PriceAdapterError(
                    code="network_error",
                    message=sanitize_error_message(f"GOG price query failed: {exc}"),
                    retryable=True,
                ) from exc

            check_http_error("GOG", resp)
            data = parse_json_response("GOG", resp)
            items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            now_iso = utc_now_iso()

            for item in items:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("id") or "").strip()
                if not pid:
                    continue
                price_data = item.get("price") or {}
                if not isinstance(price_data, dict):
                    continue
                final_amount = price_data.get("finalAmount")
                base_amount = price_data.get("baseAmount")
                cur = _parse_gog_amount(final_amount)
                if cur is None:
                    continue
                reg = _parse_gog_amount(base_amount)
                if reg is None:
                    reg = cur
                cut = int(price_data.get("discountPercentage") or 0)
                item_currency = str(
                    price_data.get("currency")
                    or (price_data.get("finalMoney") or {}).get("currency")
                    or curr
                ).upper()
                slug = str(item.get("slug") or "").strip()
                store_url = f"https://www.gog.com/en/game/{slug}" if slug else f"https://www.gog.com/game/{pid}"
                quotes.append(
                    NormalizedPriceQuote(
                        storefront="gog",
                        store_sku_id=pid,
                        platform_family="PC",
                        platform_label="PC (GOG)",
                        edition_name="Standard Edition",
                        edition_tier="base",
                        current_price_usd=cur,
                        regular_price_usd=reg,
                        discount_pct=cut,
                        store_url=store_url,
                        is_authorized_seller=True,
                        is_drm_free=True,
                        currency=item_currency,
                        region=region,
                        retrieved_at=now_iso,
                    )
                )

        return quotes
