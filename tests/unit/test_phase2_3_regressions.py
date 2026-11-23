"""Regression tests proving and guarding Phase 2 & 3 + GOG edge-case fixes."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.activity.service import ActivityService
from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.service import IdentityService
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.platforms.base import PlatformAdapterError
from vertex.integrations.platforms.gog import GOGAdapter
from vertex.integrations.platforms.sync_service import PlatformSyncService
from vertex.integrations.platforms.telemetry import TelemetryService
from vertex.integrations.platforms.xbox import XboxAdapter
from vertex.library.service import LibraryService
from vertex.main import create_app


class TestPhase2And3Regressions(unittest.IsolatedAsyncioTestCase):
    def _make_stack(self, tmp_path: Path, handler, *, xbl_api_key: str = ""):
        cfg = Settings(
            data_dir=tmp_path,
            migrations_dir=MIGRATIONS_DIR,
            frontend_dist_dir=tmp_path / "dist",
            secret_key="regression-test-secret-key-0001",
            xbl_api_key=xbl_api_key,
            worker_enabled=False,
            telemetry_flush_delay_seconds=0.0,
        )
        mgr = DatabaseManager(cfg)
        mgr.ensure_system_ready()
        jq = JobQueue(mgr)
        ident = IdentityService(mgr, SecretBox(cfg.secret_key), jq)
        lib = LibraryService(mgr)
        act = ActivityService(mgr)
        cat = CatalogService(mgr, outbound_sync_enabled=True)
        client_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sync_svc = PlatformSyncService(
            db_manager=mgr,
            job_queue=jq,
            identity_service=ident,
            catalog_service=cat,
            library_service=lib,
            activity_service=act,
            client_factory=client_factory,
        )
        tel_svc = TelemetryService(
            db_manager=mgr,
            identity_service=ident,
            library_service=lib,
            activity_service=act,
            xbox_adapter=sync_svc.xbox_adapter,
            gog_adapter=sync_svc.gog_adapter,
            client_factory=client_factory,
            flush_delay_seconds=0.0,
        )
        return cfg, mgr, jq, ident, lib, act, sync_svc, tel_svc

    async def test_xbox_daily_sweep_filters_system_apps_and_old_titles_and_uses_title_history(self):
        now = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
        recent_iso = (now - timedelta(hours=2)).isoformat()
        stale_iso = (now - timedelta(hours=48)).isoformat()
        requested_stats_tids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/v2/achievements/player/2533274000001":
                return httpx.Response(
                    200,
                    json={
                        "titles": [
                            {
                                "titleId": "750323071",
                                "name": "Xbox Home",
                                "titleHistory": {"lastTimePlayed": recent_iso},
                            },
                            {
                                "titleId": "9001",
                                "name": "Old Game From Months Ago",
                                "titleHistory": {"lastTimePlayed": stale_iso},
                            },
                            {
                                "titleId": "9002",
                                "name": "Recent Handheld Game",
                                "titleHistory": {"lastTimePlayed": recent_iso},
                            },
                        ]
                    },
                )
            if path.endswith("/v2/player/stats"):
                import json

                body = json.loads(request.content.decode("utf-8"))
                for st in body.get("stats", []):
                    requested_stats_tids.append(str(st.get("titleId")))
                return httpx.Response(
                    200,
                    json={
                        "statlistscollection": [
                            {
                                "stats": [
                                    {"titleid": "750323071", "name": "MinutesPlayed", "value": "999"},
                                    {"titleid": "9001", "name": "MinutesPlayed", "value": "500"},
                                    {"titleid": "9002", "name": "MinutesPlayed", "value": "60"},
                                ]
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, lib, _, _, tel = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            ident.update_connections(scope, xbox_xuid="2533274000001", xbl_api_key="xbl-key")

            res = await tel.run_daily_sweep_for_user(scope, now=now)
            self.assertEqual(res["titles_checked"], 1)
            self.assertEqual(requested_stats_tids, ["9002"])
            games = lib.list_games(scope)
            self.assertEqual([g.external_id for g in games], ["xbox:9002"])
            self.assertEqual(games[0].last_played, recent_iso)

    async def test_telemetry_close_prevents_double_count_even_with_earlier_last_time_played_and_deletion(self):
        t0 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
        t_exit_poll = datetime(2026, 10, 15, 18, 30, tzinfo=timezone.utc)
        earlier_last_played = datetime(2026, 10, 15, 18, 22, tzinfo=timezone.utc).isoformat()
        state = {"active": True, "minutes": 100}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "/v2/presence" in path:
                if state["active"]:
                    return httpx.Response(
                        200,
                        json={"state": "Online", "devices": [{"titles": [{"id": "1001", "name": "Halo", "state": "Active"}]}]},
                    )
                return httpx.Response(200, json={"state": "Offline"})
            if path.endswith("/v2/player/stats"):
                return httpx.Response(
                    200,
                    json={"statlistscollection": [{"stats": [{"titleid": "1001", "name": "MinutesPlayed", "value": str(state["minutes"])}]}]},
                )
            if "/v2/achievements/player/2533274000001/1001" in path:
                return httpx.Response(200, json={"achievements": []})
            if path == "/v2/achievements/player/2533274000001":
                return httpx.Response(
                    200,
                    json={
                        "titles": [
                            {
                                "titleId": "1001",
                                "name": "Halo",
                                "titleHistory": {"lastTimePlayed": earlier_last_played},
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, _, act, _, tel = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            ident.update_connections(scope, xbox_xuid="2533274000001", xbl_api_key="xbl-key")

            await tel.poll_user(scope, now=t0, sleep_before_flush=False)
            state["active"] = False
            state["minutes"] = 125
            await tel.poll_user(scope, now=t_exit_poll, sleep_before_flush=False)

            sessions = act.list_sessions(scope)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].duration_minutes, 25)

            # Deleting the telemetry session right after close MUST NOT cause sweep to resurrect it!
            act.delete_session(scope, sessions[0].id)
            sweep1 = await tel.run_daily_sweep_for_user(
                scope, now=datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(sweep1["reconciled_count"], 0)
            self.assertEqual(len(act.list_sessions(scope)), 0)

    async def test_telemetry_wall_clock_fallback_plus_earlier_last_time_played_no_double_count(self):
        t0 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
        t_exit_poll = datetime(2026, 10, 15, 18, 30, tzinfo=timezone.utc)
        earlier_last_played = datetime(2026, 10, 15, 18, 22, tzinfo=timezone.utc).isoformat()
        state = {"active": True, "minutes": 100}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "/v2/presence" in path:
                if state["active"]:
                    return httpx.Response(
                        200,
                        json={"state": "Online", "devices": [{"titles": [{"id": "1001", "name": "Halo", "state": "Active"}]}]},
                    )
                return httpx.Response(200, json={"state": "Offline"})
            if path.endswith("/v2/player/stats"):
                return httpx.Response(
                    200,
                    json={"statlistscollection": [{"stats": [{"titleid": "1001", "name": "MinutesPlayed", "value": str(state["minutes"])}]}]},
                )
            if "/v2/achievements/player/2533274000001/1001" in path:
                return httpx.Response(200, json={"achievements": []})
            if path == "/v2/achievements/player/2533274000001":
                return httpx.Response(
                    200,
                    json={
                        "titles": [
                            {
                                "titleId": "1001",
                                "name": "Halo",
                                "titleHistory": {"lastTimePlayed": earlier_last_played},
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, _, act, _, tel = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            ident.update_connections(scope, xbox_xuid="2533274000001", xbl_api_key="xbl-key")

            # Start at 18:00, exit at 18:30 while cloud still lags at 100 (wall-clock fallback = 30m exact session)
            await tel.poll_user(scope, now=t0, sleep_before_flush=False)
            state["active"] = False
            await tel.poll_user(scope, now=t_exit_poll, sleep_before_flush=False)
            sessions = act.list_sessions(scope)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].duration_minutes, 30)

            # Later cloud flushes 125m with titleHistory.lastTimePlayed = 18:22 (inside [18:00, 18:30]):
            # MUST recognize exact session and NOT double-count!
            state["minutes"] = 125
            sweep = await tel.run_daily_sweep_for_user(
                scope, now=datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(sweep["reconciled_count"], 0)
            self.assertEqual(len(act.list_sessions(scope)), 1)

    async def test_gog_json_bundle_and_code_url_in_access_token_field(self):
        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "auth.gog.com" and path == "/token":
                if request.url.params.get("grant_type") == "authorization_code":
                    self.assertEqual(request.url.params.get("code"), "AUTH999")
                    return httpx.Response(
                        200,
                        json={"access_token": "exchanged-acc", "refresh_token": "exchanged-ref", "user_id": "555"},
                    )
            if host == "embed.gog.com" and path == "/userData.json":
                return httpx.Response(200, json={"userId": "555", "username": "Geralt"})
            if host == "embed.gog.com" and path == "/account/getFilteredProducts":
                if request.headers.get("Authorization") == "Bearer exchanged-acc" or request.headers.get("Authorization") == "Bearer acc1":
                    return httpx.Response(200, json={"totalPages": 1, "products": [{"id": 101, "title": "Witcher"}]})
                return httpx.Response(401)
            if host == "gameplay.gog.com" and "/sessions" in path:
                return httpx.Response(200, json={"time_sum": 45})
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, lib, _, sync_svc, _ = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()

            # 1. JSON bundle pasted into gog_access_token
            ident.update_connections(
                scope,
                gog_access_token='{"access_token": "acc1", "refresh_token": "ref1", "user_id": "555"}',
            )
            sync_svc.enqueue_gog_sync(scope)
            job1 = await sync_svc.process_next_job("w1")
            self.assertEqual(job1.state, "succeeded", job1)

            # 2. OAuth code redirect URL pasted into gog_access_token
            ident.update_connections(
                scope,
                gog_access_token="https://embed.gog.com/on_login_success?origin=client&code=AUTH999",
            )
            sync_svc.enqueue_gog_sync(scope)
            job2 = await sync_svc.process_next_job("w1")
            self.assertEqual(job2.state, "succeeded", job2)
            creds = ident.get_decrypted_connections(scope)
            self.assertEqual(creds.gog_access_token, "exchanged-acc")
            self.assertEqual(creds.gog_refresh_token, "exchanged-ref")

    async def test_xbox_personal_key_without_gamertag_or_xuid_syncs_and_polls(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/v2/account"):
                return httpx.Response(
                    200,
                    json={"profileUsers": [{"id": "2533274000099", "settings": [{"id": "Gamertag", "value": "SoloKey"}]}]},
                )
            if path.endswith("/v2/achievements") or "/v2/achievements/player/" in path:
                return httpx.Response(
                    200,
                    json={"titles": [{"titleId": "5001", "name": "Forza", "devices": ["XboxSeries"]}]},
                )
            if path.endswith("/titles"):
                return httpx.Response(200, json={"titles": []})
            if path.endswith("/v2/player/stats"):
                return httpx.Response(200, json={"statlistscollection": []})
            if "/v2/presence" in path:
                return httpx.Response(200, json={"state": "Offline"})
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, lib, _, sync_svc, tel = self._make_stack(
                Path(tmp), handler, xbl_api_key="SERVER-KEY"
            )
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            ident.update_connections(scope, xbl_api_key="PERSONAL-XBL-KEY")

            creds = ident.get_decrypted_connections(scope)
            self.assertIn("xbox", sync_svc.configured_providers(creds))
            sync_svc.enqueue_xbox_sync(scope)
            job = await sync_svc.process_next_job("w1")
            self.assertEqual(job.state, "succeeded", job)
            self.assertEqual(len(lib.list_games(scope)), 1)
            self.assertTrue(tel.get_status(scope)["providers"]["xbox"])
            poll_res = await tel.poll_user(scope, sleep_before_flush=False)
            self.assertEqual(poll_res["status"], "ok")

    async def test_gog_last_session_dict_genres_and_auth_error_propagation(self):
        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "www.gog.com" and "/games/stats" in path:
                return httpx.Response(
                    200,
                    json={
                        "_embedded": {
                            "items": [
                                {
                                    "game": {"id": "101", "title": "Witcher"},
                                    "stats": {"u1": {"playtime": 60, "lastSession": "2026-10-12T18:00:00Z"}},
                                }
                            ]
                        }
                    },
                )
            if host == "embed.gog.com" and path == "/account/getFilteredProducts":
                return httpx.Response(200, json={"products": []})
            if host == "embed.gog.com" and path == "/user/data/games":
                return httpx.Response(200, json={"owned": [202]})
            if host == "api.gog.com" and path == "/products":
                return httpx.Response(
                    200,
                    json=[{"id": 202, "title": "Baldur's Gate", "genres": [{"name": "RPG"}]}],
                )
            if host == "gameplay.gog.com" and "/sessions" in path:
                return httpx.Response(401)
            return httpx.Response(404)

        adapter = GOGAdapter()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            stats, _ = await adapter.fetch_public_profile_stats(username="u1", user_id="u1", client=client)
            self.assertEqual(stats["101"]["last_played"], "2026-10-12T18:00:00+00:00")

            with self.assertRaises(PlatformAdapterError) as ctx:
                await adapter.fetch_library_detailed(
                    access_token="expired-tok",
                    user_id="u1",
                    client=client,
                )
            self.assertEqual(ctx.exception.code, "auth_failed")

    async def test_xbox_verify_connection_does_not_leak_personal_key_in_gt_query(self):
        seen_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(str(request.url))
            if request.url.path == "/v2/account":
                return httpx.Response(
                    200,
                    json={"profileUsers": [{"id": "2533274", "settings": [{"id": "Gamertag", "value": "Owner"}]}]},
                )
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, _, _, sync_svc, _ = self._make_stack(
                Path(tmp), handler, xbl_api_key="SERVER-KEY"
            )
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            res = await sync_svc.verify_connection(
                scope,
                provider="xbox",
                credential="MY-SECRET-KEY-XYZ",
                secondary_credential="MY-SECRET-KEY-XYZ",
            )
            self.assertEqual(res["status"], "ok")
            self.assertFalse(any("MY-SECRET-KEY-XYZ" in u for u in seen_paths), seen_paths)

    def test_homeassistant_sync_network_error_returns_502_not_500(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = Settings(
                data_dir=tmp_path,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=tmp_path / "dist",
                secret_key="ha-err-secret-key-0001",
                worker_enabled=False,
            )
            app = create_app(
                cfg,
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            )
            with TestClient(app) as client:
                client.post("/api/auth/bootstrap", json={"username": "alice", "password": "AlicePassword!1"})
                res = client.post(
                    "/api/integrations/homeassistant/sync",
                    json={"ha_url": "http://ha.local:8123", "ha_token": "tok", "entity_id": "sensor.x"},
                )
                self.assertEqual(res.status_code, 502)

    async def test_xbox_verify_connection_surfaces_server_key_rate_limit_or_auth_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/v2/friends/search" in request.url.path:
                return httpx.Response(429, headers={"Retry-After": "30"})
            return httpx.Response(401)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, _, _, sync_svc, _ = self._make_stack(
                Path(tmp), handler, xbl_api_key="SERVER-KEY"
            )
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            res = await sync_svc.verify_connection(
                scope,
                provider="xbox",
                credential="Chief117",
            )
            self.assertEqual(res["status"], "error")
            self.assertIn("rate limited", res["message"].lower())

    async def test_xbox_and_gog_429_rate_limit_retry_backoff_and_username_only_gog(self):
        attempts = {"xbox": 0, "gog": 0}
        mode = {"private_gog": False}

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if "xbl.io" in host:
                if "/v2/account" in path:
                    return httpx.Response(200, json={"profileUsers": [{"id": "2533274000011"}]})
                if "/v2/achievements" in path:
                    attempts["xbox"] += 1
                    if attempts["xbox"] == 1:
                        return httpx.Response(429, headers={"Retry-After": "15"})
                    return httpx.Response(200, json={"titles": [{"titleId": "777", "name": "Gears 5"}]})
                if "/v2/titles" in path:
                    return httpx.Response(200, json={"titles": []})
                if path.endswith("/v2/player/stats"):
                    return httpx.Response(200, json={"statlistscollection": []})
            if host == "www.gog.com" and "/games/stats" in path:
                attempts["gog"] += 1
                if attempts["gog"] == 1:
                    return httpx.Response(429, headers={"Retry-After": "10"})
                if mode["private_gog"]:
                    return httpx.Response(403)
                return httpx.Response(
                    200,
                    json={
                        "_embedded": {
                            "items": [
                                {
                                    "game": {"id": "333", "title": "Hollow Knight"},
                                    "stats": {"u1": {"playtime": 120, "lastSession": "2026-10-12T18:00:00Z"}},
                                }
                            ]
                        }
                    },
                )
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, jq, ident, lib, _, sync_svc, _ = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            ident.update_connections(scope, xbl_api_key="PERSONAL-KEY", gog_username="u1")

            # Enqueue both -> 429 puts them back to queued with rate_limited & future available_at
            xb_job = sync_svc.enqueue_xbox_sync(scope)
            gog_job = sync_svc.enqueue_gog_sync(scope)

            j1 = await sync_svc.process_next_job("w1")
            self.assertEqual(j1.state, "queued")
            self.assertEqual(j1.last_error_code, "rate_limited")

            j2 = await sync_svc.process_next_job("w1")
            self.assertEqual(j2.state, "queued")
            self.assertEqual(j2.last_error_code, "rate_limited")

            # Make jobs immediately runnable again and clear provider rate limits to simulate time passing
            with sync_svc.db_manager.system_connection(immediate=True) as conn:
                conn.execute("UPDATE jobs SET available_at = '2020-01-01T00:00:00+00:00'")
                conn.execute("DELETE FROM provider_rate_limits")

            r1 = await sync_svc.process_next_job("w1")
            r2 = await sync_svc.process_next_job("w1")
            self.assertEqual({r1.state, r2.state}, {"succeeded"})
            games = {g.external_id: g for g in lib.list_games(scope)}
            self.assertIn("xbox:777", games)
            self.assertIn("gog:333", games)
            self.assertEqual(games["gog:333"].playtime_minutes, 120)

    def test_delta_zero_does_not_regress_provider_baseline_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, mgr, _, ident, lib, act, _, _ = self._make_stack(Path(tmp), lambda r: httpx.Response(404))
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            g, _ = lib.apply_platform_snapshot(
                scope,
                external_id="xbox:1",
                title="Halo",
                platform="Xbox",
                source="Xbox",
                playtime_minutes=100,
            )
            act.record_cumulative_observation(
                scope,
                game_id=g.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=100,
                observed_at="2026-10-15T18:30:00+00:00",
                idempotency_key="k1",
            )
            act.record_cumulative_observation(
                scope,
                game_id=g.id,
                source="Xbox",
                platform="Xbox",
                observed_minutes=100,
                observed_at="2026-10-15T18:10:00+00:00",
                idempotency_key="k2",
            )
            with mgr.library_connection(scope) as conn:
                row = conn.execute(
                    "SELECT last_observed_at FROM provider_baselines WHERE game_id = ? AND source = 'Xbox'",
                    (g.id,),
                ).fetchone()
                self.assertEqual(row["last_observed_at"], "2026-10-15T18:30:00+00:00")

    async def test_rta_manager_adds_and_removes_clients_dynamically(self):
        from vertex.integrations.platforms.telemetry import XboxRtaManager

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, mgr, _, ident, _, _, _, tel = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()

            rta_mgr = XboxRtaManager(identity_service=ident, telemetry_service=tel)
            with patch.object(rta_mgr, "_spawn_task") as mock_spawn:
                fake_task = MagicMock()
                mock_spawn.return_value = fake_task

                # Initially no watcher key -> 0 tasks
                await rta_mgr.reconcile()
                self.assertEqual(len(rta_mgr.active_clients), 0)

                # Add watcher key + xuid -> starts 1 task
                ident.update_connections(
                    scope,
                    xbox_xuid="2533274000001",
                    xbl_watcher_api_key="watcher-key-1",
                )
                await rta_mgr.reconcile()
                self.assertIn(scope.user_id, rta_mgr.active_clients)

                # Clear watcher key -> stops task cleanly
                ident.update_connections(scope, clear_xbl_watcher_api_key=True)
                await rta_mgr.reconcile()
                self.assertNotIn(scope.user_id, rta_mgr.active_clients)
                fake_task.cancel.assert_called_once()

    # Regression (2026-11-23): Xbox telemetry closing a session + advancing baseline must still emit playtime nudge
    async def test_xbox_telemetry_session_on_locked_plan_to_play_creates_playtime_prompt(self):
        t0 = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=5)
        t_exit_poll = t0 + timedelta(minutes=39)
        state = {"active": True, "minutes": 1}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "/v2/presence" in path:
                if state["active"]:
                    return httpx.Response(
                        200,
                        json={
                            "state": "Online",
                            "devices": [{"titles": [{"id": "2087575977", "name": "MOUSE: P.I. For Hire", "state": "Active"}]}],
                        },
                    )
                return httpx.Response(200, json={"state": "Offline"})
            if path.endswith("/v2/player/stats"):
                return httpx.Response(
                    200,
                    json={
                        "statlistscollection": [
                            {"stats": [{"titleid": "2087575977", "name": "MinutesPlayed", "value": str(state["minutes"])}]}
                        ]
                    },
                )
            if "/v2/achievements/player/2533274000001/2087575977" in path:
                return httpx.Response(200, json={"achievements": []})
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, ident, lib, act, _, tel = self._make_stack(Path(tmp), handler)
            acct, _ = ident.bootstrap_admin(username="alice", password="AlicePassword!1")
            scope = acct.to_scope()
            ident.update_connections(scope, xbox_xuid="2533274000001", xbl_api_key="xbl-key")

            w_copy = lib.add_manual_game(
                scope,
                title="Mouse P.I. For Hire",
                platform="Xbox",
                completion_status="Plan to Play",
                status_locked=True,
                playtime_hours=0.0,
            )
            x_copy, _ = lib.apply_platform_snapshot(
                scope,
                external_id="xbox:2087575977",
                title="MOUSE: P.I. For Hire",
                platform="Xbox / PC",
                source="Xbox",
                playtime_minutes=1,
            )
            canon_id = lib.merge_games(scope, [w_copy.id, x_copy.id])
            lib.update_game(scope, x_copy.id, completion_status="Plan to Play")

            await tel.poll_user(scope, now=t0, sleep_before_flush=False)
            state["active"] = False
            state["minutes"] = 40
            await tel.poll_user(scope, now=t_exit_poll, sleep_before_flush=False)

            prompts = act.list_playtime_prompts(scope)
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0].game_id, canon_id)
            self.assertEqual(prompts[0].prompt_type, "playtime")
            self.assertEqual(prompts[0].current_status, "Plan to Play")

            # Follow-up scheduled sync sees baseline=40 (no_change) and must preserve the pending prompt
            follow = act.record_cumulative_observation(
                scope,
                game_id=x_copy.id,
                source="Xbox",
                platform="Xbox / PC",
                observed_minutes=40,
                observed_at=(t_exit_poll + timedelta(minutes=30)).isoformat(),
                idempotency_key="xbox_sync:follow_up:40",
            )
            self.assertEqual(follow.status, "no_change")
            prompts_after = act.list_playtime_prompts(scope)
            self.assertEqual(len(prompts_after), 1)
            self.assertEqual(prompts_after[0].prompt_type, "playtime")


if __name__ == "__main__":
    unittest.main()
