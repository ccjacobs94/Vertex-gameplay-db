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

        # Cache buster must be bumped to v=35 and sw to v32
        self.assertIn('href="/static/styles.css?v=35"', index_html)
        self.assertIn('src="/static/app.js?v=35"', index_html)
        self.assertRegex(sw_js, r'const CACHE_NAME = "vertex-pwa-v(3[1-9]|[4-9]\d+)";')

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
        self.assertIn('id="btn-wizard-dismiss-forever"', index_html)
        self.assertIn('id="empty-library-welcome"', index_html)
        self.assertIn('onclick="window.openConnectionsModal && window.openConnectionsModal()"', index_html)
        self.assertIn('onclick="window.openOnboardingWizard && window.openOnboardingWizard()"', index_html)

        # JavaScript exports and safety
        self.assertIn("window.openSettingsModal = openSettingsModal;", app_js)
        self.assertIn("window.switchSettingsTab = switchSettingsTab;", app_js)
        self.assertIn("window.loadAccountInfo = loadAccountInfo;", app_js)
        self.assertIn("window.openOnboardingWizard = openOnboardingWizard;", app_js)
        self.assertIn("window.closeOnboardingWizard = closeOnboardingWizard;", app_js)
        self.assertIn("window.dismissWizardForever = dismissWizardForever;", app_js)
        self.assertIn("window.openConnectionsModal = openConnectionsModal;", app_js)
        self.assertIn("if (!dialog.open)", app_js)

    def test_mobile_settings_and_logout_elements(self):
        index_path = os.path.join(self.static_dir, "index.html")
        app_js_path = os.path.join(self.static_dir, "app.js")
        styles_path = os.path.join(self.static_dir, "styles.css")

        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()
        with open(styles_path, "r", encoding="utf-8") as f:
            styles_css = f.read()

        # Mobile bottom navigation bar includes Settings
        self.assertIn('id="mnav-settings"', index_html)

        # Mobile header includes Settings button and mobile Sign Out link
        self.assertIn('id="btn-mobile-settings"', index_html)
        self.assertIn('id="btn-mobile-logout"', index_html)

        # Settings dialog Account section has a direct Sign Out action
        self.assertIn('id="btn-settings-logout"', index_html)

        # JavaScript wires mnav-settings and toggles mobile/settings logout buttons
        self.assertIn('document.getElementById("mnav-settings")', app_js)
        self.assertIn('document.getElementById("btn-mobile-settings")', app_js)
        self.assertIn('document.getElementById("btn-mobile-logout")', app_js)
        self.assertIn('document.getElementById("btn-settings-logout")', app_js)

        # CSS styles mobile header actions and 7-column bottom navigation + 3-column view-tabs-bar
        self.assertIn(".mobile-header-actions", styles_css)
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr))", styles_css)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", styles_css)

    def test_admin_delete_user_modal_integration(self):
        index_path = os.path.join(self.static_dir, "index.html")
        app_js_path = os.path.join(self.static_dir, "app.js")

        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()

        # HTML dialog markup and elements
        self.assertIn('id="admin-delete-user-dialog"', index_html)
        self.assertIn('id="delete-user-modal-title"', index_html)
        self.assertIn('id="delete-user-confirm-input"', index_html)
        self.assertIn('id="btn-confirm-delete-user"', index_html)
        self.assertIn('onclick="closeAdminDeleteUserDialog()"', index_html)
        self.assertIn('onclick="confirmAdminDeleteUser()"', index_html)
        self.assertIn("Warning: Irreversible Task", index_html)

        # JavaScript exports and handler bindings
        self.assertIn("window.openAdminDeleteUserDialog = openAdminDeleteUserDialog;", app_js)
        self.assertIn("window.closeAdminDeleteUserDialog = closeAdminDeleteUserDialog;", app_js)
        self.assertIn("window.confirmAdminDeleteUser = confirmAdminDeleteUser;", app_js)
        self.assertIn("openAdminDeleteUserDialog", app_js)
        self.assertIn("confirmAdminDeleteUser", app_js)

    def test_scroll_rendering_performance_optimizations(self):
        app_js_path = os.path.join(self.static_dir, "app.js")
        styles_path = os.path.join(self.static_dir, "styles.css")

        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()
        with open(styles_path, "r", encoding="utf-8") as f:
            styles_css = f.read()

        # 1. Async image decoding on game cards to prevent main-thread scroll jank
        self.assertIn('img.decoding = "async";', app_js)

        # 2. DocumentFragment batching for game card rendering (prevents repeated live DOM layout thrashing)
        self.assertIn("createDocumentFragment()", app_js)

        # 3. RequestAnimationFrame throttling for scroll event listener
        self.assertIn("requestAnimationFrame", app_js)

        # 4. Self-contained flex column cards instead of multi-pass subgrid tracks
        self.assertNotIn("grid-template-rows: subgrid", styles_css)
        self.assertNotIn("grid-row: span 3", styles_css)
        self.assertIn("display: flex;\n  flex-direction: column;", styles_css)

        # 5. Calibrated contain-intrinsic-size for desktop and mobile
        self.assertIn("contain-intrinsic-size: auto none auto 440px", styles_css)
        self.assertIn("contain-intrinsic-size: auto none auto 340px", styles_css)

        # 6. Badges avoid expensive backdrop-filter blur passes during scrolling
        self.assertNotIn(".gamepass-badge {\n  position: absolute;\n  inset-block-start: 2.15rem;\n  inset-inline-start: 0.5rem;\n  display: inline-flex;\n  align-items: center;\n  justify-content: center;\n  gap: 0.25rem;\n  padding-block: 0.18rem;\n  padding-inline: 0.45rem;\n  border-radius: var(--radius-sm);\n  font-size: 0.66rem;\n  font-weight: 800;\n  letter-spacing: 0.04em;\n  text-transform: uppercase;\n  backdrop-filter: blur(4px);", styles_css)
        self.assertNotIn(".rating-badge {\n  position: absolute;\n  inset-block-end: 0.5rem;\n  inset-inline-end: 0.5rem;\n  background-color: rgba(15, 23, 42, 0.88);\n  backdrop-filter: blur(4px);", styles_css)

        # 7. Hover transitions scoped to fine pointer devices with hover capability
        self.assertIn("@media (hover: hover) and (pointer: fine)", styles_css)

        # 8. Compositor layer promotion for sticky/fixed elements
        self.assertIn("transform: translateZ(0);", styles_css)

    def test_wishlist_modal_dialogs_structure_and_exports(self):
        index_path = os.path.join(self.static_dir, "index.html")
        app_js_path = os.path.join(self.static_dir, "app.js")
        sw_path = os.path.join(self.static_dir, "sw.js")

        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_js = f.read()

        # 1. Dialog opening and closing tags must be balanced (no unclosed dialog nesting)
        dialog_opens = index_html.count("<dialog")
        dialog_closes = index_html.count("</dialog>")
        self.assertEqual(
            dialog_opens,
            dialog_closes,
            f"Mismatched <dialog> tags: {dialog_opens} opening vs {dialog_closes} closing",
        )

        # 2. triage-dialog must be properly closed before wishlist-add-dialog
        triage_idx = index_html.find('id="triage-dialog"')
        wishlist_add_idx = index_html.find('id="wishlist-add-dialog"')
        self.assertNotEqual(triage_idx, -1)
        self.assertNotEqual(wishlist_add_idx, -1)
        triage_slice = index_html[triage_idx:wishlist_add_idx]
        self.assertIn(
            "</dialog>",
            triage_slice,
            "triage-dialog must have a closing </dialog> before wishlist-add-dialog",
        )

        # 3. + Watch Game button must have inline window fallback handler
        self.assertIn(
            'id="btn-open-wishlist-add"',
            index_html,
        )
        self.assertIn(
            'onclick="window.openWishlistAddModal && window.openWishlistAddModal()"',
            index_html,
        )

        # 4. app.js must export wishlist modal functions to window
        self.assertIn("window.openWishlistAddModal = openWishlistAddModal;", app_js)
        self.assertIn("window.openWishlistEditPlatformsModal = openWishlistEditPlatformsModal;", app_js)
        self.assertIn("window.openWishlistHistoryModal = openWishlistHistoryModal;", app_js)
        self.assertIn("window.openWishlistPurchaseModal = openWishlistPurchaseModal;", app_js)
        self.assertIn("window.deleteWishlistItem = deleteWishlistItem;", app_js)
        self.assertIn("window.selectCandidateForConfig = selectCandidateForConfig;", app_js)

        # 5. Cache buster must be bumped to v=35 and sw to v32
        self.assertIn('href="/static/styles.css?v=35"', index_html)
        self.assertIn('src="/static/app.js?v=35"', index_html)
        self.assertRegex(sw_js, r'const CACHE_NAME = "vertex-pwa-v(3[1-9]|[4-9]\d+)";')

        # 6. Abridged platform summary (lowest price per platform) with 1-click expandable detailed store view & brand SVG logos
        self.assertIn("function formatWishlistStorefrontLabel(", app_js)
        self.assertIn("function formatWishlistLastScanTimestamp(", app_js)
        self.assertIn("function getWishlistPlatformBrandIconHtml(", app_js)
        self.assertIn("function getWishlistStoreBrandIconHtml(", app_js)
        self.assertIn("<details class=\"", app_js)
        self.assertIn("<summary class=\"wishlist-platform-header wishlist-abridged-row\"", app_js)
        self.assertIn("abridged-lowest-price", app_js)
        self.assertIn("marketplace-price-row", app_js)
        self.assertIn("btn-store-link", app_js)
        self.assertIn("Last scan:", app_js)
        self.assertIn("marketplace-price-compare", app_js)
        self.assertIn("marketplace-target-price", app_js)

    def test_xbox_gamertag_ui_assets(self):
        index_path = os.path.join(self.static_dir, "index.html")
        styles_path = os.path.join(self.static_dir, "styles.css")
        app_js_path = os.path.join(self.static_dir, "app.js")
        sw_path = os.path.join(self.static_dir, "sw.js")

        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()
        with open(styles_path, "r", encoding="utf-8") as f:
            styles_css = f.read()
        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_js = f.read()

        # PWA cache name bumped
        self.assertRegex(sw_js, r'const CACHE_NAME = "vertex-pwa-v(3[3-9]|[4-9]\d+)";')

        # Index HTML: Onboarding & Settings Xbox elements
        self.assertIn('id="wizard-xbl-key"', index_html)
        self.assertIn('id="wizard-xbox-status"', index_html)
        self.assertIn('id="setting-xbox-input-row"', index_html)
        self.assertIn('id="settings-xbox-connected-card"', index_html)
        self.assertIn('id="settings-xbox-gamertag"', index_html)
        self.assertIn('id="settings-xbox-gamerscore"', index_html)
        self.assertIn('id="settings-xbox-avatar"', index_html)
        self.assertIn('id="btn-disconnect-xbox"', index_html)

        # Styles CSS: Player card and badge styling
        self.assertIn(".xbox-connected-player-card", styles_css)
        self.assertIn(".xbox-player-info", styles_css)
        self.assertIn(".xbox-player-gamertag", styles_css)
        self.assertIn(".xbox-player-gamerscore-badge", styles_css)
        self.assertIn(".xbox-sync-status", styles_css)
        self.assertIn(".btn-disconnect-action", styles_css)

        # App JS: Live Gamertag resolution, auto-save, auto-sync, disconnect
        self.assertIn("async function performLiveXboxLink(", app_js)
        self.assertIn("async function disconnectXboxAccount(", app_js)
        self.assertIn("window.performLiveXboxLink = performLiveXboxLink;", app_js)
        self.assertIn("window.disconnectXboxAccount = disconnectXboxAccount;", app_js)
        self.assertIn('fetch("/api/sync?platform=xbox"', app_js)


if __name__ == "__main__":
    unittest.main()
