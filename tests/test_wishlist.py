"""Tests for the Multi-Platform Wishlist, Edition Hierarchy, and Price Monitoring schema and DB helpers.

Covers:
- Canonical parent games and edition tier hierarchies (Standard, Deluxe, Ultimate).
- Edition platform SKUs and price history append-only tracking with All-Time Lows (ATL).
- Granular platform target thresholds and library ownership conflict auto-muting.
- Smart Upgrade deal evaluation logic (higher-tier edition <= base target price).
- Purchase flow migration from wishlist to owned library.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import db


class TestWishlistDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "wishlist_test.db")
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_tables_exist(self):
        with db.get_connection(self.db_path) as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        expected = {
            "canonical_games",
            "game_editions",
            "edition_platform_skus",
            "wishlist_items",
            "wishlist_platform_targets",
            "price_history",
            "push_subscriptions",
        }
        for t in expected:
            self.assertIn(t, tables, f"Expected table '{t}' to be created by init_db()")

    def test_canonical_game_and_edition_tier_rankings(self):
        canonical = db.upsert_canonical_game(
            title="Cyberpunk 2077",
            release_year=2020,
            developer="CD Projekt Red",
            publisher="CD Projekt",
            cover_url="https://example.com/cyberpunk.jpg",
            genres=["RPG", "Open World"],
            db_path=self.db_path,
        )
        self.assertIsNotNone(canonical["id"])
        self.assertEqual(canonical["normalized_title"], "cyberpunk 2077")

        # Create Standard, Deluxe, and Ultimate editions
        std = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Standard Edition",
            edition_tier="base",
            is_base_edition=True,
            db_path=self.db_path,
        )
        self.assertEqual(std["tier_rank"], 1)
        self.assertTrue(std["is_base_edition"])

        deluxe = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Deluxe Edition",
            edition_tier="deluxe",
            db_path=self.db_path,
        )
        self.assertEqual(deluxe["tier_rank"], 2)

        ultimate = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Ultimate Edition",
            edition_tier="ultimate",
            db_path=self.db_path,
        )
        self.assertEqual(ultimate["tier_rank"], 4)

        editions = db.list_game_editions(canonical["id"], db_path=self.db_path)
        self.assertEqual(len(editions), 3)

    def test_edition_platform_skus_and_price_history_atl(self):
        canonical = db.upsert_canonical_game(
            title="Elden Ring",
            release_year=2022,
            db_path=self.db_path,
        )
        ed = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Standard Edition",
            edition_tier="base",
            is_base_edition=True,
            db_path=self.db_path,
        )
        sku = db.upsert_edition_sku(
            edition_id=ed["id"],
            platform_family="PC",
            platform_label="PC (Steam)",
            storefront="steam",
            store_sku_id="1245620",
            store_url="https://store.steampowered.com/app/1245620",
            msrp_usd=59.99,
            current_price_usd=59.99,
            db_path=self.db_path,
        )
        self.assertEqual(sku["current_price_usd"], 59.99)
        self.assertEqual(sku["historical_low_usd"], 59.99)

        # Update price to $39.99 -> triggers ATL
        res1 = db.record_sku_price(
            sku_id=sku["id"],
            price_usd=39.99,
            regular_price_usd=59.99,
            source="test",
            db_path=self.db_path,
        )
        self.assertTrue(res1["is_all_time_low"])
        self.assertEqual(res1["historical_low_usd"], 39.99)

        # Price rises to $49.99 -> not an ATL
        res2 = db.record_sku_price(
            sku_id=sku["id"],
            price_usd=49.99,
            regular_price_usd=59.99,
            source="test",
            db_path=self.db_path,
        )
        self.assertFalse(res2["is_all_time_low"])
        self.assertEqual(res2["historical_low_usd"], 39.99)

        # Price drops to $29.99 -> new ATL
        res3 = db.record_sku_price(
            sku_id=sku["id"],
            price_usd=29.99,
            regular_price_usd=59.99,
            source="test",
            db_path=self.db_path,
        )
        self.assertTrue(res3["is_all_time_low"])
        self.assertEqual(res3["historical_low_usd"], 29.99)

        # Verify price history rows
        history = db.get_sku_price_history(sku["id"], db_path=self.db_path)
        self.assertGreaterEqual(len(history), 3)

    def test_library_ownership_conflict_detection_and_muting(self):
        # User already owns Halo Infinite on Xbox with 25.5 hours playtime (synced as Xbox / PC)
        db.add_manual_game(
            title="Halo Infinite",
            platform="Xbox / PC",
            ownership_status="Owned",
            playtime_hours=25.5,
            db_path=self.db_path,
        )

        # Checking platform family normalization for Xbox / PC connector platform
        self.assertEqual(db.normalize_platform_family("Xbox / PC"), "Xbox")

        # Checking conflicts for Halo Infinite
        conflict = db.check_library_ownership_conflicts(
            title="Halo Infinite",
            db_path=self.db_path,
        )
        self.assertTrue(conflict["already_in_library"])
        self.assertIn("Xbox", conflict["conflicted_platforms"])
        self.assertNotIn("PC", conflict["conflicted_platforms"])
        self.assertIn("Owned on Xbox", conflict["conflict_warning"])

        # Add to wishlist without force_track_owned:
        # Xbox target must be auto-muted, PC target must remain active
        item = db.add_wishlist_item(
            title="Halo Infinite",
            platform_targets=[
                {"platform": "PC", "target_price_usd": 19.99},
                {"platform": "Xbox", "target_price_usd": 14.99},
            ],
            force_track_owned=False,
            db_path=self.db_path,
        )
        targets = {t["platform_family"]: t for t in item["targets"]}
        self.assertTrue(targets["PC"]["is_active"])
        self.assertFalse(targets["PC"]["muted_owned_conflict"])

        self.assertFalse(targets["Xbox"]["is_active"])
        self.assertTrue(targets["Xbox"]["muted_owned_conflict"])

        # Now test with force_track_owned=True
        item_forced = db.update_wishlist_platforms(
            wishlist_item_id=item["id"],
            platform_targets=[
                {"platform": "Xbox", "target_price_usd": 14.99, "is_active": True}
            ],
            force_track_owned=True,
            db_path=self.db_path,
        )
        targets_forced = {t["platform_family"]: t for t in item_forced["targets"]}
        self.assertTrue(targets_forced["Xbox"]["is_active"])

    def test_smart_upgrade_deal_evaluation(self):
        # Setup canonical game with Standard and Deluxe editions
        canonical = db.upsert_canonical_game(
            title="Control",
            release_year=2019,
            db_path=self.db_path,
        )
        base_ed = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Standard Edition",
            edition_tier="base",
            tier_rank=1,
            is_base_edition=True,
            db_path=self.db_path,
        )
        deluxe_ed = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Ultimate Edition",
            edition_tier="ultimate",
            tier_rank=4,
            is_base_edition=False,
            db_path=self.db_path,
        )

        base_sku = db.upsert_edition_sku(
            edition_id=base_ed["id"],
            platform_family="PC",
            platform_label="PC (Steam)",
            storefront="steam",
            store_sku_id="870780",
            current_price_usd=29.99,
            db_path=self.db_path,
        )
        deluxe_sku = db.upsert_edition_sku(
            edition_id=deluxe_ed["id"],
            platform_family="PC",
            platform_label="PC (Steam)",
            storefront="steam",
            store_sku_id="870781",
            current_price_usd=19.99,
            db_path=self.db_path,
        )

        item = db.add_wishlist_item(
            title="Control",
            platform_targets=[{"platform": "PC", "target_price_usd": 20.00}],
            allow_smart_upgrade=True,
            db_path=self.db_path,
        )

        deals = db.evaluate_wishlist_deals(db_path=self.db_path)
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertTrue(deal["is_smart_upgrade"])
        self.assertEqual(deal["edition_name"], "Ultimate Edition")
        self.assertEqual(deal["current_price_usd"], 19.99)
        self.assertEqual(deal["target_price_usd"], 20.00)
        self.assertIn("SMART UPGRADE", deal["deal_headline"])

        # If allow_smart_upgrade is False, the higher tier should not trigger
        db.update_wishlist_item(
            item_id=item["id"],
            allow_smart_upgrade=False,
            db_path=self.db_path,
        )
        deals_no_upgrade = db.evaluate_wishlist_deals(db_path=self.db_path)
        self.assertEqual(len(deals_no_upgrade), 0)

    def test_mark_as_purchased_flow(self):
        canonical = db.upsert_canonical_game(
            title="Alan Wake 2",
            release_year=2023,
            developer="Remedy",
            publisher="Epic Games",
            genres=["Survival Horror", "Adventure"],
            cover_url="https://example.com/aw2.jpg",
            db_path=self.db_path,
        )
        deluxe_ed = db.upsert_game_edition(
            canonical_game_id=canonical["id"],
            edition_name="Deluxe Edition",
            edition_tier="deluxe",
            tier_rank=2,
            db_path=self.db_path,
        )
        item = db.add_wishlist_item(
            title="Alan Wake 2",
            platform_targets=[{"platform": "PC", "target_price_usd": 40.00}],
            db_path=self.db_path,
        )

        purchased = db.mark_wishlist_item_purchased(
            wishlist_item_id=item["id"],
            platform="PC",
            edition_name="Deluxe Edition",
            price_paid_usd=34.99,
            completion_status="Plan to Play",
            db_path=self.db_path,
        )
        self.assertEqual(purchased["status"], "purchased")
        self.assertIsNotNone(purchased.get("library_game_id"))

        # Check that it exists in the main `games` table with preserved metadata
        with db.get_connection(self.db_path) as conn:
            game = conn.execute(
                "SELECT * FROM games WHERE id = ?", (purchased["library_game_id"],)
            ).fetchone()
        self.assertIsNotNone(game)
        self.assertEqual(game["title"], "Alan Wake 2")
        self.assertEqual(game["platform"], "PC")
        self.assertEqual(game["completion_status"], "Plan to Play")
        self.assertIn("Purchased Deluxe Edition for $34.99", game["notes"])
        import json
        self.assertEqual(json.loads(game["genres"]), ["Survival Horror", "Adventure"])


    def test_wishlist_accepts_string_platform_targets(self):
        # Test adding wishlist item with list of strings instead of dicts
        item = db.add_wishlist_item(
            title="Dead Cells",
            platform_targets=["PC", "Xbox", "PlayStation"],
            db_path=self.db_path,
        )
        self.assertIsNotNone(item)
        target_fams = {t["platform_family"] for t in item["targets"]}
        self.assertEqual(target_fams, {"PC", "Xbox", "PlayStation"})

        # Test updating with strings
        updated = db.update_wishlist_platforms(
            wishlist_item_id=item["id"],
            platform_targets=["PC"],
            remove_platforms=["Xbox", "PlayStation"],
            db_path=self.db_path,
        )
        active_fams = {t["platform_family"] for t in updated["targets"]}
        self.assertEqual(active_fams, {"PC"})


if __name__ == "__main__":
    unittest.main()
