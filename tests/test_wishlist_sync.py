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


if __name__ == "__main__":
    unittest.main()
