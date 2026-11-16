"""Contracts verifying OpenAPI-to-TypeScript sync, offline frontend build, static SW rules, and legacy inventory facts."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import tests._path  # noqa: F401
from tests._path import MIGRATIONS_DIR, REPO_ROOT, SCRIPTS_DIR
from vertex.config import Settings
from vertex.main import create_app


class TestContractsAndFrontend(unittest.TestCase):
    def test_openapi_typescript_contract_is_in_sync(self):
        import sys

        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        from export_openapi import generate_openapi_types  # type: ignore

        expected = generate_openapi_types()
        committed = (REPO_ROOT / "frontend" / "src" / "shared" / "api-types.ts").read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            expected,
            "frontend/src/shared/api-types.ts drifted from FastAPI OpenAPI schema; run scripts/export_openapi.py",
        )

    def test_offline_frontend_build_and_static_serving_and_sw_rules(self):
        import sys

        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        from build_frontend import build  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dist_dir = tmp_path / "dist"
            emitted = build(out_dir=dist_dir)
            self.assertGreaterEqual(len(emitted), 7)
            self.assertTrue((dist_dir / "index.html").exists())
            self.assertTrue((dist_dir / "manifest.webmanifest").exists())
            self.assertTrue((dist_dir / "assets" / "main.js").exists())
            self.assertTrue((dist_dir / "assets" / "styles" / "main.css").exists())

            # Service worker must explicitly bypass /api/ responses and never cache authenticated API data.
            sw_text = (dist_dir / "sw.js").read_text(encoding="utf-8")
            self.assertIn('"/api/"', sw_text)
            self.assertIn("return;", sw_text)
            self.assertIn("cache.addAll(STATIC_ASSETS)", sw_text)
            for asset_path in (
                "/assets/features/library/libraryView.js",
                "/assets/features/activity/activityView.js",
                "/assets/features/insights/insightsView.js",
                "/assets/features/wishlist/wishlistView.js",
                "/assets/features/settings/settingsView.js",
                "/assets/features/admin/adminView.js",
                "/assets/features/auth/authView.js",
            ):
                self.assertIn(asset_path, sw_text)
                self.assertTrue((dist_dir / asset_path.lstrip("/")).is_file(), f"Missing built view: {asset_path}")

            # Verify strict TS class field erasure and canonical Vertex statuses in compiled JS
            api_js = (dist_dir / "assets" / "shared" / "api.js").read_text(encoding="utf-8")
            self.assertIn("export class ApiError extends Error", api_js)
            self.assertIn("constructor(status, code, detail)", api_js)
            lib_js = (dist_dir / "assets" / "features" / "library" / "libraryView.js").read_text(encoding="utf-8")
            for expected_st in ('"Beaten"', '"Dropped"', '"Borrowed"', '"None"'):
                self.assertIn(expected_st, lib_js)
            insights_js = (dist_dir / "assets" / "features" / "insights" / "insightsView.js").read_text(encoding="utf-8")
            self.assertIn("playtime_hours", insights_js)
            ledger_doc = (REPO_ROOT / "docs" / "plans" / "feature-parity-ledger.md").read_text(encoding="utf-8")
            self.assertIn("81 distinct non-OpenAPI paths", ledger_doc)

            # Verify Dockerfile.v2 builds frontend cleanly, includes HEALTHCHECK, and uses pinned lockfile
            dockerfile_v2 = (REPO_ROOT / "deploy" / "Dockerfile.v2").read_text(encoding="utf-8")
            self.assertNotIn("COPY scripts", dockerfile_v2)
            self.assertIn("vertex build-frontend", dockerfile_v2)
            self.assertIn("HEALTHCHECK", dockerfile_v2)
            self.assertIn("/readyz", dockerfile_v2)
            self.assertIn("requirements.lock", dockerfile_v2)

            # Verify all ADRs have required sections and README index
            adr_dir = REPO_ROOT / "docs" / "adr"
            self.assertTrue((adr_dir / "README.md").is_file())
            for adr_num in ("0001", "0002", "0003", "0004", "0005"):
                matches = list(adr_dir.glob(f"{adr_num}-*.md"))
                self.assertEqual(len(matches), 1)
                adr_text = matches[0].read_text(encoding="utf-8")
                self.assertIn("Alternatives Considered", adr_text)
                self.assertIn("Revisit Triggers", adr_text)

            app = create_app(
                Settings(
                    data_dir=tmp_path / "data",
                    migrations_dir=MIGRATIONS_DIR,
                    frontend_dist_dir=dist_dir,
                    secret_key="frontend-serving-secret",
                    worker_enabled=False,
                )
            )
            with TestClient(app) as client:
                r_index = client.get("/")
                self.assertEqual(r_index.status_code, 200)
                self.assertIn("text/html", r_index.headers.get("content-type", ""))
                self.assertIn("Vertex", r_index.text)

                r_manifest = client.get("/manifest.webmanifest")
                self.assertEqual(r_manifest.status_code, 200)
                self.assertIn("manifest+json", r_manifest.headers.get("content-type", ""))

                r_main_js = client.get("/assets/main.js")
                self.assertEqual(r_main_js.status_code, 200)
                self.assertIn("function boot()", r_main_js.text)

                for view_path in (
                    "/assets/features/library/libraryView.js",
                    "/assets/features/activity/activityView.js",
                    "/assets/features/insights/insightsView.js",
                    "/assets/features/wishlist/wishlistView.js",
                    "/assets/features/settings/settingsView.js",
                    "/assets/features/admin/adminView.js",
                ):
                    rv = client.get(view_path)
                    self.assertEqual(rv.status_code, 200, f"Failed serving {view_path}")

                r_sw = client.get("/sw.js")
                self.assertEqual(r_sw.status_code, 200)
                self.assertIn("no-cache", r_sw.headers.get("cache-control", ""))

    def test_reconciled_legacy_inventory_baseline(self):
        # Verify the two documented inventory corrections directly against legacy files:
        # 1) mcp_server.py defines 23 @mcp.tool() tools (including update_session_notes & import_homeassistant_sessions) and 2 resources.
        mcp_src = (REPO_ROOT / "mcp_server.py").read_text(encoding="utf-8")
        tree = ast.parse(mcp_src)
        tools: list[str] = []
        resources: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    dec_str = ast.unparse(dec)
                    if "mcp.tool" in dec_str:
                        tools.append(node.name)
                    if "mcp.resource" in dec_str:
                        resources.append(node.name)
        self.assertEqual(len(tools), 23)
        self.assertIn("update_session_notes", tools)
        self.assertIn("import_homeassistant_sessions", tools)
        self.assertEqual(len(resources), 2)

        # 2) Legacy /api/user/library/export in app.py returns a SQLite .db file, not JSON.
        app_src = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
        idx = app_src.index("/api/user/library/export")
        snippet = app_src[idx : idx + 1500]
        self.assertIn(".db", snippet)
        self.assertIn("FileResponse", snippet)
