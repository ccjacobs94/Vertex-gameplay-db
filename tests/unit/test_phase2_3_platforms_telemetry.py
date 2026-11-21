"""Unit tests for Phase 2 & 3 platform adapters, telemetry, achievement backfill, and HA history."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR, REPO_ROOT
from vertex.activity.service import ActivityService
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.homeassistant.service import HomeAssistantService
from vertex.integrations.platforms.base import PlatformAdapterError
from vertex.integrations.platforms.gog import GOGAdapter, extract_gog_auth_code
from vertex.integrations.platforms.psn import PSNAdapter
from vertex.integrations.platforms.telemetry import (
    TelemetryService,
    XboxRtaClient,
    extract_active_xbox_title,
    is_system_app,
)
from vertex.integrations.platforms.xbox import XboxAdapter
from vertex.library.service import LibraryService


class TestDomainBoundaryImports(unittest.TestCase):
    def test_domain_packages_do_not_import_web_or_network_libraries(self):
        forbidden = {"fastapi", "httpx", "psnawp_api", "websockets"}
        domain_pkgs = (
            "identity",
            "catalog",
            "library",
            "activity",
            "analytics",
            "wishlist",
            "migration",
            "infrastructure",
        )
        violations: list[str] = []
        for pkg in domain_pkgs:
            pkg_dir = REPO_ROOT / "src" / "vertex" / pkg
            for py_file in sorted(pkg_dir.rglob("*.py")):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            top = alias.name.split(".")[0]
                            if top in forbidden:
                                violations.append(f"{py_file}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        top = node.module.split(".")[0]
                        if top in forbidden:
                            violations.append(f"{py_file}: from {node.module}")
        self.assertEqual(violations, [])


class TestXboxAdapterUnit(unittest.IsolatedAsyncioTestCase):
    async def test_xbox_library_stats_achievements_and_redaction(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.url.params.get("gt") == "bad_secret":
                return httpx.Response(401, text="Unauthorized key=SECRET999")
            if "/v2/friends/search" in path:
                return httpx.Response(
                    200,
                    json={"people": [{"xuid": "2533274000001", "gamertag": "Chief94", "gamerScore": "42000"}]},
                )
            if "/v2/achievements/player/2533274000001/1001" in path:
                return httpx.Response(
                    200,
                    json={
                        "achievements": [
                            {
                                "id": "ach_1",
                                "name": "Ring Bearer",
                                "description": "Land on Halo",
                                "progressState": "Achieved",
                                "progression": {"timeUnlocked": "2026-10-10T15:00:00Z"},
                                "rewards": [{"type": "Gamerscore", "value": "25"}],
                                "rarity": {"currentPercentage": "44.5"},
                                "mediaAssets": [{"type": "Icon", "url": "https://img/ach1.png"}],
                            },
                            {
                                "id": "ach_2",
                                "name": "Hidden Skull",
                                "description": "Find secret skull",
                                "progressState": "NotStarted",
                                "isSecret": True,
                            },
                        ]
                    },
                )
            if "/v2/achievements/player/2533274000001" in path:
                return httpx.Response(
                    200,
                    json={
                        "titles": [
                            {
                                "titleId": "1001",
                                "name": "Halo Infinite",
                                "devices": ["XboxSeries", "PC"],
                                "titleHistory": {"lastTimePlayed": "2026-10-10T16:00:00Z"},
                                "achievement": {"currentAchievements": 12, "totalAchievements": 100},
                                "detail": {"releaseDate": "2021-12-08T00:00:00Z", "genres": ["Shooter"]},
                                "gamePass": {"isGamePass": True},
                                "images": [
                                    {"type": "BoxArt", "url": "https://img/box.jpg"},
                                    {"type": "Poster", "url": "https://img/poster.jpg"},
                                ],
                            }
                        ]
                    },
                )
            if path.endswith("/v2/player/stats"):
                return httpx.Response(
                    200,
                    json={
                        "statlistscollection": [
                            {"stats": [{"titleid": "1001", "name": "MinutesPlayed", "value": "320"}]}
                        ]
                    },
                )
            return httpx.Response(404)

        adapter = XboxAdapter()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            snapshots, xuid = await adapter.fetch_library_with_xuid(
                api_key="xbl-secret-token",
                xbox_gamertag="Chief94",
                client=client,
            )
            self.assertEqual(xuid, "2533274000001")
            self.assertEqual(len(snapshots), 1)
            snap = snapshots[0]
            self.assertEqual(snap.external_id, "xbox:1001")
            self.assertEqual(snap.platform, "Xbox / PC")
            self.assertTrue(snap.is_play_anywhere)
            self.assertTrue(snap.is_game_pass)
            self.assertEqual(snap.playtime_minutes, 320)
            self.assertEqual(snap.cover_url, "https://img/poster.jpg")
            self.assertTrue(snap.cover_is_poster)
            self.assertEqual(snap.achievements_unlocked, 12)

            achs = await adapter.fetch_achievements(
                api_key="xbl-secret-token",
                title_id="1001",
                xuid=xuid,
                client=client,
            )
            self.assertEqual(len(achs), 2)
            self.assertTrue(achs[0].unlocked)
            self.assertEqual(achs[0].reward_value, "25")
            self.assertEqual(achs[0].rarity_pct, 44.5)
            self.assertTrue(achs[1].is_hidden)

            with self.assertRaises(PlatformAdapterError) as err_ctx:
                await adapter.resolve_gamertag(
                    gamertag="bad_secret",
                    api_key="xbl-secret-token",
                    client=client,
                )
            self.assertFalse(err_ctx.exception.retryable)
            self.assertEqual(err_ctx.exception.code, "auth_failed")
            self.assertNotIn("SECRET999", err_ctx.exception.safe_message)

    def test_extract_active_xbox_title_filters_system_apps(self):
        self.assertTrue(is_system_app("750323071", "Xbox Home"))
        self.assertTrue(is_system_app("999", "Netflix"))
        payload = {
            "content": [
                {
                    "state": "Online",
                    "devices": [
                        {
                            "titles": [
                                {"id": "750323071", "name": "Xbox Home", "state": "Active"},
                                {
                                    "id": "1001",
                                    "name": "Halo Infinite",
                                    "state": "Active",
                                    "activity": {"richPresence": "Campaign - Zeta Halo"},
                                },
                            ]
                        }
                    ],
                }
            ]
        }
        active = extract_active_xbox_title(payload)
        self.assertIsNotNone(active)
        self.assertEqual(active["title_id"], "1001")
        self.assertEqual(active["title"], "Halo Infinite")
        self.assertEqual(active["rich_presence"], "Campaign - Zeta Halo")

    # Regression (2026-11-19): Xbox presence "Online" non-game ghost entries
    async def test_is_system_app_extract_active_and_library_sync_filter_non_games(self):
        for tid, name in [
            ("704208617", "Online"),
            ("1022622766", "Online"),
            ("999999", "Online"),
            ("999999", "Offline"),
            ("999999", "Away"),
            ("999999", "Busy"),
            ("999999", ""),
            ("999999", "   "),
            ("1626579248", ""),
            ("999999", "Xbox Title 1626579248"),
            ("994947109", "Xbox Assist"),
            ("999999", "YouTube™"),
            ("999999", "Discord"),
        ]:
            with self.subTest(expected=True, tid=tid, name=name):
                self.assertTrue(is_system_app(tid, name))

        for tid, name in [
            ("1001", None),
            ("1001", "Grand Theft Auto Online"),
            ("1002", "Online Soccer Manager"),
            ("1003", "Homefront"),
            ("1004", "Homeworld"),
            ("1005", "Edge of Eternity"),
            ("2082978535", "Halo: Campaign Evolved"),
        ]:
            with self.subTest(expected=False, tid=tid, name=name):
                self.assertFalse(is_system_app(tid, name))

        # Generic "Online", blank title, "Xbox Assist", and Background placement must return None
        for bad_payload in [
            {"state": "Online", "devices": [{"titles": [{"id": "704208617", "name": "Online", "state": "Active"}]}]},
            {"state": "Online", "devices": [{"titles": [{"id": "1626579248", "name": "", "state": "Active"}]}]},
            {"state": "Online", "devices": [{"titles": [{"id": "994947109", "name": "Xbox Assist", "state": "Active"}]}]},
            {
                "state": "Online",
                "devices": [{"titles": [{"id": "1001", "name": "Halo Infinite", "state": "Active", "placement": "Background"}]}],
            },
            {"state": "Online", "titleId": "704208617", "titleName": "Online"},
            {"state": "Online", "titleId": "1626579248", "titleName": ""},
        ]:
            self.assertIsNone(extract_active_xbox_title(bad_payload))

        # Ignore "Online" / Background Home and pick real Full game
        mixed_payload = {
            "state": "Online",
            "devices": [
                {
                    "type": "WindowsOneCore",
                    "titles": [{"id": "704208617", "name": "Online", "state": "Active", "placement": "Full"}],
                },
                {
                    "type": "Scarlett",
                    "titles": [
                        {"id": "750323071", "name": "Home", "state": "Active", "placement": "Background"},
                        {"id": "2082978535", "name": "Halo: Campaign Evolved", "state": "Active", "placement": "Full"},
                    ],
                },
            ],
        }
        picked = extract_active_xbox_title(mixed_payload)
        self.assertIsNotNone(picked)
        self.assertEqual(picked["title_id"], "2082978535")
        self.assertEqual(picked["title"], "Halo: Campaign Evolved")

        # Prefer placement == "Full" over unspecified placement
        prefer_full_payload = {
            "state": "Online",
            "devices": [
                {
                    "type": "PC",
                    "titles": [{"id": "1003", "name": "Homefront", "state": "Active"}],
                },
                {
                    "type": "Scarlett",
                    "titles": [{"id": "2082978535", "name": "Halo: Campaign Evolved", "state": "Active", "placement": "Full"}],
                },
            ],
        }
        picked_full = extract_active_xbox_title(prefer_full_payload)
        self.assertIsNotNone(picked_full)
        self.assertEqual(picked_full["title_id"], "2082978535")

        # XboxAdapter.fetch_library_with_xuid skips non-game entries even when type is missing or "Game"
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "/v2/achievements/player/2533274000001" in path:
                return httpx.Response(
                    200,
                    json={
                        "titles": [
                            {"titleId": "704208617", "name": "Online", "type": "Game", "devices": ["XboxSeries"]},
                            {"titleId": "994947109", "name": "Xbox Assist", "devices": ["XboxSeries"]},
                            {"titleId": "2082978535", "name": "Halo: Campaign Evolved", "type": "Game", "devices": ["XboxSeries"]},
                        ]
                    },
                )
            if path.endswith("/v2/player/2533274000001/titles"):
                return httpx.Response(200, json={"titles": []})
            if path.endswith("/v2/player/stats"):
                return httpx.Response(200, json={"statlistscollection": []})
            return httpx.Response(404)

        adapter = XboxAdapter()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            snapshots, _ = await adapter.fetch_library_with_xuid(
                api_key="xbl-secret-token",
                xbox_xuid="2533274000001",
                client=client,
            )
            self.assertEqual([s.external_id for s in snapshots], ["xbox:2082978535"])


class TestPSNAdapterUnit(unittest.IsolatedAsyncioTestCase):
    async def test_psn_library_and_trophies(self):
        class FakeUser:
            online_id = "AstroPlayer"
            account_id = "acc-99"

            def title_stats(self):
                return [
                    SimpleNamespace(
                        title_id="CUSA12345",
                        name="Bloodborne",
                        category=SimpleNamespace(value="ps4_game"),
                        play_duration=timedelta(hours=12, minutes=30),
                        last_played_date_time=datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc),
                        image_url="https://psn/bb.png",
                    )
                ]

            def trophy_titles(self):
                return [
                    SimpleNamespace(
                        np_communication_id="NPWR999",
                        title_name="Bloodborne",
                        title_platform={"PS4"},
                        earned_trophies=SimpleNamespace(bronze=5, silver=2, gold=1, platinum=0),
                        defined_trophies=SimpleNamespace(bronze=20, silver=8, gold=5, platinum=1),
                    )
                ]

            def trophies(self, np_comm_id: str, target_plat: str, include_progress: bool = True):
                assert np_comm_id == "NPWR999"
                assert include_progress
                return [
                    SimpleNamespace(
                        trophy_id=1,
                        trophy_name="Cleric Beast",
                        trophy_detail="Defeat Cleric Beast",
                        trophy_icon_url="https://psn/t1.png",
                        earned=True,
                        earned_date_time=datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc),
                        trophy_type=SimpleNamespace(value="bronze"),
                        trophy_earn_rate="48.2",
                        trophy_hidden=False,
                    )
                ]

        adapter = PSNAdapter(client_factory=lambda npsso: FakeUser())
        hex_npsso = "a1b2c3d4" * 8
        snaps, trophy_map = await adapter.fetch_library_with_trophies(npsso=hex_npsso)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0].external_id, "psn:CUSA12345")
        self.assertEqual(snaps[0].platform, "PlayStation 4")
        self.assertEqual(snaps[0].playtime_minutes, 750)
        self.assertEqual(snaps[0].achievements_unlocked, 8)
        self.assertEqual(snaps[0].achievements_total, 34)
        self.assertIn("psn:CUSA12345", trophy_map)
        self.assertEqual(trophy_map["psn:CUSA12345"][0].reward_value, "bronze")

        verified = await adapter.verify_connection(credential=hex_npsso)
        self.assertEqual(verified["status"], "ok")
        self.assertEqual(verified["online_id"], "AstroPlayer")


class TestGOGAdapterUnit(unittest.IsolatedAsyncioTestCase):
    async def test_gog_oauth_refresh_library_achievements_presence(self):
        state = {"expired_once": True}

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            auth = request.headers.get("Authorization", "")
            if host == "auth.gog.com" and path == "/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "gog-access-fresh",
                        "refresh_token": "gog-refresh-2",
                        "user_id": "555001",
                    },
                )
            if host == "embed.gog.com" and path == "/userData.json":
                return httpx.Response(200, json={"userId": "555001", "username": "GeraltGOG"})
            if host == "embed.gog.com" and path == "/account/getFilteredProducts":
                if auth == "Bearer gog-access-old" and state["expired_once"]:
                    state["expired_once"] = False
                    return httpx.Response(401, json={"error": "expired"})
                return httpx.Response(
                    200,
                    json={
                        "totalPages": 1,
                        "products": [
                            {
                                "id": 1207658924,
                                "title": "The Witcher 3: Wild Hunt",
                                "image": "//images.gog.com/w3",
                                "category": "RPG",
                                "releaseDate": 1431993600,
                                "rating": 48,
                            },
                            {
                                "id": 1441974651,
                                "title": "Cyberpunk 2077",
                                "image": "//images.gog.com/cp77",
                                "category": "RPG",
                            },
                        ],
                    },
                )
            if host == "embed.gog.com" and path == "/user/data/games":
                return httpx.Response(200, json={"owned": [1207658924]})
            if host == "www.gog.com" and "/games/stats" in path:
                return httpx.Response(
                    200,
                    json={
                        "_embedded": {
                            "items": [
                                {
                                    "game": {
                                        "id": "1207658924",
                                        "title": "The Witcher 3: Wild Hunt",
                                        "achievementSupport": True,
                                    },
                                    "stats": {
                                        "555001": {
                                            "playtime": 420,
                                            "achievementsPercentage": 50,
                                            "lastSession": "2026-10-12T18:00:00Z",
                                        }
                                    },
                                }
                            ]
                        }
                    },
                )
            if host == "gameplay.gog.com" and "/games/1441974651/" in path and "/sessions" in path:
                return httpx.Response(
                    200,
                    json={"time_sum": 95, "last_session_date": "2026-10-12T20:15:00Z"},
                )
            if host == "gameplay.gog.com" and "/achievements" in path:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "achievement_id": "w3_lilac",
                                "name": "Lilac and Gooseberries",
                                "description": "Find Yennefer",
                                "date_unlocked": "2026-10-12T19:00:00Z",
                                "rarity": 71.5,
                                "rarity_level_description": "Common",
                                "rarity_level_slug": "common",
                                "visible": True,
                            }
                        ]
                    },
                )
            if host == "presence.gog.com" and path == "/statuses":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "user_id": "555001",
                                "presence": "online",
                                "status": "playing",
                                "game_id": "1207658924",
                                "game_title": "The Witcher 3: Wild Hunt",
                                "rich_presence": "Velen - Exploring",
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        self.assertEqual(
            extract_gog_auth_code("https://embed.gog.com/on_login_success?origin=client&code=AUTH123"),
            "AUTH123",
        )
        adapter = GOGAdapter()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await adapter.fetch_library_detailed(
                access_token="gog-access-old",
                refresh_token="gog-refresh-1",
                client=client,
            )
            self.assertIsNotNone(res.tokens)
            self.assertEqual(res.tokens.access_token, "gog-access-fresh")
            self.assertEqual(res.tokens.username, "GeraltGOG")
            self.assertEqual(len(res.snapshots), 2)
            by_ext = {s.external_id: s for s in res.snapshots}
            snap = by_ext["gog:1207658924"]
            self.assertEqual(snap.platform, "PC (GOG)")
            self.assertEqual(snap.source, "GOG")
            self.assertEqual(snap.playtime_minutes, 420)
            self.assertIn("RPG", snap.genres)
            self.assertEqual(by_ext["gog:1441974651"].playtime_minutes, 95)

            achs, _ = await adapter.fetch_achievements(
                access_token=res.tokens.access_token,
                refresh_token=res.tokens.refresh_token,
                user_id=res.tokens.user_id,
                product_id="1207658924",
                client=client,
            )
            self.assertEqual(len(achs), 1)
            self.assertTrue(achs[0].unlocked)
            self.assertEqual(achs[0].rarity_pct, 71.5)

            pres, _ = await adapter.fetch_presence(
                access_token=res.tokens.access_token,
                refresh_token=res.tokens.refresh_token,
                user_id=res.tokens.user_id,
                client=client,
            )
            self.assertIsNotNone(pres)
            self.assertEqual(pres.game_id, "1207658924")
            self.assertEqual(pres.rich_presence, "Velen - Exploring")


class TestTelemetryBackfillAndHAUnit(unittest.IsolatedAsyncioTestCase):
    def _make_services(self, tmp_path: Path):
        cfg = Settings(
            data_dir=tmp_path,
            migrations_dir=MIGRATIONS_DIR,
            frontend_dist_dir=tmp_path / "dist",
            secret_key="telemetry-unit-secret-key-9876",
            worker_enabled=False,
            telemetry_flush_delay_seconds=0.0,
        )
        mgr = DatabaseManager(cfg)
        mgr.ensure_system_ready()
        ident = IdentityService(mgr, SecretBox(cfg.secret_key), JobQueue(mgr))
        acct, _ = ident.bootstrap_admin(username="player1", password="PlayerPassword!1")
        scope = acct.to_scope()
        lib = LibraryService(mgr)
        act = ActivityService(mgr)
        return cfg, mgr, ident, scope, lib, act

    async def test_xbox_and_gog_telemetry_anti_double_counting_and_rta(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, mgr, ident, scope, lib, act = self._make_services(Path(tmp))
            ident.update_connections(
                scope,
                xbox_gamertag="Chief94",
                xbox_xuid="2533274000001",
                xbl_api_key="xbl-key-1",
                gog_user_id="555001",
                gog_username="GeraltGOG",
                gog_access_token="gog-tok-1",
                gog_refresh_token="gog-ref-1",
            )

            mode = {
                "xbox_active": True,
                "xbox_minutes": 100,
                "gog_active": True,
                "gog_minutes": 200,
            }

            def handler(request: httpx.Request) -> httpx.Response:
                host = request.url.host
                path = request.url.path
                if "xbl.io" in host:
                    if "/v2/presence" in path:
                        if mode["xbox_active"]:
                            return httpx.Response(
                                200,
                                json={
                                    "state": "Online",
                                    "devices": [
                                        {
                                            "titles": [
                                                {"id": "1001", "name": "Halo Infinite", "state": "Active"}
                                            ]
                                        }
                                    ],
                                },
                            )
                        return httpx.Response(200, json={"state": "Offline"})
                    if path.endswith("/v2/player/stats"):
                        return httpx.Response(
                            200,
                            json={
                                "statlistscollection": [
                                    {
                                        "stats": [
                                            {
                                                "titleid": "1001",
                                                "name": "MinutesPlayed",
                                                "value": str(mode["xbox_minutes"]),
                                            }
                                        ]
                                    }
                                ]
                            },
                        )
                    if "/v2/achievements/player/2533274000001/1001" in path:
                        return httpx.Response(
                            200,
                            json={
                                "achievements": [
                                    {
                                        "id": "a1",
                                        "name": "Win Match",
                                        "progressState": "Achieved",
                                        "progression": {"timeUnlocked": "2026-10-15T18:10:00Z"},
                                    }
                                ]
                            },
                        )
                    if "/v2/achievements/player/2533274000001" in path:
                        return httpx.Response(
                            200,
                            json={
                                "titles": [
                                    {
                                        "titleId": "1001",
                                        "name": "Halo Infinite",
                                        "titleHistory": {"lastTimePlayed": "2026-10-15T18:50:00Z"},
                                    }
                                ]
                            },
                        )
                if host == "presence.gog.com":
                    if mode["gog_active"]:
                        return httpx.Response(
                            200,
                            json={
                                "items": [
                                    {
                                        "user_id": "555001",
                                        "presence": "online",
                                        "game_id": "1207658924",
                                        "game_title": "Witcher 3",
                                        "rich_presence": "Novigrad",
                                    }
                                ]
                            },
                        )
                    return httpx.Response(200, json={"items": [{"user_id": "555001", "presence": "offline"}]})
                if host == "gameplay.gog.com" and "/sessions" in path:
                    return httpx.Response(200, json={"time_sum": mode["gog_minutes"]})
                if host == "embed.gog.com" and path == "/account/getFilteredProducts":
                    return httpx.Response(200, json={"totalPages": 1, "products": [{"id": 1207658924, "title": "Witcher 3"}]})
                if host == "embed.gog.com" and path == "/user/data/games":
                    return httpx.Response(200, json={"owned": [1207658924]})
                return httpx.Response(404)

            telemetry = TelemetryService(
                db_manager=mgr,
                identity_service=ident,
                library_service=lib,
                activity_service=act,
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                flush_delay_seconds=0.0,
            )

            t0 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
            res1 = await telemetry.poll_user(scope, now=t0, sleep_before_flush=False)
            self.assertEqual(res1["status"], "ok")
            self.assertEqual(len(res1["active_sessions"]), 2)

            # Simulate 25 min Xbox session & 15 min GOG session
            mode["xbox_active"] = False
            mode["xbox_minutes"] = 125
            mode["gog_active"] = False
            mode["gog_minutes"] = 215
            t1 = datetime(2026, 10, 15, 18, 25, tzinfo=timezone.utc)
            res2 = await telemetry.poll_user(scope, now=t1, sleep_before_flush=False)
            self.assertEqual(res2["status"], "ok")
            self.assertEqual(len(res2["active_sessions"]), 0)

            sessions = act.list_sessions(scope)
            self.assertEqual(len(sessions), 2)
            by_source = {s.source: s for s in sessions}
            self.assertEqual(by_source["xbox_telemetry"].duration_minutes, 25)
            self.assertEqual(by_source["xbox_telemetry"].precision_level, "exact")
            self.assertEqual(by_source["xbox_telemetry"].achievements_unlocked, 1)
            self.assertEqual(by_source["gog_telemetry"].duration_minutes, 15)
            self.assertEqual(by_source["gog_telemetry"].precision_level, "exact")

            # Now simulate daily sweep with exact same cloud totals -> MUST NOT double-count!
            sweep_same = await telemetry.run_daily_sweep_for_user(
                scope, now=datetime(2026, 10, 15, 18, 55, tzinfo=timezone.utc)
            )
            self.assertEqual(sweep_same["reconciled_count"], 0)
            self.assertEqual(len(act.list_sessions(scope)), 2)

            # Simulate 10m offline Xbox play after telemetry closed -> sweeps ONLY +10m delta!
            mode["xbox_minutes"] = 135
            sweep_delta = await telemetry.run_daily_sweep_for_user(
                scope, now=datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(sweep_delta["reconciled_count"], 1)
            sessions_after = act.list_sessions(scope)
            self.assertEqual(len(sessions_after), 3)
            reconciled = [s for s in sessions_after if s.source == "Xbox"]
            self.assertEqual(len(reconciled), 1)
            self.assertEqual(reconciled[0].duration_minutes, 10)

            # Verify RTA WebSocket packet handler drives start/close cleanly
            mode["xbox_minutes"] = 135
            rta = XboxRtaClient(
                scope=scope,
                watcher_api_key="xbl-key-1",
                xuid="2533274000001",
                telemetry_service=telemetry,
            )
            rta_events = await rta.handle_rta_packet(
                json.dumps(
                    [
                        3,
                        1,
                        "https://userpresence.xboxlive.com/users/xuid(2533274000001)",
                        {"state": "Online", "titles": [{"id": "1001", "name": "Halo Infinite", "state": "Active"}]},
                    ]
                )
            )
            self.assertEqual(rta_events[0]["action"], "started")
            self.assertIsNotNone(act.get_active_telemetry_session(scope, "xbox"))

    def test_achievement_backfill_respects_existing_precision_and_deletions(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, scope, lib, act = self._make_services(Path(tmp))
            game, _ = lib.apply_platform_snapshot(
                scope,
                external_id="steam_620",
                title="Portal 2",
                platform="PC (Steam)",
                source="Steam",
                playtime_minutes=120,
            )
            lib.upsert_game_achievements(
                scope,
                game_id=game.id,
                source="Steam",
                platform="PC (Steam)",
                achievements=[
                    {
                        "external_ach_id": "a1",
                        "name": "Wake Up",
                        "unlocked": True,
                        "unlock_time": "2026-09-01T10:00:00+00:00",
                    },
                    {
                        "external_ach_id": "a2",
                        "name": "Portal Gun",
                        "unlocked": True,
                        "unlock_time": "2026-09-01T10:25:00+00:00",
                    },
                    {
                        "external_ach_id": "a3",
                        "name": "Chapter 2",
                        "unlocked": True,
                        "unlock_time": "2026-09-05T19:00:00+00:00",
                    },
                ],
            )
            # Pre-existing exact session overlaps with a3 -> a3 cluster must be ignored!
            act.record_interval_observation(
                scope,
                game_id=game.id,
                source="xbox_telemetry",
                platform="PC (Steam)",
                started_at="2026-09-05T18:30:00+00:00",
                ended_at="2026-09-05T19:30:00+00:00",
                duration_minutes=60,
                precision_level="exact",
                idempotency_key="exact-existing",
            )

            bf1 = act.backfill_historical_sessions_from_achievements(scope)
            self.assertEqual(bf1.sessions_created, 1)
            self.assertEqual(bf1.games_backfilled, 1)
            sessions = act.list_sessions(scope, game_id=game.id)
            coarse = [s for s in sessions if s.source == "achievement_backfill"]
            self.assertEqual(len(coarse), 1)
            self.assertEqual(coarse[0].precision_level, "coarse")
            self.assertEqual(coarse[0].achievements_unlocked, 2)

            # Deleting backfilled session must NOT recreate it on subsequent run
            act.delete_session(scope, coarse[0].id)
            bf2 = act.backfill_historical_sessions_from_achievements(scope)
            self.assertEqual(bf2.sessions_created, 0)

    async def test_homeassistant_sync_history_coalescing_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, mgr, _, scope, lib, act = self._make_services(Path(tmp))

            class FakeHAProvider:
                async def fetch_history(self, **kwargs: Any) -> list[dict[str, Any]]:
                    return [
                        {"state": "Halo Infinite", "last_changed": "2026-10-11T18:00:00Z"},
                        {"state": "unavailable", "last_changed": "2026-10-11T18:10:00Z"},
                        {"state": "Halo Infinite", "last_changed": "2026-10-11T18:12:00Z"},
                        {"state": "off", "last_changed": "2026-10-11T18:45:00Z"},
                    ]

            ha = HomeAssistantService(mgr, lib, act, history_provider=FakeHAProvider())
            res1 = await ha.sync_history(
                scope,
                ha_url="http://ha.local:8123",
                ha_token="ha-secret-token",
                entity_id="sensor.xbox",
                platform="Xbox",
                days_back=7,
            )
            self.assertEqual(res1.states_retrieved, 4)
            self.assertEqual(res1.total_spans, 1)
            self.assertEqual(res1.imported_count, 1)
            self.assertEqual(act.list_sessions(scope)[0].duration_minutes, 45)

            res2 = await ha.sync_history(
                scope,
                ha_url="http://ha.local:8123",
                ha_token="ha-secret-token",
                entity_id="sensor.xbox",
                platform="Xbox",
                days_back=7,
            )
            self.assertEqual(res2.imported_count, 0)
            self.assertEqual(res2.skipped_count, 1)

    # Regression (2026-11-19): Xbox presence "Online" non-game ghost entries
    async def test_xbox_telemetry_ignores_online_and_unverified_non_game_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, mgr, ident, scope, lib, act = self._make_services(Path(tmp))
            ident.update_connections(
                scope,
                xbox_gamertag="Chief94",
                xbox_xuid="2533274000001",
                xbl_api_key="xbl-key-1",
            )

            scenario = {"title": {"id": "704208617", "name": "Online", "state": "Active"}}

            def handler(request: httpx.Request) -> httpx.Response:
                path = request.url.path
                if "/v2/presence" in path:
                    if scenario["title"] is None:
                        return httpx.Response(200, json={"state": "Offline"})
                    return httpx.Response(
                        200,
                        json={"state": "Online", "devices": [{"titles": [scenario["title"]]}]},
                    )
                if path.endswith("/v2/player/stats"):
                    # Only real games with cloud stats appear here; neither 888777666 nor 555444333 is present
                    return httpx.Response(200, json={"statlistscollection": [{"stats": []}]})
                if "/v2/achievements/player/2533274000001/555444333" in path:
                    return httpx.Response(
                        200,
                        json={
                            "achievements": [
                                {
                                    "id": "ach_new_1",
                                    "name": "First Step",
                                    "description": "Start playing",
                                    "progressState": "NotStarted",
                                }
                            ]
                        },
                    )
                if "/v2/achievements/player/2533274000001/" in path:
                    return httpx.Response(200, json={"achievements": []})
                return httpx.Response(404)

            telemetry = TelemetryService(
                db_manager=mgr,
                identity_service=ident,
                library_service=lib,
                activity_service=act,
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                flush_delay_seconds=0.0,
            )

            # 1. Generic "Online" presence creates 0 games, 0 active sessions, 0 play_sessions, 0 ledger rows
            t0 = datetime(2026, 11, 18, 21, 0, tzinfo=timezone.utc)
            res_online = await telemetry.poll_user(scope, now=t0, sleep_before_flush=False)
            self.assertEqual(res_online["status"], "ok")
            self.assertEqual(res_online["active_sessions"], [])
            self.assertEqual(lib.list_games(scope), [])
            self.assertEqual(act.list_sessions(scope), [])
            self.assertEqual(act.get_daily_ledger(scope), [])

            # 2. Unseen UWP/utility app with no MinutesPlayed stat and no achievements is ignored
            scenario["title"] = {"id": "888777666", "name": "Dolby Access", "state": "Active", "placement": "Full"}
            t1 = datetime(2026, 11, 18, 21, 15, tzinfo=timezone.utc)
            res_app = await telemetry.poll_user(scope, now=t1, sleep_before_flush=False)
            self.assertEqual(res_app["status"], "ok")
            self.assertEqual(res_app["events"], [{"provider": "xbox", "action": "ignored_non_game", "title": "Dolby Access", "title_id": "888777666"}])
            self.assertEqual(res_app["active_sessions"], [])
            self.assertEqual(lib.list_games(scope), [])
            self.assertEqual(act.list_sessions(scope), [])

            # 3. Unseen brand-new real game verified via achievements creates the game & starts session
            scenario["title"] = {"id": "555444333", "name": "Brand New Indie", "state": "Active", "placement": "Full"}
            t2 = datetime(2026, 11, 18, 21, 30, tzinfo=timezone.utc)
            res_real = await telemetry.poll_user(scope, now=t2, sleep_before_flush=False)
            self.assertEqual(res_real["status"], "ok")
            self.assertEqual(len(res_real["active_sessions"]), 1)
            self.assertEqual([g.external_id for g in lib.list_games(scope)], ["xbox:555444333"])

            # 4. Switching presence to "Online" closes the real game cleanly without creating "Online"
            scenario["title"] = {"id": "704208617", "name": "Online", "state": "Active"}
            t3 = datetime(2026, 11, 18, 22, 0, tzinfo=timezone.utc)
            res_close = await telemetry.poll_user(scope, now=t3, sleep_before_flush=False)
            self.assertEqual(res_close["status"], "ok")
            self.assertEqual(res_close["active_sessions"], [])
            self.assertEqual([g.external_id for g in lib.list_games(scope)], ["xbox:555444333"])
            sessions = act.list_sessions(scope)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].duration_minutes, 30)
