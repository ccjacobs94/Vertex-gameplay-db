"""Integration tests for Phase 2 & 3 sync, GOG, telemetry, achievements sync, and HA history."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.config import Settings
from vertex.integrations.platforms.psn import PSNAdapter
from vertex.main import create_app


def _make_fake_psn_factory():
    class FakePSNUser:
        online_id = "IntegrationPSN"
        account_id = "psn-acc-777"

        def trophy_titles(self):
            return [
                SimpleNamespace(
                    np_communication_id="NPWR777",
                    title_name="Bloodborne",
                    title_platform={"PS4"},
                    earned_trophies=SimpleNamespace(bronze=1, silver=0, gold=0, platinum=0),
                    defined_trophies=SimpleNamespace(bronze=2, silver=0, gold=0, platinum=0),
                )
            ]

        def title_stats(self):
            return [
                SimpleNamespace(
                    title_id="CUSA00900",
                    name="Bloodborne",
                    category=SimpleNamespace(value="ps4_game"),
                    play_duration=timedelta(hours=4, minutes=30),
                    last_played_date_time=datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc),
                    image_url="https://psn.example/bb.jpg",
                )
            ]

        def trophies(self, np_comm_id: str, target_plat: str, include_progress: bool = True):
            return [
                SimpleNamespace(
                    trophy_id=1,
                    trophy_name="Yharnam Sunrise",
                    trophy_detail="Survive the night",
                    trophy_icon_url="https://psn.example/t1.png",
                    earned=True,
                    earned_date_time=datetime(2026, 10, 14, 20, 45, tzinfo=timezone.utc),
                    trophy_type="gold",
                    trophy_earn_rate="32.4",
                    trophy_hidden=False,
                )
            ]

    return lambda npsso: FakePSNUser()


class TestPhase2And3Integration(unittest.IsolatedAsyncioTestCase):
    async def test_connections_test_subaccount_isolation_multiplatform_sync_achievements_ha_and_telemetry(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = {
                "xbox_boxart_only": True,
                "xbox_minutes": 180,
                "xbox_presence_active": False,
                "gog_expired_once": True,
                "gog_presence_active": False,
                "gog_session_minutes": 240,
            }

            def handler(request: httpx.Request) -> httpx.Response:
                host = request.url.host
                path = request.url.path

                # Steam
                if host == "api.steampowered.com":
                    if path.endswith("/ResolveVanityURL/v1/"):
                        return httpx.Response(200, json={"response": {"success": 1, "steamid": "76561198000000111"}})
                    if path.endswith("/GetPlayerSummaries/v2/"):
                        return httpx.Response(
                            200,
                            json={"response": {"players": [{"steamid": "76561198000000111", "personaname": "AliceSteam"}]}},
                        )
                    if path.endswith("/GetOwnedGames/v1/"):
                        return httpx.Response(
                            200,
                            json={
                                "response": {
                                    "games": [{"appid": 1145360, "name": "Hades", "playtime_forever": 300}]
                                }
                            },
                        )
                    if path.endswith("/GetSchemaForGame/v2/"):
                        return httpx.Response(
                            200,
                            json={
                                "game": {
                                    "availableGameStats": {
                                        "achievements": [
                                            {"name": "ESCAPED", "displayName": "Escaped Tartarus", "description": "Clear Tartarus"}
                                        ]
                                    }
                                }
                            },
                        )
                    if path.endswith("/GetPlayerAchievements/v1/"):
                        return httpx.Response(
                            200,
                            json={
                                "playerstats": {
                                    "steamID": "76561198000000111",
                                    "gameName": "Hades",
                                    "success": True,
                                    "achievements": [
                                        {"apiname": "ESCAPED", "achieved": 1, "unlocktime": 1760000000}
                                    ],
                                }
                            },
                        )

                # Xbox OpenXBL
                if "xbl.io" in host:
                    if "/v2/friends/search" in path:
                        return httpx.Response(
                            200,
                            json={
                                "profileUsers": [
                                    {
                                        "id": "2533274999999",
                                        "settings": [
                                            {"id": "Gamertag", "value": "AliceXBL"},
                                            {"id": "Gamerscore", "value": "15000"},
                                        ],
                                    }
                                ]
                            },
                        )
                    if "/v2/presence" in path:
                        if state["xbox_presence_active"]:
                            return httpx.Response(
                                200,
                                json={
                                    "state": "Online",
                                    "devices": [
                                        {
                                            "titles": [
                                                {
                                                    "id": "2002",
                                                    "name": "Hades",
                                                    "state": "Active",
                                                    "activity": {"richPresence": "Styx"},
                                                }
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
                                    {"stats": [{"titleid": "2002", "name": "MinutesPlayed", "value": str(state["xbox_minutes"])}]}
                                ]
                            },
                        )
                    if "/v2/achievements/player/2533274999999/2002" in path:
                        return httpx.Response(
                            200,
                            json={
                                "achievements": [
                                    {
                                        "id": "xb_esc",
                                        "name": "Escaped Tartarus",
                                        "description": "Clear Tartarus",
                                        "progressState": "Achieved",
                                        "progression": {"timeUnlocked": "2026-10-12T14:00:00Z"},
                                        "rewards": [{"type": "Gamerscore", "value": "15"}],
                                    }
                                ]
                            },
                        )
                    if "/v2/achievements/player/2533274999999" in path:
                        imgs = (
                            [{"type": "BoxArt", "url": "https://xbox/box.jpg"}]
                            if state["xbox_boxart_only"]
                            else [
                                {"type": "BoxArt", "url": "https://xbox/box.jpg"},
                                {"type": "Poster", "url": "https://xbox/poster.jpg"},
                            ]
                        )
                        return httpx.Response(
                            200,
                            json={
                                "titles": [
                                    {
                                        "titleId": "2002",
                                        "name": "Hades",
                                        "devices": ["XboxSeries", "PC"],
                                        "titleHistory": {
                                            "lastTimePlayed": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                                        },
                                        "achievement": {"currentAchievements": 1, "totalAchievements": 1},
                                        "gamePass": {"isGamePass": True},
                                        "images": imgs,
                                    }
                                ]
                            },
                        )

                # GOG Galaxy
                if host == "auth.gog.com" and path == "/token":
                    return httpx.Response(
                        200,
                        json={"access_token": "gog-fresh-token", "refresh_token": "gog-fresh-refresh", "user_id": "888001"},
                    )
                if host == "embed.gog.com" and path == "/userData.json":
                    return httpx.Response(200, json={"userId": "888001", "username": "AliceGOG"})
                if host == "embed.gog.com" and path == "/account/getFilteredProducts":
                    if request.headers.get("Authorization") == "Bearer gog-stale-token" and state["gog_expired_once"]:
                        state["gog_expired_once"] = False
                        return httpx.Response(401, json={"error": "expired"})
                    return httpx.Response(
                        200,
                        json={
                            "totalPages": 1,
                            "products": [{"id": 1999, "title": "Hades", "category": "Action"}],
                        },
                    )
                if host == "gameplay.gog.com" and "/games/1999/users/888001/sessions" in path:
                    return httpx.Response(200, json={"time_sum": state["gog_session_minutes"]})
                if host == "gameplay.gog.com" and "/clients/1999/users/888001/achievements" in path:
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "achievement_id": "gog_esc",
                                    "name": "Escaped Tartarus",
                                    "description": "Clear Tartarus",
                                    "date_unlocked": "2026-10-11T10:00:00Z",
                                    "rarity": 64.0,
                                }
                            ]
                        },
                    )
                if host == "presence.gog.com" and path == "/statuses":
                    if state["gog_presence_active"]:
                        return httpx.Response(
                            200,
                            json={
                                "items": [
                                    {
                                        "user_id": "888001",
                                        "presence": "online",
                                        "game_id": "1999",
                                        "game_title": "Hades",
                                        "rich_presence": "Asphodel",
                                    }
                                ]
                            },
                        )
                    return httpx.Response(200, json={"items": [{"user_id": "888001", "presence": "offline"}]})

                # Home Assistant
                if host == "ha.local":
                    if path == "/api/":
                        return httpx.Response(200, json={"message": "API running."})
                    if path.startswith("/api/history/period/"):
                        return httpx.Response(
                            200,
                            json=[
                                [
                                    {"state": "Celeste", "last_changed": "2026-10-14T16:00:00Z"},
                                    {"state": "unavailable", "last_changed": "2026-10-14T16:10:00Z"},
                                    {"state": "Celeste", "last_changed": "2026-10-14T16:12:00Z"},
                                    {"state": "off", "last_changed": "2026-10-14T16:40:00Z"},
                                ]
                            ],
                        )

                return httpx.Response(404)

            cfg = Settings(
                data_dir=tmp_path,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=tmp_path / "dist",
                secret_key="phase23-integration-secret-key-0001",
                xbl_api_key="SERVER-XBL-KEY-PRIVATE",
                worker_enabled=False,
                telemetry_enabled=True,
                telemetry_flush_delay_seconds=0.0,
                outbound_sync_enabled=True,
            )
            app = create_app(
                cfg,
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            )
            app.state.services.platform_sync.psn_adapter = PSNAdapter(
                client_factory=_make_fake_psn_factory()
            )

            with TestClient(app) as client:
                # 1. Bootstrap Alice & invite Bob
                self.assertEqual(
                    client.post(
                        "/api/auth/bootstrap",
                        json={"username": "alice", "password": "AlicePassword!1"},
                    ).status_code,
                    201,
                )
                inv = client.post(
                    "/api/admin/invites",
                    json={"role": "member", "ttl_hours": 24},
                ).json()

                # Strict subaccount Xbox server key isolation: unconfigured Bob sees False & gets None decrypted key
                bob_client = TestClient(app)
                self.assertEqual(
                    bob_client.post(
                        "/api/auth/invites/accept",
                        json={"invite_code": inv["code"], "username": "bob", "password": "BobPassword!1"},
                    ).status_code,
                    201,
                )
                bob_conns = bob_client.get("/api/connections").json()
                self.assertFalse(bob_conns["xbl_api_key_configured"])
                bob_scope = next(
                    s for s in app.state.services.identity.list_active_user_scopes() if s.username == "bob"
                )
                self.assertIsNone(app.state.services.identity.get_decrypted_connections(bob_scope).xbl_api_key)

                # Once Bob configures xbox_gamertag, server fallback becomes active internally
                # while summary keeps xbl_api_key_configured=False (personal key not stored)
                bob_conns_after = bob_client.put(
                    "/api/connections",
                    json={"xbox_gamertag": "AliceXBL"},
                ).json()
                self.assertFalse(bob_conns_after["xbl_api_key_configured"])
                self.assertEqual(bob_conns_after["xbox_gamertag"], "AliceXBL")
                self.assertEqual(
                    app.state.services.identity.get_decrypted_connections(bob_scope).xbl_api_key,
                    "SERVER-XBL-KEY-PRIVATE",
                )
                bob_xb_test = bob_client.post("/api/connections/test", json={"provider": "xbox"})
                self.assertEqual(bob_xb_test.status_code, 200)
                self.assertEqual(bob_xb_test.json()["status"], "ok")

                # Create & suspend Carol -> scheduled sync must ignore her
                inv2 = client.post("/api/admin/invites", json={"role": "member"}).json()
                carol_client = TestClient(app)
                self.assertEqual(
                    carol_client.post(
                        "/api/auth/invites/accept",
                        json={"invite_code": inv2["code"], "username": "carol", "password": "CarolPassword!1"},
                    ).status_code,
                    201,
                )
                carol_client.put("/api/connections", json={"steam_id": "alice_v", "steam_api_key": "K1"})
                users = client.get("/api/admin/users").json()
                carol_id = next(u["id"] for u in users if u["username"] == "carol")
                self.assertEqual(
                    client.patch(f"/api/admin/users/{carol_id}", json={"status": "suspended"}).status_code,
                    200,
                )

                # 2. Alice saves Steam, Xbox, PSN, GOG, Home Assistant connections (all masked on GET)
                hex_npsso = "abcdef12" * 8
                put_resp = client.put(
                    "/api/connections",
                    json={
                        "steam_id": "alice_vanity",
                        "steam_api_key": "STEAM-KEY-1",
                        "xbox_gamertag": "AliceXBL",
                        "xbl_api_key": "ALICE-XBL-KEY",
                        "xbl_watcher_api_key": "ALICE-WATCHER-KEY",
                        "psn_npsso": hex_npsso,
                        "gog_user_id": "888001",
                        "gog_username": "AliceGOG",
                        "gog_access_token": "gog-stale-token",
                        "gog_refresh_token": "gog-refresh-1",
                        "ha_url": "http://ha.local:8123",
                        "ha_token": "HA-SECRET-TOKEN",
                        "ha_entity_id": "sensor.xbox_now",
                    },
                )
                self.assertEqual(put_resp.status_code, 200)
                conns_json = put_resp.json()
                self.assertTrue(conns_json["steam_api_key_configured"])
                self.assertTrue(conns_json["xbl_api_key_configured"])
                self.assertTrue(conns_json["xbl_watcher_api_key_configured"])
                self.assertTrue(conns_json["psn_configured"])
                self.assertTrue(conns_json["gog_token_configured"])
                self.assertTrue(conns_json["ha_token_configured"])
                self.assertNotIn("ALICE-XBL-KEY", json.dumps(conns_json))
                self.assertNotIn("HA-SECRET-TOKEN", json.dumps(conns_json))

                # 3. Connection test endpoint for all 5 providers
                for prov in ("steam", "xbox", "psn", "gog", "homeassistant"):
                    t_res = client.post("/api/connections/test", json={"provider": prov})
                    self.assertEqual(t_res.status_code, 200, prov)
                    self.assertEqual(t_res.json()["status"], "ok", t_res.json())

                # 4. Trigger Sync All -> queues steam, xbox, psn, gog
                sync_all = client.post("/api/sync")
                self.assertEqual(sync_all.status_code, 202)
                self.assertEqual(set(sync_all.json()["providers"]), {"steam", "xbox", "psn", "gog"})

                for _ in range(4):
                    res = await app.state.services.platform_sync.process_next_job("test-worker")
                    self.assertIsNotNone(res)
                    self.assertEqual(res.state, "succeeded", res)

                # Verify GOG token refresh persisted in system.db
                alice_scope = next(
                    s for s in app.state.services.identity.list_active_user_scopes() if s.username == "alice"
                )
                alice_creds = app.state.services.identity.get_decrypted_connections(alice_scope)
                self.assertEqual(alice_creds.gog_access_token, "gog-fresh-token")
                self.assertEqual(alice_creds.xbox_xuid, "2533274999999")

                games = client.get("/api/games").json()
                by_ext = {g["external_id"]: g for g in games}
                self.assertIn("steam:1145360", by_ext)
                self.assertIn("xbox:2002", by_ext)
                self.assertIn("psn:CUSA00900", by_ext)
                self.assertIn("gog:1999", by_ext)

                xb_hades = by_ext["xbox:2002"]
                self.assertEqual(xb_hades["cover_url"], "https://xbox/box.jpg")
                self.assertTrue(xb_hades["is_game_pass"])
                self.assertTrue(xb_hades["is_play_anywhere"])

                # User locks status & saves personal edits on Xbox Hades
                client.patch(
                    f"/api/games/{xb_hades['id']}",
                    json={
                        "completion_status": "Beaten",
                        "status_locked": True,
                        "ownership_status": "Subscription",
                        "user_rating": 10,
                        "notes": "Cross-save favorite",
                        "favorite": True,
                    },
                )

                # Xbox upgrades cover BoxArt -> Poster without clobbering user locks/notes
                state["xbox_boxart_only"] = False
                self.assertEqual(client.post("/api/sync/xbox").status_code, 202)
                await app.state.services.platform_sync.process_next_job("test-worker")

                xb_updated = client.get(f"/api/games/{xb_hades['id']}").json()
                self.assertEqual(xb_updated["cover_url"], "https://xbox/poster.jpg")
                self.assertEqual(xb_updated["completion_status"], "Beaten")
                self.assertTrue(xb_updated["status_locked"])
                self.assertEqual(xb_updated["ownership_status"], "Subscription")
                self.assertEqual(xb_updated["user_rating"], 10)
                self.assertEqual(xb_updated["notes"], "Cross-save favorite")

                # 5. Reconcile Steam + Xbox + GOG Hades and verify unified achievement sync
                gog_hades = by_ext["gog:1999"]
                st_hades = by_ext["steam:1145360"]
                rec_res = client.post(
                    "/api/reconcile/confirm",
                    json={"game_ids": [st_hades["id"], xb_hades["id"], gog_hades["id"]]},
                )
                self.assertEqual(rec_res.status_code, 200)

                ach_sync = client.post(f"/api/games/{xb_hades['id']}/achievements/sync")
                self.assertEqual(ach_sync.status_code, 200)
                ach_data = ach_sync.json()
                self.assertEqual(ach_data["synced_copies"], 3)
                self.assertEqual(set(ach_data["synced_providers"]), {"Steam", "Xbox", "GOG"})
                self.assertEqual(ach_data["unified_summary"]["overlap_count"], 1)
                self.assertEqual(len(ach_data["per_platform_summary"]), 3)
                # Locked completion_status must stay "Beaten" even at 100% achievements!
                self.assertEqual(client.get(f"/api/games/{xb_hades['id']}").json()["completion_status"], "Beaten")

                # 6. Achievement historical backfill + delete non-recreation via HTTP
                bf = client.post("/api/sessions/backfill-achievements")
                self.assertEqual(bf.status_code, 200)
                self.assertGreaterEqual(bf.json()["sessions_created"], 1)
                sessions_before_del = client.get("/api/sessions").json()
                bf_sess = next(s for s in sessions_before_del if s["source"] == "achievement_backfill")
                self.assertEqual(client.delete(f"/api/sessions/{bf_sess['id']}").status_code, 204)
                # Rerunning backfill must not recreate deleted session
                bf_again = client.post("/api/sessions/backfill-achievements").json()
                self.assertEqual(bf_again["sessions_created"], 0)

                # 7. Home Assistant history pull & idempotency
                ha_sync1 = client.post(
                    "/api/integrations/homeassistant/sync",
                    json={"platform": "Nintendo Switch", "days_back": 7},
                )
                self.assertEqual(ha_sync1.status_code, 200)
                self.assertEqual(ha_sync1.json()["states_retrieved"], 4)
                self.assertEqual(ha_sync1.json()["imported_count"], 1)

                ha_sync2 = client.post(
                    "/api/integrations/homeassistant/sync",
                    json={"platform": "Nintendo Switch", "days_back": 7},
                )
                self.assertEqual(ha_sync2.status_code, 200)
                self.assertEqual(ha_sync2.json()["imported_count"], 0)
                self.assertEqual(ha_sync2.json()["skipped_count"], 1)

                # 8. Live telemetry status, start poll, close poll, daily sweep
                tel_stat = client.get("/api/telemetry/status")
                self.assertEqual(tel_stat.status_code, 200)
                self.assertTrue(tel_stat.json()["providers"]["xbox"])
                self.assertTrue(tel_stat.json()["providers"]["xbox_rta"])
                self.assertTrue(tel_stat.json()["providers"]["gog"])

                state["xbox_presence_active"] = True
                state["gog_presence_active"] = True
                poll1 = client.post("/api/telemetry/poll")
                self.assertEqual(poll1.status_code, 200)
                self.assertEqual(len(poll1.json()["active_sessions"]), 2)

                # Stop both games with cloud deltas (+22m Xbox, +18m GOG)
                state["xbox_presence_active"] = False
                state["xbox_minutes"] = 202
                state["gog_presence_active"] = False
                state["gog_session_minutes"] = 258
                poll2 = client.post("/api/telemetry/poll")
                self.assertEqual(poll2.status_code, 200)
                self.assertEqual(len(poll2.json()["active_sessions"]), 0)
                closed_evs = {ev["provider"]: ev for ev in poll2.json()["events"]}
                self.assertEqual(closed_evs["xbox"]["duration_minutes"], 22)
                self.assertEqual(closed_evs["gog"]["duration_minutes"], 18)

                # Sweep immediately with identical totals -> 0 double-counted sessions
                sweep1 = client.post("/api/telemetry/sweep")
                self.assertEqual(sweep1.status_code, 200)
                self.assertEqual(sweep1.json()["reconciled_count"], 0)

                # Offline play (+9m Xbox) -> sweep adds exactly 9m
                state["xbox_minutes"] = 211
                sweep2 = client.post("/api/telemetry/sweep")
                self.assertEqual(sweep2.status_code, 200)
                self.assertEqual(sweep2.json()["reconciled_count"], 1)

                # 9. Scheduled sync queues active users (Alice + Bob) and skips suspended Carol
                queued = app.state.services.platform_sync.enqueue_scheduled_sync_iteration()
                self.assertGreaterEqual(queued, 5)  # 4 for Alice + 1 for Bob (when deduped or fresh)

                # Also verify passive GET /api/games/{id}/achievements?auto_sync=true on a copy with cleared synced_at
                with app.state.services.db_manager.library_connection(alice_scope, immediate=True) as conn:
                    conn.execute("UPDATE games SET achievements_synced_at = NULL WHERE id = ?", (xb_hades["id"],))
                get_ach = client.get(f"/api/games/{xb_hades['id']}/achievements?auto_sync=true")
                self.assertEqual(get_ach.status_code, 200)
                self.assertGreaterEqual(get_ach.json()["unified_summary"]["total"], 1)

                # 10. Outbound disabled fail-closed check (via immutable Settings copy)
            offline_app = create_app(dataclasses.replace(cfg, outbound_sync_enabled=False))
            with TestClient(offline_app) as offline_client:
                self.assertEqual(
                    offline_client.post(
                        "/api/auth/login",
                        json={"username": "alice", "password": "AlicePassword!1"},
                    ).status_code,
                    200,
                )
                ha_disabled = offline_client.post("/api/integrations/homeassistant/sync", json={})
                self.assertEqual(ha_disabled.status_code, 422)
                test_disabled = offline_client.post("/api/connections/test", json={"provider": "xbox"})
                self.assertEqual(test_disabled.status_code, 200)
                self.assertEqual(test_disabled.json()["status"], "error")
                self.assertIn("disabled", test_disabled.json()["message"].lower())
                tel_poll_disabled = offline_client.post("/api/telemetry/poll")
                self.assertEqual(tel_poll_disabled.status_code, 200)
                self.assertEqual(tel_poll_disabled.json()["status"], "skipped")
                sweep_disabled = offline_client.post("/api/telemetry/sweep")
                self.assertEqual(sweep_disabled.status_code, 200)
                self.assertEqual(sweep_disabled.json()["status"], "skipped")
