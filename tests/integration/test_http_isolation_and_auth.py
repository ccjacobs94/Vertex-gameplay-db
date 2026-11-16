"""Integration tests for FastAPI HTTP auth, bootstrap, invites, isolation, CSRF, and admin guardrails."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.config import Settings
from vertex.main import create_app


class TestHttpIsolationAndAuth(unittest.TestCase):
    def _make_client(self, tmp_path: Path, **overrides) -> TestClient:
        cfg_kwargs = {
            "data_dir": tmp_path,
            "migrations_dir": MIGRATIONS_DIR,
            "frontend_dist_dir": tmp_path / "dist",
            "secret_key": "http-test-secret-key-1234",
            "worker_enabled": False,
            "login_max_attempts": 3,
            "login_window_seconds": 60,
        }
        cfg_kwargs.update(overrides)
        app = create_app(Settings(**cfg_kwargs))
        return TestClient(app)

    def test_bootstrap_invites_two_user_isolation_and_logout_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            alice_client = self._make_client(Path(tmp))
            bob_client = TestClient(alice_client.app)

            # Public health endpoints open; private API always requires auth
            self.assertEqual(alice_client.get("/healthz").status_code, 200)
            self.assertEqual(alice_client.get("/readyz").status_code, 200)
            self.assertEqual(alice_client.get("/api/games").status_code, 401)
            self.assertEqual(alice_client.get("/api/auth/bootstrap").json()["bootstrap_open"], True)

            # First admin bootstrap succeeds & closes setup permanently
            r_boot = alice_client.post(
                "/api/auth/bootstrap",
                json={"username": "alice", "password": "AlicePassword!1"},
            )
            self.assertEqual(r_boot.status_code, 201)
            self.assertEqual(r_boot.json()["account"]["role"], "admin")
            self.assertIn("no-store", r_boot.headers.get("cache-control", ""))

            self.assertEqual(alice_client.get("/api/auth/bootstrap").json()["bootstrap_open"], False)
            r_boot2 = bob_client.post(
                "/api/auth/bootstrap",
                json={"username": "attacker", "password": "AttackPassword!1"},
            )
            self.assertEqual(r_boot2.status_code, 409)

            # Admin issues single-use expiring invite -> Bob accepts; replay blocked
            inv_resp = alice_client.post("/api/admin/invites", json={"role": "member", "note": "Bob"})
            self.assertEqual(inv_resp.status_code, 201)
            invite_code = inv_resp.json()["code"]

            r_acc = bob_client.post(
                "/api/auth/invites/accept",
                json={"invite_code": invite_code, "username": "bob", "password": "BobPassword!1"},
            )
            self.assertEqual(r_acc.status_code, 201)
            self.assertEqual(r_acc.json()["account"]["role"], "member")

            r_replay = TestClient(alice_client.app).post(
                "/api/auth/invites/accept",
                json={"invite_code": invite_code, "username": "eve", "password": "EvePassword!1"},
            )
            self.assertEqual(r_replay.status_code, 401)

            # Alice adds game & encrypted Steam connection; Bob creates own game
            ag = alice_client.post(
                "/api/games",
                json={"title": "Alice Secret Game", "platform": "PC (Steam)", "notes": "Private A"},
            )
            self.assertEqual(ag.status_code, 201)
            alice_gid = ag.json()["id"]
            ag2 = alice_client.post(
                "/api/games",
                json={"title": "Alice Second Secret", "platform": "PC (Steam)"},
            )
            self.assertEqual(ag2.status_code, 201)
            alice_gid2 = ag2.json()["id"]

            alice_client.put(
                "/api/connections",
                json={"steam_id": "76561198000000001", "steam_api_key": "ALICE-KEY"},
            )

            bg = bob_client.post(
                "/api/games",
                json={"title": "Bob Game", "platform": "Nintendo Switch"},
            )
            self.assertEqual(bg.status_code, 201)

            # Strict cross-user isolation (Bob's SQLite DB is completely independent; Alice's id=2 does not exist)
            self.assertEqual(len(bob_client.get("/api/games").json()), 1)
            self.assertEqual(bob_client.get("/api/games").json()[0]["title"], "Bob Game")
            self.assertEqual(bob_client.get(f"/api/games/{alice_gid}").json()["title"], "Bob Game")
            self.assertEqual(bob_client.get(f"/api/games/{alice_gid2}").status_code, 404)
            self.assertEqual(bob_client.patch(f"/api/games/{alice_gid2}", json={"notes": "hack"}).status_code, 404)
            self.assertEqual(bob_client.delete(f"/api/games/{alice_gid2}").status_code, 404)
            self.assertIsNone(bob_client.get("/api/connections").json()["steam_id"])
            self.assertEqual(bob_client.get("/api/admin/users").status_code, 403)

            # Shared browser account-switch: A logs out -> B logs in -> A's data completely inaccessible
            shared_browser = TestClient(alice_client.app)
            self.assertEqual(
                shared_browser.post("/api/auth/login", json={"username": "alice", "password": "AlicePassword!1"}).status_code,
                200,
            )
            self.assertEqual(len(shared_browser.get("/api/games").json()), 2)
            self.assertEqual(shared_browser.post("/api/auth/logout").status_code, 204)
            self.assertEqual(shared_browser.get("/api/games").status_code, 401)

            self.assertEqual(
                shared_browser.post("/api/auth/login", json={"username": "bob", "password": "BobPassword!1"}).status_code,
                200,
            )
            b_games = shared_browser.get("/api/games").json()
            self.assertEqual([g["title"] for g in b_games], ["Bob Game"])
            self.assertEqual(shared_browser.get(f"/api/games/{alice_gid2}").status_code, 404)

    def test_last_admin_guardrails_suspension_revocation_pat_scopes_csrf_and_throttling(self):
        with tempfile.TemporaryDirectory() as tmp:
            alice = self._make_client(Path(tmp))
            alice.post("/api/auth/bootstrap", json={"username": "alice", "password": "AlicePassword!1"})
            alice.post("/api/admin/users", json={"username": "bob", "password": "BobPassword!1", "role": "member"})

            # Last active admin cannot demote, suspend, or delete self
            self.assertEqual(alice.patch("/api/admin/users/alice", json={"role": "member"}).status_code, 422)
            self.assertEqual(alice.patch("/api/admin/users/alice", json={"status": "suspended"}).status_code, 422)
            self.assertEqual(alice.delete("/api/admin/users/alice").status_code, 422)

            # Bob logs in and mints a read-only PAT
            bob = TestClient(alice.app)
            bob.post("/api/auth/login", json={"username": "bob", "password": "BobPassword!1"})
            pat_resp = bob.post("/api/account/tokens", json={"name": "readonly", "scopes": ["read:library"]})
            self.assertEqual(pat_resp.status_code, 201)
            raw_pat = pat_resp.json()["token"]

            # PAT read scope succeeds for GET, blocked for write or admin
            anon = TestClient(alice.app)
            self.assertEqual(anon.get("/api/games", headers={"Authorization": f"Bearer {raw_pat}"}).status_code, 200)
            self.assertEqual(
                anon.post(
                    "/api/games",
                    json={"title": "Blocked", "platform": "PC"},
                    headers={"Authorization": f"Bearer {raw_pat}"},
                ).status_code,
                403,
            )

            # Cross-origin cookie POST blocked with 403; disallowed Host blocked with 400
            self.assertEqual(
                bob.post(
                    "/api/games",
                    json={"title": "CSRF", "platform": "PC"},
                    headers={"Sec-Fetch-Site": "cross-site"},
                ).status_code,
                403,
            )
            self.assertEqual(
                bob.post(
                    "/api/games",
                    json={"title": "CSRF Origin", "platform": "PC"},
                    headers={"Origin": "https://evil.invalid"},
                ).status_code,
                403,
            )
            self.assertEqual(bob.get("/api/games", headers={"Host": "evil.invalid"}).status_code, 400)

            # Suspending Bob immediately revokes both Bob's session AND PAT
            susp = alice.patch("/api/admin/users/bob", json={"status": "suspended", "status_reason": "left"})
            self.assertEqual(susp.status_code, 200)
            self.assertEqual(bob.get("/api/games").status_code, 401)
            self.assertEqual(anon.get("/api/games", headers={"Authorization": f"Bearer {raw_pat}"}).status_code, 401)

            # Login rate limiter blocks after 3 failed attempts
            brute = TestClient(alice.app)
            for _ in range(3):
                r = brute.post("/api/auth/login", json={"username": "alice", "password": "WrongPassword!"})
                self.assertEqual(r.status_code, 401)
            r_throttled = brute.post("/api/auth/login", json={"username": "alice", "password": "WrongPassword!"})
            self.assertEqual(r_throttled.status_code, 429)

            # Hosted billing/signup routes do not exist
            self.assertEqual(alice.get("/billing").status_code, 404)
            self.assertEqual(alice.get("/signup").status_code, 404)

    def test_cidr_trusted_proxies_host_validation_and_naive_expiry_handling(self):
        from starlette.requests import Request
        from vertex.interfaces.http.deps import resolve_client_ip, validate_request_host_and_csrf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                data_dir=root,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=root / "dist",
                secret_key="cidr-test-secret-key",
                allowed_hosts=("games.home.arpa", "testserver"),
                trusted_proxies=("172.18.0.0/16",),
                worker_enabled=False,
            )
            proxied_req = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/games",
                    "headers": [
                        (b"host", b"vertex:8090"),
                        (b"x-forwarded-host", b"games.home.arpa"),
                        (b"x-forwarded-for", b"100.64.2.15, 172.18.0.2"),
                    ],
                    "client": ("172.18.0.2", 54321),
                }
            )
            self.assertEqual(resolve_client_ip(proxied_req, cfg), "100.64.2.15")
            validate_request_host_and_csrf(proxied_req, cfg)

            # Empty Host header is rejected
            client = self._make_client(root)
            self.assertEqual(client.get("/api/games", headers={"Host": ""}).status_code, 400)

            # Naive expires_at on invite works without TypeError, and malformed import JSON returns 422
            client.post("/api/auth/bootstrap", json={"username": "alice", "password": "AlicePassword!1"})
            inv = client.post("/api/admin/invites", json={"role": "member"}).json()
            with client.app.state.services.db_manager.system_connection(immediate=True) as conn:
                conn.execute(
                    "UPDATE invites SET expires_at = '2030-01-01T00:00:00' WHERE code_hash = ?",
                    (inv["record"]["code_hash"],),
                )
            acc = client.post(
                "/api/auth/invites/accept",
                json={"invite_code": inv["code"], "username": "bob", "password": "BobPassword!1"},
            )
            self.assertEqual(acc.status_code, 201)

            bad_imp = client.post(
                "/api/portability/import/json",
                json={
                    "payload": {
                        "format_version": 1,
                        "games": [{"external_id": "m:1", "title": "X", "platform": "PC"}],
                        "play_sessions": [{"game_external_id": "m:1", "duration_minutes": 10}],
                    },
                    "mode": "merge",
                    "dry_run": True,
                },
            )
            self.assertEqual(bad_imp.status_code, 422)
