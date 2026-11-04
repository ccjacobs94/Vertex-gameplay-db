"""Tests for Wishlist Sync adapters, batch chunking, error isolation, and Web Push.

External APIs (ITAD, CheapShark, GOG, Xbox Display Catalog, PSN GraphQL) are mocked.
Tests never make real network calls and adhere to network isolation rules.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import db
import system_db
import web_push
import wishlist_sync


class TestWishlistSyncAndPush(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "sync_test.db")
        self.system_db_path = str(Path(self.temp_dir.name) / "system_test.db")
        self.orig_system_db = system_db.DEFAULT_SYSTEM_DB_PATH
        system_db.DEFAULT_SYSTEM_DB_PATH = self.system_db_path
        db.init_db(self.db_path)
        system_db.init_system_db(self.system_db_path)

    async def asyncTearDown(self):
        system_db.DEFAULT_SYSTEM_DB_PATH = self.orig_system_db
        self.temp_dir.cleanup()

    def test_vapid_keypair_generation_and_persistence(self):
        # Should generate a valid ECDSA P-256 keypair and store in system_db
        keys = web_push.get_or_create_vapid_keys(db_path=self.system_db_path)
        self.assertIn("public_key", keys)
        self.assertIn("private_key", keys)
        self.assertTrue(len(keys["public_key"]) > 40)
        self.assertTrue(len(keys["private_key"]) > 30)

        # Retrieving again should return the exact same persisted keys
        keys2 = web_push.get_or_create_vapid_keys(db_path=self.system_db_path)
        self.assertEqual(keys["public_key"], keys2["public_key"])
        self.assertEqual(keys["private_key"], keys2["private_key"])

    @patch("web_push._send_raw_push")
    async def test_push_notification_dispatch_and_deduplication(self, mock_send_push):
        mock_send_push.return_value = {"status": "sent"}

        # Register a push subscription
        sub = db.upsert_push_subscription(
            endpoint="https://push.example.com/sub/12345",
            p256dh_key="mock_p256dh",
            auth_key="mock_auth",
            user_agent="Firefox",
            device_label="Pixel 7",
            db_path=self.db_path,
        )
        self.assertEqual(sub["endpoint"], "https://push.example.com/sub/12345")

        # Create wishlist item with PC target $20
        item = db.add_wishlist_item(
            title="Hades II",
            platform_targets=[{"platform": "PC", "target_price_usd": 20.00}],
            db_path=self.db_path,
        )
        canonical = item["canonical_game"]
        ed = item["editions"][0]
        sku = db.upsert_edition_sku(
            edition_id=ed["id"],
            platform_family="PC",
            platform_label="PC (Steam)",
            storefront="steam",
            store_sku_id="1145350",
            current_price_usd=15.00,
            db_path=self.db_path,
        )

        deals = db.evaluate_wishlist_deals(wishlist_item_id=item["id"], db_path=self.db_path)
        self.assertEqual(len(deals), 1)

        # First alert dispatch: should send push
        dispatched = await web_push.dispatch_wishlist_deal_alerts(
            deals=deals,
            db_path=self.db_path,
            system_db_path=self.system_db_path,
        )
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(mock_send_push.call_count, 1)

        # Second alert dispatch with same price: should be deduplicated
        dispatched2 = await web_push.dispatch_wishlist_deal_alerts(
            deals=deals,
            db_path=self.db_path,
            system_db_path=self.system_db_path,
        )
        self.assertEqual(len(dispatched2), 0)
        self.assertEqual(mock_send_push.call_count, 1)

        # Price drops further to $12: should send new alert
        db.record_sku_price(sku["id"], price_usd=12.00, db_path=self.db_path)
        deals3 = db.evaluate_wishlist_deals(wishlist_item_id=item["id"], db_path=self.db_path)
        dispatched3 = await web_push.dispatch_wishlist_deal_alerts(
            deals=deals3,
            db_path=self.db_path,
            system_db_path=self.system_db_path,
        )
        self.assertEqual(len(dispatched3), 1)
        self.assertEqual(mock_send_push.call_count, 2)

    async def test_empty_subscriptions_does_not_mark_target_alerted(self):
        # Create wishlist item with PC target $20 and deal at $14.99 with NO push subscriptions
        item = db.add_wishlist_item(
            title="Silksong Deals",
            platform_targets=[{"platform": "PC", "target_price_usd": 20.00}],
            db_path=self.db_path,
        )
        ed = item["editions"][0]
        db.upsert_edition_sku(
            edition_id=ed["id"],
            platform_family="PC",
            platform_label="PC (Steam)",
            storefront="steam",
            store_sku_id="99999",
            current_price_usd=14.99,
            db_path=self.db_path,
        )
        deals = db.evaluate_wishlist_deals(wishlist_item_id=item["id"], db_path=self.db_path)
        self.assertEqual(len(deals), 1)

        # Dispatch when subscriptions table is completely empty
        dispatched = await web_push.dispatch_wishlist_deal_alerts(
            deals=deals,
            db_path=self.db_path,
            system_db_path=self.system_db_path,
        )
        self.assertEqual(len(dispatched), 0)

        # Verify target was NOT marked as alerted
        with db.get_connection(self.db_path) as conn:
            target = conn.execute(
                "SELECT * FROM wishlist_platform_targets WHERE wishlist_item_id = ?",
                (item["id"],),
            ).fetchone()
            self.assertIsNone(target["last_alerted_at"])
            self.assertIsNone(target["last_alerted_price_usd"])

    @patch("httpx.AsyncClient.post")
    async def test_itad_adapter_parsing_and_chunking(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": "itad-uuid-1",
                "deals": [
                    {
                        "shop": {"id": 61, "name": "Steam"},
                        "price": {"amount": 19.99},
                        "regular": {"amount": 39.99},
                        "cut": 50,
                        "url": "https://store.steampowered.com/app/123",
                    }
                ],
            }
        ]
        mock_post.return_value = mock_response

        adapter = wishlist_sync.ITADAdapter(api_key="test_key")
        prices = await adapter.get_prices_for_uuids(["itad-uuid-1"])
        self.assertIn("itad-uuid-1", prices)
        p = prices["itad-uuid-1"]
        self.assertEqual(p["current_price_usd"], 19.99)
        self.assertEqual(p["regular_price_usd"], 39.99)
        self.assertEqual(p["discount_pct"], 50)
        self.assertEqual(p["storefront"], "steam")

    @patch("httpx.AsyncClient.get")
    async def test_cheapshark_adapter_parsing(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "100": {
                "info": {"title": "Batman Arkham City"},
                "cheapestPriceEver": {"price": "4.99", "date": 1600000000},
                "deals": [
                    {
                        "dealID": "deal1",
                        "storeID": "1",
                        "price": "9.99",
                        "retailPrice": "19.99",
                        "savings": "50.025",
                    }
                ],
            }
        }
        mock_get.return_value = mock_response

        adapter = wishlist_sync.CheapSharkAdapter()
        prices = await adapter.get_prices_for_game_ids(["100"])
        self.assertIn("100", prices)
        p = prices["100"]
        self.assertEqual(p["current_price_usd"], 9.99)
        self.assertEqual(p["regular_price_usd"], 19.99)
        self.assertEqual(p["historical_low_usd"], 4.99)

    @patch("httpx.AsyncClient.get")
    async def test_xbox_catalog_adapter_parsing(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Products": [
                {
                    "ProductId": "9NBLGGH4R6P9",
                    "LocalizedProperties": [{"ProductTitle": "Forza Horizon 5"}],
                    "Properties": {"XboxConsoleGenOptimized": ["XboxSeriesX", "XboxSeriesS"]},
                    "DisplaySkuAvailabilities": [
                        {
                            "Availabilities": [
                                {
                                    "OrderManagementData": {
                                        "Price": {
                                            "ListPrice": 29.99,
                                            "MSRP": 59.99,
                                        }
                                    }
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        mock_get.return_value = mock_response

        adapter = wishlist_sync.XboxCatalogAdapter()
        prices = await adapter.get_prices_for_big_ids(["9NBLGGH4R6P9"])
        self.assertIn("9NBLGGH4R6P9", prices)
        p = prices["9NBLGGH4R6P9"]
        self.assertEqual(p["current_price_usd"], 29.99)
        self.assertEqual(p["regular_price_usd"], 59.99)
        self.assertEqual(p["discount_pct"], 50)
        self.assertIn("Xbox Series X|S", p["platform_label"])

    @patch("httpx.AsyncClient.get")
    async def test_psn_graphql_adapter_parsing(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "productRetrieve": {
                    "id": "UP0001-PPSA01234_00-0000000000000000",
                    "name": "Death Stranding Director's Cut",
                    "price": {
                        "basePrice": "$49.99",
                        "discountedPrice": "$19.99",
                        "isFree": False,
                    },
                    "platforms": ["PS5"],
                }
            }
        }
        mock_get.return_value = mock_response

        adapter = wishlist_sync.PSNStoreGraphQLAdapter()
        prices = await adapter.get_product_price("UP0001-PPSA01234_00-0000000000000000")
        self.assertIsNotNone(prices)
        self.assertEqual(prices["current_price_usd"], 19.99)
        self.assertEqual(prices["regular_price_usd"], 49.99)
        self.assertEqual(prices["platform_family"], "PlayStation")
        self.assertEqual(prices["platform_label"], "PS5")

    @patch("httpx.AsyncClient.get")
    async def test_sync_orchestrator_error_isolation(self, mock_get):
        # Simulate Xbox endpoint failing with HTTP 500 while CheapShark succeeds
        def mock_get_side_effect(url, *args, **kwargs):
            mock_resp = MagicMock()
            if "displaycatalog.mp.microsoft.com" in str(url):
                mock_resp.status_code = 500
                mock_resp.raise_for_status.side_effect = Exception("Microsoft Server Error")
                return mock_resp
            elif "cheapshark.com" in str(url):
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "101": {
                        "cheapestPriceEver": {"price": "14.99"},
                        "deals": [{"storeID": "1", "price": "14.99", "retailPrice": "29.99", "savings": "50.0"}],
                    }
                }
                return mock_resp
            mock_resp.status_code = 404
            return mock_resp

        mock_get.side_effect = mock_get_side_effect

        # Create two SKUs: one CheapShark/PC, one Xbox
        canonical = db.upsert_canonical_game(title="Test Isolation Game", db_path=self.db_path)
        ed = db.upsert_game_edition(canonical_game_id=canonical["id"], edition_name="Standard", db_path=self.db_path)
        pc_sku = db.upsert_edition_sku(
            edition_id=ed["id"],
            platform_family="PC",
            platform_label="PC (Steam)",
            storefront="cheapshark",
            store_sku_id="101",
            db_path=self.db_path,
        )
        xbox_sku = db.upsert_edition_sku(
            edition_id=ed["id"],
            platform_family="Xbox",
            platform_label="Xbox Series X|S",
            storefront="xbox_store",
            store_sku_id="BAD_BIG_ID",
            db_path=self.db_path,
        )
        db.add_wishlist_item(
            title="Test Isolation Game",
            platform_targets=[
                {"platform": "PC", "target_price_usd": 20.00},
                {"platform": "Xbox", "target_price_usd": 20.00},
            ],
            db_path=self.db_path,
        )

        # Run batch sync — must NOT throw exception, should isolate error and update PC SKU
        results = await wishlist_sync.sync_wishlist_prices_for_db(db_path=self.db_path)
        self.assertGreaterEqual(results["updated_skus"], 1)

        # Verify PC SKU price was updated
        with db.get_connection(self.db_path) as conn:
            row = conn.execute("SELECT current_price_usd FROM edition_platform_skus WHERE id = ?", (pc_sku["id"],)).fetchone()
            self.assertEqual(row["current_price_usd"], 14.99)

    def test_title_matching_and_edition_extraction(self):
        # Exact title
        self.assertTrue(wishlist_sync.matches_canonical_title("Hades", "Hades"))
        # Sequel / different games must NOT match
        self.assertFalse(wishlist_sync.matches_canonical_title("Hades", "Hades II"))
        self.assertFalse(wishlist_sync.matches_canonical_title("Forza Horizon 5", "Forza Horizon 4"))
        self.assertFalse(wishlist_sync.matches_canonical_title("Portal", "Portal 2"))
        self.assertFalse(wishlist_sync.matches_canonical_title("Portal", "Portal 2: Standard Edition"))
        self.assertFalse(wishlist_sync.matches_canonical_title("Hollow Knight", "Hollow Knight: Silksong"))

        # Edition variations of the canonical game must match
        self.assertTrue(wishlist_sync.matches_canonical_title("Hades II", "Hades II - Deluxe Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("Forza Horizon 5", "Forza Horizon 5 Premium Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("Cyberpunk 2077", "Cyberpunk 2077: Ultimate Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("The Witcher 3: Wild Hunt", "The Witcher 3: Wild Hunt - Complete Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("Hollow Knight", "Hollow Knight: Voidheart Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("Grand Theft Auto V", "Grand Theft Auto V: Premium Online Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("Dishonored", "Dishonored: Definitive Edition"))
        self.assertTrue(wishlist_sync.matches_canonical_title("DOOM (2016)", "DOOM"))
        self.assertTrue(wishlist_sync.matches_canonical_title("DOOM", "DOOM (2016)"))

        # Edition extraction
        self.assertEqual(wishlist_sync.extract_edition_name("Hades II - Deluxe Edition", canonical_title="Hades II"), "Deluxe Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Forza Horizon 5 Premium Edition", canonical_title="Forza Horizon 5"), "Premium Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Cyberpunk 2077: Ultimate Edition", canonical_title="Cyberpunk 2077"), "Ultimate Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Death Stranding Director's Cut", canonical_title="Death Stranding"), "Director's Cut")
        self.assertEqual(wishlist_sync.extract_edition_name("The Witcher 3 - Complete Edition", canonical_title="The Witcher 3"), "Complete Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Dishonored: Definitive Edition", canonical_title="Dishonored"), "Definitive Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Hollow Knight: Voidheart Edition", canonical_title="Hollow Knight"), "Complete Edition")

        # Edge cases: titles containing words that resemble editions must NOT be treated as special editions
        self.assertEqual(wishlist_sync.extract_edition_name("Golden Axe", canonical_title="Golden Axe"), "Standard Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("GoldenEye 007", canonical_title="GoldenEye 007"), "Standard Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("The Collector", canonical_title="The Collector"), "Standard Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Tomb Raider: Anniversary", canonical_title="Tomb Raider: Anniversary"), "Standard Edition")
        self.assertEqual(wishlist_sync.extract_edition_name("Tomb Raider: Anniversary - Deluxe Edition", canonical_title="Tomb Raider: Anniversary"), "Deluxe Edition")

        # Upgrade packs / soundtrack / DLC add-ons must NOT match as standalone full-game editions
        self.assertFalse(wishlist_sync.matches_canonical_title("CONTROL Resonant", "CONTROL Resonant Digital Deluxe Upgrade"))
        self.assertFalse(wishlist_sync.matches_canonical_title("CONTROL Resonant", "CONTROL Resonant Original Soundtrack"))
        self.assertTrue(wishlist_sync.matches_canonical_title("CONTROL Resonant", "CONTROL Resonant Digital Deluxe Edition"))

    @patch("httpx.AsyncClient.get")
    async def test_gog_currency_symbol_parsing_and_target_platform_expansion(self, mock_get):
        # Regression test: GOG catalog v1 returns "$59.99" with leading '$' and productType='pack' for upgrades;
        # Also tests that enabling PC after initially only watching Xbox discovers PC storefronts on next sync.
        def mock_router(url, *args, **kwargs):
            url_str = str(url)
            resp = MagicMock()
            resp.status_code = 200

            if "displaycatalog.mp.microsoft.com/v7.0/productFamilies/autosuggest" in url_str:
                resp.json.return_value = {
                    "Results": [
                        {
                            "ProductFamilyName": "Games",
                            "Products": [
                                {"Title": "CONTROL Resonant", "ProductId": "9P092299FSGW", "Type": "Game"},
                                {"Title": "CONTROL Resonant Digital Deluxe Upgrade", "ProductId": "9P3T5QQ5SDP0", "Type": "Game"},
                            ],
                        }
                    ]
                }
                return resp

            if "displaycatalog.mp.microsoft.com/v7.0/products" in url_str:
                resp.json.return_value = {
                    "Products": [
                        {
                            "ProductId": "9P092299FSGW",
                            "Properties": {"XboxConsoleGenOptimized": ["XboxSeriesX"]},
                            "DisplaySkuAvailabilities": [
                                {
                                    "Availabilities": [
                                        {"OrderManagementData": {"Price": {"ListPrice": 59.99, "MSRP": 59.99}}}
                                    ]
                                }
                            ],
                        }
                    ]
                }
                return resp

            if "catalog.gog.com/v1/catalog" in url_str:
                resp.json.return_value = {
                    "products": [
                        {
                            "id": "1627372090",
                            "productType": "game",
                            "title": "CONTROL Resonant",
                            "slug": "control_resonant",
                            "price": {
                                "final": "$59.99",
                                "base": "$59.99",
                                "discount": None,
                                "finalMoney": {"amount": "59.99", "currency": "USD"},
                                "baseMoney": {"amount": "59.99", "currency": "USD"},
                            },
                        },
                        {
                            "id": "1846427364",
                            "productType": "pack",
                            "title": "CONTROL Resonant Digital Deluxe Upgrade",
                            "slug": "control_resonant_digital_deluxe_upgrade",
                            "price": {"final": "$10.00", "base": "$10.00", "discount": None},
                        },
                    ]
                }
                return resp

            if "store.steampowered.com/api/storesearch" in url_str:
                resp.json.return_value = {
                    "total": 1,
                    "items": [{"id": 3669870, "name": "CONTROL Resonant"}],
                }
                return resp

            if "store.steampowered.com/api/appdetails" in url_str:
                resp.json.return_value = {
                    "3669870": {
                        "success": True,
                        "data": {
                            "name": "CONTROL Resonant",
                            "price_overview": {"final": 4799, "initial": 5999, "discount_percent": 20},
                        },
                    }
                }
                return resp

            resp.status_code = 404
            return resp

        mock_get.side_effect = mock_router

        # 1. Add item watching ONLY Xbox initially (PC & PlayStation deselected / is_active=False)
        item = db.add_wishlist_item(
            title="CONTROL Resonant",
            platform_targets=[
                {"platform": "PC", "target_price_usd": 19.99, "is_active": False},
                {"platform": "Xbox", "target_price_usd": 35.00, "is_active": True},
                {"platform": "PlayStation", "target_price_usd": 29.99, "is_active": False},
            ],
            db_path=self.db_path,
        )
        await wishlist_sync.discover_skus_for_item(item["id"], db_path=self.db_path)
        item_after_xb = db.get_wishlist_item(item["id"], db_path=self.db_path)
        # Only Xbox SKUs should be discovered (and NOT the $10 upgrade DLC)
        self.assertEqual({s["platform_family"] for s in item_after_xb["skus"]}, {"Xbox"})
        self.assertEqual(len(item_after_xb["skus"]), 1)

        # 2. User enables PC monitoring -> discover_skus_for_item must now discover Steam & GOG SKUs
        db.update_wishlist_platforms(
            wishlist_item_id=item["id"],
            platform_targets=[
                {"platform": "PC", "target_price_usd": 19.99, "is_active": True},
                {"platform": "Xbox", "target_price_usd": 35.00, "is_active": True},
                {"platform": "PlayStation", "target_price_usd": 29.99, "is_active": False},
            ],
            db_path=self.db_path,
        )
        await wishlist_sync.discover_skus_for_item(item["id"], db_path=self.db_path)
        item_after_pc = db.get_wishlist_item(item["id"], db_path=self.db_path)
        storefronts = {s["storefront"] for s in item_after_pc["skus"]}
        self.assertIn("xbox_store", storefronts)
        self.assertIn("steam", storefronts)
        self.assertIn("gog", storefronts)
        self.assertNotIn("psn_store", storefronts)

        gog_sku = [s for s in item_after_pc["skus"] if s["storefront"] == "gog"][0]
        self.assertEqual(gog_sku["current_price_usd"], 59.99)
        self.assertEqual(gog_sku["store_url"], "https://www.gog.com/en/game/control_resonant")

    @patch("httpx.AsyncClient.get")
    async def test_auto_discovery_newly_added_wishlist_item_zero_initial_skus(self, mock_get):
        # Setup mock router for external APIs
        def mock_router(url, *args, **kwargs):
            url_str = str(url)
            params = kwargs.get("params") or {}
            resp = MagicMock()
            resp.status_code = 200

            if "cheapshark.com/api/1.0/games" in url_str:
                if "title" in params or "title=" in url_str:
                    resp.json.return_value = [
                        {"gameID": "214532", "external": "Hades II", "steamAppID": "1145350"},
                        {"gameID": "214533", "external": "Hades II - Deluxe Edition"},
                    ]
                elif params.get("id") == "214532" or "id=214532" in url_str:
                    resp.json.return_value = {
                        "info": {"title": "Hades II"},
                        "cheapestPriceEver": {"price": "19.99"},
                        "deals": [
                            {"storeID": "1", "price": "24.99", "retailPrice": "29.99", "savings": "16.6", "dealID": "deal_cs1"},
                            {"storeID": "25", "price": "29.99", "retailPrice": "29.99", "savings": "0.0", "dealID": "deal_cs2"},
                        ],
                    }
                elif params.get("id") == "214533" or "id=214533" in url_str:
                    resp.json.return_value = {
                        "info": {"title": "Hades II - Deluxe Edition"},
                        "cheapestPriceEver": {"price": "29.99"},
                        "deals": [
                            {"storeID": "1", "price": "34.99", "retailPrice": "39.99", "savings": "12.5", "dealID": "deal_cs3"},
                        ],
                    }
                elif "ids" in params or "ids=" in url_str:
                    resp.json.return_value = {
                        "214532": {
                            "info": {"title": "Hades II"},
                            "cheapestPriceEver": {"price": "19.99"},
                            "deals": [
                                {"storeID": "1", "price": "24.99", "retailPrice": "29.99", "savings": "16.6", "dealID": "deal_cs1"},
                                {"storeID": "25", "price": "29.99", "retailPrice": "29.99", "savings": "0.0", "dealID": "deal_cs2"},
                            ],
                        },
                        "214533": {
                            "info": {"title": "Hades II - Deluxe Edition"},
                            "cheapestPriceEver": {"price": "29.99"},
                            "deals": [
                                {"storeID": "1", "price": "34.99", "retailPrice": "39.99", "savings": "12.5", "dealID": "deal_cs3"},
                            ],
                        },
                    }
                else:
                    resp.json.return_value = {}
                return resp

            if "store.steampowered.com/api/storesearch" in url_str:
                resp.json.return_value = {
                    "total": 1,
                    "items": [{"id": 1145350, "name": "Hades II"}],
                }
                return resp

            if "store.steampowered.com/api/appdetails" in url_str:
                resp.json.return_value = {
                    "1145350": {
                        "success": True,
                        "data": {
                            "name": "Hades II",
                            "price_overview": {"final": 2999, "initial": 2999, "discount_percent": 0},
                            "package_groups": [
                                {
                                    "subs": [
                                        {"packageid": 9001, "option_text": "Hades II Deluxe Edition - $39.99", "price_in_cents_with_discount": 3999, "percent_savings": 0}
                                    ]
                                }
                            ],
                        },
                    }
                }
                return resp

            if "catalog.gog.com/v1/catalog" in url_str:
                resp.json.return_value = {
                    "products": [
                        {
                            "id": "1207658930",
                            "title": "Hades II",
                            "slug": "hades_ii",
                            "price": {"final": "29.99", "base": "29.99", "discount": 0},
                        }
                    ]
                }
                return resp

            if "displaycatalog.mp.microsoft.com/v7.0/productFamilies/autosuggest" in url_str:
                resp.json.return_value = {
                    "Results": [
                        {
                            "ProductFamilyName": "Games",
                            "Products": [
                                {"Title": "Hades II", "ProductId": "9NBLGGH4R6P9", "Type": "Game"}
                            ]
                        }
                    ]
                }
                return resp

            if "displaycatalog.mp.microsoft.com/v7.0/products" in url_str:
                resp.json.return_value = {
                    "Products": [
                        {
                            "ProductId": "9NBLGGH4R6P9",
                            "Properties": {"XboxConsoleGenOptimized": ["XboxSeriesX"]},
                            "DisplaySkuAvailabilities": [
                                {
                                    "Availabilities": [
                                        {"OrderManagementData": {"Price": {"ListPrice": 29.99, "MSRP": 29.99}}}
                                    ]
                                }
                            ],
                        }
                    ]
                }
                return resp

            if "playstation.com" in url_str:
                resp.status_code = 403
                return resp

            resp.status_code = 404
            return resp

        mock_get.side_effect = mock_router

        # Add newly added wishlist item with 0 initial SKUs
        item = db.add_wishlist_item(
            title="Hades II",
            platform_targets=[
                {"platform": "PC", "target_price_usd": 20.00},
                {"platform": "Xbox", "target_price_usd": 20.00},
                {"platform": "PlayStation", "target_price_usd": 20.00},
            ],
            db_path=self.db_path,
        )
        self.assertEqual(len(item["skus"]), 0)

        # Trigger batch sync (which runs discovery for items with 0 SKUs)
        results = await wishlist_sync.sync_wishlist_prices_for_db(db_path=self.db_path)
        self.assertGreater(results["updated_skus"], 0)

        # Re-fetch item from DB and verify discovered editions, SKUs, and prices
        updated_item = db.get_wishlist_item(item["id"], db_path=self.db_path)
        skus = updated_item["skus"]
        self.assertGreater(len(skus), 0)

        # Check editions: Standard Edition and Deluxe Edition must be present
        editions = updated_item["editions"]
        edition_names = {e["edition_name"] for e in editions}
        self.assertIn("Standard Edition", edition_names)
        self.assertIn("Deluxe Edition", edition_names)

        # Check storefront SKUs exist for PC, Xbox, and PlayStation (via resilient fallback)
        platforms = {s["platform_family"] for s in skus}
        self.assertIn("PC", platforms)
        self.assertIn("Xbox", platforms)
        self.assertIn("PlayStation", platforms)

        # Verify no duplicate Steam SKUs exist for Standard Edition
        std_ed = [e for e in editions if e["edition_name"] == "Standard Edition"][0]
        std_steam = [s for s in skus if s["edition_id"] == std_ed["id"] and s["storefront"] == "steam"]
        self.assertEqual(len(std_steam), 1)

        # Verify historical low ATL was recorded from CheapShark ($19.99)
        cs_skus = [s for s in skus if s["storefront"] in ("cheapshark", "steam", "epic")]
        self.assertTrue(any(s.get("historical_low_usd") == 19.99 for s in cs_skus))

        # Verify price_history rows were inserted for the discovered SKUs
        for s in skus:
            history = db.get_sku_price_history(s["id"], db_path=self.db_path)
            if s.get("current_price_usd") is not None:
                self.assertGreaterEqual(len(history), 1)

    @patch("httpx.AsyncClient.get")
    async def test_smart_upgrade_deal_detected_from_discovered_editions(self, mock_get):
        # CheapShark returns Deluxe Edition on sale for $18.99 while user targeted Base at $20.00
        def mock_router(url, *args, **kwargs):
            url_str = str(url)
            params = kwargs.get("params") or {}
            resp = MagicMock()
            resp.status_code = 200

            if "cheapshark.com/api/1.0/games" in url_str:
                if "title" in params or "title=" in url_str:
                    resp.json.return_value = [
                        {"gameID": "500", "external": "Cyberpunk 2077"},
                        {"gameID": "501", "external": "Cyberpunk 2077: Ultimate Edition"},
                    ]
                elif params.get("id") == "500" or "id=500" in url_str:
                    resp.json.return_value = {
                        "info": {"title": "Cyberpunk 2077"},
                        "cheapestPriceEver": {"price": "29.99"},
                        "deals": [
                            {"storeID": "1", "price": "29.99", "retailPrice": "59.99", "savings": "50.0", "dealID": "deal1"}
                        ],
                    }
                elif params.get("id") == "501" or "id=501" in url_str:
                    resp.json.return_value = {
                        "info": {"title": "Cyberpunk 2077: Ultimate Edition"},
                        "cheapestPriceEver": {"price": "18.99"},
                        "deals": [
                            {"storeID": "1", "price": "18.99", "retailPrice": "79.99", "savings": "76.0", "dealID": "deal2"}
                        ],
                    }
                elif "ids" in params or "ids=" in url_str:
                    resp.json.return_value = {
                        "500": {
                            "info": {"title": "Cyberpunk 2077"},
                            "cheapestPriceEver": {"price": "29.99"},
                            "deals": [
                                {"storeID": "1", "price": "29.99", "retailPrice": "59.99", "savings": "50.0", "dealID": "deal1"}
                            ],
                        },
                        "501": {
                            "info": {"title": "Cyberpunk 2077: Ultimate Edition"},
                            "cheapestPriceEver": {"price": "18.99"},
                            "deals": [
                                {"storeID": "1", "price": "18.99", "retailPrice": "79.99", "savings": "76.0", "dealID": "deal2"}
                            ],
                        },
                    }
                return resp
            resp.status_code = 404
            return resp

        mock_get.side_effect = mock_router

        # Add wishlist item with PC target $20.00 and allow_smart_upgrade=True
        item = db.add_wishlist_item(
            title="Cyberpunk 2077",
            platform_targets=[{"platform": "PC", "target_price_usd": 20.00}],
            allow_smart_upgrade=True,
            db_path=self.db_path,
        )

        results = await wishlist_sync.sync_wishlist_prices_for_db(db_path=self.db_path)
        self.assertGreater(results["deals_found"], 0)

        deals = db.evaluate_wishlist_deals(wishlist_item_id=item["id"], db_path=self.db_path)
        smart_deals = [d for d in deals if d.get("is_smart_upgrade")]
        self.assertGreaterEqual(len(smart_deals), 1)
        deal = smart_deals[0]
        self.assertEqual(deal["edition_name"], "Ultimate Edition")
        self.assertEqual(deal["current_price_usd"], 18.99)
        self.assertTrue(deal["is_smart_upgrade"])
        self.assertIn("SMART UPGRADE", deal["deal_headline"])

    @patch("httpx.AsyncClient.get")
    async def test_network_error_resilience_during_discovery(self, mock_get):
        mock_get.side_effect = Exception("Network Connection Refused")

        item = db.add_wishlist_item(
            title="Offline Game Test",
            platform_targets=[
                {"platform": "PC", "target_price_usd": 15.00},
                {"platform": "PlayStation", "target_price_usd": 15.00},
            ],
            db_path=self.db_path,
        )

        # Batch sync must complete without unhandled exceptions
        results = await wishlist_sync.sync_wishlist_prices_for_db(db_path=self.db_path)
        self.assertIsInstance(results, dict)
        self.assertIn("updated_skus", results)

        # Fallback PlayStation SKU should still be recorded for watched platform
        updated_item = db.get_wishlist_item(item["id"], db_path=self.db_path)
        ps_skus = [s for s in updated_item["skus"] if s["platform_family"] == "PlayStation"]
        self.assertGreaterEqual(len(ps_skus), 1)
        self.assertEqual(ps_skus[0]["storefront"], "psn_store")

    @patch("httpx.AsyncClient.get")
    async def test_playstation_resilient_fallback_cross_references_pc_msrp(self, mock_get):
        # PC Steam returns $59.99 while PlayStation GraphQL search blocks or fails
        def mock_router(url, *args, **kwargs):
            url_str = str(url)
            resp = MagicMock()
            if "store.steampowered.com/api/storesearch" in url_str:
                resp.status_code = 200
                resp.json.return_value = {
                    "total": 1,
                    "items": [{"id": 1091500, "name": "Cyberpunk 2077"}],
                }
                return resp
            if "store.steampowered.com/api/appdetails" in url_str:
                resp.status_code = 200
                resp.json.return_value = {
                    "1091500": {
                        "success": True,
                        "data": {
                            "name": "Cyberpunk 2077",
                            "price_overview": {"final": 5999, "initial": 5999, "discount_percent": 0},
                        },
                    }
                }
                return resp
            if "playstation.com" in url_str:
                resp.status_code = 403
                return resp
            resp.status_code = 404
            return resp

        mock_get.side_effect = mock_router

        # User watches ONLY PC and PlayStation (no Xbox)
        item = db.add_wishlist_item(
            title="Cyberpunk 2077",
            platform_targets=[
                {"platform": "PC", "target_price_usd": 30.00},
                {"platform": "PlayStation", "target_price_usd": 30.00},
            ],
            db_path=self.db_path,
        )

        results = await wishlist_sync.sync_wishlist_prices_for_db(db_path=self.db_path)
        self.assertGreater(results["updated_skus"], 0)

        updated_item = db.get_wishlist_item(item["id"], db_path=self.db_path)
        ps_skus = [s for s in updated_item["skus"] if s["platform_family"] == "PlayStation"]
        self.assertGreaterEqual(len(ps_skus), 1)
        ps_sku = ps_skus[0]
        # PlayStation fallback MUST cross-reference PC price ($59.99) instead of remaining None
        self.assertEqual(ps_sku["regular_price_usd"], 59.99)
        self.assertEqual(ps_sku["current_price_usd"], 59.99)

        # Price history row must exist for the cross-referenced price
        history = db.get_sku_price_history(ps_sku["id"], db_path=self.db_path)
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["price_usd"], 59.99)

    @patch("wishlist_sync._make_client")
    async def test_xbox_and_steam_cover_art_backfill_and_discovery(self, mock_client_factory):
        mock_client = AsyncMock()
        mock_client_factory.return_value.__aenter__.return_value = mock_client

        async def mock_router(url, *args, **kwargs):
            resp = MagicMock()
            url_str = str(url)
            params = kwargs.get("params") or {}

            # Xbox autosuggest
            if "displaycatalog.mp.microsoft.com" in url_str and "autosuggest" in url_str:
                resp.status_code = 200
                resp.json.return_value = {
                    "Results": [
                        {"Products": [{"ProductId": "9NN3HCKW5TPC", "Title": "Gears Tactics"}]}
                    ]
                }
                return resp

            # Xbox display catalog product lookup
            if "displaycatalog.mp.microsoft.com" in url_str and "products" in url_str:
                resp.status_code = 200
                resp.json.return_value = {
                    "Products": [
                        {
                            "ProductId": "9NN3HCKW5TPC",
                            "Properties": {"XboxConsoleGenOptimized": ["XboxSeriesX"]},
                            "DisplaySkuAvailabilities": [
                                {
                                    "Availabilities": [
                                        {
                                            "OrderManagementData": {
                                                "Price": {"ListPrice": 39.99, "MSRP": 39.99}
                                            }
                                        }
                                    ]
                                }
                            ],
                            "LocalizedProperties": [
                                {
                                    "Images": [
                                        {
                                            "ImagePurpose": "Poster",
                                            "Uri": "//store-images.s-microsoft.com/image/poster_gt.jpg",
                                        },
                                        {
                                            "ImagePurpose": "BoxArt",
                                            "Uri": "//store-images.s-microsoft.com/image/boxart_gt.jpg",
                                        },
                                    ]
                                }
                            ],
                        }
                    ]
                }
                return resp

            resp.status_code = 404
            return resp

        mock_client.get.side_effect = mock_router

        # Add game with Xbox only, no initial cover_url
        item = db.add_wishlist_item(
            title="Gears Tactics",
            platform_targets=[{"platform": "Xbox", "target_price_usd": 20.00}],
            db_path=self.db_path,
        )
        self.assertIsNone(item["canonical_game"].get("cover_url"))

        # Run discovery
        res = await wishlist_sync.discover_skus_for_item(item["id"], db_path=self.db_path)
        self.assertGreater(res["discovered_skus"], 0)

        # Canonical game must now have Xbox Poster cover art
        refreshed = db.get_wishlist_item(item["id"], db_path=self.db_path)
        canonical = refreshed["canonical_game"]
        self.assertEqual(canonical.get("cover_url"), "https://store-images.s-microsoft.com/image/poster_gt.jpg")


if __name__ == "__main__":
    unittest.main()

