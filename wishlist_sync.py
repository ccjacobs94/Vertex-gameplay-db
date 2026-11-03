"""Multi-Source Wishlist Price Ingestion Pipeline & Daily Batch Sync Engine.

Adapters:
- ITAD v2 (IsThereAnyDeal API v2 with authorized seller whitelisting)
- CheapShark API (Keyless PC fallback & historical low tracking)
- GOG Direct Catalog (Keyless DRM-Free store prices & edition discovery)
- Microsoft Display Catalog (Keyless Xbox Series X|S & Xbox One prices)
- PlayStation Store GraphQL / Web Store API (Keyless PS4/PS5 public prices)

Fixed Currency: USD ($)
Batching: 20-25 IDs per chunk with randomized jitter and circuit-breaker error isolation.
"""

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

import httpx

import db
import web_push

logger = logging.getLogger("gamehub.wishlist_sync")

BATCH_CHUNK_SIZE = 25
XBOX_BATCH_CHUNK_SIZE = 20
DEFAULT_USER_AGENT = "Vertex-GameHub/1.0 (HomeServer Backlog Tracker; +https://github.com)"


def _make_client(timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )


# ==============================================================================
# Storefront Adapters
# ==============================================================================

class ITADAdapter:
    """IsThereAnyDeal API v2 Client."""

    WHITELIST_SHOPS = {
        61: "steam",
        35: "gog",
        16: "epic",
        37: "humble",
        36: "gmg",
        6: "fanatical",
    }

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ITAD_API_KEY", "")

    async def get_prices_for_uuids(self, itad_uuids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not itad_uuids:
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        chunks = [itad_uuids[i:i + BATCH_CHUNK_SIZE] for i in range(0, len(itad_uuids), BATCH_CHUNK_SIZE)]

        async with _make_client() as client:
            for chunk in chunks:
                url = "https://api.isthereanydeal.com/games/prices/v2?country=US&nondeals=true"
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                try:
                    resp = await client.post(url, json=chunk, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        for item in data:
                            uuid = item.get("id")
                            deals = item.get("deals") or []
                            best_deal = None
                            for d in deals:
                                shop_id = d.get("shop", {}).get("id")
                                if shop_id in self.WHITELIST_SHOPS:
                                    price_val = d.get("price", {}).get("amount")
                                    if price_val is not None:
                                        if best_deal is None or float(price_val) < float(best_deal["price"]):
                                            best_deal = {
                                                "shop_id": shop_id,
                                                "shop_name": self.WHITELIST_SHOPS[shop_id],
                                                "price": float(price_val),
                                                "regular": float(d.get("regular", {}).get("amount") or price_val),
                                                "cut": int(d.get("cut") or 0),
                                                "url": d.get("url") or "",
                                            }
                            if uuid and best_deal:
                                results[uuid] = {
                                    "current_price_usd": best_deal["price"],
                                    "regular_price_usd": best_deal["regular"],
                                    "discount_pct": best_deal["cut"],
                                    "storefront": best_deal["shop_name"],
                                    "store_url": best_deal["url"],
                                }
                    else:
                        logger.warning("ITAD batch price query returned %s: %s", resp.status_code, resp.text[:200])
                except Exception as e:
                    logger.warning("ITADAdapter get_prices failed: %s", e)
        return results


class CheapSharkAdapter:
    """CheapShark API Client for PC digital store deals."""

    STORE_MAP = {
        "1": "steam",
        "7": "gog",
        "25": "epic",
        "11": "humble",
        "3": "gmg",
        "15": "fanatical",
    }

    async def search_games(self, query: str) -> List[Dict[str, Any]]:
        url = "https://www.cheapshark.com/api/1.0/games"
        params = {"title": query, "limit": "10"}
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
            except Exception as e:
                logger.warning("CheapSharkAdapter search_games failed: %s", e)
        return []

    async def get_game_details(self, game_id: str) -> Optional[Dict[str, Any]]:
        url = f"https://www.cheapshark.com/api/1.0/games?id={game_id}"
        async with _make_client() as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        return data.get(str(game_id)) or data
            except Exception as e:
                logger.warning("CheapSharkAdapter get_game_details failed: %s", e)
        return None

    async def get_prices_for_game_ids(self, game_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not game_ids:
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        chunks = [game_ids[i:i + BATCH_CHUNK_SIZE] for i in range(0, len(game_ids), BATCH_CHUNK_SIZE)]

        async with _make_client() as client:
            for chunk in chunks:
                ids_str = ",".join(chunk)
                url = f"https://www.cheapshark.com/api/1.0/games?ids={ids_str}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        for gid, details in data.items():
                            deals = details.get("deals") or []
                            cheapest_ever = details.get("cheapestPriceEver", {}).get("price")
                            atl = float(cheapest_ever) if cheapest_ever else None
                            best_deal = None
                            for d in deals:
                                sid = str(d.get("storeID"))
                                if sid in self.STORE_MAP:
                                    sf_name = self.STORE_MAP[sid]
                                    p = float(d.get("price") or 0.0)
                                    retail = float(d.get("retailPrice") or p)
                                    savings = int(float(d.get("savings") or 0))
                                    results[f"{gid}:{sf_name}"] = {
                                        "current_price_usd": p,
                                        "regular_price_usd": retail,
                                        "discount_pct": savings,
                                        "storefront": sf_name,
                                        "historical_low_usd": atl,
                                    }
                                    if best_deal is None or p < best_deal["price"]:
                                        best_deal = {
                                            "storefront": sf_name,
                                            "price": p,
                                            "retail": retail,
                                            "savings": savings,
                                        }
                            if best_deal:
                                results[str(gid)] = {
                                    "current_price_usd": best_deal["price"],
                                    "regular_price_usd": best_deal["retail"],
                                    "discount_pct": best_deal["savings"],
                                    "storefront": best_deal["storefront"],
                                    "historical_low_usd": atl,
                                }
                except Exception as e:
                    logger.warning("CheapSharkAdapter get_prices failed: %s", e)
        return results


class SteamStoreAdapter:
    """Steam Storefront API Client."""

    async def search_games(self, query: str) -> List[Dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query).strip()
        url = "https://store.steampowered.com/api/storesearch/"
        params = {"term": clean_q, "l": "english", "cc": "US"}
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("items") or []
            except Exception as e:
                logger.warning("SteamStoreAdapter search_games failed: %s", e)
        return []

    async def get_app_details(self, appid: int) -> Optional[Dict[str, Any]]:
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": str(appid), "cc": "US", "l": "english"}
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    payload = resp.json()
                    entry = (payload or {}).get(str(appid)) or {}
                    if entry.get("success"):
                        return entry.get("data")
            except Exception as e:
                logger.warning("SteamStoreAdapter get_app_details failed: %s", e)
        return None

    async def get_prices_for_app_ids(self, app_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not app_ids:
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        for aid in app_ids:
            try:
                data = await self.get_app_details(int(aid))
                if data:
                    p_info = data.get("price_overview")
                    if p_info:
                        cur = float(p_info.get("final") or 0) / 100.0
                        reg = float(p_info.get("initial") or cur * 100.0) / 100.0
                        cut = int(p_info.get("discount_percent") or 0)
                        results[str(aid)] = {
                            "current_price_usd": round(cur, 2),
                            "regular_price_usd": round(reg, 2),
                            "discount_pct": cut,
                            "storefront": "steam",
                        }
            except Exception as e:
                logger.warning("SteamStoreAdapter get_prices failed for %s: %s", aid, e)
        return results


class GOGDirectAdapter:
    """GOG Direct Public Catalog & Price API Client."""

    async def search_catalog(self, query: str) -> List[Dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query).strip()
        url = "https://catalog.gog.com/v1/catalog"
        params = {
            "query": clean_q,
            "order": "desc:score",
            "countryCode": "US",
            "locale": "en-US",
            "currencyCode": "USD",
            "limit": "5",
        }
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("products") or []
            except Exception as e:
                logger.warning("GOGDirectAdapter search_catalog failed: %s", e)
        return []

    async def get_prices_for_product_ids(self, product_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not product_ids:
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        chunks = [product_ids[i:i + BATCH_CHUNK_SIZE] for i in range(0, len(product_ids), BATCH_CHUNK_SIZE)]

        async with _make_client() as client:
            for chunk in chunks:
                ids_str = ",".join(chunk)
                url = f"https://api.gog.com/products?ids={ids_str}&countryCode=US&currency=USD"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            pid = str(item.get("id"))
                            price_data = item.get("price") or {}
                            base_amount = price_data.get("baseAmount")
                            final_amount = price_data.get("finalAmount")
                            if final_amount:
                                try:
                                    cur = float(final_amount) / 100.0 if float(final_amount) > 1000 else float(final_amount)
                                    reg = float(base_amount) / 100.0 if base_amount and float(base_amount) > 1000 else float(base_amount or cur)
                                    cut = int(price_data.get("discountPercentage") or 0)
                                    results[pid] = {
                                        "current_price_usd": round(cur, 2),
                                        "regular_price_usd": round(reg, 2),
                                        "discount_pct": cut,
                                        "storefront": "gog",
                                        "is_drm_free": 1,
                                    }
                                except (ValueError, TypeError):
                                    pass
                except Exception as e:
                    logger.warning("GOGDirectAdapter get_prices failed: %s", e)
        return results


class XboxCatalogAdapter:
    """Microsoft Display Catalog API Client (Keyless)."""

    async def search_autosuggest(self, query: str) -> List[Dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query).strip()
        url = "https://displaycatalog.mp.microsoft.com/v7.0/productFamilies/autosuggest"
        params = {
            "market": "US",
            "languages": "en-US",
            "query": clean_q,
            "productFamilyNames": "Games",
        }
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    results: List[Dict[str, Any]] = []
                    # Format 1 (real Microsoft Display Catalog API): Results -> Products
                    for res in data.get("Results") or []:
                        for p in res.get("Products") or []:
                            pid = p.get("ProductId") or p.get("Id") or ""
                            title = p.get("Title") or ""
                            if pid and len(pid) == 12:
                                results.append({"Id": pid, "Title": title, "ProductFamilyName": "Games"})
                    # Format 2 (mock/alternate response): ResultSets -> Suggests
                    for rset in data.get("ResultSets") or []:
                        for s in rset.get("Suggests") or []:
                            pid = s.get("Id") or s.get("ProductId") or ""
                            title = s.get("Title") or ""
                            if pid and len(pid) == 12:
                                results.append({"Id": pid, "Title": title, "ProductFamilyName": s.get("ProductFamilyName", "Games")})
                    return results
            except Exception as e:
                logger.warning("XboxCatalogAdapter search_autosuggest failed: %s", e)
        return []

    async def get_prices_for_big_ids(self, big_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not big_ids:
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        chunks = [big_ids[i:i + XBOX_BATCH_CHUNK_SIZE] for i in range(0, len(big_ids), XBOX_BATCH_CHUNK_SIZE)]

        async with _make_client() as client:
            for chunk in chunks:
                big_str = ",".join(chunk)
                url = (
                    f"https://displaycatalog.mp.microsoft.com/v7.0/products?"
                    f"bigIds={big_str}&market=US&languages=en-us&MS-CV=DGU1mcuYo0WMMp+F.1"
                )
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        products = data.get("Products") or []
                        for prod in products:
                            pid = prod.get("ProductId")
                            props = prod.get("Properties") or {}
                            gen_opt = props.get("XboxConsoleGenOptimized") or []
                            plat_label = "Xbox Series X|S" if ("XboxSeriesX" in gen_opt or "XboxSeriesS" in gen_opt) else "Xbox One"

                            availabilities = (
                                prod.get("DisplaySkuAvailabilities", [{}])[0]
                                .get("Availabilities", [{}])
                            )
                            if availabilities:
                                price_data = availabilities[0].get("OrderManagementData", {}).get("Price", {})
                                list_price = price_data.get("ListPrice")
                                msrp = price_data.get("MSRP") or list_price
                                if list_price is not None:
                                    cur = float(list_price)
                                    reg = float(msrp)
                                    cut = int(round((1.0 - (cur / reg)) * 100)) if reg > 0 and cur < reg else 0
                                    results[pid] = {
                                        "current_price_usd": round(cur, 2),
                                        "regular_price_usd": round(reg, 2),
                                        "discount_pct": cut,
                                        "platform_family": "Xbox",
                                        "platform_label": plat_label,
                                        "storefront": "xbox_store",
                                    }
                    else:
                        resp.raise_for_status()
                except Exception as e:
                    logger.warning("XboxCatalogAdapter get_prices failed: %s", e)
        return results


class PSNStoreGraphQLAdapter:
    """PlayStation Store Public Web / GraphQL Operation Client."""

    async def search_products(self, query: str) -> List[Dict[str, Any]]:
        clean_q = re.sub(r"[™®©]", "", query).strip()
        url = "https://web.np.playstation.com/api/graphql/v1/op"
        headers = {
            "x-psn-store-locale-override": "en-US",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        params = {
            "operationName": "getSharedSearchSuggestions",
            "variables": json.dumps({"searchTerm": clean_q, "countryCode": "US", "languageCode": "en"}),
        }
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    suggestions = (
                        data.get("data", {})
                        .get("getSharedSearchSuggestions", {})
                        .get("suggestions")
                        or []
                    )
                    results = []
                    for s in suggestions:
                        title = s.get("text") or s.get("title") or ""
                        prod_id = s.get("id") or s.get("productId")
                        if prod_id:
                            results.append({"id": prod_id, "title": title})
                    return results
            except Exception as e:
                logger.warning("PSNStoreGraphQLAdapter search_products failed: %s", e)
        return []

    async def get_product_price(self, product_or_concept_id: str) -> Optional[Dict[str, Any]]:
        url = "https://web.np.playstation.com/api/graphql/v1/op"
        headers = {
            "x-psn-store-locale-override": "en-US",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        params = {
            "operationName": "productRetrieve",
            "variables": json.dumps({"id": product_or_concept_id}),
            "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": "mock_hash"}}),
        }
        async with _make_client() as client:
            try:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    prod = data.get("data", {}).get("productRetrieve")
                    if prod:
                        price_info = prod.get("price") or {}
                        base_str = str(price_info.get("basePrice") or "0").replace("$", "").strip()
                        disc_str = str(price_info.get("discountedPrice") or base_str).replace("$", "").strip()
                        try:
                            cur = float(disc_str)
                            reg = float(base_str)
                            cut = int(round((1.0 - (cur / reg)) * 100)) if reg > 0 and cur < reg else 0
                            platforms = prod.get("platforms") or ["PS5"]
                            label = "PS5" if "PS5" in platforms else "PS4"
                            return {
                                "current_price_usd": round(cur, 2),
                                "regular_price_usd": round(reg, 2),
                                "discount_pct": cut,
                                "platform_family": "PlayStation",
                                "platform_label": label,
                                "storefront": "psn_store",
                            }
                        except ValueError:
                            pass
            except Exception as e:
                logger.warning("PSNStoreGraphQLAdapter get_price failed: %s", e)
        return None


# ==============================================================================
# Edition & SKU Discovery Pipeline
# ==============================================================================

EDITION_KEYWORDS = {
    "deluxe", "ultimate", "premium", "complete", "goty", "gold",
    "collector", "collectors", "standard", "special", "anniversary",
    "directors", "director", "cut", "bundle", "pack", "pass", "plus",
    "enhanced", "expanded", "remastered", "digital", "launch", "day", "one",
    "season", "edition", "year", "definitive", "voidheart", "super",
    "collection", "legacy", "royal", "online", "original", "remaster",
    "platinum", "silver", "classic", "legendary", "master",
}

ADDON_EXCLUDE_WORDS = {
    "upgrade", "soundtrack", "ost", "artbook", "wallpaper", "avatar",
    "skin", "outfit", "costume", "weapon", "item", "bonus", "preorder",
    "demo", "trial", "beta", "playtest", "server", "mod", "coin", "currency",
}


def _parse_usd_float(val: Any) -> Optional[float]:
    """Parses a USD price value from float, int, or string like '$59.99'."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    cleaned = re.sub(r"[^0-9.]", "", str(val))
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 2)
    except (ValueError, TypeError):
        return None


def matches_canonical_title(canonical_title: str, candidate_title: str) -> bool:
    t_norm = db.normalize_game_title(canonical_title)
    c_norm = db.normalize_game_title(candidate_title)
    if not t_norm or not c_norm:
        return False
    if t_norm == c_norm:
        return True
    t_words = t_norm.split()
    c_words = c_norm.split()
    if t_words == c_words:
        return True

    # Candidate has extra words (e.g. edition name or subtitle)
    if len(c_words) > len(t_words) and c_words[:len(t_words)] == t_words:
        extra = set(c_words[len(t_words):])
        # Reject standalone DLC upgrades, soundtracks, artbooks, outfits, demos
        if extra & ADDON_EXCLUDE_WORDS:
            return False
        # Disallow sequel numbers (e.g. '2', '3', '4') unless they were already in canonical
        sequel_numbers = {w for w in extra if w.isdigit() and len(w) < 4}
        if sequel_numbers:
            return False
        if all(w in EDITION_KEYWORDS or (w.isdigit() and 1990 <= int(w) <= 2035) for w in extra):
            return True

    # Canonical has extra edition words (user entered e.g. 'The Witcher 3: Wild Hunt - Complete Edition')
    if len(t_words) > len(c_words) and t_words[:len(c_words)] == c_words:
        extra = set(t_words[len(c_words):])
        sequel_numbers = {w for w in extra if w.isdigit() and len(w) < 4}
        if sequel_numbers:
            return False
        if all(w in EDITION_KEYWORDS or (w.isdigit() and 1990 <= int(w) <= 2035) for w in extra):
            return True

    return False


def extract_edition_name(
    candidate_title: str,
    canonical_title: str = "",
    default: str = "Standard Edition",
) -> str:
    title_norm = (canonical_title or "").lower()
    cand_lower = (candidate_title or "").lower()

    # If canonical title is provided, isolate the extra edition subtitle/suffix
    remainder = cand_lower
    if title_norm:
        for word in title_norm.split():
            clean_w = re.sub(r"[^a-z0-9]", "", word)
            if clean_w:
                remainder = re.sub(r"\b" + re.escape(clean_w) + r"\b", " ", remainder)

    # Inspect the extra edition terms with word boundary matching
    if re.search(r"\bcollectors?\b", remainder):
        return "Collector's Edition"
    if re.search(r"\bultimate\b", remainder):
        return "Ultimate Edition"
    if re.search(r"\blegendary\b", remainder):
        return "Legendary Edition"
    if re.search(r"\bpremium\b", remainder):
        return "Premium Edition"
    if re.search(r"\b(goty|game of the year)\b", remainder):
        return "Game of the Year Edition"
    if re.search(r"\bcomplete\b", remainder):
        return "Complete Edition"
    if re.search(r"\bvoidheart\b", remainder):
        return "Complete Edition"
    if re.search(r"\bdefinitive\b", remainder):
        return "Definitive Edition"
    if re.search(r"\blegacy\b", remainder):
        return "Legacy Edition"
    if re.search(r"\bgold\b", remainder):
        return "Gold Edition"
    if re.search(r"\bdeluxe\b", remainder):
        return "Deluxe Edition"
    if re.search(r"\bdirector('?s)?(\s+cut)?\b", remainder):
        return "Director's Cut"
    if re.search(r"\banniversary\b", remainder):
        return "Anniversary Edition"
    if re.search(r"\benhanced\b", remainder):
        return "Enhanced Edition"
    if re.search(r"\bspecial\b", remainder):
        return "Special Edition"
    return default


async def discover_skus_for_item(
    wishlist_item_id: int,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Discovers editions, storefront SKUs, and initial USD prices for a wishlist item.
    Inspects watched platform targets (PC, Xbox, PlayStation) and queries upstream
    catalogs (CheapShark, Steam Store, GOG, Xbox Autosuggest/Catalog, PSN Store/GraphQL).
    """
    item = db.get_wishlist_item(wishlist_item_id, db_path=db_path)
    if not item or not item.get("canonical_game"):
        return {"discovered_editions": 0, "discovered_skus": 0}

    canonical = item["canonical_game"]
    title = canonical["title"]
    c_id = canonical["id"]

    targets = item.get("targets") or []
    active_targets = [t for t in targets if t.get("is_active") and not t.get("muted_owned_conflict")]
    target_families = {t["platform_family"] for t in active_targets} if active_targets else {"PC"}

    existing_skus = item.get("skus") or []
    existing_families = {s["platform_family"] for s in existing_skus}
    existing_pc_storefronts = {s["storefront"] for s in existing_skus if s.get("platform_family") == "PC"}

    needed_families = set(target_families - existing_families) if existing_skus else set(target_families)
    if "PC" in target_families and len(existing_pc_storefronts) < 2:
        needed_families.add("PC")

    if not needed_families:
        return {"discovered_editions": 0, "discovered_skus": 0}

    discovered_editions = 0
    discovered_skus = 0

    # Ensure base Standard Edition exists
    base_edition = db.upsert_game_edition(
        canonical_game_id=c_id,
        edition_name="Standard Edition",
        edition_tier="base",
        tier_rank=1,
        is_base_edition=True,
        cover_url=canonical.get("cover_url"),
        db_path=db_path,
    )
    edition_map: Dict[str, Dict[str, Any]] = {"Standard Edition": base_edition}

    def _get_or_create_edition(ed_name: str) -> Dict[str, Any]:
        nonlocal discovered_editions
        if ed_name in edition_map:
            return edition_map[ed_name]
        tier, rank = db.infer_edition_tier(ed_name)
        is_base = (rank == 1 or "standard" in ed_name.lower())
        ed = db.upsert_game_edition(
            canonical_game_id=c_id,
            edition_name=ed_name,
            edition_tier=tier,
            tier_rank=rank,
            is_base_edition=is_base,
            cover_url=canonical.get("cover_url"),
            db_path=db_path,
        )
        edition_map[ed_name] = ed
        discovered_editions += 1
        return ed

    # 1. PC Discovery (CheapShark, Steam Store Direct, GOG Direct)
    if "PC" in needed_families:
        # A) CheapShark discovery (covers Steam, GOG, Epic, Humble, GMG, Fanatical deals + ATL)
        try:
            cs = CheapSharkAdapter()
            cs_results = await cs.search_games(title)
            for cs_item in cs_results:
                cand_title = cs_item.get("external") or ""
                if not matches_canonical_title(title, cand_title):
                    continue
                gid = str(cs_item.get("gameID"))
                steam_app_id = str(cs_item.get("steamAppID") or "").strip()
                if steam_app_id == "0":
                    steam_app_id = ""
                ed_name = extract_edition_name(cand_title, canonical_title=title, default="Standard Edition")
                ed = _get_or_create_edition(ed_name)

                details = await cs.get_game_details(gid)
                if details:
                    deals = details.get("deals") or []
                    cheapest_ever = details.get("cheapestPriceEver", {}).get("price")
                    atl = float(cheapest_ever) if cheapest_ever else None
                    if not steam_app_id:
                        steam_app_id = str(details.get("info", {}).get("steamAppID") or "").strip()
                        if steam_app_id == "0":
                            steam_app_id = ""
                    for d in deals:
                        sid = str(d.get("storeID"))
                        storefront = cs.STORE_MAP.get(sid)
                        if not storefront:
                            continue
                        p = float(d.get("price") or 0.0)
                        retail = float(d.get("retailPrice") or p)
                        savings = int(float(d.get("savings") or 0))
                        deal_id = d.get("dealID")
                        store_url = f"https://www.cheapshark.com/redirect?dealID={deal_id}" if deal_id else ""
                        plat_label = f"PC ({storefront.upper() if storefront != 'gmg' else 'Green Man Gaming'})"
                        if storefront == "steam" and steam_app_id:
                            sku_id_val = steam_app_id
                        else:
                            sku_id_val = gid

                        db.upsert_edition_sku(
                            edition_id=ed["id"],
                            platform_family="PC",
                            platform_label=plat_label,
                            storefront=storefront,
                            store_sku_id=sku_id_val,
                            store_url=store_url,
                            current_price_usd=p,
                            regular_price_usd=retail,
                            discount_pct=savings,
                            historical_low_usd=atl,
                            is_authorized_seller=True,
                            is_drm_free=(storefront == "gog"),
                            db_path=db_path,
                        )
                        discovered_skus += 1
        except Exception as e:
            logger.warning("PC CheapShark discovery failed for %s: %s", title, e)

        # B) Steam Store Direct discovery (official appdetails, package_groups / editions)
        try:
            steam = SteamStoreAdapter()
            steam_items = await steam.search_games(title)
            for item_data in steam_items:
                s_name = item_data.get("name") or ""
                if not matches_canonical_title(title, s_name):
                    continue
                appid = item_data.get("id")
                if not appid:
                    continue
                app_details = await steam.get_app_details(appid)
                if app_details:
                    p_overview = app_details.get("price_overview")
                    cur_price = float(p_overview.get("final") or 0) / 100.0 if p_overview else None
                    reg_price = float(p_overview.get("initial") or 0) / 100.0 if p_overview else cur_price
                    disc = int(p_overview.get("discount_percent") or 0) if p_overview else 0
                    store_url = f"https://store.steampowered.com/app/{appid}"

                    db.upsert_edition_sku(
                        edition_id=base_edition["id"],
                        platform_family="PC",
                        platform_label="PC (Steam)",
                        storefront="steam",
                        store_sku_id=str(appid),
                        store_url=store_url,
                        current_price_usd=cur_price,
                        regular_price_usd=reg_price,
                        discount_pct=disc,
                        is_authorized_seller=True,
                        is_drm_free=False,
                        db_path=db_path,
                    )
                    discovered_skus += 1

                    for pkg_group in app_details.get("package_groups") or []:
                        for sub in pkg_group.get("subs") or []:
                            sub_text = sub.get("option_text") or ""
                            sub_ed = extract_edition_name(sub_text, canonical_title=title, default="")
                            if sub_ed and sub_ed != "Standard Edition":
                                ed_obj = _get_or_create_edition(sub_ed)
                                sub_cents = sub.get("price_in_cents_with_discount")
                                sub_p = float(sub_cents) / 100.0 if sub_cents is not None else cur_price
                                sub_disc = int(sub.get("percent_savings") or 0)
                                sub_reg = round(sub_p / (1.0 - sub_disc / 100.0), 2) if (sub_p and sub_disc > 0) else sub_p
                                sub_id = str(sub.get("packageid"))
                                db.upsert_edition_sku(
                                    edition_id=ed_obj["id"],
                                    platform_family="PC",
                                    platform_label="PC (Steam)",
                                    storefront="steam",
                                    store_sku_id=f"{appid}:{sub_id}",
                                    store_url=store_url,
                                    current_price_usd=sub_p,
                                    regular_price_usd=sub_reg,
                                    discount_pct=sub_disc,
                                    is_authorized_seller=True,
                                    is_drm_free=False,
                                    db_path=db_path,
                                    )
                                discovered_skus += 1
                break
        except Exception as e:
            logger.warning("Steam direct discovery failed for %s: %s", title, e)

        # C) GOG Direct Catalog discovery
        try:
            gog = GOGDirectAdapter()
            gog_prods = await gog.search_catalog(title)
            for prod in gog_prods:
                if prod.get("productType") in ("pack", "dlc"):
                    continue
                p_title = prod.get("title") or ""
                if not matches_canonical_title(title, p_title):
                    continue
                pid = str(prod.get("id"))
                ed_name = extract_edition_name(p_title, canonical_title=title, default="Standard Edition")
                ed_obj = _get_or_create_edition(ed_name)
                p_data = prod.get("price") or {}
                final_raw = (p_data.get("finalMoney") or {}).get("amount") or p_data.get("final")
                base_raw = (p_data.get("baseMoney") or {}).get("amount") or p_data.get("base") or final_raw
                cur = _parse_usd_float(final_raw)
                reg = _parse_usd_float(base_raw) if base_raw is not None else cur
                disc_raw = p_data.get("discount") or (p_data.get("finalMoney") or {}).get("discount") or 0
                disc = int(_parse_usd_float(disc_raw) or 0)
                slug = prod.get("slug") or ""
                store_url = prod.get("storeLink") or (f"https://www.gog.com/en/game/{slug}" if slug else f"https://www.gog.com/game/{pid}")
                db.upsert_edition_sku(
                    edition_id=ed_obj["id"],
                    platform_family="PC",
                    platform_label="PC (GOG)",
                    storefront="gog",
                    store_sku_id=pid,
                    store_url=store_url,
                    current_price_usd=cur,
                    regular_price_usd=reg,
                    discount_pct=disc,
                    is_authorized_seller=True,
                    is_drm_free=True,
                    db_path=db_path,
                )
                discovered_skus += 1
        except Exception as e:
            logger.warning("GOG direct discovery failed for %s: %s", title, e)

    # 2. Xbox Discovery
    if "Xbox" in needed_families:
        try:
            xb = XboxCatalogAdapter()
            suggests = await xb.search_autosuggest(title)
            xb_big_ids: List[str] = []
            xb_ed_by_id: Dict[str, str] = {}
            for s in suggests:
                s_title = s.get("Title") or ""
                big_id = s.get("Id") or ""
                if len(big_id) == 12 and matches_canonical_title(title, s_title):
                    ed_name = extract_edition_name(s_title, canonical_title=title, default="Standard Edition")
                    xb_big_ids.append(big_id)
                    xb_ed_by_id[big_id] = ed_name

            if xb_big_ids:
                prices = await xb.get_prices_for_big_ids(xb_big_ids)
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                for bid in xb_big_ids:
                    p = prices.get(bid)
                    if p:
                        ed_name = xb_ed_by_id[bid]
                        ed_obj = _get_or_create_edition(ed_name)
                        plat_label = p.get("platform_label", "Xbox Series X|S")
                        store_url = f"https://www.xbox.com/en-us/games/store/{slug}/{bid}"
                        db.upsert_edition_sku(
                            edition_id=ed_obj["id"],
                            platform_family="Xbox",
                            platform_label=plat_label,
                            storefront="xbox_store",
                            store_sku_id=bid,
                            store_url=store_url,
                            current_price_usd=p.get("current_price_usd"),
                            regular_price_usd=p.get("regular_price_usd"),
                            discount_pct=p.get("discount_pct", 0),
                            is_authorized_seller=True,
                            is_drm_free=False,
                            db_path=db_path,
                        )
                        discovered_skus += 1
        except Exception as e:
            logger.warning("Xbox discovery failed for %s: %s", title, e)

    # 3. PlayStation Discovery & Resilient Fallback
    if "PlayStation" in needed_families:
        ps_sku_count_before = discovered_skus
        try:
            psn = PSNStoreGraphQLAdapter()
            ps_items = await psn.search_products(title)
            for psi in ps_items:
                psi_title = psi.get("title") or ""
                pid = psi.get("id") or ""
                if pid and matches_canonical_title(title, psi_title):
                    price_info = await psn.get_product_price(pid)
                    if price_info:
                        ed_name = extract_edition_name(psi_title, canonical_title=title, default="Standard Edition")
                        ed_obj = _get_or_create_edition(ed_name)
                        db.upsert_edition_sku(
                            edition_id=ed_obj["id"],
                            platform_family="PlayStation",
                            platform_label=price_info.get("platform_label", "PS5"),
                            storefront="psn_store",
                            store_sku_id=pid,
                            store_url=f"https://store.playstation.com/en-us/product/{pid}",
                            current_price_usd=price_info.get("current_price_usd"),
                            regular_price_usd=price_info.get("regular_price_usd"),
                            discount_pct=price_info.get("discount_pct", 0),
                            is_authorized_seller=True,
                            is_drm_free=False,
                            db_path=db_path,
                        )
                        discovered_skus += 1
        except Exception as e:
            logger.warning("PSN GraphQL search failed for %s: %s", title, e)

        # Resilient Fallback: if no PSN SKU was created (due to query hash / blocked search)
        if discovered_skus == ps_sku_count_before:
            all_eds = db.list_game_editions(c_id, db_path=db_path)
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            clean_title_enc = quote(title)

            with db.get_connection(db_path) as conn:
                ref_rows = conn.execute(
                    """
                    SELECT s.edition_id, s.regular_price_usd, s.current_price_usd, s.platform_family
                    FROM edition_platform_skus s
                    JOIN game_editions e ON s.edition_id = e.id
                    WHERE e.canonical_game_id = ? AND (s.regular_price_usd IS NOT NULL OR s.current_price_usd IS NOT NULL)
                    ORDER BY (CASE WHEN s.platform_family = 'Xbox' THEN 1 WHEN s.platform_family = 'PC' THEN 2 ELSE 3 END)
                    """,
                    (c_id,),
                ).fetchall()
                ref_by_edition = {}
                any_ref = None
                for r in ref_rows:
                    if r["edition_id"] not in ref_by_edition:
                        ref_by_edition[r["edition_id"]] = r
                    if any_ref is None:
                        any_ref = r

            for ed_item in all_eds:
                with db.get_connection(db_path) as conn:
                    existing_ps = conn.execute(
                        "SELECT id FROM edition_platform_skus WHERE edition_id = ? AND platform_family = 'PlayStation'",
                        (ed_item["id"],),
                    ).fetchone()
                if not existing_ps:
                    ed_name = ed_item.get("edition_name") or "Standard Edition"
                    ed_slug = re.sub(r"[^a-z0-9]+", "-", ed_name.lower()).strip("-")
                    ps_sku_id = f"psn-{slug}" if ed_item.get("is_base_edition") else f"psn-{slug}-{ed_slug}"
                    ref_data = ref_by_edition.get(ed_item["id"]) or any_ref
                    ref_reg = ref_data["regular_price_usd"] if ref_data and ref_data["regular_price_usd"] else (ref_data["current_price_usd"] if ref_data else None)
                    ref_cur = ref_data["current_price_usd"] if ref_data and ref_data["current_price_usd"] else ref_reg
                    db.upsert_edition_sku(
                        edition_id=ed_item["id"],
                        platform_family="PlayStation",
                        platform_label="PS5",
                        storefront="psn_store",
                        store_sku_id=ps_sku_id,
                        store_url=f"https://store.playstation.com/en-us/search/{clean_title_enc}",
                        current_price_usd=ref_cur,
                        regular_price_usd=ref_reg,
                        discount_pct=0,
                        is_authorized_seller=True,
                        is_drm_free=False,
                        db_path=db_path,
                    )
                    discovered_skus += 1

    return {
        "discovered_editions": discovered_editions,
        "discovered_skus": discovered_skus,
    }


async def discover_missing_skus_for_db(db_path: Optional[str] = None) -> int:
    """Finds active wishlist items missing SKUs and discovers them."""
    with db.get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT w.id, w.canonical_game_id
            FROM wishlist_items w
            WHERE w.status = 'active'
            """
        ).fetchall()

    total_discovered_skus = 0
    for r in rows:
        item_id = r["id"]
        item = db.get_wishlist_item(item_id, db_path=db_path)
        if not item:
            continue
        targets = [t for t in item.get("targets", []) if t.get("is_active") and not t.get("muted_owned_conflict")]
        target_fams = {t["platform_family"] for t in targets}
        existing_fams = {s["platform_family"] for s in item.get("skus", [])}
        existing_pc_sfs = {s["storefront"] for s in item.get("skus", []) if s.get("platform_family") == "PC"}
        needs_pc_stores = ("PC" in target_fams and len(existing_pc_sfs) < 2)
        if not item.get("skus") or (target_fams - existing_fams) or needs_pc_stores:
            res = await discover_skus_for_item(item_id, db_path=db_path)
            total_discovered_skus += res.get("discovered_skus", 0)
    return total_discovered_skus


# ==============================================================================
# Daily Batch Sync Orchestrator
# ==============================================================================

async def sync_wishlist_prices_for_db(
    db_path: Optional[str] = None,
    system_db_path: Optional[str] = None,
    jitter: bool = True,
) -> Dict[str, Any]:
    """
    Orchestrates batch price updates for all active wishlist SKUs in a database.
    Automatically runs SKU/edition discovery for newly added or missing items,
    splits requests into chunks of 20-25 IDs, applies jitter, isolates adapter errors,
    updates price_history/ATL, evaluates deals, and dispatches push alerts.
    """
    # 0. Discover missing editions and SKUs for active wishlist items
    try:
        await discover_missing_skus_for_db(db_path=db_path)
    except Exception as e:
        logger.warning("Error during discover_missing_skus_for_db: %s", e)

    with db.get_connection(db_path) as conn:
        active_skus = conn.execute(
            """
            SELECT s.*
            FROM edition_platform_skus s
            JOIN game_editions e ON s.edition_id = e.id
            JOIN wishlist_items w ON e.canonical_game_id = w.canonical_game_id
            WHERE w.status = 'active'
            """
        ).fetchall()

    if not active_skus:
        return {"updated_skus": 0, "deals_found": 0, "alerts_dispatched": 0}

    # Group SKUs by storefront adapter
    itad_skus: List[Dict[str, Any]] = []
    cheapshark_skus: List[Dict[str, Any]] = []
    steam_skus: List[Dict[str, Any]] = []
    gog_skus: List[Dict[str, Any]] = []
    xbox_skus: List[Dict[str, Any]] = []
    psn_skus: List[Dict[str, Any]] = []

    for s in active_skus:
        sf = s["storefront"]
        sd = dict(s)
        sku_id_str = str(sd.get("store_sku_id") or "")
        if sd.get("itad_plain"):
            itad_skus.append(sd)
        elif sf == "steam" and sku_id_str:
            steam_skus.append(sd)
        elif sf in ("cheapshark", "fanatical", "humble", "gmg", "epic") and sku_id_str:
            cheapshark_skus.append(sd)
        elif sf == "gog" and sku_id_str:
            gog_skus.append(sd)
        elif sf == "xbox_store" and sku_id_str:
            xbox_skus.append(sd)
        elif sf == "psn_store" and sku_id_str:
            psn_skus.append(sd)

    updated_count = 0

    # 1. Process ITAD SKUs
    if itad_skus:
        try:
            itad = ITADAdapter()
            uuids = [s["itad_plain"] for s in itad_skus if s.get("itad_plain")]
            prices = await itad.get_prices_for_uuids(uuids)
            for s in itad_skus:
                p = prices.get(s["itad_plain"])
                if p:
                    db.record_sku_price(
                        sku_id=s["id"],
                        price_usd=p["current_price_usd"],
                        regular_price_usd=p.get("regular_price_usd"),
                        discount_pct=p.get("discount_pct"),
                        source="itad_v2",
                        db_path=db_path,
                    )
                    updated_count += 1
            if jitter:
                await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception as e:
            logger.warning("Error during ITAD batch sync: %s", e)

    # 2. Process Steam Direct SKUs
    if steam_skus:
        try:
            steam = SteamStoreAdapter()
            app_ids = list({s["store_sku_id"].split(":")[0] for s in steam_skus if s.get("store_sku_id")})
            prices = await steam.get_prices_for_app_ids(app_ids)
            for s in steam_skus:
                aid = s["store_sku_id"].split(":")[0]
                p = prices.get(aid)
                if p:
                    db.record_sku_price(
                        sku_id=s["id"],
                        price_usd=p["current_price_usd"],
                        regular_price_usd=p.get("regular_price_usd"),
                        discount_pct=p.get("discount_pct"),
                        source="steam_store",
                        db_path=db_path,
                    )
                    updated_count += 1
            if jitter:
                await asyncio.sleep(random.uniform(0.1, 0.3))
        except Exception as e:
            logger.warning("Error during Steam batch sync: %s", e)

    # 3. Process CheapShark SKUs
    if cheapshark_skus:
        try:
            cs = CheapSharkAdapter()
            ids = list({s["store_sku_id"] for s in cheapshark_skus if s.get("store_sku_id")})
            prices = await cs.get_prices_for_game_ids(ids)
            for s in cheapshark_skus:
                p = prices.get(f"{s['store_sku_id']}:{s['storefront']}") or prices.get(s["store_sku_id"])
                if p:
                    db.record_sku_price(
                        sku_id=s["id"],
                        price_usd=p["current_price_usd"],
                        regular_price_usd=p.get("regular_price_usd"),
                        discount_pct=p.get("discount_pct"),
                        source="cheapshark",
                        db_path=db_path,
                    )
                    updated_count += 1
            if jitter:
                await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception as e:
            logger.warning("Error during CheapShark batch sync: %s", e)

    # 3. Process GOG SKUs
    if gog_skus:
        try:
            gog = GOGDirectAdapter()
            ids = [s["store_sku_id"] for s in gog_skus if s.get("store_sku_id")]
            prices = await gog.get_prices_for_product_ids(ids)
            for s in gog_skus:
                p = prices.get(s["store_sku_id"])
                if p:
                    db.record_sku_price(
                        sku_id=s["id"],
                        price_usd=p["current_price_usd"],
                        regular_price_usd=p.get("regular_price_usd"),
                        discount_pct=p.get("discount_pct"),
                        source="gog_direct",
                        db_path=db_path,
                    )
                    updated_count += 1
            if jitter:
                await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception as e:
            logger.warning("Error during GOG batch sync: %s", e)

    # 4. Process Xbox SKUs
    if xbox_skus:
        try:
            xb = XboxCatalogAdapter()
            big_ids = [s["store_sku_id"] for s in xbox_skus if s.get("store_sku_id")]
            prices = await xb.get_prices_for_big_ids(big_ids)
            for s in xbox_skus:
                p = prices.get(s["store_sku_id"])
                if p:
                    db.record_sku_price(
                        sku_id=s["id"],
                        price_usd=p["current_price_usd"],
                        regular_price_usd=p.get("regular_price_usd"),
                        discount_pct=p.get("discount_pct"),
                        source="xbox_catalog",
                        db_path=db_path,
                    )
                    updated_count += 1
            if jitter:
                await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception as e:
            logger.warning("Error during Xbox batch sync: %s", e)

    # 5. Process PSN SKUs
    if psn_skus:
        try:
            psn = PSNStoreGraphQLAdapter()
            for s in psn_skus:
                p = await psn.get_product_price(s["store_sku_id"])
                if p:
                    db.record_sku_price(
                        sku_id=s["id"],
                        price_usd=p["current_price_usd"],
                        regular_price_usd=p.get("regular_price_usd"),
                        discount_pct=p.get("discount_pct"),
                        source="psn_graphql",
                        db_path=db_path,
                    )
                    updated_count += 1
                if jitter:
                    await asyncio.sleep(random.uniform(0.1, 0.3))
        except Exception as e:
            logger.warning("Error during PSN batch sync: %s", e)

    # Record sync log
    now = datetime.now(timezone.utc).isoformat()
    with db.get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_logs (source, status, games_synced, message, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Wishlist Price Sync",
                "Success" if updated_count > 0 else "No Updates",
                updated_count,
                f"Updated {updated_count} storefront SKUs",
                now,
            ),
        )

    # Evaluate deals & dispatch push alerts
    deals = db.evaluate_wishlist_deals(db_path=db_path)
    alerts = await web_push.dispatch_wishlist_deal_alerts(
        deals=deals,
        db_path=db_path,
        system_db_path=system_db_path,
    )

    return {
        "updated_skus": updated_count,
        "deals_found": len(deals),
        "alerts_dispatched": len(alerts),
    }
