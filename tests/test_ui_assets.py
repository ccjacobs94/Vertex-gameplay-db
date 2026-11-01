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


if __name__ == "__main__":
    unittest.main()
