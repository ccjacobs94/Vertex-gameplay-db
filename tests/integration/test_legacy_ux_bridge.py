"""Integration tests for resurrected legacy UI serving, X-Vertex-UI rewrite, and compat routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR
from vertex.config import Settings
from vertex.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_HDR = {"X-Vertex-UI": "1"}


class TestLegacyUxBridge(unittest.TestCase):
    def _make_app(self, tmp_path: Path):
        return create_app(
            Settings(
                data_dir=tmp_path,
                migrations_dir=MIGRATIONS_DIR,
                frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
                secret_key="legacy-ux-bridge-secret-key-123",
                worker_enabled=False,
                outbound_sync_enabled=False,
            )
        )

    def test_serves_original_ux_assets_and_openapi_stays_pure(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(Path(tmp))
            with TestClient(app) as client:
                r_index = client.get("/")
                self.assertEqual(r_index.status_code, 200)
                self.assertIn("text/html", r_index.headers.get("content-type", ""))
                self.assertIn("Vertex", r_index.text)
                self.assertIn("setting-gog-username", r_index.text)
                self.assertIn('data-platform="GOG"', r_index.text)
                self.assertIn("/static/styles.css?v=67", r_index.text)
                self.assertIn("/static/app.js?v=67", r_index.text)

                r_app_js = client.get("/static/app.js?v=67")
                self.assertEqual(r_app_js.status_code, 200)
                self.assertIn("X-Vertex-UI", r_app_js.text)
                self.assertIn("no-cache", r_app_js.headers.get("cache-control", ""))

                r_sw = client.get("/sw.js")
                self.assertEqual(r_sw.status_code, 200)
                self.assertIn("vertex-pwa-v67", r_sw.text)

                r_manifest = client.get("/manifest.webmanifest")
                self.assertEqual(r_manifest.status_code, 200)

                openapi_paths = set(app.openapi().get("paths", {}).keys())
                self.assertFalse(any(p.startswith("/api/ui/") for p in openapi_paths))
                self.assertNotIn("/login", openapi_paths)
                self.assertNotIn("/api/user/connections", openapi_paths)

    def test_login_and_legacy_ux_api_roundtrip_including_gog_and_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            app = self._make_app(tmp_path)
            with TestClient(app) as client:
                # 1. GET /login renders bootstrap hint on empty instance
                r_login_get = client.get("/login")
                self.assertEqual(r_login_get.status_code, 200)
                self.assertIn("Create Admin", r_login_get.text)

                # 2. POST /login bootstraps admin account and sets session cookie
                r_login_post = client.post(
                    "/login",
                    data={"username": "alex99", "password": "StrongPassword!99", "next": "/"},
                    follow_redirects=False,
                )
                self.assertEqual(r_login_post.status_code, 303)
                self.assertEqual(r_login_post.headers["location"], "/")

                # 3. Direct /api/user/connections including GOG masking + onboarding_completed
                r_conn_set = client.post(
                    "/api/user/connections",
                    json={
                        "steam_id": "76561198000000001",
                        "gog_username": "alex_gog",
                        "gog_access_token": "super-secret-gog-token-value",
                    },
                )
                self.assertEqual(r_conn_set.status_code, 200)
                conn_data = client.get("/api/user/connections").json()
                self.assertEqual(conn_data["steam_id"], "76561198000000001")
                self.assertEqual(conn_data["gog_username"], "alex_gog")
                self.assertTrue(conn_data["has_gog_token"])
                self.assertFalse(conn_data["onboarding_completed"])
                self.assertNotIn("super-secret-gog-token-value", json.dumps(conn_data))

                # 4. Complete onboarding
                r_onb = client.post("/api/user/onboarding/complete")
                self.assertEqual(r_onb.status_code, 200)
                self.assertTrue(client.get("/api/user/connections").json()["onboarding_completed"])

                # 5. Games CRUD + achievements + analytics via X-Vertex-UI header
                r_add_1 = client.post(
                    "/api/games",
                    headers=UI_HDR,
                    json={
                        "title": "Cyberpunk 2077",
                        "platform": "PC (GOG)",
                        "completion_status": "Playing",
                        "playtime_hours": 14.5,
                        "genres": ["Action RPG", "Sci-Fi"],
                        "tags": ["Open World"],
                    },
                )
                self.assertEqual(r_add_1.status_code, 200)
                g1 = r_add_1.json()["game"]

                r_add_2 = client.post(
                    "/api/games",
                    headers=UI_HDR,
                    json={
                        "title": "Cyberpunk 2077",
                        "platform": "Xbox",
                        "completion_status": "Not Played",
                        "playtime_hours": 1.0,
                    },
                )
                self.assertEqual(r_add_2.status_code, 200)
                g2 = r_add_2.json()["game"]

                r_patch = client.patch(
                    f"/api/games/{g1['id']}",
                    headers=UI_HDR,
                    json={"user_rating": 9, "achievements_unlocked": 12, "achievements_total": 44},
                )
                self.assertEqual(r_patch.status_code, 200)
                self.assertEqual(r_patch.json()["game"]["user_rating"], 9)
                self.assertEqual(r_patch.json()["game"]["achievements_unlocked"], 12)

                # Verify bidirectional genre/tag trigger synced legacy writes -> v2 JSON columns
                lib_db = tmp_path / "users" / "alex99" / "library.db"
                with sqlite3.connect(lib_db) as raw_conn:
                    row = raw_conn.execute(
                        "SELECT genres, genres_json, tags, tags_json FROM games WHERE id = ?",
                        (g1["id"],),
                    ).fetchone()
                    self.assertEqual(row[0], row[1])
                    self.assertEqual(row[2], row[3])
                    self.assertIn("Action RPG", json.loads(row[1]))

                r_stats = client.get("/api/stats", headers=UI_HDR)
                self.assertEqual(r_stats.status_code, 200)
                stats_data = r_stats.json()
                self.assertEqual(stats_data["total_games"], 2)
                self.assertTrue(stats_data["configured_sources"]["gog"])
                self.assertTrue(stats_data["is_admin"])

                # Same /api/games without X-Vertex-UI header still speaks v2 OpenAPI shape (plain list)
                r_v2_games = client.get("/api/games")
                self.assertEqual(r_v2_games.status_code, 200)
                self.assertIsInstance(r_v2_games.json(), list)

                # With X-Vertex-UI header returns {count, games} legacy dict
                r_ui_games = client.get("/api/games?platform=GOG", headers=UI_HDR)
                self.assertEqual(r_ui_games.status_code, 200)
                self.assertEqual(r_ui_games.json()["count"], 1)
                self.assertEqual(r_ui_games.json()["games"][0]["title"], "Cyberpunk 2077")

                # Analytics habits + sessions endpoints work via X-Vertex-UI
                r_analytics = client.get("/api/analytics", headers=UI_HDR)
                self.assertEqual(r_analytics.status_code, 200)
                self.assertGreater(r_analytics.json()["summary"]["total_hours"], 0)

                # Reconcile confirm -> unlink round-trip
                r_rec = client.post(
                    "/api/reconcile/confirm",
                    headers=UI_HDR,
                    json={
                        "game_ids": [g1["id"], g2["id"]],
                        "primary_id": g1["id"],
                        "custom_title": "Cyberpunk 2077",
                        "completion_status": "Playing",
                    },
                )
                self.assertEqual(r_rec.status_code, 200)
                self.assertEqual(r_rec.json()["game"]["linked_count"], 2)
                self.assertEqual(r_rec.json()["stats"]["total_games"], 1)

                r_unlink = client.post(f"/api/reconcile/unlink/{g2['id']}", headers=UI_HDR)
                self.assertEqual(r_unlink.status_code, 200)
                self.assertEqual(r_unlink.json()["stats"]["total_games"], 2)

                # MCP token list/create/revoke matches legacy keys
                r_tok = client.post("/api/user/mcp/tokens", json={"label": "Claude Desktop"})
                self.assertEqual(r_tok.status_code, 200)
                tok_id = r_tok.json()["access_token"]
                self.assertTrue(r_tok.json()["token"].startswith("vtx_pat_"))
                r_mcp = client.get("/api/user/mcp")
                self.assertEqual(r_mcp.status_code, 200)
                self.assertEqual(r_mcp.json()["tokens"][0]["client_id"], "Claude Desktop")
                self.assertIsInstance(r_mcp.json()["tokens"][0]["created_at"], float)
                self.assertEqual(client.delete(f"/api/user/mcp/tokens/{tok_id}").status_code, 200)

                # Password change returns JSON as expected by saveAccountPassword()
                r_pw = client.post(
                    "/api/account/password",
                    headers=UI_HDR,
                    json={
                        "current_password": "StrongPassword!99",
                        "new_password": "EvenStronger!999",
                    },
                )
                self.assertEqual(r_pw.status_code, 200)
                self.assertEqual(r_pw.json()["status"], "ok")

                # Admin users role ("user"->"member") + status ("inactive"->"suspended") + invites + settings
                r_admin_u = client.post(
                    "/api/admin/users",
                    headers=UI_HDR,
                    json={"username": "player2", "role": "user", "password": None},
                )
                self.assertEqual(r_admin_u.status_code, 200)
                self.assertTrue(r_admin_u.json().get("temporary_password"))
                p2_id = r_admin_u.json()["user"]["id"]
                self.assertEqual(r_admin_u.json()["user"]["role"], "user")

                r_disable = client.patch(
                    f"/api/admin/users/{p2_id}",
                    headers=UI_HDR,
                    json={"status": "inactive", "status_reason": "admin_suspended"},
                )
                self.assertEqual(r_disable.status_code, 200)
                self.assertEqual(r_disable.json()["user"]["status"], "inactive")

                r_set = client.put(
                    "/api/admin/settings",
                    headers=UI_HDR,
                    json={"registration_policy": "invite"},
                )
                self.assertEqual(r_set.status_code, 200)
                self.assertEqual(r_set.json()["registration_policy"], "invite")

                r_inv = client.post(
                    "/api/admin/invites",
                    headers=UI_HDR,
                    json={"ttl_days": 7},
                )
                self.assertEqual(r_inv.status_code, 200)
                self.assertIn("/signup?invite=", r_inv.json()["invite_url"])
                self.assertGreaterEqual(len(client.get("/api/admin/invites", headers=UI_HDR).json()["invites"]), 1)

                # Export library SQLite backup works cleanly
                r_exp = client.get("/api/user/library/export")
                self.assertEqual(r_exp.status_code, 200)
                self.assertTrue(r_exp.content.startswith(b"SQLite format 3\x00"))

    def test_historical_legacy_gs_observations_restore_analytics_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            app = self._make_app(tmp_path)
            now_dt = datetime.now(timezone.utc).replace(microsecond=0)
            hist_end = now_dt - timedelta(days=2)
            hist_start = hist_end - timedelta(minutes=75)
            ach_time = (hist_start + timedelta(minutes=25)).isoformat()

            with TestClient(app) as client:
                r_boot = client.post(
                    "/api/auth/bootstrap",
                    json={"username": "historian1", "password": "StrongPassword!99"},
                )
                self.assertEqual(r_boot.status_code, 201)

                r_game = client.post(
                    "/api/games",
                    json={
                        "title": "Halo Infinite",
                        "platform": "Xbox",
                        "completion_status": "Playing",
                        "playtime_hours": 5.0,
                    },
                )
                self.assertEqual(r_game.status_code, 201)
                gid = r_game.json()["id"]

                # Simulate pre-0004 migrated DB: legacy_gs interval without achievements_earned
                # and without daily_playtime_ledger populated yet.
                lib_db = tmp_path / "users" / "historian1" / "library.db"
                with sqlite3.connect(lib_db) as raw_conn:
                    raw_conn.execute(
                        """
                        INSERT INTO game_achievements (
                            game_id, external_ach_id, source, platform, name, description,
                            unlocked, unlock_time, is_hidden, updated_at
                        ) VALUES (?, 'ach-1', 'Xbox', 'Xbox', 'Spartan', 'Desc', 1, ?, 0, ?)
                        """,
                        (gid, ach_time, now_dt.isoformat()),
                    )
                    raw_conn.execute(
                        """
                        INSERT INTO play_observations (
                            idempotency_key, game_id, source, platform, observation_kind,
                            observed_at, interval_started_at, interval_ended_at, interval_minutes,
                            precision_level, payload_json, created_at
                        ) VALUES (
                            'legacy_gs:42', ?, 'Xbox', 'Xbox', 'interval',
                            ?, ?, ?, 75,
                            'exact', '{"detection_method": "achievement_backfill", "status": "reconciled"}', ?
                        )
                        """,
                        (gid, hist_end.isoformat(), hist_start.isoformat(), hist_end.isoformat(), now_dt.isoformat()),
                    )
                    # Roll back migration 0004 marker & game_sessions to exercise real 0004 upgrade
                    raw_conn.execute("DELETE FROM schema_migrations WHERE version = '0004_legacy_sessions_ledger_fix'")
                    raw_conn.execute("DELETE FROM daily_playtime_ledger")
                    raw_conn.commit()

                # First legacy-UI analytics call applies 0004 backfill, unified view, and ledger rebuild
                r_analytics = client.get("/api/analytics", headers=UI_HDR)
                self.assertEqual(r_analytics.status_code, 200)
                telemetry = r_analytics.json()["session_telemetry"]
                self.assertGreaterEqual(telemetry["total_sessions"], 1)
                self.assertGreaterEqual(telemetry["snack_vs_binge"]["standard_runs"]["count"], 1)
                active_heatmap_days = [
                    d for d in telemetry["heatmap_365"] if (d.get("active_minutes") or 0) >= 75
                ]
                self.assertGreaterEqual(len(active_heatmap_days), 1)
                self.assertEqual(active_heatmap_days[0]["achievements_unlocked"], 1)

                with sqlite3.connect(lib_db) as raw_conn:
                    gs_row = raw_conn.execute(
                        "SELECT id, active_minutes, achievements_earned FROM game_sessions WHERE id = 42"
                    ).fetchone()
                    self.assertIsNotNone(gs_row)
                    self.assertEqual(int(gs_row[1]), 75)
                    self.assertEqual(int(gs_row[2]), 1)

    def test_mobile_contribution_heatmap_layout_and_scroll_behavior(self) -> None:
        # Regression guard for 2026-11-16 mobile heatmap cutoff & wrapped circadian labels.
        css_text = (REPO_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        html_text = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js_text = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        sw_text = (REPO_ROOT / "static" / "sw.js").read_text(encoding="utf-8")

        # 1. CSS braces must stay balanced (catches orphaned blocks like old .habits-panel tail).
        self.assertEqual(css_text.count("{"), css_text.count("}"), "static/styles.css has unbalanced braces")

        # 2. Mobile stacked heatmap column must stretch full width and enable contained horizontal scroll.
        mobile_900_match = re.search(
            r"@media\s*\(max-width:\s*900px\)\s*\{.*?\.heatmap-circadian-layout\s*\{(.*?)\}",
            css_text,
            re.DOTALL,
        )
        self.assertIsNotNone(mobile_900_match)
        self.assertIn("flex-direction: column;", mobile_900_match.group(1))
        self.assertIn("align-items: stretch;", mobile_900_match.group(1))

        side_col_match = re.search(r"\.heatmap-side-col\s*\{(.*?)\}", css_text, re.DOTALL)
        self.assertIsNotNone(side_col_match)
        self.assertIn("inline-size: 100%;", side_col_match.group(1))
        self.assertIn("max-inline-size: 100%;", side_col_match.group(1))

        scroll_box_match = re.search(r"\.heatmap-scroll-container\s*\{(.*?)\}", css_text, re.DOTALL)
        self.assertIsNotNone(scroll_box_match)
        self.assertIn("inline-size: 100%;", scroll_box_match.group(1))
        self.assertIn("max-inline-size: 100%;", scroll_box_match.group(1))
        self.assertIn("overflow-x: auto;", scroll_box_match.group(1))
        self.assertIn("overscroll-behavior-x: contain;", scroll_box_match.group(1))

        days_col_match = re.search(r"\.heatmap-days-col\s*\{(.*?)\}", css_text, re.DOTALL)
        self.assertIsNotNone(days_col_match)
        self.assertIn("position: sticky;", days_col_match.group(1))
        self.assertIn("inset-inline-start: 0;", days_col_match.group(1))

        # 3. Circadian window labels must stay on a single line without wrapping inside 45px.
        hour_lbl_match = re.search(r"\.circadian-bar-hour-label\s*\{(.*?)\}", css_text, re.DOTALL)
        self.assertIsNotNone(hour_lbl_match)
        self.assertIn("white-space: nowrap;", hour_lbl_match.group(1))
        self.assertNotIn("inline-size: 45px;", hour_lbl_match.group(1))

        # 4. Month label positioning must scale with CSS variables and auto-align to latest weeks.
        self.assertIn('id="heatmap-scroll-hint"', html_text)
        self.assertIn("--heatmap-cell-size", css_text)
        self.assertIn("--heatmap-cell-size", js_text)
        self.assertNotIn("const leftPx = 28 + mp.weekIndex * 16;", js_text)
        self.assertIn("alignHeatmapViewport", js_text)

        # 5. Bumped cache-busting assets in HTML and service worker.
        self.assertRegex(html_text, r'/static/styles\.css\?v=(6[5-9]|[7-9]\d+)"')
        self.assertRegex(html_text, r'/static/app\.js\?v=(6[5-9]|[7-9]\d+)"')
        self.assertRegex(sw_text, r'const CACHE_NAME = "vertex-pwa-v(6[5-9]|[7-9]\d+)";')

    def test_library_sort_order_persists_independently_of_filters(self) -> None:
        # Library sort order must carry over across sessions via localStorage without
        # triggering filter badges or being cleared by Reset Filters.
        html_text = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js_text = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        sort_select_match = re.search(r'<select id="sort-select"[^>]*>(.*?)</select>', html_text, re.DOTALL)
        self.assertIsNotNone(sort_select_match)
        html_sort_values = re.findall(r'<option value="([^"]+)"', sort_select_match.group(1))
        self.assertEqual(
            html_sort_values,
            [
                "playtime_desc",
                "achievements_desc",
                "last_played_desc",
                "rating_desc",
                "title_asc",
                "recent_added",
            ],
        )

        # Storage helpers and state initialization
        self.assertIn('const DEFAULT_SORT_BY = "playtime_desc";', js_text)
        self.assertIn('const SORT_STORAGE_KEY = "vertex_sort_by";', js_text)
        self.assertIn("function getSavedSortBy(", js_text)
        self.assertIn("function applySortBy(", js_text)
        self.assertIn("sortBy: getSavedSortBy(),", js_text)

        for sort_val in html_sort_values:
            self.assertIn(f'"{sort_val}"', js_text)

        # Startup syncs saved sort before switchMainTab triggers fetchGames()
        dom_ready_match = re.search(
            r'document\.addEventListener\("DOMContentLoaded",\s*\(\)\s*=>\s*\{(.*?)switchMainTab\(state\.activeTab',
            js_text,
            re.DOTALL,
        )
        self.assertIsNotNone(dom_ready_match)
        self.assertIn("applySortBy(state.sortBy);", dom_ready_match.group(1))

        # Sort selector persists changes through applySortBy
        sort_change_match = re.search(
            r'sortSelectEl\.addEventListener\("change",\s*\(e\)\s*=>\s*\{(.*?)\}\);',
            js_text,
            re.DOTALL,
        )
        self.assertIsNotNone(sort_change_match)
        self.assertIn("applySortBy(e.target.value);", sort_change_match.group(1))

        # syncFilterInputs keeps #sort-select synced with state.sortBy
        sync_inputs_match = re.search(r"function syncFilterInputs\(\)\s*\{(.*?)\n\}", js_text, re.DOTALL)
        self.assertIsNotNone(sync_inputs_match)
        self.assertIn('document.getElementById("sort-select")', sync_inputs_match.group(1))
        self.assertIn("sortSelect.value = state.sortBy;", sync_inputs_match.group(1))

        # updateActiveFiltersUI must NOT count sortBy in filter badge or Reset Filters visibility
        active_ui_match = re.search(r"function updateActiveFiltersUI\(\)\s*\{(.*?)\n\}", js_text, re.DOTALL)
        self.assertIsNotNone(active_ui_match)
        self.assertNotIn("state.sortBy", active_ui_match.group(1))

        # Reset Filters button must NOT reset sortBy or #sort-select
        reset_match = re.search(
            r'resetFiltersBtn\.addEventListener\("click",\s*\(\)\s*=>\s*\{(.*?)\}\);\s*\n\s*\}',
            js_text,
            re.DOTALL,
        )
        self.assertIsNotNone(reset_match)
        self.assertNotIn("sortBy", reset_match.group(1))
        self.assertNotIn("sort-select", reset_match.group(1))

    def test_beaten_transition_shows_congrats_card_and_dismiss_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            app = self._make_app(tmp_path)
            now_dt = datetime.now(timezone.utc).replace(microsecond=0)
            start_iso = (now_dt - timedelta(hours=2)).isoformat()
            end_iso = (now_dt - timedelta(hours=1)).isoformat()

            with TestClient(app) as client:
                r_boot = client.post(
                    "/api/auth/bootstrap",
                    json={"username": "finisher1", "password": "StrongPassword!99"},
                )
                self.assertEqual(r_boot.status_code, 201)

                r_game = client.post(
                    "/api/games",
                    headers=UI_HDR,
                    json={
                        "title": "Metaphor: ReFantazio",
                        "platform": "PC (Steam)",
                        "completion_status": "Playing",
                        "playtime_hours": 72.5,
                    },
                )
                self.assertEqual(r_game.status_code, 200)
                gid = r_game.json()["game"]["id"]

                # Record a session that ended 1 hour ago
                self.assertEqual(
                    client.post(
                        "/api/integrations/homeassistant/events",
                        json={
                            "event": "start",
                            "title": "Metaphor: ReFantazio",
                            "platform": "PC (Steam)",
                            "started_at": start_iso,
                        },
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.post(
                        "/api/integrations/homeassistant/events",
                        json={
                            "event": "stop",
                            "title": "Metaphor: ReFantazio",
                            "platform": "PC (Steam)",
                            "ended_at": end_iso,
                        },
                    ).status_code,
                    200,
                )

                # User switches Playing -> Beaten via legacy UI PATCH
                r_beaten = client.patch(
                    f"/api/games/{gid}",
                    headers=UI_HDR,
                    json={"completion_status": "Beaten"},
                )
                self.assertEqual(r_beaten.status_code, 200)

                r_stats = client.get("/api/stats", headers=UI_HDR)
                self.assertEqual(r_stats.status_code, 200)
                prompts = r_stats.json()["playtime_prompts"]
                self.assertEqual(len(prompts), 1)
                self.assertEqual(prompts[0]["prompt_type"], "congrats")
                self.assertEqual(prompts[0]["current_status"], "Beaten")
                self.assertEqual(prompts[0]["sessions_count"], 1)

                # User uses Rate & Review -> submits rating with completion_status="Beaten"
                r_rate = client.patch(
                    f"/api/games/{gid}",
                    headers=UI_HDR,
                    json={"completion_status": "Beaten", "user_rating": 10, "notes": "Masterpiece JRPG"},
                )
                self.assertEqual(r_rate.status_code, 200)
                prompts_after_rate = client.get("/api/stats", headers=UI_HDR).json()["playtime_prompts"]
                self.assertEqual(len(prompts_after_rate), 1)
                self.assertEqual(prompts_after_rate[0]["user_rating"], 10)

                # Dismiss persists across subsequent stats fetches
                prompt_id = prompts_after_rate[0]["prompt_id"]
                r_dismiss = client.post(
                    f"/api/playtime-prompts/{prompt_id}/resolve",
                    headers=UI_HDR,
                    json={"action": "dismiss"},
                )
                self.assertEqual(r_dismiss.status_code, 200)
                self.assertEqual(r_dismiss.json()["stats"]["playtime_prompts"], [])
                self.assertEqual(client.get("/api/stats", headers=UI_HDR).json()["playtime_prompts"], [])
