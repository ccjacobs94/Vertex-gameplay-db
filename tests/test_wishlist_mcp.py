"""Tests for Wishlist MCP tools and resources.

Validates the 6 tools:
- search_game_candidates
- add_to_wishlist
- update_wishlist_platforms
- update_item_price
- get_wishlist_deals
- mark_as_purchased

And the 2 resources:
- vertex://wishlist/active
- vertex://wishlist/{wishlist_id}/history
"""

import functools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db

try:
    import mcp_server
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


class TestWishlistMCP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "mcp_test.db")
        db.init_db(self.db_path)

        # Patch db calls in mcp_server to use self.db_path
        self._orig_add_wishlist = db.add_wishlist_item
        self._orig_get_wishlist = db.get_wishlist_item
        self._orig_list_wishlist = db.list_wishlist_items
        self._orig_eval_deals = db.evaluate_wishlist_deals
        self._orig_purchased = db.mark_wishlist_item_purchased
        self._orig_update_platforms = db.update_wishlist_platforms
        self._orig_record_price = db.record_sku_price
        self._orig_conflict = db.check_library_ownership_conflicts

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    @patch("mcp_server.search_external_metadata")
    async def test_search_game_candidates_and_conflict(self, mock_ext_search):
        mock_ext_search.return_value = [
            {
                "title": "Starfield",
                "release_year": 2023,
                "cover_url": "https://example.com/starfield.jpg",
                "genres": ["RPG", "Sci-Fi"],
            }
        ]
        # Pre-seed library with owned Starfield on Xbox
        db.add_manual_game(
            title="Starfield",
            platform="Xbox",
            ownership_status="Owned",
            playtime_hours=42.0,
            db_path=self.db_path,
        )

        with patch("mcp_server.get_default_db_path", return_value=self.db_path):
            candidates = await mcp_server.search_game_candidates(query="Starfield")
            self.assertIn("candidates", candidates)
            self.assertGreaterEqual(len(candidates["candidates"]), 1)
            c = candidates["candidates"][0]
            self.assertEqual(c["title"], "Starfield")
            self.assertTrue(c["ownership_conflict"]["already_in_library"])
            self.assertIn("Xbox", c["ownership_conflict"]["conflicted_platforms"])

    async def test_add_to_wishlist_auto_mute_and_force_track(self):
        # Pre-seed library with owned game
        db.add_manual_game(
            title="Forza Motorsport",
            platform="Xbox",
            ownership_status="Owned",
            playtime_hours=10.0,
            db_path=self.db_path,
        )

        with patch("mcp_server.get_default_db_path", return_value=self.db_path):
            # 1. Add without force_track_owned: Xbox target must be auto-muted
            res = await mcp_server.add_to_wishlist(
                title="Forza Motorsport",
                platform_targets=[
                    {"platform": "PC", "target_price_usd": 29.99},
                    {"platform": "Xbox", "target_price_usd": 19.99},
                ],
                force_track_owned=False,
            )
            self.assertIn("wishlist_item", res)
            targets = {t["platform_family"]: t for t in res["wishlist_item"]["targets"]}
            self.assertTrue(targets["PC"]["is_active"])
            self.assertFalse(targets["Xbox"]["is_active"])
            self.assertTrue(targets["Xbox"]["muted_owned_conflict"])

            # 2. Add with force_track_owned=True: leaves Xbox target active
            res2 = await mcp_server.add_to_wishlist(
                title="Forza Motorsport",
                platform_targets=[
                    {"platform": "Xbox", "target_price_usd": 19.99, "is_active": True},
                ],
                force_track_owned=True,
            )
            targets2 = {t["platform_family"]: t for t in res2["wishlist_item"]["targets"]}
            self.assertTrue(targets2["Xbox"]["is_active"])

    async def test_update_item_price_and_smart_upgrade_deals(self):
        with patch("mcp_server.get_default_db_path", return_value=self.db_path):
            # Create wishlist item with PC target $30
            item = await mcp_server.add_to_wishlist(
                title="Dead Space",
                platform_targets=[{"platform": "PC", "target_price_usd": 30.00}],
                allow_smart_upgrade=True,
            )
            w_id = item["wishlist_item"]["id"]

            # Report Deluxe Edition price drop to $25.00
            price_res = mcp_server.update_item_price(
                wishlist_item_id=w_id,
                platform="PC",
                edition_name="Deluxe Edition",
                edition_tier="deluxe",
                price_usd=25.00,
                regular_price_usd=69.99,
                storefront="steam",
            )
            self.assertTrue(price_res["is_all_time_low"])

            # Query deals
            deals = mcp_server.get_wishlist_deals(platform="PC")
            self.assertIn("deals", deals)
            self.assertEqual(len(deals["deals"]), 1)
            deal = deals["deals"][0]
            self.assertTrue(deal["is_smart_upgrade"])
            self.assertEqual(deal["edition_name"], "Deluxe Edition")
            self.assertEqual(deal["current_price_usd"], 25.00)

    async def test_mark_as_purchased(self):
        with patch("mcp_server.get_default_db_path", return_value=self.db_path):
            item = await mcp_server.add_to_wishlist(
                title="Resident Evil 4",
                platform_targets=[{"platform": "PlayStation", "target_price_usd": 29.99}],
            )
            w_id = item["wishlist_item"]["id"]

            res = mcp_server.mark_as_purchased(
                wishlist_item_id=w_id,
                platform="PlayStation 5",
                edition_name="Gold Edition",
                price_paid_usd=29.99,
                completion_status="Plan to Play",
            )
            self.assertEqual(res["status"], "purchased")
            self.assertIsNotNone(res.get("library_game_id"))

            # Check that it exists in games table
            with db.get_connection(self.db_path) as conn:
                game = conn.execute("SELECT * FROM games WHERE id = ?", (res["library_game_id"],)).fetchone()
                self.assertIsNotNone(game)
                self.assertEqual(game["title"], "Resident Evil 4")
                self.assertEqual(game["platform"], "PlayStation 5")

    async def test_mcp_resources(self):
        with patch("mcp_server.get_default_db_path", return_value=self.db_path):
            item = await mcp_server.add_to_wishlist(
                title="Hollow Knight Silksong",
                platform_targets=[{"platform": "PC", "target_price_usd": 19.99}],
            )
            w_id = item["wishlist_item"]["id"]

            # Resource: active wishlist
            active_json = mcp_server.resource_wishlist_active()
            active_data = json.loads(active_json)
            self.assertIsInstance(active_data, list)
            self.assertEqual(len(active_data), 1)
            self.assertEqual(active_data[0]["canonical_game"]["title"], "Hollow Knight Silksong")

            # Resource: item history
            history_json = mcp_server.resource_wishlist_history(str(w_id))
            history_data = json.loads(history_json)
            self.assertEqual(history_data["id"], w_id)
            self.assertIn("editions", history_data)


if __name__ == "__main__":
    unittest.main()
