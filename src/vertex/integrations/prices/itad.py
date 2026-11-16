"""IsThereAnyDeal API v2 price adapter."""

from __future__ import annotations

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
    utc_now_iso,
)


class ITADAdapter:
    """IsThereAnyDeal API v2 client with authorized shop whitelisting."""

    WHITELIST_SHOPS: dict[int, str] = {
        61: "steam",
        35: "gog",
        16: "epic",
        37: "humble",
        36: "gmg",
        6: "fanatical",
    }

    def __init__(self, *, country: str = "US") -> None:
        self.country = (country or "US").strip().upper() or "US"

    def capabilities(self) -> PriceAdapterCapabilities:
        return PriceAdapterCapabilities(
            storefront="itad",
            display_name="IsThereAnyDeal v2",
            platform_families=("PC",),
            requires_api_key=False,
            supports_editions=True,
            currency="USD" if self.country == "US" else self.country,
        )

    async def fetch_prices(
        self,
        *,
        store_sku_ids: list[str],
        client: httpx.AsyncClient,
        api_key: str = "",
        country: str | None = None,
    ) -> list[NormalizedPriceQuote]:
        cleaned_ids = [s.strip() for s in store_sku_ids if s and s.strip()]
        if not cleaned_ids:
            return []

        region = (country or self.country).strip().upper() or "US"
        quotes: list[NormalizedPriceQuote] = []
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if api_key and api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"

        for i in range(0, len(cleaned_ids), BATCH_CHUNK_SIZE):
            chunk = cleaned_ids[i : i + BATCH_CHUNK_SIZE]
            try:
                resp = await client.post(
                    "https://api.isthereanydeal.com/games/prices/v2",
                    params={"country": region, "nondeals": "true"},
                    json=chunk,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                raise PriceAdapterError(
                    code="network_error",
                    message=sanitize_error_message(f"ITAD request failed: {exc}"),
                    retryable=True,
                ) from exc

            check_http_error("ITAD", resp)
            data = resp.json()
            if not isinstance(data, list):
                continue

            now_iso = utc_now_iso()
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                history_low_amt = ((item.get("historyLow") or {}).get("all") or {}).get("amount")
                history_low_val = round(float(history_low_amt), 2) if history_low_amt is not None else None

                best: dict[str, Any] | None = None
                for deal in item.get("deals") or []:
                    if not isinstance(deal, dict):
                        continue
                    shop_id = (deal.get("shop") or {}).get("id")
                    if shop_id not in self.WHITELIST_SHOPS:
                        continue
                    price_obj = deal.get("price") or {}
                    price_amt = price_obj.get("amount")
                    if price_amt is None:
                        continue
                    price_val = round(float(price_amt), 2)
                    reg_obj = deal.get("regular") or {}
                    reg_amt = reg_obj.get("amount")
                    reg_val = round(float(reg_amt), 2) if reg_amt is not None else price_val
                    currency = str(price_obj.get("currency") or ("USD" if region == "US" else "")).upper() or "USD"
                    cut = int(deal.get("cut") or 0)
                    shop_name = self.WHITELIST_SHOPS[int(shop_id)]
                    if best is None or price_val < float(best["price"]):
                        best = {
                            "storefront": shop_name,
                            "price": price_val,
                            "regular": reg_val,
                            "cut": cut,
                            "url": str(deal.get("url") or ""),
                            "currency": currency,
                        }

                if best is not None:
                    sf = str(best["storefront"])
                    quotes.append(
                        NormalizedPriceQuote(
                            storefront=sf,
                            store_sku_id=item_id,
                            platform_family="PC",
                            platform_label=f"PC ({sf.upper() if sf != 'gmg' else 'Green Man Gaming'})",
                            edition_name="Standard Edition",
                            edition_tier="base",
                            current_price_usd=float(best["price"]),
                            regular_price_usd=float(best["regular"]),
                            discount_pct=int(best["cut"]),
                            historical_low_usd=history_low_val,
                            store_url=str(best["url"]),
                            is_authorized_seller=True,
                            is_drm_free=(sf == "gog"),
                            currency=str(best["currency"]),
                            region=region,
                            retrieved_at=now_iso,
                        )
                    )

        return quotes
