"""Offline unit tests for Phase 4 price adapters, wishlist sync, optional Web Push, and domain boundaries."""

from __future__ import annotations

import ast
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest

import httpx

from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.models import UserScope
from vertex.identity.service import IdentityService
from vertex.infrastructure.jobs import JobQueue
from vertex.infrastructure.secrets import SecretBox
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.prices import (
    CheapSharkAdapter,
    GOGDirectAdapter,
    ITADAdapter,
    PSNStoreGraphQLAdapter,
    SteamStoreAdapter,
    WishlistSyncService,
    XboxCatalogAdapter,
)
from vertex.integrations.prices.matching import extract_edition_name, matches_canonical_title
from vertex.integrations.push import (
    WebPushDisabledError,
    WebPushError,
    WebPushService,
    is_valid_push_endpoint,
)
from vertex.library.service import LibraryService
from vertex.wishlist.service import WishlistService


def _make_settings(tmp_dir: str, **overrides) -> Settings:
    repo_root = Path(__file__).resolve().parents[2]
    data = {
        "data_dir": Path(tmp_dir) / "data",
        "migrations_dir": repo_root / "migrations",
        "frontend_dist_dir": repo_root / "frontend" / "dist",
        "secret_key": "unit-test-secret-key-phase4-only",
        "outbound_sync_enabled": True,
        "web_push_enabled": False,
    }
    data.update(overrides)
    return Settings(**data)


class TestMatchingAndEditions(unittest.TestCase):
    def test_title_matching_filters_addons_and_sequels(self) -> None:
        self.assertTrue(matches_canonical_title("Hollow Knight", "Hollow Knight"))
        self.assertTrue(matches_canonical_title("Hollow Knight", "Hollow Knight: Voidheart Edition"))
        self.assertTrue(matches_canonical_title("Control", "Control Ultimate Edition"))
        self.assertFalse(matches_canonical_title("Hollow Knight", "Hollow Knight: Silksong"))
        self.assertFalse(matches_canonical_title("Hollow Knight", "Hollow Knight 2"))
        self.assertFalse(matches_canonical_title("Hollow Knight", "Hollow Knight Original Soundtrack"))
        self.assertFalse(matches_canonical_title("Hollow Knight", "Hollow Knight Artbook"))
        self.assertFalse(matches_canonical_title("Control", "Control Season Pass Upgrade"))

    def test_extract_edition_name_tiers(self) -> None:
        self.assertEqual(extract_edition_name("Hades", "Hades"), "Standard Edition")
        self.assertEqual(
            extract_edition_name("Control Ultimate Edition", "Control"),
            "Ultimate Edition",
        )
        self.assertEqual(
            extract_edition_name("Death Stranding Director's Cut", "Death Stranding"),
            "Director's Cut",
        )
        self.assertEqual(
            extract_edition_name("Hollow Knight: Voidheart Edition", "Hollow Knight"),
            "Complete Edition",
        )


