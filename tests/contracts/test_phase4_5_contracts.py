"""Contract tests for Phase 4-6: ADR 0006, 23 MCP tools & 2 resources parity, OpenAPI, PWA SW, and Compose."""

from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

from vertex.config import Settings
from vertex.interfaces.mcp.server import MCP_RESOURCE_URIS, MCP_TOOL_NAMES, build_mcp_server
from vertex.main import build_app_services, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _extract_legacy_mcp_contracts(legacy_mcp_path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(legacy_mcp_path.read_text(encoding="utf-8"), filename=str(legacy_mcp_path))
    tools: set[str] = set()
    resources: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if dec.func.attr == "tool":
                        tools.add(node.name)
                    elif dec.func.attr == "resource" and dec.args:
                        if isinstance(dec.args[0], ast.Constant) and isinstance(
                            dec.args[0].value, str
                        ):
                            resources.add(dec.args[0].value)
    return tools, resources


class TestPhase4And5Contracts(unittest.TestCase):
    def test_adr_0006_and_index_requirements(self) -> None:
        adr = REPO_ROOT / "docs" / "adr" / "0006-mcp-private-transport-and-pat-auth.md"
        self.assertTrue(adr.exists(), "ADR 0006 missing")
        text = adr.read_text(encoding="utf-8")
        self.assertIn("Alternatives Considered", text)
        self.assertIn("Revisit Triggers", text)

        index_text = (REPO_ROOT / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
        self.assertIn("0006-mcp-private-transport-and-pat-auth.md", index_text)

    def test_mcp_23_tools_and_2_resources_match_legacy_oracle(self) -> None:
        legacy_tools, legacy_resources = _extract_legacy_mcp_contracts(REPO_ROOT / "mcp_server.py")
        self.assertEqual(len(legacy_tools), 23)
        self.assertEqual(set(MCP_TOOL_NAMES), legacy_tools)
        self.assertEqual(set(MCP_RESOURCE_URIS), legacy_resources)

        with tempfile.TemporaryDirectory() as td:
            cfg = Settings(
                data_dir=Path(td) / "data",
                migrations_dir=REPO_ROOT / "migrations",
                frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
                secret_key="contract-test-secret-key-mcp-only",
            )
            services = build_app_services(cfg)
            mcp = build_mcp_server(services)
            registered_tools = {t.name for t in mcp._tool_manager.list_tools()}
            self.assertEqual(registered_tools, legacy_tools)

            registered_res = {str(r.uri) for r in mcp._resource_manager.list_resources()}
            registered_tmpl = {t.uri_template for t in mcp._resource_manager.list_templates()}
            self.assertIn("vertex://wishlist/active", registered_res)
            self.assertIn("vertex://wishlist/{id}/history", registered_tmpl)

    def test_openapi_exposes_phase4_wishlist_sync_candidates_and_push_routes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Settings(
                data_dir=Path(td) / "data",
                migrations_dir=REPO_ROOT / "migrations",
                frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
                secret_key="contract-test-secret-key-openapi",
            )
            app = create_app(cfg)
            paths = app.openapi()["paths"]
            self.assertIn("/api/wishlist/candidates", paths)
            self.assertIn("/api/wishlist/sync", paths)
            self.assertIn("/api/push/vapid-public-key", paths)
            self.assertIn("/api/push/subscribe", paths)
            self.assertIn("/api/push/test", paths)

    def test_frontend_sw_cache_bumped_and_bypasses_mcp_and_compose_private(self) -> None:
        sw_text = (REPO_ROOT / "frontend" / "src" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("vertex-v2-static-8", sw_text)
        self.assertIn("/mcp", sw_text)

        compose_text = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8095:8090", compose_text)
        self.assertIn("dedicated-mcp", compose_text)
        self.assertIn("serve-mcp", compose_text)
        self.assertIn('VERTEX_WEB_PUSH_ENABLED: "${VERTEX_WEB_PUSH_ENABLED:-false}"', compose_text)
