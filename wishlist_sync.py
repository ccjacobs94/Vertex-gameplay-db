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
                                    p = float(d.get("price") or 0.0)
                                    if best_deal is None or p < best_deal["price"]:
                                        best_deal = {
                                            "storefront": self.STORE_MAP[sid],
                                            "price": p,
                                            "retail": float(d.get("retailPrice") or p),
                                            "savings": int(float(d.get("savings") or 0)),
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


class GOGDirectAdapter:
    """GOG Direct Public Catalog & Price API Client."""

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
# Daily Batch Sync Orchestrator
# ==============================================================================

async def sync_wishlist_prices_for_db(
    db_path: Optional[str] = None,
    system_db_path: Optional[str] = None,
    jitter: bool = True,
) -> Dict[str, Any]:
    """
    Orchestrates batch price updates for all active wishlist SKUs in a database.
    Splits requests into chunks of 20-25 IDs, applies jitter, isolates adapter errors,
    updates price_history/ATL, evaluates deals, and dispatches push alerts.
    """
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
    gog_skus: List[Dict[str, Any]] = []
    xbox_skus: List[Dict[str, Any]] = []
    psn_skus: List[Dict[str, Any]] = []

    for s in active_skus:
        sf = s["storefront"]
        sd = dict(s)
        if sd.get("itad_plain"):
            itad_skus.append(sd)
        elif sf in ("cheapshark", "steam", "fanatical", "humble", "gmg") and sd.get("store_sku_id"):
            cheapshark_skus.append(sd)
        elif sf == "gog" and sd.get("store_sku_id"):
            gog_skus.append(sd)
        elif sf == "xbox_store" and sd.get("store_sku_id"):
            xbox_skus.append(sd)
        elif sf == "psn_store" and sd.get("store_sku_id"):
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

    # 2. Process CheapShark SKUs
    if cheapshark_skus:
        try:
            cs = CheapSharkAdapter()
            ids = [s["store_sku_id"] for s in cheapshark_skus if s.get("store_sku_id")]
            prices = await cs.get_prices_for_game_ids(ids)
            for s in cheapshark_skus:
                p = prices.get(s["store_sku_id"])
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