class TestSixPriceAdapters(unittest.IsolatedAsyncioTestCase):
    async def test_itad_adapter_whitelisting_region_currency_and_chunking(self) -> None:
        seen_chunks: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("api.isthereanydeal.com/games/prices/v2", str(request.url))
            ids = httpx.Request("POST", request.url, content=request.content)
            import json

            payload = json.loads(ids.content.decode())
            seen_chunks.append(payload)
            if payload[0] == "id-0":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "id-0",
                            "historyLow": {"all": {"amount": 7.49}},
                            "deals": [
                                {
                                    "shop": {"id": 999, "name": "GreyMarketKey"},
                                    "price": {"amount": 2.00, "currency": "USD"},
                                    "regular": {"amount": 29.99},
                                    "cut": 93,
                                    "url": "https://grey.example/id-0",
                                },
                                {
                                    "shop": {"id": 35, "name": "GOG"},
                                    "price": {"amount": 11.99, "currency": "USD"},
                                    "regular": {"amount": 29.99},
                                    "cut": 60,
                                    "url": "https://gog.com/game/id-0",
                                },
                            ],
                        }
                    ],
                )
            return httpx.Response(200, json=[])

        adapter = ITADAdapter(country="US")
        caps = adapter.capabilities()
        self.assertEqual(caps.storefront, "itad")
        self.assertEqual(caps.currency, "USD")

        ids = [f"id-{i}" for i in range(65)]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            quotes = await adapter.fetch_prices(
                store_sku_ids=ids, client=client, api_key="secret-itad-key"
            )
        self.assertEqual([len(c) for c in seen_chunks], [25, 25, 15])
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual(q.storefront, "gog")
        self.assertTrue(q.is_drm_free)
        self.assertEqual(q.current_price_usd, 11.99)
        self.assertEqual(q.historical_low_usd, 7.49)
        self.assertEqual(q.currency, "USD")
        self.assertEqual(q.region, "US")

    async def test_cheapshark_adapter_search_and_batch_quotes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "title=" in str(request.url):
                return httpx.Response(
                    200, json=[{"gameID": "101", "external": "Hades", "cheapest": "9.99"}]
                )
            return httpx.Response(
                200,
                json={
                    "101": {
                        "cheapestPriceEver": {"price": "8.49"},
                        "deals": [
                            {
                                "storeID": "1",
                                "price": "12.49",
                                "retailPrice": "24.99",
                                "savings": "50.0",
                                "dealID": "cs-deal-1",
                            },
                            {
                                "storeID": "99",
                                "price": "1.00",
                                "retailPrice": "24.99",
                                "savings": "96.0",
                                "dealID": "unmapped",
                            },
                        ],
                    }
                },
            )

        adapter = CheapSharkAdapter()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hits = await adapter.search_games("Hades", client=client)
            quotes = await adapter.fetch_prices(store_sku_ids=["101"], client=client)

        self.assertEqual(hits[0]["gameID"], "101")
        # Emits mapped 'steam' quote + 'cheapshark' aggregate quote (ignores unmapped storeID=99)
        storefronts = {q.storefront for q in quotes}
        self.assertEqual(storefronts, {"steam", "cheapshark"})
        self.assertEqual(quotes[0].historical_low_usd, 8.49)
        self.assertEqual(quotes[0].currency, "USD")

    async def test_steam_adapter_base_edition_and_non_usd_region(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "storesearch" in url:
                return httpx.Response(
                    200,
                    json={"items": [{"id": 1145360, "name": "Hades", "price": {"final": 1249}}]},
                )
            cc = request.url.params.get("cc", "US")
            currency = "EUR" if cc == "DE" else "USD"
            return httpx.Response(
                200,
                json={
                    "1145360": {
                        "success": True,
                        "data": {
                            "name": "Hades",
                            "header_image": "https://cdn.example/hades.jpg",
                            "price_overview": {
                                "currency": currency,
                                "initial": 2499,
                                "final": 1249,
                                "discount_percent": 50,
                            },
                            "package_groups": [
                                {
                                    "subs": [
                                        {
                                            "packageid": 499001,
                                            "option_text": "Hades Deluxe Edition",
                                            "price_in_cents_with_discount": 1999,
                                            "percent_savings": 33,
                                        }
                                    ]
                                }
                            ],
                        },
                    }
                },
            )

        adapter = SteamStoreAdapter(country="US")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            quotes_us = await adapter.fetch_prices(
                store_sku_ids=["1145360", "1145360:499001"], client=client
            )
            quotes_de = await adapter.fetch_prices(
                store_sku_ids=["1145360"], client=client, country="DE"
            )

        self.assertEqual(len(quotes_us), 2)
        self.assertEqual(quotes_us[0].edition_name, "Standard Edition")
        self.assertEqual(quotes_us[0].current_price_usd, 12.49)
        self.assertEqual(quotes_us[1].edition_name, "Deluxe Edition")
        self.assertEqual(quotes_us[1].edition_tier, "deluxe")
        self.assertEqual(quotes_us[1].current_price_usd, 19.99)
        self.assertEqual(quotes_de[0].currency, "EUR")
        self.assertEqual(quotes_de[0].region, "DE")

    async def test_gog_adapter_amounts_and_drm_free(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "12345",
                        "slug": "witcher_3",
                        "price": {
                            "finalAmount": "14.99",
                            "baseAmount": "39.99",
                            "discountPercentage": 63,
                            "currency": "USD",
                        },
                    },
                    {
                        "id": "67890",
                        "slug": "silksong_gog",
                        "price": {
                            "finalAmount": 1999,
                            "baseAmount": 1999,
                            "discountPercentage": 0,
                            "currency": "USD",
                        },
                    },
                ],
            )

        adapter = GOGDirectAdapter()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            quotes = await adapter.fetch_prices(store_sku_ids=["12345", "67890"], client=client)

        self.assertEqual(len(quotes), 2)
        self.assertTrue(all(q.is_drm_free and q.storefront == "gog" for q in quotes))
        self.assertEqual(quotes[0].current_price_usd, 14.99)
        self.assertEqual(quotes[1].current_price_usd, 19.99)

    async def test_xbox_adapter_chunking_gen_and_images(self) -> None:
        chunk_sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "autosuggest" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "Results": [
                            {"Products": [{"ProductId": "9NBLGGH42CFD", "Title": "Forza Horizon 5"}]}
                        ]
                    },
                )
            big_ids = request.url.params.get("bigIds", "").split(",")
            chunk_sizes.append(len(big_ids))
            return httpx.Response(
                200,
                json={
                    "Products": [
                        {
                            "ProductId": big_ids[0],
                            "Properties": {"XboxConsoleGenOptimized": ["XboxSeriesX"]},
                            "LocalizedProperties": [
                                {
                                    "Images": [
                                        {
                                            "ImagePurpose": "Poster",
                                            "Uri": "//store-images.s-microsoft.com/poster.jpg",
                                        }
                                    ]
                                }
                            ],
                            "DisplaySkuAvailabilities": [
                                {
                                    "Availabilities": [
                                        {
                                            "OrderManagementData": {
                                                "Price": {
                                                    "ListPrice": 29.99,
                                                    "MSRP": 59.99,
                                                    "CurrencyCode": "USD",
                                                }
                                            }
                                        }
                                    ]
                                }
                            ],
                        }
                    ]
                },
            )

        adapter = XboxCatalogAdapter(market="US")
        ids = [f"9NBLGGH42C{i:02d}" for i in range(25)]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hits = await adapter.search_autosuggest("Forza Horizon 5", client=client)
            quotes = await adapter.fetch_prices(store_sku_ids=ids, client=client)

        self.assertEqual(hits[0]["Id"], "9NBLGGH42CFD")
        self.assertEqual(chunk_sizes, [20, 5])
        self.assertEqual(quotes[0].platform_label, "Xbox Series X|S")
        self.assertEqual(quotes[0].image_url, "https://store-images.s-microsoft.com/poster.jpg")
        self.assertEqual(quotes[0].discount_pct, 50)

    async def test_psn_adapter_graphql_and_synthetic_skip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            op = request.url.params.get("operationName")
            if op == "getSharedSearchSuggestions":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "getSharedSearchSuggestions": {
                                "suggestions": [{"id": "UP0001-PPSA01-00", "text": "Returnal"}]
                            }
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "productRetrieve": {
                            "platforms": ["PS5"],
                            "price": {
                                "basePrice": "$69.99",
                                "discountedPrice": "$34.99",
                                "currencyCode": "USD",
                            },
                        }
                    }
                },
            )

        adapter = PSNStoreGraphQLAdapter(country="US")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hits = await adapter.search_products("Returnal", client=client)
            quotes = await adapter.fetch_prices(
                store_sku_ids=["psn-returnal", "UP0001-PPSA01-00"], client=client
            )

        self.assertEqual(hits[0]["id"], "UP0001-PPSA01-00")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].store_sku_id, "UP0001-PPSA01-00")
        self.assertEqual(quotes[0].platform_label, "PS5")
        self.assertEqual(quotes[0].current_price_usd, 34.99)
        self.assertEqual(quotes[0].discount_pct, 50)


