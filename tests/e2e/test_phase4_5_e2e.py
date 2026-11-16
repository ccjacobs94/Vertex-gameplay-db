"""End-to-end Phase 4+5 user journey: wishlist discovery/sync -> deal push -> MCP -> library purchase."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import httpx

from vertex.config import Settings
from vertex.main import AppServices, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _e2e_store_transport(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    url = str(request.url)
    if "store.steampowered.com" in host:
        if "storesearch" in url:
            return httpx.Response(
                200,
                json={"items": [{"id": 1145360, "name": "Hades", "price": {"final": 999}}]},
            )
        return httpx.Response(
            200,
            json={
                "1145360": {
                    "success": True,
                    "data": {
                        "name": "Hades",
                        "header_image": "https://cdn.example/hades.jpg",
                        "price_overview": {
                            "currency": "USD",
                            "initial": 2499,
                            "final": 999,
                            "discount_percent": 60,
                        },
                        "genres": [{"description": "Action"}, {"description": "Roguelike"}],
                    },
                }
            },
        )
    if "catalog.gog.com" in host:
        return httpx.Response(200, json={"products": [{"id": "149123", "title": "Hades", "slug": "hades"}]})
    if "api.gog.com" in host:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "149123",
                    "slug": "hades",
                    "price": {
                        "finalAmount": "12.49",
                        "baseAmount": "24.99",
                        "discountPercentage": 50,
                        "currency": "USD",
                    },
                }
            ],
        )
    if "cheapshark.com" in host:
        if "title=" in url:
            return httpx.Response(200, json=[{"gameID": "303", "external": "Hades", "cheapest": "9.99"}])
        return httpx.Response(
            200,
            json={
                "303": {
                    "cheapestPriceEver": {"price": "8.49"},
                    "deals": [
                        {
                            "storeID": "1",
                            "price": "9.99",
                            "retailPrice": "24.99",
                            "savings": "60",
                            "dealID": "deal-hades",
                        }
                    ],
                }
            },
        )
    if "wikipedia.org" in host:
        return httpx.Response(404, json={})
    return httpx.Response(200, json={})


class TestPhase4And5EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_wishlist_sync_push_mcp_and_multiuser_isolation_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Settings(
                data_dir=Path(td) / "data",
                migrations_dir=REPO_ROOT / "migrations",
                frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
                secret_key="e2e-test-secret-key-phase45-only",
                outbound_sync_enabled=True,
                worker_enabled=False,
                telemetry_enabled=False,
                scheduled_sync_enabled=False,
                wishlist_sync_enabled=True,
                web_push_enabled=True,
                mcp_enabled=True,
                allowed_hosts=("localhost", "127.0.0.1", "testserver"),
            )
            app = create_app(
                cfg,
                client_factory=lambda: httpx.AsyncClient(
                    transport=httpx.MockTransport(_e2e_store_transport)
                ),
            )
            services: AppServices = app.state.services
            pushed_events: list[dict] = []
            services.web_push.sender = lambda sub, data, priv, claims: pushed_events.append(
                json.loads(data)
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    # 1. Bootstrap Alice & create Bob
                    r_boot = await client.post(
                        "/api/auth/bootstrap",
                        json={"username": "alice", "password": "AlicePassword!123"},
                    )
                    self.assertEqual(r_boot.status_code, 201)

                    r_bob = await client.post(
                        "/api/admin/users",
                        json={"username": "bob", "password": "BobPassword!123", "role": "member"},
                    )
                    self.assertEqual(r_bob.status_code, 201)

                    # 2. Alice subscribes to optional Web Push, adds Hades to wishlist, queues sync job
                    self.assertEqual(
                        (
                            await client.post(
                                "/api/push/subscribe",
                                json={
                                    "endpoint": "https://fcm.googleapis.com/fcm/send/alice-phone",
                                    "keys": {"p256dh": "k1", "auth": "a1"},
                                },
                            )
                        ).status_code,
                        201,
                    )

                    r_cand = await client.get("/api/wishlist/candidates?q=Hades")
                    self.assertEqual(r_cand.status_code, 200)
                    self.assertTrue(any(c["title"] == "Hades" for c in r_cand.json()["candidates"]))

                    r_wish = await client.post(
                        "/api/wishlist",
                        json={
                            "title": "Hades",
                            "platform_targets": [{"platform": "PC", "target_price_usd": 15.0}],
                        },
                    )
                    self.assertEqual(r_wish.status_code, 201)
                    hades_wish_id = r_wish.json()["id"]

                    r_sync = await client.post("/api/wishlist/sync", json={"force": True})
                    self.assertEqual(r_sync.status_code, 202)
                    job = await services.platform_sync.process_next_job(worker_id="e2e-worker")
                    self.assertEqual(job.state, "succeeded")
                    self.assertGreaterEqual(job.result["deals_triggered"], 1)
                    self.assertEqual(len(pushed_events), 1)
                    self.assertIn("Hades", pushed_events[0]["body"])

                    # 3. Alice uses scoped PAT over MCP Streamable HTTP to inspect history & purchase Hades
                    r_pat = await client.post(
                        "/api/account/tokens",
                        json={"name": "ai-client", "scopes": ["read:library", "write:library"]},
                    )
                    self.assertEqual(r_pat.status_code, 201)
                    alice_pat = r_pat.json()["token"]
                    alice_pat_id = r_pat.json()["record"]["id"]

                    mcp_hdrs = {
                        "Authorization": f"Bearer {alice_pat}",
                        "Accept": "application/json, text/event-stream",
                    }
                    r_init = await client.post(
                        "/mcp",
                        headers=mcp_hdrs,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "e2e-agent", "version": "1.0"},
                            },
                        },
                    )
                    self.assertEqual(r_init.status_code, 200)
                    sid = r_init.headers["mcp-session-id"]
                    mcp_hdrs["mcp-session-id"] = sid
                    await client.post(
                        "/mcp",
                        headers=mcp_hdrs,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    )

                    # Read MCP resource history before purchase
                    r_hist = await client.post(
                        "/mcp",
                        headers=mcp_hdrs,
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "resources/read",
                            "params": {"uri": f"vertex://wishlist/{hades_wish_id}/history"},
                        },
                    )
                    hist_data = json.loads(r_hist.json()["result"]["contents"][0]["text"])
                    self.assertGreaterEqual(len(hist_data["price_history_by_sku"]), 1)

                    # Purchase via MCP -> converted into Alice's library
                    r_purch = await client.post(
                        "/mcp",
                        headers=mcp_hdrs,
                        json={
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "mark_as_purchased",
                                "arguments": {
                                    "wishlist_item_id": hades_wish_id,
                                    "platform": "PC",
                                    "price_paid_usd": 9.99,
                                },
                            },
                        },
                    )
                    purch_data = json.loads(r_purch.json()["result"]["content"][0]["text"])
                    self.assertGreater(purch_data["library_game_id"], 0)

                    # Verify Hades now appears in Alice's library API
                    r_games = await client.get("/api/games?search=Hades")
                    self.assertEqual(r_games.status_code, 200)
                    self.assertEqual(len(r_games.json()), 1)

                    # Revoke Alice's PAT via HTTP API -> immediate 401 on MCP
                    self.assertEqual(
                        (await client.delete(f"/api/account/tokens/{alice_pat_id}")).status_code,
                        204,
                    )
                    self.assertEqual(
                        (
                            await client.post(
                                "/mcp",
                                headers=mcp_hdrs,
                                json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
                            )
                        ).status_code,
                        401,
                    )

                    # Bob remains strictly isolated
                    self.assertEqual(
                        (
                            await client.post(
                                "/api/auth/login",
                                json={"username": "bob", "password": "BobPassword!123"},
                            )
                        ).status_code,
                        200,
                    )
                    self.assertEqual((await client.get("/api/games")).json(), [])
                    self.assertEqual((await client.get("/api/wishlist")).json(), [])
