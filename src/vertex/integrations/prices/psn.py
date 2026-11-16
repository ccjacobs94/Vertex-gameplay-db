"""PlayStation Store public GraphQL / web store price adapter."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from vertex.integrations.platforms.base import sanitize_error_message
from vertex.integrations.prices.base import (
    DEFAULT_USER_AGENT,
    NormalizedPriceQuote,
    PriceAdapterCapabilities,
    PriceAdapterError,
    check_http_error,
    utc_now_iso,
)


def _detect_currency(price_info: dict[str, Any], raw_str: str, default_region: str) -> str:
    explicit = str(price_info.get("currencyCode") or "").strip().upper()
    if explicit:
        return explicit
    if "€" in raw_str:
        return "EUR"
    if "£" in raw_str:
        return "GBP"
    if "¥" in raw_str:
        return "JPY"
    if "$" in raw_str or default_region == "US":
        return "USD"
    return default_region


class PSNStoreGraphQLAdapter:
    """PlayStation Store public GraphQL operation client."""

    def __init__(self, *, country: str = "US") -> None:
        self.country = (country or "US").strip().upper() or "US"

    def capabilities(self) -> PriceAdapterCapabilities:
        return PriceAdapterCapabilities(
            storefront="psn_store",
            display_name="PlayStation Store",
            platform_families=("PlayStation",),
            requires_api_key=False,
            supports_editions=True,
            currency="USD" if self.country == "US" else self.country,
        )

    async def search_products(
        self,
        query: str,
        *,
        client: httpx.AsyncClient,
        country: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query or "").strip()
        if not clean_q:
            return []
        region = (country or self.country).strip().upper() or "US"
        try:
            resp = await client.get(
                "https://web.np.playstation.com/api/graphql/v1/op",
                params={
                    "operationName": "getSharedSearchSuggestions",
                    "variables": json.dumps(
                        {"searchTerm": clean_q, "countryCode": region, "languageCode": "en"}
                    ),
                },
                headers={
                    "x-psn-store-locale-override": f"en-{region}",
                    "User-Agent": DEFAULT_USER_AGENT,
                },
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"PSN store search failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("PlayStation Store", resp)
        data = resp.json()
        suggestions = (
            ((data.get("data") or {}).get("getSharedSearchSuggestions") or {}).get("suggestions") or []
            if isinstance(data, dict)
            else []
        )
        results: list[dict[str, Any]] = []
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            title = str(s.get("text") or s.get("title") or "").strip()
            prod_id = str(s.get("id") or s.get("productId") or "").strip()
            if prod_id:
                results.append({"id": prod_id, "title": title})
        return results

    async def get_product_price(
        self,
        product_or_concept_id: str,
        *,
        client: httpx.AsyncClient,
        country: str | None = None,
    ) -> NormalizedPriceQuote | None:
        pid = str(product_or_concept_id or "").strip()
        if not pid:
            return None
        region = (country or self.country).strip().upper() or "US"
        try:
            resp = await client.get(
                "https://web.np.playstation.com/api/graphql/v1/op",
                params={
                    "operationName": "productRetrieve",
                    "variables": json.dumps({"id": pid}),
                    "extensions": json.dumps(
                        {"persistedQuery": {"version": 1, "sha256Hash": "mock_hash"}}
                    ),
                },
                headers={
                    "x-psn-store-locale-override": f"en-{region}",
                    "User-Agent": DEFAULT_USER_AGENT,
                },
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"PSN price lookup failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("PlayStation Store", resp)
        data = resp.json()
        prod = (data.get("data") or {}).get("productRetrieve") if isinstance(data, dict) else None
        if not isinstance(prod, dict):
            return None

        price_info = prod.get("price") or {}
        if not isinstance(price_info, dict):
            return None
        base_raw = str(price_info.get("basePrice") or "0")
        disc_raw = str(price_info.get("discountedPrice") or base_raw)
        currency = _detect_currency(price_info, disc_raw or base_raw, region)
        base_clean = re.sub(r"[^0-9.]", "", base_raw)
        disc_clean = re.sub(r"[^0-9.]", "", disc_raw)
        if not disc_clean or not base_clean:
            return None
        try:
            cur = round(float(disc_clean), 2)
            reg = round(float(base_clean), 2)
        except ValueError:
            return None
        if cur <= 0.0 and reg <= 0.0:
            return None

        cut = int(round((1.0 - (cur / reg)) * 100)) if reg > 0 and cur < reg else 0
        platforms = prod.get("platforms") or ["PS5"]
        label = "PS5" if "PS5" in platforms else "PS4"
        return NormalizedPriceQuote(
            storefront="psn_store",
            store_sku_id=pid,
            platform_family="PlayStation",
            platform_label=label,
            edition_name="Standard Edition",
            edition_tier="base",
            current_price_usd=cur,
            regular_price_usd=reg,
            discount_pct=cut,
            store_url=f"https://store.playstation.com/en-us/product/{pid}",
            is_authorized_seller=True,
            is_drm_free=False,
            currency=currency,
            region=region,
            retrieved_at=utc_now_iso(),
        )

    async def fetch_prices(
        self,
        *,
        store_sku_ids: list[str],
        client: httpx.AsyncClient,
        api_key: str = "",
        country: str | None = None,
    ) -> list[NormalizedPriceQuote]:
        del api_key
        cleaned_ids = list(dict.fromkeys(s.strip() for s in store_sku_ids if s and s.strip()))
        quotes: list[NormalizedPriceQuote] = []
        for pid in cleaned_ids:
            # Skip synthetic resilient fallback SKUs (e.g. 'psn-cyberpunk-2077')
            if pid.startswith("psn-"):
                continue
            quote = await self.get_product_price(pid, client=client, country=country)
            if quote is not None:
                quotes.append(quote)
        return quotes
