"""Integration tests for offline Steam platform sync jobs, retries, redaction, and throttling."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.config import Settings
from vertex.integrations.platforms.base import sanitize_error_message
from vertex.main import create_app


class TestSteamSyncJobs(unittest.IsolatedAsyncioTestCase):
    async def test_vanity_sync_retry_dedup_replay_and_edit_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            requests_seen: list[tuple[str, dict[str, str]]] = []
            mode = {"owned": "503_once", "playtime_portal2": 120, "include_hades": False}

            def handler(request: httpx.Request) -> httpx.Response:
                params = dict(request.url.params)
                requests_seen.append((request.url.path, params))
                if request.url.path.endswith("/ResolveVanityURL/v1/"):
                    return httpx.Response(
                        200,
                        json={"response": {"success": 1, "steamid": "76561198000000042"}},
                    )
                if request.url.path.endswith("/GetOwnedGames/v1/"):
                    if mode["owned"] == "503_once":
                        mode["owned"] = "ok"
                        return httpx.Response(503, text="Steam upstream unavailable")
                    games = [
                        {
                            "appid": 620,
                            "name": "Portal 2",
                            "playtime_forever": mode["playtime_portal2"],
                            "rtime_last_played": 1760000000,
                        }
                    ]
                    if mode["include_hades"]:
                        games.append(
                            {
                                "appid": 1145360,
                                "name": "Hades",
                                "playtime_forever": 45,
                                "rtime_last_played": 1760005000,
                            }
                        )
                    return httpx.Response(200, json={"response": {"game_count": len(games), "games": games}})
                return httpx.Response(404, text="Unexpected URL")

            transport = httpx.MockTransport(handler)
            app = create_app(
                Settings(
                    data_dir=tmp_path,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=tmp_path / "dist",
                    secret_key="steam-sync-test-secret-key",
                    worker_enabled=False,
                ),
                client_factory=lambda: httpx.AsyncClient(transport=transport),
            )

            with TestClient(app) as client:
                self.assertEqual(
                    client.post(
                        "/api/auth/bootstrap",
                        json={"username": "alice", "password": "AlicePassword!1"},
                    ).status_code,
                    201,
                )
                self.assertEqual(
                    client.put(
                        "/api/connections",
                        json={"steam_id": "https://steamcommunity.com/id/alice_vanity", "steam_api_key": "STEAM-KEY-SECRET"},
                    ).status_code,
                    200,
                )

                # Enqueue twice -> deduplicated to one queued job
                j1 = client.post("/api/sync/steam")
                j2 = client.post("/api/sync/steam")
                self.assertEqual(j1.status_code, 202)
                self.assertEqual(j2.status_code, 202)
                self.assertEqual(j1.json()["id"], j2.json()["id"])
                job_id = j1.json()["id"]

                # First attempt hits 503 -> re-queued with backoff
                first_res = await app.state.services.platform_sync.process_next_job("test-worker")
                self.assertIsNotNone(first_res)
                self.assertEqual(first_res.state, "queued")
                self.assertEqual(first_res.attempts, 1)
                self.assertEqual(first_res.last_error_code, "upstream_error")

                # Make backoff immediately eligible without sleeping
                with app.state.services.db_manager.system_connection(immediate=True) as conn:
                    conn.execute("UPDATE jobs SET available_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job_id,))

                second_res = await app.state.services.platform_sync.process_next_job("test-worker")
                self.assertIsNotNone(second_res)
                self.assertEqual(second_res.state, "succeeded")
                self.assertEqual(second_res.attempts, 2)
                self.assertEqual(second_res.result["added"], 1)

                games = client.get("/api/games").json()
                self.assertEqual(len(games), 1)
                portal2 = games[0]
                self.assertEqual(portal2["external_id"], "steam:620")
                self.assertEqual(portal2["playtime_hours"], 2.0)

                # User locks status & saves personal edits
                patch_resp = client.patch(
                    f"/api/games/{portal2['id']}",
                    json={
                        "completion_status": "Completed",
                        "status_locked": True,
                        "ownership_status": "Shared",
                        "user_rating": 9,
                        "notes": "Co-op favorite",
                        "favorite": True,
                    },
                )
                self.assertEqual(patch_resp.status_code, 200)

                # Re-sync with increased Portal 2 playtime (180m) + newly owned Hades (45m)
                mode["playtime_portal2"] = 180
                mode["include_hades"] = True
                j3 = client.post("/api/sync/steam")
                self.assertEqual(j3.status_code, 202)
                res3 = await app.state.services.platform_sync.process_next_job("test-worker")
                self.assertEqual(res3.state, "succeeded")
                self.assertEqual(res3.result["added"], 1)
                self.assertEqual(res3.result["updated"], 1)
                self.assertEqual(res3.result["sessions_changed"], 1)

                updated_portal2 = client.get(f"/api/games/{portal2['id']}").json()
                self.assertEqual(updated_portal2["completion_status"], "Completed")
                self.assertTrue(updated_portal2["status_locked"])
                self.assertEqual(updated_portal2["ownership_status"], "Shared")
                self.assertEqual(updated_portal2["user_rating"], 9)
                self.assertEqual(updated_portal2["notes"], "Co-op favorite")
                self.assertTrue(updated_portal2["favorite"])
                self.assertEqual(updated_portal2["playtime_hours"], 3.0)

                # Replay exact same snapshot -> idempotent (no new sessions recorded)
                j4 = client.post("/api/sync/steam")
                res4 = await app.state.services.platform_sync.process_next_job("test-worker")
                self.assertEqual(res4.state, "succeeded")
                self.assertEqual(res4.result["sessions_changed"], 0)
                sessions = client.get("/api/sessions", params={"game_id": portal2["id"]}).json()
                self.assertEqual(sum(s["duration_minutes"] for s in sessions), 60)

                overview = client.get("/api/jobs").json()
                self.assertEqual(len(overview["providers"]), 1)
                self.assertEqual(overview["providers"][0]["provider"], "steam")
                self.assertEqual(overview["providers"][0]["active_state"], "succeeded")
                self.assertFalse(overview["providers"][0]["credential_expired"])

    async def test_auth_failure_secret_redaction_and_rate_limit_and_offline_mode(self):
        self.assertNotIn(
            "SECRET_KEY_VALUE",
            sanitize_error_message("GET https://api.steampowered.com/?key=SECRET_KEY_VALUE&token=ABC"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            secret_steam_key = "SUPER-PRIVATE-STEAM-API-KEY-9876"
            behavior = {"status": 401}

            def handler(_request: httpx.Request) -> httpx.Response:
                if behavior["status"] == 401:
                    return httpx.Response(401, text=f"Invalid key={secret_steam_key}")
                if behavior["status"] == 429:
                    return httpx.Response(429, headers={"Retry-After": "45"}, text="Slow down")
                return httpx.Response(500, text="Server error")

            transport = httpx.MockTransport(handler)
            app = create_app(
                Settings(
                    data_dir=tmp_path,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=tmp_path / "dist",
                    secret_key="steam-auth-fail-secret",
                    worker_enabled=False,
                    outbound_sync_enabled=True,
                ),
                client_factory=lambda: httpx.AsyncClient(transport=transport),
            )

            with TestClient(app) as client:
                client.post(
                    "/api/auth/bootstrap",
                    json={"username": "alice", "password": "AlicePassword!1"},
                )
                client.post(
                    "/api/admin/users",
                    json={"username": "bob", "password": "BobPassword!1", "role": "member"},
                )
                client.put(
                    "/api/connections",
                    json={"steam_id": "76561198000000099", "steam_api_key": secret_steam_key},
                )

                # Non-retryable 401 marks credential_expired=True without leaking key
                job_resp = client.post("/api/sync/steam")
                self.assertEqual(job_resp.status_code, 202)
                failed = await app.state.services.platform_sync.process_next_job("worker-1")
                self.assertEqual(failed.state, "failed")
                self.assertEqual(failed.attempts, 1)
                self.assertEqual(failed.last_error_code, "auth_failed")
                self.assertNotIn(secret_steam_key, failed.last_error or "")

                overview = client.get("/api/jobs").json()
                self.assertTrue(overview["providers"][0]["credential_expired"])
                self.assertNotIn(secret_steam_key, json.dumps(overview))

                # Rate limit 429 throttles provider & requeues job
                behavior["status"] = 429
                client.post("/api/sync/steam")
                throttled = await app.state.services.platform_sync.process_next_job("worker-1")
                self.assertEqual(throttled.state, "queued")
                self.assertEqual(throttled.last_error_code, "rate_limited")
                with app.state.services.db_manager.system_connection() as conn:
                    prl = conn.execute("SELECT * FROM provider_rate_limits WHERE provider = 'steam'").fetchone()
                    self.assertIsNotNone(prl)

                # Suspending Bob cancels any queued jobs owned by Bob
                bob_client = TestClient(app)
                bob_login = bob_client.post("/api/auth/login", json={"username": "bob", "password": "BobPassword!1"})
                self.assertEqual(bob_login.status_code, 200)
                bob_client.put(
                    "/api/connections",
                    json={"steam_id": "76561198000000100", "steam_api_key": "BOB-KEY"},
                )
                bob_job_id = bob_client.post("/api/sync/steam").json()["id"]
                bob_id = bob_login.json()["account"]["id"]
                self.assertEqual(
                    client.patch(f"/api/admin/users/{bob_id}", json={"status": "suspended", "status_reason": "paused"}).status_code,
                    200,
                )
                with app.state.services.db_manager.system_connection() as conn:
                    row = conn.execute("SELECT state, last_error_code FROM jobs WHERE id = ?", (bob_job_id,)).fetchone()
                    self.assertEqual(row["state"], "cancelled")
                    self.assertEqual(row["last_error_code"], "account_unavailable")

    async def test_outbound_sync_disabled_leaves_manual_library_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            app = create_app(
                Settings(
                    data_dir=tmp_path,
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=tmp_path / "dist",
                    secret_key="offline-mode-secret",
                    worker_enabled=False,
                    outbound_sync_enabled=False,
                )
            )
            with TestClient(app) as client:
                client.post("/api/auth/bootstrap", json={"username": "alice", "password": "AlicePassword!1"})
                client.put("/api/connections", json={"steam_id": "76561198000000099", "steam_api_key": "KEY"})
                client.post("/api/sync/steam")
                res = await app.state.services.platform_sync.process_next_job("worker-1")
                self.assertEqual(res.state, "failed")
                self.assertEqual(res.last_error_code, "outbound_sync_disabled")

                # Manual library stays completely operational offline
                g = client.post(
                    "/api/games",
                    json={"title": "Mario Kart 8 Deluxe", "platform": "Nintendo Switch"},
                )
                self.assertEqual(g.status_code, 201)
                self.assertEqual(len(client.get("/api/games").json()), 1)
