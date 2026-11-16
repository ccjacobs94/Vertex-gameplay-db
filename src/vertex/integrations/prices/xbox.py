"""Microsoft Display Catalog API adapter for Xbox Series X|S and Xbox One prices."""

from __future__ import annotations

import re
from typing import Any

import httpx

from vertex.integrations.platforms.base import sanitize_error_message
from vertex.integrations.prices.base import (
    DEFAULT_USER_AGENT,
    XBOX_BATCH_CHUNK_SIZE,
    NormalizedPriceQuote,
    PriceAdapterCapabilities,
    PriceAdapterError,
    check_http_error,
    utc_now_iso,
)


class XboxCatalogAdapter:
    """Keyless Microsoft Display Catalog API adapter."""

    def __init__(self, *, market: str = "US") -> None:
        self.market = (market or "US").strip().upper() or "US"

    def capabilities(self) -> PriceAdapterCapabilities:
        return PriceAdapterCapabilities(
            storefront="xbox_store",
            display_name="Microsoft Xbox Store",
            platform_families=("Xbox",),
            requires_api_key=False,
            supports_editions=True,
            currency="USD" if self.market == "US" else self.market,
        )

    async def search_autosuggest(
        self,
        query: str,
        *,
        client: httpx.AsyncClient,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query or "").strip()
        if not clean_q:
            return []
        region = (market or self.market).strip().upper() or "US"
        try:
            resp = await client.get(
                "https://displaycatalog.mp.microsoft.com/v7.0/productFamilies/autosuggest",
                params={
                    "market": region,
                    "languages": "en-US",
                    "query": clean_q,
                    "productFamilyNames": "Games",
                },
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"Xbox autosuggest failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("Xbox Catalog", resp)
        data = resp.json()
        if not isinstance(data, dict):
            return []

        results: list[dict[str, Any]] = []
        for res in data.get("Results") or []:
            if not isinstance(res, dict):
                continue
            for p in res.get("Products") or []:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("ProductId") or p.get("Id") or "").strip()
                title = str(p.get("Title") or "").strip()
                if len(pid) == 12:
                    results.append({"Id": pid, "Title": title, "ProductFamilyName": "Games"})

        for rset in data.get("ResultSets") or []:
            if not isinstance(rset, dict):
                continue
            for s in rset.get("Suggests") or []:
                if not isinstance(s, dict):
                    continue
                pid = str(s.get("Id") or s.get("ProductId") or "").strip()
                title = str(s.get("Title") or "").strip()
                if len(pid) == 12:
                    results.append(
                        {
                            "Id": pid,
                            "Title": title,
                            "ProductFamilyName": str(s.get("ProductFamilyName") or "Games"),
                        }
                    )
        return results

    async def fetch_prices(
        self,
        *,
        store_sku_ids: list[str],
        client: httpx.AsyncClient,
        api_key: str = "",
        market: str | None = None,
    ) -> list[NormalizedPriceQuote]:
        del api_key
        cleaned_ids = list(dict.fromkeys(s.strip() for s in store_sku_ids if s and s.strip()))
        if not cleaned_ids:
            return []

        region = (market or self.market).strip().upper() or "US"
        quotes: list[NormalizedPriceQuote] = []

        for i in range(0, len(cleaned_ids), XBOX_BATCH_CHUNK_SIZE):
            chunk = cleaned_ids[i : i + XBOX_BATCH_CHUNK_SIZE]
            try:
                resp = await client.get(
                    "https://displaycatalog.mp.microsoft.com/v7.0/products",
                    params={
                        "bigIds": ",".join(chunk),
                        "market": region,
                        "languages": "en-us",
                        "MS-CV": "DGU1mcuYo0WMMp+F.1",
                    },
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                )
            except httpx.RequestError as exc:
                raise PriceAdapterError(
                    code="network_error",
                    message=sanitize_error_message(f"Xbox catalog query failed: {exc}"),
                    retryable=True,
                ) from exc

            check_http_error("Xbox Catalog", resp)
            data = resp.json()
            products = data.get("Products") if isinstance(data, dict) else []
            now_iso = utc_now_iso()

            for prod in products or []:
                if not isinstance(prod, dict):
                    continue
                pid = str(prod.get("ProductId") or "").strip()
                if not pid:
                    continue
                props = prod.get("Properties") or {}
                gen_opt = props.get("XboxConsoleGenOptimized") or []
                plat_label = (
                    "Xbox Series X|S"
                    if ("XboxSeriesX" in gen_opt or "XboxSeriesS" in gen_opt)
                    else "Xbox One"
                )

                image_url: str | None = None
                for lp in prod.get("LocalizedProperties") or []:
                    if not isinstance(lp, dict):
                        continue
                    imgs = lp.get("Images") or []
                    poster = next((im.get("Uri") for im in imgs if im.get("ImagePurpose") == "Poster"), None)
                    boxart = next((im.get("Uri") for im in imgs if im.get("ImagePurpose") == "BoxArt"), None)
                    branded = next((im.get("Uri") for im in imgs if im.get("ImagePurpose") == "BrandedKeyArt"), None)
                    cand_img = poster or boxart or branded
                    if isinstance(cand_img, str) and cand_img.strip():
                        image_url = f"https:{cand_img}" if cand_img.startswith("//") else cand_img
                        break

                sku_avails = prod.get("DisplaySkuAvailabilities") or []
                avails = (sku_avails[0].get("Availabilities") or []) if sku_avails else []
                if not avails:
                    continue
                price_data = ((avails[0].get("OrderManagementData") or {}).get("Price")) or {}
                list_price = price_data.get("ListPrice")
                msrp = price_data.get("MSRP") if price_data.get("MSRP") is not None else list_price
                if list_price is None:
                    continue
                cur = round(float(list_price), 2)
                reg = round(float(msrp), 2) if msrp is not None else cur
                if cur <= 0.0 and reg <= 0.0:
                    continue
                cut = int(round((1.0 - (cur / reg)) * 100)) if reg > 0 and cur < reg else 0
                currency = str(price_data.get("CurrencyCode") or ("USD" if region == "US" else "")).upper() or "USD"

                quotes.append(
                    NormalizedPriceQuote(
                        storefront="xbox_store",
                        store_sku_id=pid,
                        platform_family="Xbox",
                        platform_label=plat_label,
                        edition_name="Standard Edition",
                        edition_tier="base",
                        current_price_usd=cur,
                        regular_price_usd=reg,
                        discount_pct=cut,
                        store_url=f"https://www.xbox.com/en-us/games/store/-/{pid}",
                        is_authorized_seller=True,
                        is_drm_free=False,
                        currency=currency,
                        region=region,
                        retrieved_at=now_iso,
                        image_url=image_url,
                    )
                )

        return quotes
