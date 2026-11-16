"""CheapShark API v1.0 price adapter."""

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


class CheapSharkAdapter:
    """CheapShark public API adapter for PC digital store deals and historical lows."""

    STORE_MAP: dict[str, str] = {
        "1": "steam",
        "7": "gog",
        "25": "epic",
        "11": "humble",
        "3": "gmg",
        "15": "fanatical",
    }

    def capabilities(self) -> PriceAdapterCapabilities:
        return PriceAdapterCapabilities(
            storefront="cheapshark",
            display_name="CheapShark",
            platform_families=("PC",),
            requires_api_key=False,
            supports_editions=True,
            currency="USD",
        )

    async def search_games(
        self,
        query: str,
        *,
        client: httpx.AsyncClient,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        cleaned = (query or "").strip()
        if not cleaned:
            return []
        try:
            resp = await client.get(
                "https://www.cheapshark.com/api/1.0/games",
                params={"title": cleaned, "limit": str(max(1, min(25, limit)))},
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"CheapShark search failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("CheapShark", resp)
        data = resp.json()
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    async def get_game_details(
        self,
        game_id: str,
        *,
        client: httpx.AsyncClient,
    ) -> dict[str, Any] | None:
        cleaned = str(game_id or "").strip()
        if not cleaned:
            return None
        try:
            resp = await client.get(
                "https://www.cheapshark.com/api/1.0/games",
                params={"id": cleaned},
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"CheapShark details failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("CheapShark", resp)
        data = resp.json()
        if isinstance(data, dict):
            nested = data.get(cleaned)
            return nested if isinstance(nested, dict) else data
        return None

    async def fetch_prices(
        self,
        *,
        store_sku_ids: list[str],
        client: httpx.AsyncClient,
        api_key: str = "",
    ) -> list[NormalizedPriceQuote]:
        del api_key
        cleaned_ids = list(dict.fromkeys(s.strip() for s in store_sku_ids if s and s.strip()))
        if not cleaned_ids:
            return []

        quotes: list[NormalizedPriceQuote] = []
        for i in range(0, len(cleaned_ids), BATCH_CHUNK_SIZE):
            chunk = cleaned_ids[i : i + BATCH_CHUNK_SIZE]
            try:
                resp = await client.get(
                    "https://www.cheapshark.com/api/1.0/games",
                    params={"ids": ",".join(chunk)},
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                )
            except httpx.RequestError as exc:
                raise PriceAdapterError(
                    code="network_error",
                    message=sanitize_error_message(f"CheapShark batch query failed: {exc}"),
                    retryable=True,
                ) from exc

            check_http_error("CheapShark", resp)
            data = resp.json()
            if not isinstance(data, dict):
                continue

            now_iso = utc_now_iso()
            for gid, details in data.items():
                if not isinstance(details, dict):
                    continue
                cheapest_raw = (details.get("cheapestPriceEver") or {}).get("price")
                atl = round(float(cheapest_raw), 2) if cheapest_raw is not None else None
                best_quote: NormalizedPriceQuote | None = None

                for deal in details.get("deals") or []:
                    if not isinstance(deal, dict):
                        continue
                    sid = str(deal.get("storeID") or "")
                    sf_name = self.STORE_MAP.get(sid)
                    if not sf_name:
                        continue
                    price_val = round(float(deal.get("price") or 0.0), 2)
                    retail_val = round(float(deal.get("retailPrice") or price_val), 2)
                    savings = int(float(deal.get("savings") or 0))
                    deal_id = str(deal.get("dealID") or "").strip()
                    store_url = f"https://www.cheapshark.com/redirect?dealID={deal_id}" if deal_id else ""
                    plat_label = f"PC ({sf_name.upper() if sf_name != 'gmg' else 'Green Man Gaming'})"
                    quote = NormalizedPriceQuote(
                        storefront=sf_name,
                        store_sku_id=str(gid),
                        platform_family="PC",
                        platform_label=plat_label,
                        edition_name="Standard Edition",
                        edition_tier="base",
                        current_price_usd=price_val,
                        regular_price_usd=retail_val,
                        discount_pct=savings,
                        historical_low_usd=atl,
                        store_url=store_url,
                        is_authorized_seller=True,
                        is_drm_free=(sf_name == "gog"),
                        currency="USD",
                        region="US",
                        retrieved_at=now_iso,
                    )
                    quotes.append(quote)
                    if best_quote is None or quote.current_price_usd < best_quote.current_price_usd:
                        best_quote = quote

                if best_quote is not None:
                    # Also emit a generic storefront='cheapshark' fallback quote for legacy SKUs.
                    quotes.append(
                        NormalizedPriceQuote(
                            storefront="cheapshark",
                            store_sku_id=str(gid),
                            platform_family="PC",
                            platform_label=best_quote.platform_label,
                            edition_name="Standard Edition",
                            edition_tier="base",
                            current_price_usd=best_quote.current_price_usd,
                            regular_price_usd=best_quote.regular_price_usd,
                            discount_pct=best_quote.discount_pct,
                            historical_low_usd=atl,
                            store_url=best_quote.store_url,
                            is_authorized_seller=True,
                            is_drm_free=best_quote.is_drm_free,
                            currency="USD",
                            region="US",
                            retrieved_at=now_iso,
                        )
                    )

        return quotes
