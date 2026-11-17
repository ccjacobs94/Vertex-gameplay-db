"""Automated wishlist storefront discovery and price synchronization service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from vertex.catalog.models import normalize_title
from vertex.catalog.service import CatalogService
from vertex.config import Settings
from vertex.identity.models import UserScope
from vertex.identity.service import IdentityService
from vertex.infrastructure.jobs import JobQueue, JobRecord
from vertex.integrations.platforms.base import sanitize_error_message
from vertex.integrations.prices.base import NormalizedPriceQuote, PriceAdapterError
from vertex.integrations.prices.cheapshark import CheapSharkAdapter
from vertex.integrations.prices.gog import GOGDirectAdapter
from vertex.integrations.prices.itad import ITADAdapter
from vertex.integrations.prices.matching import extract_edition_name, matches_canonical_title
from vertex.integrations.prices.psn import PSNStoreGraphQLAdapter
from vertex.integrations.prices.steam import SteamStoreAdapter
from vertex.integrations.prices.xbox import XboxCatalogAdapter
from vertex.wishlist.models import (
    EditionPlatformSku,
    WishlistItemDetail,
    infer_edition_tier,
    normalize_platform_family,
)
from vertex.wishlist.service import WishlistService


class DealPushDispatcher(Protocol):
    """Protocol for optional Web Push deal alerting after price syncs."""

    async def dispatch_deal_alerts(self, scope: UserScope) -> dict[str, Any]: ...


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _is_stale(last_checked_at: str | None, max_age_seconds: int, now: datetime) -> bool:
    if max_age_seconds <= 0:
        return True
    parsed = _parse_iso_utc(last_checked_at)
    if parsed is None:
        return True
    return (now - parsed).total_seconds() >= max_age_seconds


def _active_target_families(item_detail: WishlistItemDetail) -> set[str]:
    if not item_detail.targets:
        return {"PC"}
    return {
        normalize_platform_family(t.platform_family)
        for t in item_detail.targets
        if t.is_active and not t.muted_owned_conflict
    }


class WishlistSyncService:
    """Coordinates SKU discovery, 6-storefront price refreshes, and deal alerts."""

    def __init__(
        self,
        *,
        settings: Settings,
        job_queue: JobQueue,
        identity_service: IdentityService,
        catalog_service: CatalogService,
        wishlist_service: WishlistService,
        itad_adapter: ITADAdapter | None = None,
        cheapshark_adapter: CheapSharkAdapter | None = None,
        steam_adapter: SteamStoreAdapter | None = None,
        gog_adapter: GOGDirectAdapter | None = None,
        xbox_adapter: XboxCatalogAdapter | None = None,
        psn_adapter: PSNStoreGraphQLAdapter | None = None,
        web_push_service: DealPushDispatcher | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self.settings = settings
        self.job_queue = job_queue
        self.identity_service = identity_service
        self.catalog_service = catalog_service
        self.wishlist_service = wishlist_service
        self.itad_adapter = itad_adapter or ITADAdapter()
        self.cheapshark_adapter = cheapshark_adapter or CheapSharkAdapter()
        self.steam_adapter = steam_adapter or SteamStoreAdapter()
        self.gog_adapter = gog_adapter or GOGDirectAdapter()
        self.xbox_adapter = xbox_adapter or XboxCatalogAdapter()
        self.psn_adapter = psn_adapter or PSNStoreGraphQLAdapter(
            product_retrieve_hash=settings.psn_product_retrieve_hash
        )
        self.web_push_service = web_push_service
        self._custom_client_factory = client_factory is not None
        self._custom_adapters = any(
            x is not None
            for x in (
                itad_adapter,
                cheapshark_adapter,
                steam_adapter,
                gog_adapter,
                xbox_adapter,
                psn_adapter,
            )
        )
        self.client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        )

    def _can_call_external(self) -> bool:
        return self.settings.outbound_sync_enabled or self._custom_client_factory or self._custom_adapters

    def _serialize_conflict(self, conflict: Any) -> dict[str, Any]:
        return {
            "already_in_library": bool(conflict.already_in_library),
            "conflicted_platforms": list(conflict.conflicted_platforms),
            "conflict_warning": conflict.conflict_warning,
        }

    async def search_candidates(
        self,
        scope: UserScope,
        query: str,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search local canonical/catalog entries + live storefront candidates with ownership alerts."""
        cleaned = (query or "").strip()
        if not cleaned:
            return {"query": "", "candidates": [], "errors": []}

        cap = max(1, min(25, limit))
        candidates: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        errors: list[str] = []

        # 1. Local canonical games + public catalog metadata
        for canonical, _editions in self.wishlist_service.search_local_canonical(
            scope, cleaned, limit=cap
        ):
            norm = canonical.normalized_title
            if norm in seen_keys:
                continue
            seen_keys.add(norm)
            conflict = self.wishlist_service.check_ownership_conflicts(scope, canonical.title)
            candidates.append(
                {
                    "title": canonical.title,
                    "normalized_title": norm,
                    "release_year": canonical.release_year,
                    "developer": canonical.developer,
                    "publisher": canonical.publisher,
                    "cover_url": canonical.cover_url,
                    "genres": list(canonical.genres),
                    "storefront": "local_canonical",
                    "store_sku_id": str(canonical.id),
                    "platform_hints": [],
                    "current_price_usd": None,
                    "ownership_conflict": self._serialize_conflict(conflict),
                }
            )

        for cat_entry in await self.catalog_service.search_catalog(cleaned, limit=cap):
            norm = cat_entry.normalized_title
            if norm in seen_keys:
                continue
            seen_keys.add(norm)
            conflict = self.wishlist_service.check_ownership_conflicts(scope, cat_entry.title)
            candidates.append(
                {
                    "title": cat_entry.title,
                    "normalized_title": norm,
                    "release_year": cat_entry.release_year,
                    "developer": "",
                    "publisher": "",
                    "cover_url": cat_entry.cover_url,
                    "genres": list(cat_entry.genres),
                    "storefront": "catalog",
                    "store_sku_id": str(cat_entry.steam_appid or cat_entry.normalized_title),
                    "platform_hints": ["PC"] if cat_entry.steam_appid else [],
                    "current_price_usd": None,
                    "ownership_conflict": self._serialize_conflict(conflict),
                }
            )

        # 2. Live storefront candidates (Steam + CheapShark) when outbound allowed or mocked
        if self._can_call_external() and len(candidates) < cap:
            async with self.client_factory() as client:
                try:
                    steam_hits = await self.steam_adapter.search_games(cleaned, client=client)
                    for hit in steam_hits[:cap]:
                        title = str(hit.get("name") or "").strip()
                        aid = str(hit.get("id") or "").strip()
                        if not title or not aid:
                            continue
                        norm = normalize_title(title) or title.lower()
                        if norm in seen_keys or any(c["title"].lower() == title.lower() for c in candidates):
                            continue
                        seen_keys.add(norm)
                        price_obj = hit.get("price") if isinstance(hit.get("price"), dict) else {}
                        final_cents = price_obj.get("final")
                        price_usd = round(float(final_cents) / 100.0, 2) if final_cents is not None else None
                        cover = hit.get("tiny_image") or hit.get("large_capsule_image")
                        conflict = self.wishlist_service.check_ownership_conflicts(scope, title)
                        candidates.append(
                            {
                                "title": title,
                                "normalized_title": norm,
                                "release_year": None,
                                "developer": "",
                                "publisher": "",
                                "cover_url": str(cover) if isinstance(cover, str) and cover else None,
                                "genres": [],
                                "storefront": "steam",
                                "store_sku_id": aid,
                                "platform_hints": ["PC"],
                                "current_price_usd": price_usd,
                                "ownership_conflict": self._serialize_conflict(conflict),
                            }
                        )
                        if len(candidates) >= cap:
                            break
                except Exception as exc:
                    errors.append(sanitize_error_message(f"Steam search failed: {exc}"))

                if len(candidates) < cap:
                    try:
                        cs_hits = await self.cheapshark_adapter.search_games(
                            cleaned, client=client, limit=cap
                        )
                        for hit in cs_hits[:cap]:
                            title = str(hit.get("external") or "").strip()
                            gid = str(hit.get("gameID") or "").strip()
                            if not title or not gid:
                                continue
                            norm = normalize_title(title) or title.lower()
                            if norm in seen_keys or any(c["title"].lower() == title.lower() for c in candidates):
                                continue
                            seen_keys.add(norm)
                            cheapest_raw = hit.get("cheapest")
                            price_usd = round(float(cheapest_raw), 2) if cheapest_raw is not None else None
                            cover = hit.get("thumb")
                            conflict = self.wishlist_service.check_ownership_conflicts(scope, title)
                            candidates.append(
                                {
                                    "title": title,
                                    "normalized_title": title.lower(),
                                    "release_year": None,
                                    "developer": "",
                                    "publisher": "",
                                    "cover_url": str(cover) if isinstance(cover, str) and cover else None,
                                    "genres": [],
                                    "storefront": "cheapshark",
                                    "store_sku_id": gid,
                                    "platform_hints": ["PC"],
                                    "current_price_usd": price_usd,
                                    "ownership_conflict": self._serialize_conflict(conflict),
                                }
                            )
                            if len(candidates) >= cap:
                                break
                    except Exception as exc:
                        errors.append(sanitize_error_message(f"CheapShark search failed: {exc}"))

        return {
            "query": cleaned,
            "candidates": candidates[:cap],
            "errors": errors,
        }

    def enqueue_wishlist_sync(
        self,
        scope: UserScope,
        *,
        wishlist_item_id: int | None = None,
        force: bool = False,
        max_age_seconds: int = 3600,
    ) -> JobRecord:
        scope.require_active()
        payload: dict[str, Any] = {
            "force": bool(force),
            "max_age_seconds": max(0, int(max_age_seconds)),
        }
        if wishlist_item_id is not None:
            payload["wishlist_item_id"] = int(wishlist_item_id)
        dedup_suffix = str(wishlist_item_id) if wishlist_item_id is not None else "all"
        return self.job_queue.enqueue(
            scope,
            kind="wishlist_sync",
            provider="wishlist",
            payload=payload,
            dedup_key=f"wishlist_sync:{scope.user_id}:{dedup_suffix}",
        )

    def enqueue_scheduled_sync_iteration(self) -> int:
        if not self.settings.wishlist_sync_enabled or not self._can_call_external():
            return 0
        grace_age = max(60, int(float(self.settings.wishlist_sync_interval_seconds) * 0.85))
        count = 0
        for scope in self.identity_service.list_active_user_scopes():
            if not self.wishlist_service.has_active_wishlist_items(scope):
                continue
            self.enqueue_wishlist_sync(
                scope,
                force=False,
                max_age_seconds=grace_age,
            )
            count += 1
        return count

    async def process_job(self, job: JobRecord, scope: UserScope, worker_id: str) -> JobRecord | None:
        try:
            raw_item_id = job.payload.get("wishlist_item_id")
            item_id = int(raw_item_id) if raw_item_id is not None else None
            summary = await self.sync_wishlist_for_user(
                scope,
                wishlist_item_id=item_id,
                force=bool(job.payload.get("force", False)),
                max_age_seconds=int(job.payload.get("max_age_seconds", 3600)),
            )
            return self.job_queue.complete_job(job.id, worker_id, result=summary)
        except PriceAdapterError as exc:
            return self.job_queue.fail_job(
                job.id,
                worker_id,
                error_code=exc.code,
                error_message=exc.safe_message,
                retryable=exc.retryable,
                throttle_seconds=exc.throttle_seconds,
            )
        except Exception as exc:
            return self.job_queue.fail_job(
                job.id,
                worker_id,
                error_code="wishlist_sync_error",
                error_message=sanitize_error_message(str(exc)),
                retryable=True,
            )

    async def discover_skus_for_item(
        self,
        scope: UserScope,
        *,
        item_detail: WishlistItemDetail,
        client: httpx.AsyncClient,
        existing_storefronts: set[str],
        allowed_families: set[str] | None = None,
    ) -> list[str]:
        """Discover missing storefront SKUs and cover art for an active wishlist item."""
        errors: list[str] = []
        canonical = item_detail.canonical_game
        title = canonical.title
        target_families = _active_target_families(item_detail)
        if allowed_families is not None:
            target_families &= allowed_families
        if not target_families:
            return errors

        if not canonical.cover_url:
            local_cover = self.wishlist_service.find_local_library_cover(scope, title)
            if local_cover:
                self.wishlist_service.update_canonical_cover_if_missing(scope, canonical.id, local_cover)

        base_edition = self.wishlist_service.upsert_discovered_edition(
            scope,
            canonical_game_id=canonical.id,
            edition_name="Standard Edition",
            edition_tier="base",
            is_base_edition=True,
        )
        edition_cache: dict[str, Any] = {"Standard Edition": base_edition}

        def _get_or_create_edition(ed_name: str):
            cached = edition_cache.get(ed_name)
            if cached is not None:
                return cached
            ed_tier, tier_rank = infer_edition_tier(ed_name)
            created = self.wishlist_service.upsert_discovered_edition(
                scope,
                canonical_game_id=canonical.id,
                edition_name=ed_name,
                edition_tier=ed_tier,
                tier_rank=tier_rank,
                is_base_edition=False,
            )
            edition_cache[ed_name] = created
            return created

        # PC discovery: Steam, GOG Direct, CheapShark
        if "PC" in target_families:
            if "steam" not in existing_storefronts:
                try:
                    steam_results = await self.steam_adapter.search_games(title, client=client)
                    matched = next(
                        (
                            it
                            for it in steam_results
                            if matches_canonical_title(title, str(it.get("name") or ""))
                        ),
                        None,
                    )
                    if matched and matched.get("id") is not None:
                        aid = str(matched["id"]).strip()
                        self.wishlist_service.upsert_discovered_sku(
                            scope,
                            edition_id=base_edition.id,
                            platform_family="PC",
                            platform_label="PC (Steam)",
                            storefront="steam",
                            store_sku_id=aid,
                            store_url=f"https://store.steampowered.com/app/{aid}",
                            is_authorized_seller=True,
                            is_drm_free=False,
                        )
                        details = await self.steam_adapter.get_app_details(aid, client=client)
                        if details:
                            hdr = details.get("header_image")
                            if isinstance(hdr, str) and hdr.strip():
                                self.wishlist_service.update_canonical_cover_if_missing(
                                    scope, canonical.id, hdr.strip()
                                )
                            for pkg_group in details.get("package_groups") or []:
                                if not isinstance(pkg_group, dict):
                                    continue
                                for sub in pkg_group.get("subs") or []:
                                    if not isinstance(sub, dict):
                                        continue
                                    opt_text = str(sub.get("option_text") or "")
                                    pkg_id = str(sub.get("packageid") or "").strip()
                                    if not pkg_id:
                                        continue
                                    ed_name = extract_edition_name(opt_text, canonical_title=title)
                                    if ed_name == "Standard Edition":
                                        continue
                                    ed_tier, tier_rank = infer_edition_tier(ed_name)
                                    ed = self.wishlist_service.upsert_discovered_edition(
                                        scope,
                                        canonical_game_id=canonical.id,
                                        edition_name=ed_name,
                                        edition_tier=ed_tier,
                                        tier_rank=tier_rank,
                                        is_base_edition=False,
                                    )
                                    self.wishlist_service.upsert_discovered_sku(
                                        scope,
                                        edition_id=ed.id,
                                        platform_family="PC",
                                        platform_label="PC (Steam)",
                                        storefront="steam",
                                        store_sku_id=f"{aid}:{pkg_id}",
                                        store_url=f"https://store.steampowered.com/app/{aid}",
                                        is_authorized_seller=True,
                                        is_drm_free=False,
                                    )
                except Exception as exc:
                    errors.append(sanitize_error_message(f"Steam discovery error for '{title}': {exc}"))

            if "gog" not in existing_storefronts:
                try:
                    gog_results = await self.gog_adapter.search_catalog(title, client=client)
                    seen_gog_ids: set[str] = set()
                    for p in gog_results:
                        ptype = str(p.get("productType") or "").strip().lower()
                        if ptype in ("pack", "dlc"):
                            continue
                        prod_title = str(p.get("title") or "").strip()
                        if not matches_canonical_title(title, prod_title):
                            continue
                        pid = str(p.get("id") or "").strip()
                        if not pid or pid in seen_gog_ids:
                            continue
                        seen_gog_ids.add(pid)
                        slug = str(p.get("slug") or "").strip()
                        cover_cand = (
                            p.get("coverVertical")
                            or p.get("coverHorizontal")
                            or p.get("image")
                        )
                        if isinstance(cover_cand, str) and cover_cand.strip():
                            self.wishlist_service.update_canonical_cover_if_missing(
                                scope, canonical.id, cover_cand.strip()
                            )
                        ed_name = extract_edition_name(prod_title, canonical_title=title)
                        target_ed = _get_or_create_edition(ed_name)
                        self.wishlist_service.upsert_discovered_sku(
                            scope,
                            edition_id=target_ed.id,
                            platform_family="PC",
                            platform_label="PC (GOG)",
                            storefront="gog",
                            store_sku_id=pid,
                            store_url=(
                                f"https://www.gog.com/en/game/{slug}"
                                if slug
                                else f"https://www.gog.com/game/{pid}"
                            ),
                            is_authorized_seller=True,
                            is_drm_free=True,
                        )
                        if len(seen_gog_ids) >= 5:
                            break
                except Exception as exc:
                    errors.append(sanitize_error_message(f"GOG discovery error for '{title}': {exc}"))

            if "cheapshark" not in existing_storefronts:
                try:
                    cs_results = await self.cheapshark_adapter.search_games(title, client=client)
                    seen_cs_ids: set[str] = set()
                    for g in cs_results:
                        cs_title = str(g.get("external") or "").strip()
                        if not matches_canonical_title(title, cs_title):
                            continue
                        gid = str(g.get("gameID") or "").strip()
                        if not gid or gid in seen_cs_ids:
                            continue
                        seen_cs_ids.add(gid)
                        ed_name = extract_edition_name(cs_title, canonical_title=title)
                        target_ed = _get_or_create_edition(ed_name)
                        details: dict[str, Any] | None = None
                        try:
                            details = await self.cheapshark_adapter.get_game_details(gid, client=client)
                        except Exception:
                            details = None
                        atl: float | None = None
                        if isinstance(details, dict):
                            cheapest_raw = (details.get("cheapestPriceEver") or {}).get("price")
                            if cheapest_raw is not None:
                                try:
                                    atl = round(float(cheapest_raw), 2)
                                except (TypeError, ValueError):
                                    atl = None
                        self.wishlist_service.upsert_discovered_sku(
                            scope,
                            edition_id=target_ed.id,
                            platform_family="PC",
                            platform_label="PC (CheapShark)",
                            storefront="cheapshark",
                            store_sku_id=gid,
                            historical_low_usd=atl,
                            store_url="",
                            is_authorized_seller=True,
                            is_drm_free=False,
                        )
                        if isinstance(details, dict):
                            for deal in details.get("deals") or []:
                                if not isinstance(deal, dict):
                                    continue
                                sid = str(deal.get("storeID") or "")
                                sf_name = self.cheapshark_adapter.STORE_MAP.get(sid)
                                if sf_name not in ("epic", "humble", "gmg", "fanatical"):
                                    continue
                                deal_id = str(deal.get("dealID") or "").strip()
                                plat_label = (
                                    f"PC ({sf_name.upper() if sf_name != 'gmg' else 'Green Man Gaming'})"
                                )
                                self.wishlist_service.upsert_discovered_sku(
                                    scope,
                                    edition_id=target_ed.id,
                                    platform_family="PC",
                                    platform_label=plat_label,
                                    storefront=sf_name,
                                    store_sku_id=gid,
                                    historical_low_usd=atl,
                                    store_url=(
                                        f"https://www.cheapshark.com/redirect?dealID={deal_id}"
                                        if deal_id
                                        else ""
                                    ),
                                    is_authorized_seller=True,
                                    is_drm_free=False,
                                )
                        if len(seen_cs_ids) >= 3:
                            break
                except Exception as exc:
                    errors.append(sanitize_error_message(f"CheapShark discovery error for '{title}': {exc}"))

        # Xbox discovery
        if "Xbox" in target_families and "xbox_store" not in existing_storefronts:
            try:
                xb_results = await self.xbox_adapter.search_autosuggest(title, client=client)
                seen_xb_ids: set[str] = set()
                for p in xb_results:
                    xb_title = str(p.get("Title") or "").strip()
                    if not matches_canonical_title(title, xb_title):
                        continue
                    pid = str(p.get("Id") or "").strip()
                    if not pid or pid in seen_xb_ids:
                        continue
                    seen_xb_ids.add(pid)
                    ed_name = extract_edition_name(xb_title, canonical_title=title)
                    target_ed = _get_or_create_edition(ed_name)
                    self.wishlist_service.upsert_discovered_sku(
                        scope,
                        edition_id=target_ed.id,
                        platform_family="Xbox",
                        platform_label="Xbox Series X|S",
                        storefront="xbox_store",
                        store_sku_id=pid,
                        store_url=f"https://www.xbox.com/en-us/games/store/-/{pid}",
                        is_authorized_seller=True,
                        is_drm_free=False,
                    )
                    if len(seen_xb_ids) >= 3:
                        break
            except Exception as exc:
                errors.append(sanitize_error_message(f"Xbox discovery error for '{title}': {exc}"))

        # PlayStation discovery
        if "PlayStation" in target_families and "psn_store" not in existing_storefronts:
            try:
                psn_results = await self.psn_adapter.search_products(title, client=client)
                seen_psn_ids: set[str] = set()
                for p in psn_results:
                    psn_title = str(p.get("title") or "").strip()
                    if not matches_canonical_title(title, psn_title):
                        continue
                    pid = str(p.get("id") or "").strip()
                    if not pid or pid in seen_psn_ids:
                        continue
                    seen_psn_ids.add(pid)
                    ed_name = extract_edition_name(psn_title, canonical_title=title)
                    target_ed = _get_or_create_edition(ed_name)
                    self.wishlist_service.upsert_discovered_sku(
                        scope,
                        edition_id=target_ed.id,
                        platform_family="PlayStation",
                        platform_label="PS5",
                        storefront="psn_store",
                        store_sku_id=pid,
                        store_url=f"https://store.playstation.com/en-us/product/{pid}",
                        is_authorized_seller=True,
                        is_drm_free=False,
                    )
                    if len(seen_psn_ids) >= 3:
                        break
            except Exception as exc:
                errors.append(sanitize_error_message(f"PSN discovery error for '{title}': {exc}"))

        return errors

    async def sync_wishlist_for_user(
        self,
        scope: UserScope,
        *,
        wishlist_item_id: int | None = None,
        force: bool = False,
        max_age_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Run SKU discovery + batched storefront price sync + deal alert evaluation for one user."""
        items = self.wishlist_service.list_items(scope, status="active")
        if wishlist_item_id is not None:
            items = [it for it in items if it.id == wishlist_item_id]

        if not items:
            return {
                "status": "ok",
                "items_checked": 0,
                "skus_synced": 0,
                "prices_recorded": 0,
                "skipped_fresh": 0,
                "deals_triggered": 0,
                "push_sent": 0,
                "errors": [],
            }

        if not self._can_call_external():
            return {
                "status": "skipped",
                "items_checked": len(items),
                "skus_synced": 0,
                "prices_recorded": 0,
                "skipped_fresh": 0,
                "deals_triggered": 0,
                "push_sent": 0,
                "errors": ["Outbound wishlist sync disabled (VERTEX_OUTBOUND_SYNC_ENABLED=false)"],
            }

        errors: list[str] = []
        now = datetime.now(UTC)

        async with self.client_factory() as client:
            # Phase 1: Discover missing SKUs per active, unmuted target family
            for item_detail in items:
                have = {sku.storefront for sku in item_detail.skus}
                active_families = _active_target_families(item_detail)
                discover_families: set[str] = set()
                for fam in active_families:
                    fam_skus = [
                        s
                        for s in item_detail.skus
                        if normalize_platform_family(s.platform_family) == fam
                    ]
                    fam_needs_check = (
                        force
                        or not fam_skus
                        or not any(
                            not _is_stale(s.last_checked_at, max_age_seconds, now)
                            for s in fam_skus
                        )
                    )
                    if not fam_needs_check:
                        continue
                    if (
                        (fam == "PC" and not ({"steam", "gog", "cheapshark"} <= have))
                        or (fam == "Xbox" and "xbox_store" not in have)
                        or (fam == "PlayStation" and "psn_store" not in have)
                    ):
                        discover_families.add(fam)
                if discover_families:
                    disc_errors = await self.discover_skus_for_item(
                        scope,
                        item_detail=item_detail,
                        client=client,
                        existing_storefronts=have,
                        allowed_families=discover_families,
                    )
                    errors.extend(disc_errors)

            # Re-fetch active items after discovery so edition_id -> canonical_game_id & active families match
            refreshed_items = self.wishlist_service.list_items(scope, status="active")
            if wishlist_item_id is not None:
                refreshed_items = [it for it in refreshed_items if it.id == wishlist_item_id]

            edition_to_canonical: dict[int, int] = {}
            canonical_active_families: dict[int, set[str]] = {}
            for item_detail in refreshed_items:
                canonical_active_families[item_detail.canonical_game_id] = _active_target_families(
                    item_detail
                )
                for ed in item_detail.editions:
                    edition_to_canonical[ed.id] = item_detail.canonical_game_id

            # Phase 2: Freshness check + batched queries across all 6 adapters
            all_skus = self.wishlist_service.list_active_skus_for_sync(scope)
            target_skus = []
            for sku in all_skus:
                cid = edition_to_canonical.get(sku.edition_id)
                if cid is None:
                    continue
                allowed_fams = canonical_active_families.get(cid, set())
                if sku.platform_family and normalize_platform_family(sku.platform_family) not in allowed_fams:
                    continue
                target_skus.append(sku)

            stale_skus: list[EditionPlatformSku] = []
            skipped_fresh = 0
            for sku in target_skus:
                if force or _is_stale(sku.last_checked_at, max_age_seconds, now):
                    stale_skus.append(sku)
                else:
                    skipped_fresh += 1

            def _native_adapter_key(sf: str) -> str | None:
                if sf == "steam":
                    return "steam"
                if sf == "gog":
                    return "gog"
                if sf in ("cheapshark", "epic", "humble", "gmg", "fanatical"):
                    return "cheapshark"
                if sf == "xbox_store":
                    return "xbox_store"
                if sf == "psn_store":
                    return "psn_store"
                return None

            by_storefront: dict[str, list[EditionPlatformSku]] = {}
            for sku in stale_skus:
                if sku.storefront == "itad" or (sku.itad_plain and sku.itad_plain.strip()):
                    by_storefront.setdefault("itad", []).append(sku)
                native_key = _native_adapter_key(sku.storefront)
                if native_key is not None:
                    by_storefront.setdefault(native_key, []).append(sku)

            skus_synced = 0
            prices_recorded = 0
            synced_row_ids: set[int] = set()

            async def _fetch_adapter(
                sf_key: str, sku_rows: list[EditionPlatformSku]
            ) -> list[NormalizedPriceQuote]:
                if sf_key == "itad":
                    ids = list(
                        dict.fromkeys(
                            (r.itad_plain or r.store_sku_id).strip()
                            for r in sku_rows
                            if (r.itad_plain and r.itad_plain.strip()) or r.store_sku_id
                        )
                    )
                else:
                    ids = list(dict.fromkeys(r.store_sku_id for r in sku_rows if r.store_sku_id))
                if not ids:
                    return []
                if sf_key == "steam":
                    return await self.steam_adapter.fetch_prices(store_sku_ids=ids, client=client)
                if sf_key == "gog":
                    return await self.gog_adapter.fetch_prices(store_sku_ids=ids, client=client)
                if sf_key == "cheapshark":
                    return await self.cheapshark_adapter.fetch_prices(store_sku_ids=ids, client=client)
                if sf_key == "itad":
                    return await self.itad_adapter.fetch_prices(
                        store_sku_ids=ids,
                        client=client,
                        api_key=self.settings.itad_api_key,
                    )
                if sf_key == "xbox_store":
                    return await self.xbox_adapter.fetch_prices(store_sku_ids=ids, client=client)
                if sf_key == "psn_store":
                    return await self.psn_adapter.fetch_prices(store_sku_ids=ids, client=client)
                return []

            adapter_order = ("itad", "cheapshark", "steam", "gog", "xbox_store", "psn_store")
            for sf_key in adapter_order:
                sku_rows = [
                    r for r in (by_storefront.get(sf_key) or []) if r.id not in synced_row_ids
                ]
                if not sku_rows:
                    continue
                try:
                    quotes = await _fetch_adapter(sf_key, sku_rows)
                except Exception as exc:
                    errors.append(sanitize_error_message(f"{sf_key} price sync failed: {exc}"))
                    continue

                quote_by_sf_and_id: dict[tuple[str, str], NormalizedPriceQuote] = {}
                best_quote_by_sku_id: dict[str, NormalizedPriceQuote] = {}
                for quote in quotes:
                    if quote.currency.upper() != "USD":
                        errors.append(
                            f"Skipped non-USD quote ({quote.currency}) for {sf_key}:{quote.store_sku_id}"
                        )
                        continue
                    sf_pair = (quote.storefront, quote.store_sku_id)
                    prev_pair = quote_by_sf_and_id.get(sf_pair)
                    if prev_pair is None or quote.current_price_usd < prev_pair.current_price_usd:
                        quote_by_sf_and_id[sf_pair] = quote
                    prev = best_quote_by_sku_id.get(quote.store_sku_id)
                    if prev is None or quote.current_price_usd < prev.current_price_usd:
                        best_quote_by_sku_id[quote.store_sku_id] = quote

                for row in sku_rows:
                    if sf_key == "itad":
                        qid = (row.itad_plain or row.store_sku_id).strip()
                        q = quote_by_sf_and_id.get((row.storefront, qid)) or best_quote_by_sku_id.get(qid)
                    else:
                        q = quote_by_sf_and_id.get((row.storefront, row.store_sku_id))
                    if q is None:
                        continue
                    synced_row_ids.add(row.id)
                    skus_synced += 1
                    canonical_id = edition_to_canonical.get(row.edition_id)
                    if q.image_url and canonical_id is not None:
                        self.wishlist_service.update_canonical_cover_if_missing(
                            scope, canonical_id, q.image_url
                        )
                    self.wishlist_service.record_sku_price(
                        scope,
                        sku_id=row.id,
                        price_usd=q.current_price_usd,
                        regular_price_usd=q.regular_price_usd,
                        discount_pct=q.discount_pct,
                        historical_low_usd=q.historical_low_usd,
                        store_url=q.store_url or None,
                        source=f"sync:{sf_key}",
                        recorded_at=q.retrieved_at,
                    )
                    prices_recorded += 1

        # Phase 3: Evaluate deals & optional Web Push
        eval_res = self.wishlist_service.evaluate_deals(
            scope, wishlist_item_id=wishlist_item_id
        )
        deals_triggered = len(eval_res.deals)
        push_sent = 0
        if self.web_push_service is not None and self.settings.web_push_enabled and deals_triggered > 0:
            try:
                push_res = await self.web_push_service.dispatch_deal_alerts(scope)
                push_sent = int(push_res.get("sent") or 0)
            except Exception as exc:
                errors.append(sanitize_error_message(f"Web push dispatch failed: {exc}"))

        return {
            "status": "ok" if not errors else "partial",
            "items_checked": len(items),
            "skus_synced": skus_synced,
            "prices_recorded": prices_recorded,
            "skipped_fresh": skipped_fresh,
            "deals_triggered": deals_triggered,
            "push_sent": push_sent,
            "errors": errors,
        }
