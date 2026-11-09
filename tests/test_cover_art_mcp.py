"""
Tests for cover art features in MCP tools and enrichment engine:
1. tools/list shows cover_url on add_game_to_library and update_game_progress, with 18 tools total.
2. add_game_to_library stores explicit cover_url, lightly validates http(s).
3. add_game_to_library auto-fills cover art when none provided via external search.
4. add_game_to_library succeeds when external search finds no art (cover_url=None).
5. enrich_library_genres fills cover_url for games without one, reporting covers_filled and covers_not_found.
6. Existing cover_url is preserved byte-for-byte across adds, updates without cover_url, and enrich runs (including force=True).
7. search_game_candidates returns candidates with cover_url.
8. search_external_metadata does not crash with NameError when RAWG or external provider fails.
"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import db
import sync_engine

try:
    import mcp_server
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


class TestCoverArtMCP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_cover.db")
        db.init_db(self.db_path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_tool_list_and_signatures(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        tools = await mcp_server.mcp.list_tools()
        self.assertEqual(len(tools), 19)
        tool_map = {t.name: t for t in tools}
        self.assertIn("add_game_to_library", tool_map)
        self.assertIn("update_game_progress", tool_map)
        self.assertIn("get_session_history", tool_map)

        add_props = tool_map["add_game_to_library"].input_schema.get("properties", {})
        self.assertIn("cover_url", add_props)

        update_props = tool_map["update_game_progress"].input_schema.get("properties", {})
        self.assertIn("cover_url", update_props)

    async def test_add_game_with_explicit_valid_cover_url(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        with patch("mcp_server.get_default_db_path", return_value=self.db_path), \
             patch("db.DEFAULT_DB_PATH", self.db_path):
            res = await mcp_server.add_game_to_library(
                title="Hades",
                platform="Nintendo Switch",
                cover_url="https://example.com/hades_cover.jpg",
            )
            self.assertEqual(res["status"], "added")
            self.assertEqual(res["game"]["cover_url"], "https://example.com/hades_cover.jpg")

            # Verify in DB
            games = db.query_games(search="Hades", db_path=self.db_path)
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0]["cover_url"], "https://example.com/hades_cover.jpg")

    async def test_add_game_with_invalid_cover_url_treated_as_none_and_not_failed(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        with patch("mcp_server.get_default_db_path", return_value=self.db_path), \
             patch("db.DEFAULT_DB_PATH", self.db_path), \
             patch("mcp_server.search_external_metadata", new_callable=AsyncMock, return_value=[]):
            res = await mcp_server.add_game_to_library(
                title="Indie Game",
                platform="Nintendo Switch",
                cover_url="ftp://invalid-url.com/art.png",
            )
            self.assertEqual(res["status"], "added")
            self.assertIsNone(res["game"]["cover_url"])

    async def test_add_game_autofill_cover_art_chorus(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        mock_candidates = [
            {
                "title": "Dead by Daylight: Chorus of Sin",
                "cover_url": "https://steam/apps/5060920/library_600x900.jpg",
                "provider": "Steam Catalog",
            },
            {
                "title": "Chorus",
                "cover_url": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1153640/library_600x900.jpg",
                "provider": "Steam Catalog",
            },
        ]
        with patch("mcp_server.get_default_db_path", return_value=self.db_path), \
             patch("db.DEFAULT_DB_PATH", self.db_path), \
             patch("mcp_server.search_external_metadata", new_callable=AsyncMock, return_value=mock_candidates) as mock_search:
            res = await mcp_server.add_game_to_library(
                title="Chorus",
                platform="Xbox",
            )
            mock_search.assert_called_once_with("Chorus")
            self.assertEqual(res["status"], "added")
            self.assertEqual(
                res["game"]["cover_url"],
                "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1153640/library_600x900.jpg",
            )

    async def test_add_game_autofill_prefers_steam_vertical_when_both_steam_and_rawg_match(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        mock_candidates = [
            {
                "title": "Celeste",
                "cover_url": "https://media.rawg.io/media/games/celeste.jpg",
                "provider": "RAWG",
            },
            {
                "title": "Celeste",
                "cover_url": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/504230/library_600x900.jpg",
                "provider": "Steam Catalog",
            },
        ]
        with patch("mcp_server.get_default_db_path", return_value=self.db_path), \
             patch("db.DEFAULT_DB_PATH", self.db_path), \
             patch("mcp_server.search_external_metadata", new_callable=AsyncMock, return_value=mock_candidates):
            res = await mcp_server.add_game_to_library(
                title="Celeste",
                platform="Nintendo Switch",
            )
            self.assertEqual(res["status"], "added")
            # Prefers Steam vertical capsule
            self.assertEqual(
                res["game"]["cover_url"],
                "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/504230/library_600x900.jpg",
            )

    async def test_add_game_no_external_match_succeeds_with_null_cover(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        with patch("mcp_server.get_default_db_path", return_value=self.db_path), \
             patch("db.DEFAULT_DB_PATH", self.db_path), \
             patch("mcp_server.search_external_metadata", new_callable=AsyncMock, return_value=[]):
            res = await mcp_server.add_game_to_library(
                title="Obscure Homebrew Game XYZ",
                platform="Nintendo Switch",
            )
            self.assertEqual(res["status"], "added")
            self.assertIsNone(res["game"]["cover_url"])

    async def test_update_game_progress_explicit_cover_url_and_preserve(self):
        if not HAS_MCP:
            self.skipTest("mcp library not available")
        with patch("mcp_server.get_default_db_path", return_value=self.db_path), \
             patch("db.DEFAULT_DB_PATH", self.db_path):
            # Seed game with existing cover
            game = db.add_manual_game(
                title="Metroid Dread",
                platform="Nintendo Switch",
                cover_url="https://example.com/metroid_orig.jpg",
                db_path=self.db_path,
            )
            self.assertEqual(game["cover_url"], "https://example.com/metroid_orig.jpg")

            # 1. Update without cover_url parameter: preserves existing cover_url byte-for-byte
            res1 = mcp_server.update_game_progress(
                title="Metroid Dread",
                user_rating=9,
            )
            self.assertEqual(res1["game"]["cover_url"], "https://example.com/metroid_orig.jpg")
            self.assertEqual(res1["game"]["user_rating"], 9)

            # 2. Update with invalid cover_url: preserves existing cover_url
            res2 = mcp_server.update_game_progress(
                title="Metroid Dread",
                cover_url="invalid://not-a-url",
            )
            self.assertEqual(res2["game"]["cover_url"], "https://example.com/metroid_orig.jpg")

            # 3. Explicit repair with valid cover_url: replaces it
            res3 = mcp_server.update_game_progress(
                title="Metroid Dread",
                cover_url="https://example.com/metroid_fixed.jpg",
            )
            self.assertEqual(res3["game"]["cover_url"], "https://example.com/metroid_fixed.jpg")

    async def test_enrich_missing_metadata_cover_backfill_and_idempotence(self):
        # Seed 3 games:
        # Game 1: has cover_url -> must not be modified even with force=True
        # Game 2: null cover_url -> found by search -> filled
        # Game 3: empty cover_url -> not found by search -> not filled
        db.add_manual_game(
            title="Firewatch",
            platform="PC (Xbox)",
            cover_url=None,
            db_path=self.db_path,
        )
        db.add_manual_game(
            title="Unknown Title 404",
            platform="PC (Xbox)",
            cover_url="",
            db_path=self.db_path,
        )
        db.add_manual_game(
            title="Hollow Knight",
            platform="Nintendo Switch",
            cover_url="https://example.com/hollow_knight_preserved.jpg",
            db_path=self.db_path,
        )

        async def fake_search(title):
            if "Firewatch" in title:
                return [
                    {
                        "title": "Firewatch",
                        "cover_url": "https://steam/apps/383870/library_600x900.jpg",
                        "provider": "Steam Catalog",
                    }
                ]
            return []

        with patch("sync_engine.search_external_metadata", side_effect=fake_search):
            # First pass: fills Firewatch, Unknown Title 404 not found
            res1 = await sync_engine.enrich_missing_metadata(db_path=self.db_path)
            self.assertIn("covers_filled", res1)
            self.assertIn("covers_not_found", res1)
            self.assertEqual(res1["covers_filled"], 1)
            self.assertEqual(res1["covers_not_found"], 1)

            # Check DB state
            g_fw = db.query_games(search="Firewatch", db_path=self.db_path)[0]
            self.assertEqual(g_fw["cover_url"], "https://steam/apps/383870/library_600x900.jpg")

            g_hk = db.query_games(search="Hollow Knight", db_path=self.db_path)[0]
            self.assertEqual(g_hk["cover_url"], "https://example.com/hollow_knight_preserved.jpg")

            # Second pass: already filled, covers_filled must be 0
            res2 = await sync_engine.enrich_missing_metadata(db_path=self.db_path)
            self.assertEqual(res2["covers_filled"], 0)

            # Force run: existing covers remain byte-for-byte untouched
            res_force = await sync_engine.enrich_missing_metadata(db_path=self.db_path, force=True)
            g_hk_after = db.query_games(search="Hollow Knight", db_path=self.db_path)[0]
            self.assertEqual(g_hk_after["cover_url"], "https://example.com/hollow_knight_preserved.jpg")

    async def test_search_external_metadata_error_handling_no_nameerror(self):
        """Regression test: RAWG API error/timeout must not fail with NameError: name 'db' is not defined."""
        with patch.dict("os.environ", {"RAWG_API_KEY": "fake_key"}), \
             patch("sync_engine._make_async_client") as mock_client_factory:
            mock_client = AsyncMock()
            mock_client.get.side_effect = TimeoutError("RAWG timed out")
            mock_client_factory.return_value.__aenter__.return_value = mock_client

            # Should catch exception and not raise NameError
            res = await sync_engine.search_external_metadata("Test Query")
            self.assertIsInstance(res, list)


if __name__ == "__main__":
    unittest.main()
