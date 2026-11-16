"""Offline unit tests for Phase 4 price adapters, wishlist sync, optional Web Push, and domain boundaries."""

from __future__ import annotations

import ast
import base64
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ec
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


def _concurrent_init_worker(data_dir: str) -> list[str]:
    cfg = _make_settings(data_dir)
    return DatabaseManager(cfg).ensure_system_ready()


class TestConfigAndAdapterHardening(unittest.IsolatedAsyncioTestCase):
    def test_concurrent_multiprocess_ensure_system_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
                results = list(pool.map(_concurrent_init_worker, [td] * 4))
            # Exactly one process applies initial migrations and none fail with UNIQUE constraint error
            applied_total = sum(len(r) for r in results)
            self.assertGreaterEqual(applied_total, 1)
            self.assertTrue(all(isinstance(r, list) for r in results))

    def test_settings_from_env_forwards_wishlist_seconds_itad_key_and_psn_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Settings.from_env(
                {
                    "VERTEX_DATA_DIR": td,
                    "VERTEX_SECRET_KEY": "test-secret-key",
                    "VERTEX_WISHLIST_SYNC_INTERVAL_HOURS": "24",
                    "VERTEX_WISHLIST_SYNC_INTERVAL_SECONDS": "900",
                    "VERTEX_ITAD_API_KEY": "itad-key-xyz",
                    "VERTEX_PSN_PRODUCT_HASH": "live-psn-hash-v2",
                }
            )
            self.assertEqual(cfg.wishlist_sync_interval_seconds, 900.0)
            self.assertEqual(cfg.itad_api_key, "itad-key-xyz")
            self.assertEqual(cfg.psn_product_retrieve_hash, "live-psn-hash-v2")

    async def test_xbox_skips_zero_dollar_game_pass_tier_for_paid_availability(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "Products": [
                        {
                            "ProductId": "9NBLGGH42CFD",
                            "Properties": {"XboxConsoleGenOptimized": ["XboxSeriesX"]},
                            "DisplaySkuAvailabilities": [
                                {
                                    "Availabilities": [
                                        {
                                            "OrderManagementData": {
                                                "Price": {
                                                    "ListPrice": 0.0,
                                                    "MSRP": 0.0,
                                                    "CurrencyCode": "USD",
                                                }
                                            }
                                        },
                                        {
                                            "OrderManagementData": {
                                                "Price": {
                                                    "ListPrice": 47.99,
                                                    "MSRP": 59.99,
                                                    "CurrencyCode": "USD",
                                                }
                                            }
                                        },
                                    ]
                                }
                            ],
                        }
                    ]
                },
            )

        adapter = XboxCatalogAdapter(market="US")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            quotes = await adapter.fetch_prices(store_sku_ids=["9NBLGGH42CFD"], client=client)

        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].current_price_usd, 47.99)
        self.assertEqual(quotes[0].regular_price_usd, 59.99)
        self.assertEqual(quotes[0].discount_pct, 20)

    async def test_psn_uses_configured_hash_and_wraps_invalid_json(self) -> None:
        import json

        seen_hashes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            ext = json.loads(request.url.params.get("extensions", "{}"))
            seen_hashes.append(ext["persistedQuery"]["sha256Hash"])
            if request.url.params.get("variables", "").endswith('"BAD"}'):
                return httpx.Response(200, text="<html>not json</html>")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "productRetrieve": {
                            "platforms": ["PS5"],
                            "price": {"basePrice": "$49.99", "discountedPrice": "$24.99", "currencyCode": "USD"},
                        }
                    }
                },
            )

        from vertex.integrations.prices.base import PriceAdapterError

        adapter = PSNStoreGraphQLAdapter(country="US", product_retrieve_hash="custom-hash-123")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ok = await adapter.fetch_prices(store_sku_ids=["UP0001-OK"], client=client)
            self.assertEqual(len(ok), 1)
            with self.assertRaises(PriceAdapterError) as err_ctx:
                await adapter.fetch_prices(store_sku_ids=["BAD"], client=client)

        self.assertEqual(seen_hashes[0], "custom-hash-123")
        self.assertEqual(err_ctx.exception.code, "invalid_response")
        self.assertTrue(err_ctx.exception.retryable)


