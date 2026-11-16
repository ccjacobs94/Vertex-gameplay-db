"""Steam Storefront public price and edition adapter."""

from __future__ import annotations

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
from vertex.integrations.prices.matching import extract_edition_name
from vertex.wishlist.models import infer_edition_tier


class SteamStoreAdapter:
    """Steam Storefront API client (storesearch + appdetails)."""

    def __init__(self, *, country: str = "US") -> None:
        self.country = (country or "US").strip().upper() or "US"

    def capabilities(self) -> PriceAdapterCapabilities:
        return PriceAdapterCapabilities(
            storefront="steam",
            display_name="Steam Store",
            platform_families=("PC",),
            requires_api_key=False,
            supports_editions=True,
            currency="USD" if self.country == "US" else self.country,
        )

    async def search_games(
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
                "https://store.steampowered.com/api/storesearch/",
                params={"term": clean_q, "l": "english", "cc": region},
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"Steam store search failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("Steam Store", resp)
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else []
        return [it for it in (items or []) if isinstance(it, dict)]

    async def get_app_details(
        self,
        appid: int | str,
        *,
        client: httpx.AsyncClient,
        country: str | None = None,
    ) -> dict[str, Any] | None:
        aid = str(appid).strip()
        if not aid.isdigit():
            return None
        region = (country or self.country).strip().upper() or "US"
        try:
            resp = await client.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": aid, "cc": region, "l": "english"},
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.RequestError as exc:
            raise PriceAdapterError(
                code="network_error",
                message=sanitize_error_message(f"Steam appdetails failed: {exc}"),
                retryable=True,
            ) from exc

        check_http_error("Steam Store", resp)
        payload = resp.json()
        if not isinstance(payload, dict):
            return None
        entry = payload.get(aid) or {}
        if isinstance(entry, dict) and entry.get("success") and isinstance(entry.get("data"), dict):
            return entry["data"]
        return None

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
        if not cleaned_ids:
            return []

        region = (country or self.country).strip().upper() or "US"
        app_ids = list(dict.fromkeys(s.split(":", 1)[0] for s in cleaned_ids if s.split(":", 1)[0].isdigit()))
        details_by_appid: dict[str, dict[str, Any]] = {}
        for aid in app_ids:
            data = await self.get_app_details(aid, client=client, country=region)
            if data:
                details_by_appid[aid] = data

        quotes: list[NormalizedPriceQuote] = []
        now_iso = utc_now_iso()
        for sku_id in cleaned_ids:
            parts = sku_id.split(":", 1)
            aid = parts[0]
            sub_id = parts[1] if len(parts) > 1 else None
            data = details_by_appid.get(aid)
            if not data:
                continue

            title = str(data.get("name") or "").strip()
            store_url = f"https://store.steampowered.com/app/{aid}"
            header_img = data.get("header_image") if isinstance(data.get("header_image"), str) else None
            p_info = data.get("price_overview") if isinstance(data.get("price_overview"), dict) else None
            currency = (
                str(p_info.get("currency") or ("USD" if region == "US" else "")).upper()
                if p_info
                else ("USD" if region == "US" else "")
            ) or "USD"

            if sub_id is None:
                if p_info:
                    cur = round(float(p_info.get("final") or 0) / 100.0, 2)
                    reg = round(float(p_info.get("initial") or cur * 100.0) / 100.0, 2)
                    cut = int(p_info.get("discount_percent") or 0)
                    quotes.append(
                        NormalizedPriceQuote(
                            storefront="steam",
                            store_sku_id=aid,
                            platform_family="PC",
                            platform_label="PC (Steam)",
                            edition_name="Standard Edition",
                            edition_tier="base",
                            current_price_usd=cur,
                            regular_price_usd=reg,
                            discount_pct=cut,
                            store_url=store_url,
                            is_authorized_seller=True,
                            is_drm_free=False,
                            currency=currency,
                            region=region,
                            retrieved_at=now_iso,
                            image_url=header_img,
                        )
                    )
            else:
                matched_sub: dict[str, Any] | None = None
                for pkg_group in data.get("package_groups") or []:
                    for sub in (pkg_group.get("subs") or []) if isinstance(pkg_group, dict) else []:
                        if str(sub.get("packageid") or "") == sub_id:
                            matched_sub = sub
                            break
                    if matched_sub is not None:
                        break
                if matched_sub is not None:
                    sub_cents = matched_sub.get("price_in_cents_with_discount")
                    if sub_cents is not None:
                        cur = round(float(sub_cents) / 100.0, 2)
                        cut = int(matched_sub.get("percent_savings") or 0)
                        reg = round(cur / (1.0 - cut / 100.0), 2) if (cur > 0 and 0 < cut < 100) else cur
                        ed_name = extract_edition_name(
                            str(matched_sub.get("option_text") or ""),
                            canonical_title=title,
                            default="Deluxe Edition",
                        )
                        ed_tier, _ = infer_edition_tier(ed_name)
                        quotes.append(
                            NormalizedPriceQuote(
                                storefront="steam",
                                store_sku_id=sku_id,
                                platform_family="PC",
                                platform_label="PC (Steam)",
                                edition_name=ed_name,
                                edition_tier=ed_tier,
                                current_price_usd=cur,
                                regular_price_usd=reg,
                                discount_pct=cut,
                                store_url=store_url,
                                is_authorized_seller=True,
                                is_drm_free=False,
                                currency=currency,
                                region=region,
                                retrieved_at=now_iso,
                                image_url=header_img,
                            )
                        )

        return quotes