class TestWishlistSyncAndOptionalPush(unittest.IsolatedAsyncioTestCase):
    async def test_sync_freshness_currency_guard_and_adapter_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_settings(td, web_push_enabled=True)
            db = DatabaseManager(cfg)
            db.ensure_system_ready()
            secret_box = SecretBox(cfg.secret_key)
            jobs = JobQueue(db)
            identity = IdentityService(db, secret_box, jobs)
            user, _ = identity.bootstrap_admin(username="alice", password="StrongPass!123")
            scope = UserScope(
                user_id=user.id, username=user.username, role=user.role, status=user.status
            )
            catalog = CatalogService(db, outbound_sync_enabled=False)
            lib = LibraryService(db)
            wish = WishlistService(db, lib)

            # Owned library game triggers ownership_conflict warning during candidate search
            lib.add_manual_game(
                scope,
                title="Hades",
                platform="PC (Steam)",
                ownership_status="Owned",
                completion_status="Completed",
            )

            detail = wish.add_item(
                scope,
                title="Cyberpunk 2077",
                platform_targets=[{"platform": "PC", "target_price_usd": 30.0}],
            )
            # Pre-attach a GOG SKU and an ITAD SKU plus Steam discovery
            ed = wish.upsert_discovered_edition(
                scope,
                canonical_game_id=detail.canonical_game.id,
                edition_name="Standard Edition",
                edition_tier="base",
            )
            wish.upsert_discovered_sku(
                scope,
                edition_id=ed.id,
                storefront="gog",
                store_sku_id="gog-cp77",
                platform_family="PC",
                platform_label="PC (GOG)",
                is_drm_free=True,
            )
            wish.upsert_discovered_sku(
                scope,
                edition_id=ed.id,
                storefront="itad",
                store_sku_id="itad-cp77",
                platform_family="PC",
                platform_label="PC (Steam)",
            )

            def transport_handler(request: httpx.Request) -> httpx.Response:
                host = request.url.host
                if "store.steampowered.com" in host:
                    if "storesearch" in str(request.url):
                        return httpx.Response(
                            200,
                            json={
                                "items": [
                                    {"id": 1091500, "name": "Hades", "price": {"final": 1249}},
                                    {"id": 1091501, "name": "Cyberpunk 2077", "price": {"final": 2499}},
                                ]
                            },
                        )
                    return httpx.Response(
                        200,
                        json={
                            "1091501": {
                                "success": True,
                                "data": {
                                    "name": "Cyberpunk 2077",
                                    "header_image": "https://cdn.example/cp77.jpg",
                                    "price_overview": {
                                        "currency": "USD",
                                        "initial": 5999,
                                        "final": 2499,
                                        "discount_percent": 58,
                                    },
                                },
                            }
                        },
                    )
                if "api.gog.com" in host:
                    # Non-USD quote MUST be rejected safely without overwriting USD prices
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "id": "gog-cp77",
                                "slug": "cyberpunk_2077",
                                "price": {
                                    "finalAmount": "29.99",
                                    "baseAmount": "59.99",
                                    "discountPercentage": 50,
                                    "currency": "EUR",
                                },
                            }
                        ],
                    )
                if "isthereanydeal.com" in host:
                    # Failing adapter MUST NOT abort Steam sync
                    return httpx.Response(503, json={"error": "upstream unavailable"})
                if "cheapshark.com" in host:
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json={})

            sent_pushes: list[dict] = []

            def fake_sender(sub_info, data, priv_key, claims):
                sent_pushes.append({"sub": sub_info, "data": data, "claims": claims})

            push_svc = WebPushService(
                settings=cfg,
                db_manager=db,
                secret_box=secret_box,
                wishlist_service=wish,
                sender=fake_sender,
            )
            push_svc.subscribe(
                scope,
                endpoint="https://fcm.googleapis.com/fcm/send/alice-token",
                p256dh="BKey123",
                auth="AuthSecret123",
            )

            sync_svc = WishlistSyncService(
                settings=cfg,
                job_queue=jobs,
                identity_service=identity,
                catalog_service=catalog,
                wishlist_service=wish,
                web_push_service=push_svc,
                client_factory=lambda: httpx.AsyncClient(
                    transport=httpx.MockTransport(transport_handler)
                ),
            )

            cand_res = await sync_svc.search_candidates(scope, "Hades", limit=5)
            hades_cands = [c for c in cand_res["candidates"] if c["title"] == "Hades"]
            self.assertTrue(hades_cands[0]["ownership_conflict"]["already_in_library"])
            self.assertIn("PC", hades_cands[0]["ownership_conflict"]["conflicted_platforms"])

            # First sync records Steam $24.99, isolates ITAD 503, rejects GOG EUR, dispatches 1 Web Push
            summary1 = await sync_svc.sync_wishlist_for_user(scope, force=False)
            self.assertEqual(summary1["status"], "partial")
            self.assertGreaterEqual(summary1["prices_recorded"], 1)
            self.assertEqual(summary1["deals_triggered"], 1)
            self.assertEqual(summary1["push_sent"], 1)
            self.assertEqual(len(sent_pushes), 1)
            self.assertTrue(any("Non-USD" in e or "non-USD" in e for e in summary1["errors"]))

            # Cover art backfilled onto canonical game
            refreshed = wish.get_item(scope, detail.id)
            self.assertEqual(refreshed.canonical_game.cover_url, "https://cdn.example/cp77.jpg")

            # Second sync skips fresh SKUs within max_age_seconds and does NOT double-alert push!
            summary2 = await sync_svc.sync_wishlist_for_user(
                scope, force=False, max_age_seconds=3600
            )
            self.assertGreaterEqual(summary2["skipped_fresh"], 1)
            self.assertEqual(summary2["push_sent"], 0)
            self.assertEqual(len(sent_pushes), 1)

            # Scheduled sync enqueues deduped wishlist_sync job for alice
            self.assertEqual(sync_svc.enqueue_scheduled_sync_iteration(), 1)
            self.assertEqual(sync_svc.enqueue_scheduled_sync_iteration(), 1)

    async def test_web_push_disabled_by_default_and_ssrf_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_settings(td, web_push_enabled=False)
            db = DatabaseManager(cfg)
            db.ensure_system_ready()
            secret_box = SecretBox(cfg.secret_key)
            jobs = JobQueue(db)
            identity = IdentityService(db, secret_box, jobs)
            user, _ = identity.bootstrap_admin(username="admin", password="StrongPass!123")
            scope = UserScope(
                user_id=user.id, username=user.username, role=user.role, status=user.status
            )
            wish = WishlistService(db, LibraryService(db))

            push_svc = WebPushService(
                settings=cfg,
                db_manager=db,
                secret_box=secret_box,
                wishlist_service=wish,
            )
            self.assertFalse(push_svc.is_enabled)
            with self.assertRaises(WebPushDisabledError):
                push_svc.get_public_key()
            with self.assertRaises(WebPushDisabledError):
                push_svc.subscribe(
                    scope,
                    endpoint="https://fcm.googleapis.com/fcm/send/abc",
                    p256dh="p",
                    auth="a",
                )
            res = await push_svc.dispatch_deal_alerts(scope)
            self.assertFalse(res["enabled"])

            # SSRF endpoint validation
            self.assertTrue(is_valid_push_endpoint("https://fcm.googleapis.com/fcm/send/token"))
            self.assertTrue(
                is_valid_push_endpoint("https://updates.push.services.mozilla.com/wpush/v2/abc")
            )
            self.assertTrue(is_valid_push_endpoint("https://web.push.apple.com/Q/abc"))
            self.assertFalse(is_valid_push_endpoint("http://fcm.googleapis.com/fcm/send/token"))
            self.assertFalse(is_valid_push_endpoint("https://127.0.0.1/push"))
            self.assertFalse(is_valid_push_endpoint("https://169.254.169.254/latest/meta-data"))
            self.assertFalse(is_valid_push_endpoint("https://localhost/push"))
            self.assertFalse(is_valid_push_endpoint("https://evil.attacker.example/push"))

            # Enabled VAPID keypair encrypted at rest in system.db
            cfg_on = _make_settings(td, web_push_enabled=True)
            push_on = WebPushService(
                settings=cfg_on,
                db_manager=db,
                secret_box=secret_box,
                wishlist_service=wish,
            )
            pub = push_on.get_public_key()
            self.assertGreater(len(pub), 40)
            with db.system_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM instance_settings WHERE key = 'vapid_private_key'"
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(row["value"].startswith("v1:") or "gAAAA" in row["value"])
            decrypted_priv = secret_box.decrypt(row["value"])
            self.assertNotEqual(decrypted_priv, row["value"])
            with self.assertRaises(WebPushError):
                push_on.subscribe(
                    scope,
                    endpoint="https://127.0.0.1/bad",
                    p256dh="p",
                    auth="a",
                )


class TestPhase4And5DomainBoundaries(unittest.TestCase):
    def test_domain_packages_never_import_mcp_push_http_or_fastapi(self) -> None:
        src_root = Path(__file__).resolve().parents[2] / "src" / "vertex"
        forbidden = {"fastapi", "httpx", "psnawp_api", "websockets", "mcp", "pywebpush"}
        domain_dirs = (
            "identity",
            "catalog",
            "library",
            "activity",
            "analytics",
            "wishlist",
            "migration",
            "infrastructure",
        )
        violations: list[str] = []
        for d in domain_dirs:
            for py_file in (src_root / d).rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            top = alias.name.split(".")[0]
                            if top in forbidden:
                                violations.append(f"{py_file.name}:{top}")
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        top = node.module.split(".")[0]
                        if top in forbidden:
                            violations.append(f"{py_file.name}:{top}")
        self.assertEqual(violations, [])
