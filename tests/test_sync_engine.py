"""Unit tests for per-user sync engine credentials, shared catalog_cache enrichment, and per-user locks."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import app as web_app
import context
import db
import sync_engine
import system_db
from sync_engine import (
    UserCredentials,
    enrich_missing_metadata,
    sync_all_platforms,
    sync_game_achievements_on_demand,
    sync_psn,
    sync_steam,
    sync_xbox,
)


class TestUserCredentialsAndSyncs(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "user_games.db")
        self.sys_db_path = str(Path(self.temp_dir.name) / "system.db")
        db.init_db(self.db_path)
        system_db.init_system_db(self.sys_db_path)
        context.current_user_id.set(None)

    def tearDown(self):
        context.current_user_id.set(None)
        self.temp_dir.cleanup()

    def test_user_credentials_dataclass_and_dict_support(self):
        creds = UserCredentials(
            steam_id="76561198000000001",
            steam_api_key="steam_key_1",
            xbl_api_key="xbl_key_1",
            psn_npsso="psn_npsso_1",
        )
        self.assertEqual(creds.steam_id, "76561198000000001")
        self.assertEqual(creds.steam_api_key, "steam_key_1")
        self.assertEqual(creds.xbl_api_key, "xbl_key_1")
        self.assertEqual(creds.psn_npsso, "psn_npsso_1")

    def test_sync_steam_uses_user_credentials_and_falls_back_to_env(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "response": {
                "games": [
                    {"appid": 620, "name": "Portal 2", "playtime_forever": 120, "rtime_last_played": 1700000000}
                ]
            }
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.dict(os.environ, {"STEAM_API_KEY": "server_default_steam_key", "STEAM_ID": ""}, clear=False), patch(
            "sync_engine._make_async_client", return_value=mock_client
        ), patch("sync_engine.sync_steam_achievements_for_game", new_callable=AsyncMock) as mock_ach:
            # User provides steam_id only; steam_api_key falls back to server env
            creds = UserCredentials(steam_id="76561198012345678")
            res = asyncio.run(sync_steam(db_path=self.db_path, credentials=creds))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["games_synced"], 1)

            call_kwargs = mock_client.get.call_args.kwargs
            self.assertEqual(call_kwargs["params"]["key"], "server_default_steam_key")
            self.assertEqual(call_kwargs["params"]["steamid"], "76561198012345678")
            mock_ach.assert_called_once()

    def test_sync_steam_skips_when_user_creds_explicitly_lack_steam_id(self):
        with patch.dict(os.environ, {"STEAM_API_KEY": "server_key", "STEAM_ID": "76561198099999999"}, clear=False):
            # When explicit UserCredentials has empty steam_id, do not sync server owner's Steam library into this user
            creds = UserCredentials(steam_id="", steam_api_key="server_key")
            res = asyncio.run(sync_steam(db_path=self.db_path, credentials=creds))
            self.assertEqual(res["status"], "skipped")

    def test_sync_xbox_uses_user_credentials(self):
        mock_ach_resp = MagicMock()
        mock_ach_resp.status_code = 200
        mock_ach_resp.json.return_value = {
            "xuid": "xuid_123",
            "titles": [
                {
                    "titleId": "999",
                    "name": "Halo Infinite",
                    "type": "Game",
                    "devices": ["XboxSeries"],
                    "achievement": {"currentAchievements": 5, "totalAchievements": 50},
                }
            ],
        }
        mock_titles_resp = MagicMock()
        mock_titles_resp.status_code = 200
        mock_titles_resp.json.return_value = {"xuid": "xuid_123", "titles": []}
        mock_stats_resp = MagicMock()
        mock_stats_resp.status_code = 200
        mock_stats_resp.json.return_value = {"content": {"statlistscollection": []}}

        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_ach_resp, mock_titles_resp]
        mock_client.post.return_value = mock_stats_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.dict(os.environ, {"XBL_API_KEY": ""}, clear=False), patch(
            "sync_engine._make_async_client", return_value=mock_client
        ) as mock_make_client:
            creds = {"xbl_api_key": "user_custom_xbl_key"}
            res = asyncio.run(sync_xbox(db_path=self.db_path, credentials=creds))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["games_synced"], 1)
            extra_headers = mock_make_client.call_args.kwargs.get("extra_headers", {})
            self.assertEqual(extra_headers.get("X-Authorization"), "user_custom_xbl_key")

    def _run_xbox_sync_with_images(self, images):
        """Run a mocked Xbox sync for one title that exposes the given image list."""
        ach_resp = MagicMock()
        ach_resp.status_code = 200
        ach_resp.json.return_value = {
            "xuid": "xuid_123",
            "titles": [
                {
                    "titleId": "555",
                    "name": "Gears 5",
                    "type": "Game",
                    "devices": ["XboxSeries"],
                    "achievement": {"currentAchievements": 1, "totalAchievements": 10},
                    "images": images,
                }
            ],
        }
        titles_resp = MagicMock()
        titles_resp.status_code = 200
        titles_resp.json.return_value = {"xuid": "xuid_123", "titles": []}
        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = {"content": {"statlistscollection": []}}

        client = AsyncMock()
        client.get.side_effect = [ach_resp, titles_resp]
        client.post.return_value = stats_resp
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        with patch.dict(os.environ, {"XBL_API_KEY": ""}, clear=False), patch(
            "sync_engine._make_async_client", return_value=client
        ):
            return asyncio.run(sync_xbox(db_path=self.db_path, credentials={"xbl_api_key": "k"}))

    def test_sync_xbox_does_not_downgrade_poster_to_square_boxart(self):
        # 2026-11-06: a later sync that only exposed square BoxArt overwrote the
        # 2:3 Poster cover, which then rendered cropped in the poster grid.
        poster = "http://store-images.example/poster-2x3"
        boxart = "http://store-images.example/boxart-1x1"
        self._run_xbox_sync_with_images([{"type": "Poster", "url": poster}])
        self.assertEqual(db.get_game_by_id(1, db_path=self.db_path)["cover_url"], poster)

        self._run_xbox_sync_with_images([{"type": "BoxArt", "url": boxart}])
        self.assertEqual(db.get_game_by_id(1, db_path=self.db_path)["cover_url"], poster)

    def test_sync_xbox_uses_boxart_when_no_cover_exists_yet(self):
        boxart = "http://store-images.example/boxart-1x1"
        self._run_xbox_sync_with_images([{"type": "BoxArt", "url": boxart}])
        self.assertEqual(db.get_game_by_id(1, db_path=self.db_path)["cover_url"], boxart)

    def test_sync_psn_uses_user_credentials(self):
        with patch.dict(os.environ, {"PSN_NPSSO": ""}, clear=False), patch(
            "sync_engine._sync_psn_blocking", return_value=3
        ) as mock_psn_block:
            creds = UserCredentials(psn_npsso="user_npsso_token_abc")
            res = asyncio.run(sync_psn(db_path=self.db_path, credentials=creds))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["games_synced"], 3)
            mock_psn_block.assert_called_once_with("user_npsso_token_abc", self.db_path)

    def test_sync_game_achievements_on_demand_accepts_credentials(self):
        g_steam = db.upsert_synced_game(
            external_id="steam:620",
            title="Portal 2",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=10.0,
            db_path=self.db_path,
        )
        g_xbox = db.upsert_synced_game(
            external_id="xbox:111",
            title="Portal 2",
            platform="Xbox",
            source="Xbox",
            playtime_hours=5.0,
            db_path=self.db_path,
        )
        g_psn = db.upsert_synced_game(
            external_id="psn:CUSA001",
            title="Portal 2",
            platform="PlayStation 4",
            source="PSN",
            playtime_hours=2.0,
            db_path=self.db_path,
        )
        merged = db.reconcile_games([g_steam["id"], g_xbox["id"], g_psn["id"]], db_path=self.db_path)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        creds = UserCredentials(
            steam_id="76561198000000099",
            steam_api_key="u_steam_key",
            xbl_api_key="u_xbl_key",
            psn_npsso="u_psn_npsso",
        )

        with patch("sync_engine._make_async_client", return_value=mock_client), patch(
            "sync_engine.sync_steam_achievements_for_game", new_callable=AsyncMock
        ) as m_steam, patch(
            "sync_engine.sync_xbox_achievements_for_game", new_callable=AsyncMock
        ) as m_xbox, patch(
            "sync_engine._sync_psn_achievements_for_single_game"
        ) as m_psn:
            asyncio.run(
                sync_game_achievements_on_demand(
                    merged["id"], db_path=self.db_path, credentials=creds
                )
            )
            m_steam.assert_called_once_with(
                mock_client, "u_steam_key", "76561198000000099", g_steam["id"], 620, db_path=self.db_path
            )
            m_xbox.assert_called_once_with(
                mock_client, "u_xbl_key", g_xbox["id"], "111", platform="Xbox", db_path=self.db_path
            )
            m_psn.assert_called_once_with(
                "u_psn_npsso", g_psn["id"], "psn:CUSA001", "Portal 2", "PlayStation 4", self.db_path
            )


class TestSharedCatalogCacheEnrichment(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user1_db = str(Path(self.temp_dir.name) / "user1.db")
        self.user2_db = str(Path(self.temp_dir.name) / "user2.db")
        self.sys_db = str(Path(self.temp_dir.name) / "system.db")
        db.init_db(self.user1_db)
        db.init_db(self.user2_db)
        system_db.init_system_db(self.sys_db)
        context.current_user_id.set(None)

    def tearDown(self):
        context.current_user_id.set(None)
        self.temp_dir.cleanup()

    def test_enrichment_writes_to_and_reads_from_shared_catalog_cache(self):
        # User 1 has Portal 2 (Steam) with empty genres
        db.upsert_synced_game(
            external_id="steam:620",
            title="Portal 2",
            platform="PC (Steam)",
            source="Steam",
            playtime_hours=15.0,
            cover_url="https://steam/620.jpg",
            db_path=self.user1_db,
        )
        # User 2 has Portal 2 (Xbox) with empty genres, no release_year, no critic_score
        db.upsert_synced_game(
            external_id="xbox:999",
            title="Portal 2 (Xbox 360)",
            platform="Xbox",
            source="Xbox",
            playtime_hours=8.0,
            db_path=self.user2_db,
        )

        mock_details = {
            "genres": ["Action", "Adventure", "Puzzle"],
            "release_year": 2011,
            "critic_score": 95,
        }

        with patch.object(system_db, "DEFAULT_SYSTEM_DB_PATH", self.sys_db), patch(
            "sync_engine.fetch_steam_app_details", new_callable=AsyncMock, return_value=mock_details
        ) as mock_steam_api, patch(
            "sync_engine._find_steam_appid_by_title", new_callable=AsyncMock
        ) as mock_search_api, patch(
            "sync_engine.fetch_wikidata_metadata", new_callable=AsyncMock
        ) as mock_wiki_api:
            # Step 1: Enrich User 1's library -> queries external API once and populates catalog_cache
            res1 = asyncio.run(enrich_missing_metadata(db_path=self.user1_db))
            self.assertEqual(res1["games_enriched"], 1)
            mock_steam_api.assert_called_once()

            # Verify catalog_cache in system.db has the entry
            with system_db.get_system_connection(self.sys_db) as conn:
                cache_row = conn.execute(
                    "SELECT * FROM catalog_cache WHERE normalized_title = 'portal 2'"
                ).fetchone()
                self.assertIsNotNone(cache_row)
                self.assertEqual(cache_row["steam_appid"], "620")
                self.assertEqual(json.loads(cache_row["genres_json"]), ["Action", "Adventure", "Puzzle"])
                self.assertEqual(cache_row["release_year"], 2011)
                self.assertEqual(cache_row["critic_score"], 95)

            # Reset mocks to prove User 2 does NOT query any external APIs
            mock_steam_api.reset_mock()
            mock_search_api.reset_mock()
            mock_wiki_api.reset_mock()

            # Step 2: Enrich User 2's library -> hits shared catalog_cache in system.db!
            res2 = asyncio.run(enrich_missing_metadata(db_path=self.user2_db))
            self.assertEqual(res2["games_enriched"], 1)
            mock_steam_api.assert_not_called()
            mock_search_api.assert_not_called()
            mock_wiki_api.assert_not_called()

            u2_games = db.query_games(db_path=self.user2_db)
            self.assertEqual(len(u2_games), 1)
            self.assertEqual(u2_games[0]["genres"], ["Action", "Adventure", "Puzzle"])
            self.assertEqual(u2_games[0]["release_year"], 2011)
            self.assertEqual(u2_games[0]["critic_score"], 95)


class TestAppPerUserSyncLocksAndSemaphore(unittest.TestCase):
    def setUp(self):
        context.current_user_id.set(None)
        web_app._user_sync_locks.clear()

    def tearDown(self):
        context.current_user_id.set(None)
        web_app._user_sync_locks.clear()

    def test_per_user_locks_and_global_semaphore_configured(self):
        self.assertIsInstance(web_app._user_sync_locks, dict)
        self.assertIsInstance(web_app._sync_semaphore, asyncio.Semaphore)
        self.assertEqual(web_app._sync_semaphore._value, 5)

    def test_concurrent_syncs_for_different_users_do_not_block_each_other(self):
        async def _run():
            started = []
            release_event = asyncio.Event()

            async def _fake_sync(*args, **kwargs):
                uid = context.get_current_user_id()
                started.append(uid)
                await release_event.wait()
                return [{"source": "Steam", "status": "success", "games_synced": 1}]

            with patch.object(web_app, "sync_all_platforms", side_effect=_fake_sync), patch.object(
                web_app, "get_library_stats", return_value={}
            ):
                async def _call_as_user(uid):
                    context.set_current_user_id(str(uid))
                    return await web_app.api_trigger_sync()

                t1 = asyncio.create_task(_call_as_user(1))
                t2 = asyncio.create_task(_call_as_user(2))
                await asyncio.sleep(0.05)

                # Both user 1 and user 2 should be inside sync_all_platforms simultaneously
                self.assertCountEqual(started, ["1", "2"])

                # A second concurrent request from user 1 while user 1 is locked returns "running"
                res_dup = await _call_as_user(1)
                self.assertEqual(res_dup["status"], "running")

                release_event.set()
                r1, r2 = await asyncio.gather(t1, t2)
                self.assertEqual(r1["status"], "completed")
                self.assertEqual(r2["status"], "completed")

        asyncio.run(_run())

    def test_global_semaphore_limits_concurrency_to_5(self):
        async def _run():
            active = 0
            max_active = 0
            all_six_spawned = asyncio.Event()

            async def _slow_sync(*args, **kwargs):
                nonlocal active, max_active
                active += 1
                if active > max_active:
                    max_active = active
                await all_six_spawned.wait()
                await asyncio.sleep(0.01)
                active -= 1
                return []

            with patch.object(web_app, "sync_all_platforms", side_effect=_slow_sync), patch.object(
                web_app, "get_library_stats", return_value={}
            ):
                async def _call_as_user(uid):
                    context.set_current_user_id(str(uid))
                    return await web_app.api_trigger_sync()

                tasks = [asyncio.create_task(_call_as_user(i)) for i in range(1, 8)]
                await asyncio.sleep(0.05)
                # At most 5 users can be executing inside the semaphore at once
                self.assertEqual(active, 5)
                self.assertEqual(max_active, 5)

                all_six_spawned.set()
                results = await asyncio.gather(*tasks)
                self.assertEqual(len(results), 7)
                self.assertLessEqual(max_active, 5)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