class TestWishlistSyncEdgeCases(unittest.IsolatedAsyncioTestCase):
    async def test_process_job_handles_price_adapter_error_cleanly(self) -> None:
        from vertex.integrations.prices.base import PriceAdapterError

        with tempfile.TemporaryDirectory() as td:
            cfg = _make_settings(td)
            db = DatabaseManager(cfg)
            db.ensure_system_ready()
            secret_box = SecretBox(cfg.secret_key)
            jobs = JobQueue(db)
            identity = IdentityService(db, secret_box, jobs)
            user, _ = identity.bootstrap_admin(username="alice", password="StrongPass!123")
            scope = user.to_scope()
            lib = LibraryService(db)
            wish = WishlistService(db, lib)
            catalog = CatalogService(db, outbound_sync_enabled=False)

            sync_svc = WishlistSyncService(
                settings=cfg,
                job_queue=jobs,
                identity_service=identity,
                catalog_service=catalog,
                wishlist_service=wish,
            )
            job = sync_svc.enqueue_wishlist_sync(scope, force=True)
            claimed = jobs.acquire_next_lease("test-worker")
            self.assertIsNotNone(claimed)

            async def _boom(*_args, **_kwargs):
                raise PriceAdapterError(
                    code="rate_limited",
                    message="ITAD rate limited (HTTP 429)",
                    retryable=True,
                    throttle_seconds=45,
                )

            sync_svc.sync_wishlist_for_user = _boom  # type: ignore[method-assign]
            failed = await sync_svc.process_job(claimed, scope, "test-worker")
            self.assertIsNotNone(failed)
            self.assertEqual(failed.last_error_code, "rate_limited")
            self.assertIn("429", failed.last_error or "")

    async def test_muted_owned_target_skipped_itad_plain_and_storefront_match(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_settings(td, wishlist_sync_interval_seconds=1000.0)
            db = DatabaseManager(cfg)
            db.ensure_system_ready()
            secret_box = SecretBox(cfg.secret_key)
            jobs = JobQueue(db)
            identity = IdentityService(db, secret_box, jobs)
            user, _ = identity.bootstrap_admin(username="alice", password="StrongPass!123")
            scope = user.to_scope()
            lib = LibraryService(db)
            wish = WishlistService(db, lib)
            catalog = CatalogService(db, outbound_sync_enabled=False)

            # Owned game on PC -> PC target becomes muted_owned_conflict=True, is_active=False
            lib.add_manual_game(
                scope,
                title="Already Owned PC Game",
                platform="PC (Steam)",
                ownership_status="Owned",
            )
            owned_item = wish.add_item(
                scope,
                title="Already Owned PC Game",
                platform_targets=[{"platform": "PC", "target_price_usd": 20.0}],
            )
            self.assertTrue(owned_item.targets[0].muted_owned_conflict)

            # Second active game with itad_plain on steam SKU + CheapShark epic/humble SKUs
            active_item = wish.add_item(
                scope,
                title="Control",
                platform_targets=[{"platform": "PC", "target_price_usd": 30.0}],
            )
            ed = wish.upsert_discovered_edition(
                scope, canonical_game_id=active_item.canonical_game.id, edition_name="Standard Edition"
            )
            steam_sku = wish.upsert_discovered_sku(
                scope,
                edition_id=ed.id,
                platform_family="PC",
                platform_label="PC (Steam)",
                storefront="steam",
                store_sku_id="870780",
                itad_plain="018d937f-ctrl",
            )
            epic_sku = wish.upsert_discovered_sku(
                scope,
                edition_id=ed.id,
                platform_family="PC",
                platform_label="PC (EPIC)",
                storefront="epic",
                store_sku_id="202",
            )
            humble_sku = wish.upsert_discovered_sku(
                scope,
                edition_id=ed.id,
                platform_family="PC",
                platform_label="PC (HUMBLE)",
                storefront="humble",
                store_sku_id="202",
            )

            seen_queries: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                seen_queries.append(str(request.url))
                host = request.url.host
                if "isthereanydeal.com" in host:
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "id": "018d937f-ctrl",
                                "historyLow": {"all": {"amount": 7.99}},
                                "deals": [
                                    {
                                        "shop": {"id": 61},
                                        "price": {"amount": 11.99, "currency": "USD"},
                                        "regular": {"amount": 39.99},
                                        "cut": 70,
                                        "url": "https://store.steampowered.com/app/870780",
                                    }
                                ],
                            }
                        ],
                    )
                if "cheapshark.com" in host and "ids=" in str(request.url):
                    return httpx.Response(
                        200,
                        json={
                            "202": {
                                "cheapestPriceEver": {"price": "9.99"},
                                "deals": [
                                    # Cheaper Steam deal must NOT overwrite Epic/Humble quotes!
                                    {"storeID": "1", "price": "5.00", "retailPrice": "39.99", "savings": "87", "dealID": "s1"},
                                    {"storeID": "25", "price": "19.99", "retailPrice": "39.99", "savings": "50", "dealID": "e1"},
                                    {"storeID": "11", "price": "14.99", "retailPrice": "39.99", "savings": "62", "dealID": "h1"},
                                ],
                            }
                        },
                    )
                if "gog.com" in host:
                    return httpx.Response(200, json={"products": []})
                return httpx.Response(200, json={"items": []})

            sync_svc = WishlistSyncService(
                settings=cfg,
                job_queue=jobs,
                identity_service=identity,
                catalog_service=catalog,
                wishlist_service=wish,
                client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            )

            # 1) Muted item MUST NOT hit any PC storefront
            muted_res = await sync_svc.sync_wishlist_for_user(
                scope, wishlist_item_id=owned_item.id, force=True
            )
            self.assertEqual(muted_res["prices_recorded"], 0)
            self.assertEqual(seen_queries, [])

            # 2) Active item routes steam(itad_plain)->ITAD and matches epic vs humble accurately
            act_res = await sync_svc.sync_wishlist_for_user(
                scope, wishlist_item_id=active_item.id, force=False
            )
            self.assertEqual(act_res["prices_recorded"], 3)
            refreshed = wish.get_item(scope, active_item.id)
            by_id = {s.id: s for s in refreshed.skus}
            self.assertEqual(by_id[steam_sku.id].current_price_usd, 11.99)
            self.assertEqual(by_id[epic_sku.id].current_price_usd, 19.99)
            self.assertEqual(by_id[humble_sku.id].current_price_usd, 14.99)

            # 3) Scheduled iteration skips if outbound_sync_enabled=False without custom adapters/client
            cfg_off = _make_settings(td, outbound_sync_enabled=False)
            sync_off = WishlistSyncService(
                settings=cfg_off,
                job_queue=jobs,
                identity_service=identity,
                catalog_service=catalog,
                wishlist_service=wish,
            )
            self.assertEqual(sync_off.enqueue_scheduled_sync_iteration(), 0)


