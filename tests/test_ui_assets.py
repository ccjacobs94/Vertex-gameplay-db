"""
Tests for UI brand vector assets and static integration.
Guards the presence and validity of platform and service brand vector logos:
Steam, Xbox, PlayStation, Nintendo Switch, and Xbox Game Pass.
"""

import os
import unittest
import xml.etree.ElementTree as ET


class TestUIBrandAssets(unittest.TestCase):
    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.static_dir = os.path.join(self.repo_root, "static")
        self.icons_dir = os.path.join(self.static_dir, "icons")

    def test_brand_svg_files_exist_and_are_valid_xml(self):
        expected_icons = [
            "steam.svg",
            "xbox.svg",
            "playstation.svg",
            "switch.svg",
            "gamepass.svg",
        ]
        for filename in expected_icons:
            path = os.path.join(self.icons_dir, filename)
            self.assertTrue(os.path.isfile(path), f"Expected asset missing: {filename}")
            try:
                tree = ET.parse(path)
                root = tree.getroot()
            except Exception as e:
                self.fail(f"Failed to parse XML for {filename}: {e}")

            self.assertTrue(
                root.tag.endswith("svg"),
                f"Root tag for {filename} is not svg: {root.tag}",
            )
            viewbox = root.attrib.get("viewBox")
            self.assertIsNotNone(viewbox, f"{filename} is missing a viewBox attribute")
            self.assertTrue(len(viewbox.strip()) > 0, f"{filename} has empty viewBox")

    def test_service_worker_caches_brand_svg_assets(self):
        sw_path = os.path.join(self.static_dir, "sw.js")
        self.assertTrue(os.path.isfile(sw_path))
        with open(sw_path, "r", encoding="utf-8") as f:
            content = f.read()

        expected_assets = [
            "/static/icons/steam.svg",
            "/static/icons/xbox.svg",
            "/static/icons/playstation.svg",
            "/static/icons/switch.svg",
            "/static/icons/gamepass.svg",
        ]
        for asset in expected_assets:
            self.assertIn(asset, content, f"Service worker STATIC_ASSETS missing: {asset}")

    def test_index_html_embeds_brand_icons(self):
        index_path = os.path.join(self.static_dir, "index.html")
        self.assertTrue(os.path.isfile(index_path))
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check platform pills have SVG brand icons
        self.assertIn("brand-icon-steam", content)
        self.assertIn("brand-icon-xbox", content)
        self.assertIn("brand-icon-playstation", content)
        self.assertIn("brand-icon-switch", content)

        # Check Game Pass toggle button has brand icon
        self.assertIn("brand-icon-gp", content)

    def test_settings_modal_integration_and_cache_busting(self):
        index_path = os.path.join(self.static_dir, "index.html")
        sw_path = os.path.join(self.static_dir, "sw.js")
        app_js_path = os.path.join(self.static_dir, "app.js")

        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_js = f.read()
        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()

        # Cache buster must be bumped to v=24 and sw to v20
        self.assertIn('href="/static/styles.css?v=24"', index_html)
        self.assertIn('src="/static/app.js?v=24"', index_html)
        self.assertIn('const CACHE_NAME = "vertex-pwa-v20";', sw_js)

        # Settings dialog markup and buttons
        self.assertIn('id="settings-dialog"', index_html)
        self.assertIn('id="btn-open-settings"', index_html)
        self.assertIn('onclick="window.openSettingsModal && window.openSettingsModal()"', index_html)
        self.assertIn('id="ssection-connections"', index_html)
        self.assertIn('id="ssection-mcp"', index_html)
        self.assertIn('id="ssection-portability"', index_html)
        self.assertIn('id="ssection-account"', index_html)
        self.assertIn('id="stab-account"', index_html)
        self.assertIn('onclick="window.switchSettingsTab && window.switchSettingsTab(\'stab-account\')"', index_html)

        # Onboarding wizard & connected accounts integration
        self.assertIn('id="onboarding-dialog"', index_html)
        self.assertIn('id="btn-open-connections"', index_html)
        self.assertIn('id="empty-library-welcome"', index_html)
        self.assertIn('onclick="window.openConnectionsModal && window.openConnectionsModal()"', index_html)
        self.assertIn('onclick="window.openOnboardingWizard && window.openOnboardingWizard()"', index_html)

        # JavaScript exports and safety
        self.assertIn("window.openSettingsModal = openSettingsModal;", app_js)
        self.assertIn("window.switchSettingsTab = switchSettingsTab;", app_js)
        self.assertIn("window.loadAccountInfo = loadAccountInfo;", app_js)
        self.assertIn("window.openOnboardingWizard = openOnboardingWizard;", app_js)
        self.assertIn("window.openConnectionsModal = openConnectionsModal;", app_js)
        self.assertIn("if (!dialog.open)", app_js)


if __name__ == "__main__":
    unittest.main()
