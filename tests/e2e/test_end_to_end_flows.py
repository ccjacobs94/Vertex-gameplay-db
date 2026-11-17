"""End-to-end HTTP + worker workflow tests covering the full self-hosted Vertex rebuild."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.config import Settings
from vertex.main import create_app


class TestEndToEndFlows(unittest.IsolatedAsyncioTestCase):
    async def test_full_household_multiuser_sync_analytics_wishlist_and_portability_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            steam_playtime = {"portal2": 60}

            def handler(request: httpx.Request) -> httpx.Response:
                host = request.url.host or ""
                path = request.url.path
                if host == "api.steampowered.com" and path.endswith("/GetOwnedGames/v1/"):
                    games = [
                        {
                            "appid": 620,
                            "name": "Portal 2",
                            "playtime_forever": steam_playtime["portal2"],
                            "rtime_last_played": 1760000000,
                        },
                        {
                            "appid": 1145360,
                            "name": "Hades",
                            "playtime_forever": 90,
                            "rtime_last_played": 1760005000,
                        },
                    ]
                    return httpx.Response(200, json={"response": {"game_count": len(games), "games": games}})
                if host == "store.steampowered.com" and path.endswith("/api/storesearch/"):
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "id": 367520,
                                    "name": "Hollow Knight",
                                    "tiny_image": "https://cdn.example.test/hk.jpg",
                                    "metascore": "90",
                                }
                            ]
                        },
                    )
                if host == "store.steampowered.com" and path.endswith("/api/appdetails"):
                    return httpx.Response(
                        200,
                        json={
                            "367520": {
                                "success": True,
                                "data": {
                                    "name": "Hollow Knight",
                                    "genres": [{"description": "Metroidvania"}, {"description": "Action"}],
                                    "categories": [{"description": "Co-op"}],
                                    "release_date": {"date": "Feb 24, 2017"},
                                    "metacritic": {"score": 90},
                                    "header_image": "https://cdn.example.test/hk_hdr.jpg",
                                },
                            }
                        },
                    )
                return httpx.Response(404, text="Unexpected upstream call")

            transport = httpx.MockTransport(handler)
            app = create_app(
                Settings(
                    data_dir=tmp_path,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=tmp_path / "dist",
                    secret_key="e2e-test-secret-key-0123456789",
                    worker_enabled=False,
                    outbound_sync_enabled=True,
                ),
                client_factory=lambda: httpx.AsyncClient(transport=transport),
            )

            with TestClient(app) as alice_client:
                bob_client = TestClient(app)

                # 1. Public health vs closed private routes
                self.assertEqual(alice_client.get("/healthz").status_code, 200)
                self.assertEqual(alice_client.get("/readyz").status_code, 200)
                self.assertEqual(alice_client.get("/api/games").status_code, 401)
                self.assertEqual(alice_client.get("/api/wishlist").status_code, 401)
                self.assertEqual(alice_client.get("/api/analytics").status_code, 401)

                # 2. Bootstrap Alice (admin), set timezone & onboarding, invite Bob
                boot = alice_client.post(
                    "/api/auth/bootstrap",
                    json={"username": "alice", "password": "AlicePassword!1"},
                )
                self.assertEqual(boot.status_code, 201)
                self.assertIn("no-store", boot.headers.get("cache-control", ""))

                self.assertEqual(
                    bob_client.post(
                        "/api/auth/bootstrap",
                        json={"username": "evil", "password": "EvilPassword!1"},
                    ).status_code,
                    409,
                )
                self.assertEqual(
                    alice_client.patch("/api/account/timezone", json={"timezone": "America/Chicago"}).status_code,
                    200,
                )
                self.assertEqual(alice_client.post("/api/account/onboarding/complete").status_code, 200)

                invite = alice_client.post("/api/admin/invites", json={"role": "member", "note": "Bob"})
                self.assertEqual(invite.status_code, 201)
                inv_code = invite.json()["code"]
                self.assertEqual(
                    bob_client.post(
                        "/api/auth/invites/accept",
                        json={"invite_code": inv_code, "username": "bob", "password": "BobPassword!1"},
                    ).status_code,
                    201,
                )

                # 3. Manual Switch entry + reconcile dismiss + reset-dismissals + merge
                g_sw = alice_client.post(
                    "/api/games",
                    json={
                        "title": "Hades",
                        "platform": "Nintendo Switch",
                        "completion_status": "Playing",
                        "ownership_status": "Owned",
                        "playtime_hours": 10.0,
                        "genres": ["Roguelike"],
                    },
                )
                self.assertEqual(g_sw.status_code, 201)
                sw_id = g_sw.json()["id"]

                # Bob cannot see or mutate Alice's game
                self.assertEqual(bob_client.get("/api/games").json(), [])
                self.assertEqual(bob_client.get(f"/api/games/{sw_id}").status_code, 404)

                # 4. Alice configures Steam, runs two sync cycles; second creates delta + playtime prompt on locked Beaten Portal 2
                self.assertEqual(
                    alice_client.put(
                        "/api/connections",
                        json={"steam_id": "76561198000000001", "steam_api_key": "ALICE-STEAM-KEY"},
                    ).status_code,
                    200,
                )
                self.assertEqual(alice_client.post("/api/sync/steam").status_code, 202)
                j1 = await app.state.services.platform_sync.process_next_job("e2e-worker")
                self.assertIsNotNone(j1)
                self.assertEqual(j1.state, "succeeded")

                alice_games = alice_client.get("/api/games").json()
                portal2 = next(g for g in alice_games if g["title"] == "Portal 2")
                hades_pc = next(g for g in alice_games if g["title"] == "Hades" and g["platform"] != "Nintendo Switch")

                # Reconcile Hades Switch + Hades PC: dismiss, reset-dismissals, then confirm merge
                cands_1 = alice_client.get("/api/reconcile/candidates").json()
                self.assertEqual(len(cands_1), 1)
                pair_ids = [g["id"] for g in cands_1[0]["games"]]
                self.assertEqual(alice_client.post("/api/reconcile/dismiss", json={"game_ids": pair_ids}).status_code, 204)
                self.assertEqual(alice_client.get("/api/reconcile/candidates").json(), [])
                rst = alice_client.post("/api/reconcile/reset-dismissals")
                self.assertEqual(rst.status_code, 200)
                self.assertEqual(rst.json()["cleared"], 1)
                self.assertEqual(alice_client.post("/api/reconcile/confirm", json={"game_ids": pair_ids}).status_code, 200)

                # Lock Portal 2 as Beaten & give user rating/notes; then Steam playtime increases by 45m -> triggers prompt without unlocking Beaten
                self.assertEqual(
                    alice_client.patch(
                        f"/api/games/{portal2['id']}",
                        json={
                            "completion_status": "Beaten",
                            "status_locked": True,
                            "user_rating": 10,
                            "notes": "Masterpiece",
                            "favorite": True,
                        },
                    ).status_code,
                    200,
                )
                steam_playtime["portal2"] = 105  # +45 mins over 60m baseline
                self.assertEqual(alice_client.post("/api/sync/steam").status_code, 202)
                j2 = await app.state.services.platform_sync.process_next_job("e2e-worker")
                self.assertEqual(j2.state, "succeeded")

                p2_after = alice_client.get(f"/api/games/{portal2['id']}").json()
                self.assertEqual(p2_after["completion_status"], "Beaten")
                self.assertTrue(p2_after["status_locked"])
                self.assertEqual(p2_after["user_rating"], 10)
                self.assertEqual(p2_after["notes"], "Masterpiece")

                prompts = alice_client.get("/api/playtime-prompts").json()
                self.assertEqual(len(prompts), 1)
                self.assertEqual(prompts[0]["game_id"], portal2["id"])
                res_prompt = alice_client.post(f"/api/playtime-prompts/{prompts[0]['id']}/resolve", json={"action": "accept"})
                self.assertEqual(res_prompt.status_code, 200)
                self.assertEqual(res_prompt.json()["completion_status"], "Playing")
                self.assertEqual(alice_client.get("/api/playtime-prompts").json(), [])

                # Achievements endpoint returns summary
                ach = alice_client.get(f"/api/games/{hades_pc['id']}/achievements")
                self.assertEqual(ach.status_code, 200)
                self.assertEqual(len(ach.json()["per_platform_summary"]), 2)

                # 5. Home Assistant events + CSV + Session edit/delete + Ledger
                ha_start = alice_client.post(
                    "/api/integrations/homeassistant/events",
                    json={"event": "start", "title": "Balatro", "platform": "PC (Steam)", "started_at": "2026-10-05T18:00:00Z"},
                )
                self.assertEqual(ha_start.status_code, 200)
                ha_stop = alice_client.post(
                    "/api/integrations/homeassistant/events",
                    json={"event": "stop", "title": "Balatro", "platform": "PC (Steam)", "ended_at": "2026-10-05T19:00:00Z"},
                )
                self.assertEqual(ha_stop.status_code, 200)
                sess_id = ha_stop.json()["session_id"]
                self.assertIsNotNone(sess_id)

                patched_sess = alice_client.patch(
                    f"/api/sessions/{sess_id}",
                    json={"notes": "Great run", "is_locked": True},
                )
                self.assertEqual(patched_sess.status_code, 200)
                self.assertTrue(patched_sess.json()["is_locked"])
                self.assertEqual(patched_sess.json()["notes"], "Great run")

                ha_csv = alice_client.post(
                    "/api/integrations/homeassistant/import-csv",
                    json={
                        "platform": "Xbox",
                        "csv_content": "entity_id,state,last_changed\nsensor.xbox,Forza,2026-10-06T20:00:00Z\nsensor.xbox,off,2026-10-06T20:30:00Z\n",
                    },
                )
                self.assertEqual(ha_csv.status_code, 200)
                self.assertEqual(ha_csv.json()["imported_count"], 1)

                ledger_rows = alice_client.get("/api/ledger").json()
                self.assertGreaterEqual(len(ledger_rows), 2)

                # 6. Catalog search & enrich + Read-only Analytics
                cat_search = alice_client.get("/api/catalog/search", params={"q": "Hollow", "limit": 5})
                self.assertEqual(cat_search.status_code, 200)
                self.assertGreaterEqual(len(cat_search.json()), 1)

                enrich = alice_client.post("/api/catalog/enrich", json={"limit": 50, "force": False})
                self.assertEqual(enrich.status_code, 200)

                stats_r = alice_client.get("/api/stats")
                self.assertEqual(stats_r.status_code, 200)
                ana_r = alice_client.get("/api/analytics")
                self.assertEqual(ana_r.status_code, 200)
                self.assertEqual(ana_r.json()["cross_platform"]["double_dipped_count"], 1)

                sess_ana = alice_client.get("/api/analytics/sessions", params={"days": 365})
                self.assertEqual(sess_ana.status_code, 200)
                self.assertGreaterEqual(sess_ana.json()["total_sessions"], 2)

                sess_win = alice_client.get("/api/sessions/stats", params={"days": 365})
                self.assertEqual(sess_win.status_code, 200)
                self.assertGreaterEqual(sess_win.json()["total_sessions"], 2)

                # 7. Wishlist conflict + manual price + smart upgrade deal + idempotent double purchase
                w_create = alice_client.post(
                    "/api/wishlist",
                    json={
                        "title": "Portal 2",
                        "platform_targets": [
                            {"platform": "PC", "target_price_usd": 10.0},
                            {"platform": "Xbox", "target_price_usd": 15.0},
                        ],
                    },
                )
                self.assertEqual(w_create.status_code, 201)
                self.assertTrue(w_create.json()["conflict"]["already_in_library"])

                w_hk = alice_client.post(
                    "/api/wishlist",
                    json={
                        "title": "Hollow Knight",
                        "platform_targets": [{"platform": "PC", "target_price_usd": 15.0}],
                        "allow_smart_upgrade": True,
                    },
                )
                self.assertEqual(w_hk.status_code, 201)
                hk_wid = w_hk.json()["id"]

                p_res = alice_client.post(
                    f"/api/wishlist/{hk_wid}/price",
                    json={
                        "platform": "PC",
                        "storefront": "steam",
                        "edition_name": "Voidheart Complete Edition",
                        "price_usd": 12.0,
                        "regular_price_usd": 25.0,
                    },
                )
                self.assertEqual(p_res.status_code, 200)
                self.assertTrue(p_res.json()["is_all_time_low"])

                deals = alice_client.get("/api/wishlist/deals", params={"smart_upgrades_only": True}).json()
                self.assertEqual(deals["total_deals"], 1)
                self.assertTrue(deals["deals"][0]["is_smart_upgrade"])

                hist = alice_client.get(f"/api/wishlist/{hk_wid}/history").json()
                self.assertTrue(len(hist["price_history_by_sku"]) >= 1)

                buy1 = alice_client.post(
                    f"/api/wishlist/{hk_wid}/purchase",
                    json={"platform": "PC (Steam)", "edition_name": "Voidheart Complete Edition", "price_paid_usd": 12.0},
                )
                self.assertEqual(buy1.status_code, 200)
                self.assertFalse(buy1.json()["already_purchased"])

                buy2 = alice_client.post(
                    f"/api/wishlist/{hk_wid}/purchase",
                    json={"platform": "PC (Steam)", "edition_name": "Voidheart Complete Edition", "price_paid_usd": 12.0},
                )
                self.assertEqual(buy2.status_code, 200)
                self.assertTrue(buy2.json()["already_purchased"])
                self.assertEqual(buy1.json()["library_game_id"], buy2.json()["library_game_id"])

                # 8. Export JSON from Alice -> Import into Bob -> verify ledger rebuilt & no secrets leaked
                export_json = alice_client.get("/api/portability/export.json").json()
                self.assertNotIn("password_hash", str(export_json))
                self.assertNotIn("ALICE-STEAM-KEY", str(export_json))

                bob_imp = bob_client.post(
                    "/api/portability/import/json",
                    json={"payload": export_json, "mode": "merge", "dry_run": False},
                )
                self.assertEqual(bob_imp.status_code, 200)
                bob_ledger = bob_client.get("/api/ledger").json()
                self.assertGreaterEqual(len(bob_ledger), 2)

                # 9. Session deletion & rebuild + Wishlist PATCH/DELETE over HTTP
                del_sess = alice_client.delete(f"/api/sessions/{sess_id}")
                self.assertEqual(del_sess.status_code, 204)
                rebuilt_ledger = alice_client.post("/api/ledger/rebuild")
                self.assertEqual(rebuilt_ledger.status_code, 200)

                w_upd = alice_client.patch(f"/api/wishlist/{w_create.json()['id']}", json={"notes": "Updated note"})
                self.assertEqual(w_upd.status_code, 200)
                self.assertEqual(w_upd.json()["notes"], "Updated note")

                w_plat = alice_client.patch(
                    f"/api/wishlist/{w_create.json()['id']}/platforms",
                    json={"platform_targets": [{"platform": "PC", "target_price_usd": 12.0}], "force_track_owned": True},
                )
                self.assertEqual(w_plat.status_code, 200)
                pc_t = next(t for t in w_plat.json()["targets"] if t["platform_family"] == "PC")
                self.assertFalse(pc_t["muted_owned_conflict"])

                self.assertEqual(alice_client.delete(f"/api/wishlist/{w_create.json()['id']}").status_code, 204)
                self.assertEqual(alice_client.get(f"/api/wishlist/{w_create.json()['id']}").status_code, 404)

                # 10. Shared browser logout + session isolation
                out = alice_client.post("/api/auth/logout")
                self.assertEqual(out.status_code, 204)
                self.assertIn("no-store", out.headers.get("cache-control", ""))
                self.assertEqual(alice_client.get("/api/account").status_code, 401)

                # Log Bob into the former Alice client jar -> must see ONLY Bob
                self.assertEqual(
                    alice_client.post("/api/auth/login", json={"username": "bob", "password": "BobPassword!1"}).status_code,
                    200,
                )
                self.assertEqual(alice_client.get("/api/account").json()["username"], "bob")

    def test_offline_mode_manual_library_wishlist_catalog_and_ha_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            app = create_app(
                Settings(
                    data_dir=tmp_path,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=tmp_path / "dist",
                    secret_key="e2e-offline-secret-key-012345",
                    worker_enabled=False,
                    outbound_sync_enabled=False,
                ),
            )
            with TestClient(app) as client:
                self.assertEqual(
                    client.post("/api/auth/bootstrap", json={"username": "solo", "password": "SoloPassword!1"}).status_code,
                    201,
                )
                # Seed public cache entry locally (works without network)
                app.state.services.catalog.upsert_public_metadata(
                    title="Celeste",
                    source="local_seed",
                    genres=["Platformer"],
                    tags=["Indie", "Story-Rich"],
                    release_year=2018,
                    critic_score=92,
                )
                g_r = client.post("/api/games", json={"title": "Celeste", "platform": "Nintendo Switch", "completion_status": "Plan to Play"})
                self.assertEqual(g_r.status_code, 201)

                search_r = client.get("/api/catalog/search", params={"q": "Celeste"})
                self.assertEqual(search_r.status_code, 200)
                self.assertEqual(len(search_r.json()), 1)
                self.assertTrue(search_r.json()[0]["cached"])

                enrich_r = client.post("/api/catalog/enrich", json={"limit": 50, "force": False})
                self.assertEqual(enrich_r.status_code, 200)
                self.assertFalse(enrich_r.json()["used_network"])
                self.assertEqual(enrich_r.json()["games_enriched"], 1)

                # Wishlist & analytics operate cleanly offline
                self.assertEqual(
                    client.post(
                        "/api/wishlist",
                        json={"title": "UFO 50", "platform_targets": [{"platform": "PC", "target_price_usd": 20.0}]},
                    ).status_code,
                    201,
                )
                self.assertEqual(client.get("/api/analytics").json()["summary"]["unique_titles"], 1)

    def test_timezone_update_rebuilds_daily_playtime_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            app = create_app(
                Settings(
                    data_dir=tmp_path,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=tmp_path / "dist",
                    secret_key="e2e-tz-secret-key-0123456789",
                    worker_enabled=False,
                    outbound_sync_enabled=False,
                ),
            )
            with TestClient(app) as client:
                self.assertEqual(
                    client.post("/api/auth/bootstrap", json={"username": "tzuser", "password": "TzPassword!123"}).status_code,
                    201,
                )
                # Record session at 03:00 UTC -> under auto/UTC this lands on 2026-10-05;
                # under America/Chicago (-05:00 CDT) it lands on 2026-10-04 at 22:00.
                ev = client.post(
                    "/api/integrations/homeassistant/events",
                    json={
                        "event": "record",
                        "title": "Outer Wilds",
                        "platform": "PC (Steam)",
                        "started_at": "2026-10-05T02:30:00Z",
                        "ended_at": "2026-10-05T03:00:00Z",
                        "duration_minutes": 30,
                    },
                )
                self.assertEqual(ev.status_code, 200)
                before_dates = [r["date"] for r in client.get("/api/ledger").json()]
                self.assertEqual(before_dates, ["2026-10-05"])

                tz_resp = client.patch("/api/account/timezone", json={"timezone": "America/Chicago"})
                self.assertEqual(tz_resp.status_code, 200)
                after_dates = [r["date"] for r in client.get("/api/ledger").json()]
                self.assertEqual(after_dates, ["2026-10-04"])


if __name__ == "__main__":
    unittest.main()