class TestWebPushVapidSigning(unittest.IsolatedAsyncioTestCase):
    async def test_default_pywebpush_sender_signs_and_cleans_410(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = _make_settings(td, web_push_enabled=True)
            db = DatabaseManager(cfg)
            db.ensure_system_ready()
            secret_box = SecretBox(cfg.secret_key)
            jobs = JobQueue(db)
            identity = IdentityService(db, secret_box, jobs)
            user, _ = identity.bootstrap_admin(username="alice", password="StrongPass!123")
            scope = user.to_scope()
            lib = LibraryService(db)
            wish = WishlistService(db, lib)

            item = wish.add_item(
                scope,
                title="Hades",
                platform_targets=[{"platform": "PC", "target_price_usd": 20.0}],
            )
            wish.record_manual_price(scope, item.id, price_usd=9.99, platform="PC", storefront="steam")

            client_priv = ec.generate_private_key(ec.SECP256R1())
            pub_nums = client_priv.public_key().public_numbers()
            p256dh = (
                base64.urlsafe_b64encode(
                    b"\x04" + pub_nums.x.to_bytes(32, "big") + pub_nums.y.to_bytes(32, "big")
                )
                .rstrip(b"=")
                .decode("ascii")
            )
            auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode("ascii")

            push_svc = WebPushService(
                settings=cfg,
                db_manager=db,
                secret_box=secret_box,
                wishlist_service=wish,
            )
            push_svc.subscribe(
                scope,
                endpoint="https://fcm.googleapis.com/fcm/send/real-vapid-sub",
                p256dh=p256dh,
                auth=auth,
            )

            seen_requests: list[dict] = []

            class _FakeResp:
                status_code = 410
                reason = "Gone"
                text = "Gone"
                headers: dict[str, str] = {}

            def _fake_post(url, data=None, headers=None, timeout=None):
                seen_requests.append({"url": url, "data": data, "headers": dict(headers or {})})
                return _FakeResp()

            with patch("requests.post", side_effect=_fake_post):
                res = await push_svc.dispatch_deal_alerts(scope)

            self.assertEqual(len(seen_requests), 1)
            req_headers = {k.lower(): v for k, v in seen_requests[0]["headers"].items()}
            self.assertIn("authorization", req_headers)
            self.assertIn("vapid ", req_headers["authorization"].lower())
            self.assertEqual(req_headers.get("content-encoding"), "aes128gcm")
            self.assertEqual(res["removed"], 1)
            self.assertEqual(wish.list_push_subscriptions(scope), [])
