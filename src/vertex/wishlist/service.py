"""Personal wishlist, edition SKU, price history, deal evaluation, and idempotent purchase service."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from vertex.catalog.models import normalize_title
from vertex.catalog.taxonomy import validate_genres
from vertex.infrastructure.errors import NotFoundError, ValidationError
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.library.service import LibraryService
from vertex.wishlist.models import (
    EDITION_TIER_RANKS,
    VALID_WISHLIST_STATUSES,
    CanonicalGame,
    EditionPlatformSku,
    GameEdition,
    OwnershipConflict,
    PriceHistoryEntry,
    PushSubscriptionRecord,
    WishlistDeal,
    WishlistDealEvaluation,
    WishlistHistoryDetail,
    WishlistItemDetail,
    WishlistPlatformTarget,
    WishlistPriceRecordResult,
    WishlistPurchaseResult,
    infer_edition_tier,
    normalize_platform_family,
)
from vertex.wishlist.repository import (
    row_to_canonical,
    row_to_edition,
    row_to_price_history,
    row_to_push_subscription,
    row_to_sku,
    row_to_target,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matches_platform_target(sku: EditionPlatformSku, target: WishlistPlatformTarget) -> bool:
    if target.platform_family and sku.platform_family and target.platform_family != sku.platform_family:
        return False
    if target.allowed_storefronts and sku.storefront not in target.allowed_storefronts:
        return False
    return True


def _append_price_history(
    conn: sqlite3.Connection,
    *,
    sku_id: int,
    storefront: str,
    price_usd: float,
    regular_price_usd: float | None,
    discount_pct: int,
    is_all_time_low: bool,
    source: str,
    recorded_at: str,
) -> None:
    last_row = conn.execute(
        "SELECT price_usd, recorded_at FROM price_history WHERE sku_id = ? ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (sku_id,),
    ).fetchone()
    today_prefix = recorded_at[:10]
    if (
        last_row is not None
        and str(last_row["recorded_at"]).startswith(today_prefix)
        and abs(float(last_row["price_usd"]) - price_usd) < 0.001
    ):
        return
    conn.execute(
        """
        INSERT INTO price_history (
            sku_id, storefront, price_usd, regular_price_usd, discount_pct,
            is_all_time_low, source, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sku_id,
            storefront,
            price_usd,
            regular_price_usd,
            discount_pct,
            1 if is_all_time_low else 0,
            source,
            recorded_at,
        ),
    )


class WishlistService:
    """Scoped application service managing wishlist items, editions, prices, deals, and purchases."""

    def __init__(self, db_manager: DatabaseManager, library_service: LibraryService) -> None:
        self.db_manager = db_manager
        self.library_service = library_service

    def check_ownership_conflicts(
        self,
        scope: UserScope,
        title: str,
        *,
        library_game_id: int | None = None,
    ) -> OwnershipConflict:
        scope.require_active()
        norm_title = normalize_title(title)
        games = self.library_service.list_games(scope)

        matched = []
        for g in games:
            if library_game_id is not None and (g.id == library_game_id or g.canonical_id == library_game_id):
                matched.append(g)
            elif norm_title and normalize_title(g.title) == norm_title:
                matched.append(g)

        conflicted: set[str] = set()
        details: dict[str, tuple[str, float]] = {}
        for g in matched:
            has_ownership = (
                g.ownership_status in ("Owned", "Subscription")
                or g.is_game_pass
                or g.playtime_hours > 0.0
                or g.achievements_unlocked > 0
            )
            fam = normalize_platform_family(g.platform)
            if has_ownership:
                conflicted.add(fam)
                prev_status, prev_hrs = details.get(fam, (g.ownership_status, 0.0))
                details[fam] = (prev_status, max(prev_hrs, g.playtime_hours))

        warning: str | None = None
        if conflicted:
            parts: list[str] = []
            for fam in sorted(conflicted):
                st, hrs = details[fam]
                if hrs > 0:
                    parts.append(f"{st} on {fam} ({hrs:.1f} hrs played)")
                else:
                    parts.append(f"{st} on {fam}")
            warning = f"Already in Library: {', '.join(parts)}"

        return OwnershipConflict(
            already_in_library=len(conflicted) > 0,
            conflicted_platforms=tuple(sorted(conflicted)),
            conflict_warning=warning,
        )

    def _hydrate_item(
        self,
        conn: sqlite3.Connection,
        scope: UserScope,
        item_row: sqlite3.Row,
        *,
        conflict: OwnershipConflict | None = None,
    ) -> WishlistItemDetail:
        item_id = int(item_row["id"])
        cg_row = conn.execute(
            "SELECT * FROM canonical_games WHERE id = ?",
            (item_row["canonical_game_id"],),
        ).fetchone()
        if cg_row is None:
            raise NotFoundError(f"Canonical game for wishlist item {item_id} not found")
        canonical = row_to_canonical(cg_row)

        target_rows = conn.execute(
            "SELECT * FROM wishlist_platform_targets WHERE wishlist_item_id = ? ORDER BY id ASC",
            (item_id,),
        ).fetchall()
        targets = tuple(row_to_target(r) for r in target_rows)

        ed_rows = conn.execute(
            "SELECT * FROM game_editions WHERE canonical_game_id = ? ORDER BY tier_rank ASC, id ASC",
            (canonical.id,),
        ).fetchall()
        editions: list[GameEdition] = []
        all_skus: list[EditionPlatformSku] = []
        for er in ed_rows:
            sku_rows = conn.execute(
                "SELECT * FROM edition_platform_skus WHERE edition_id = ? ORDER BY id ASC",
                (er["id"],),
            ).fetchall()
            skus_tuple = tuple(row_to_sku(sr) for sr in sku_rows)
            all_skus.extend(skus_tuple)
            editions.append(row_to_edition(er, skus_tuple))

        eff_conflict = conflict or self.check_ownership_conflicts(
            scope,
            canonical.title,
            library_game_id=canonical.library_game_id,
        )
        return WishlistItemDetail(
            id=item_id,
            canonical_game_id=canonical.id,
            preferred_edition_id=item_row["preferred_edition_id"],
            allow_smart_upgrade=bool(item_row["allow_smart_upgrade"]),
            status=item_row["status"],
            notes=item_row["notes"] or "",
            added_via=item_row["added_via"] or "web",
            created_at=item_row["created_at"],
            updated_at=item_row["updated_at"],
            canonical_game=canonical,
            targets=targets,
            editions=tuple(editions),
            skus=tuple(all_skus),
            conflict=eff_conflict,
        )

    def _parse_platform_targets(
        self,
        raw_targets: list[str | dict[str, Any]] | None,
        conflict: OwnershipConflict,
        *,
        force_track_owned: bool,
    ) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        if not raw_targets:
            return parsed
        for entry in raw_targets:
            if isinstance(entry, str):
                fam = normalize_platform_family(entry)
                price = 19.99
                storefronts: list[str] = []
                is_conflicted = fam in conflict.conflicted_platforms
                is_active = not (is_conflicted and not force_track_owned)
                muted = bool(is_conflicted and not force_track_owned)
            elif isinstance(entry, dict):
                fam = normalize_platform_family(str(entry.get("platform") or entry.get("platform_family") or "PC"))
                try:
                    price = float(entry.get("target_price_usd", 19.99))
                except (TypeError, ValueError) as exc:
                    raise ValidationError("target_price_usd must be numeric") from exc
                if price < 0:
                    raise ValidationError("target_price_usd cannot be negative")
                raw_sf = entry.get("allowed_storefronts") or entry.get("storefronts") or []
                storefronts = [str(s).strip().lower() for s in raw_sf if str(s).strip()]
                is_conflicted = fam in conflict.conflicted_platforms
                if is_conflicted and not force_track_owned:
                    is_active = False
                    muted = True
                elif "is_active" in entry:
                    is_active = bool(entry["is_active"])
                    muted = bool(is_conflicted and not is_active)
                else:
                    is_active = True
                    muted = False
            else:
                continue
            parsed.append(
                {
                    "platform_family": fam,
                    "target_price_usd": round(price, 2),
                    "allowed_storefronts": storefronts,
                    "is_active": is_active,
                    "muted_owned_conflict": muted,
                }
            )
        return parsed

    def add_item(
        self,
        scope: UserScope,
        *,
        title: str,
        platform_targets: list[str | dict[str, Any]] | None = None,
        release_year: int | None = None,
        developer: str = "",
        publisher: str = "",
        cover_url: str | None = None,
        genres: list[str] | None = None,
        critic_score: int | None = None,
        allow_smart_upgrade: bool = True,
        preferred_edition_id: int | None = None,
        notes: str = "",
        added_via: str = "web",
        force_track_owned: bool = False,
    ) -> WishlistItemDetail:
        scope.require_active()
        cleaned_title = (title or "").strip()
        if not cleaned_title:
            raise ValidationError("Wishlist title is required")
        norm = normalize_title(cleaned_title)
        if not norm:
            raise ValidationError("Wishlist title must contain alphanumeric characters")

        valid_genres = validate_genres(genres)
        conflict = self.check_ownership_conflicts(scope, cleaned_title)
        parsed_targets = self._parse_platform_targets(
            platform_targets,
            conflict,
            force_track_owned=force_track_owned,
        )
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO canonical_games (
                    normalized_title, title, release_year, developer, publisher,
                    cover_url, genres_json, critic_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_title) DO UPDATE SET
                    title = excluded.title,
                    release_year = COALESCE(excluded.release_year, canonical_games.release_year),
                    developer = CASE WHEN excluded.developer != '' THEN excluded.developer ELSE canonical_games.developer END,
                    publisher = CASE WHEN excluded.publisher != '' THEN excluded.publisher ELSE canonical_games.publisher END,
                    cover_url = COALESCE(excluded.cover_url, canonical_games.cover_url),
                    genres_json = CASE WHEN excluded.genres_json != '[]' THEN excluded.genres_json ELSE canonical_games.genres_json END,
                    critic_score = COALESCE(excluded.critic_score, canonical_games.critic_score),
                    updated_at = excluded.updated_at
                """,
                (
                    norm,
                    cleaned_title,
                    release_year,
                    developer or "",
                    publisher or "",
                    cover_url,
                    json.dumps(valid_genres),
                    critic_score,
                    now_iso,
                    now_iso,
                ),
            )
            cg_row = conn.execute(
                "SELECT * FROM canonical_games WHERE normalized_title = ?",
                (norm,),
            ).fetchone()
            assert cg_row is not None
            cg_id = int(cg_row["id"])

            conn.execute(
                """
                INSERT INTO game_editions (
                    canonical_game_id, edition_name, edition_tier, tier_rank,
                    is_base_edition, included_dlc_notes, cover_url, created_at, updated_at
                ) VALUES (?, 'Standard Edition', 'base', 1, 1, '', ?, ?, ?)
                ON CONFLICT(canonical_game_id, edition_name) DO UPDATE SET
                    cover_url = COALESCE(excluded.cover_url, game_editions.cover_url),
                    updated_at = excluded.updated_at
                """,
                (cg_id, cover_url, now_iso, now_iso),
            )

            conn.execute(
                """
                INSERT INTO wishlist_items (
                    canonical_game_id, preferred_edition_id, allow_smart_upgrade,
                    status, notes, added_via, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                ON CONFLICT(canonical_game_id) DO UPDATE SET
                    preferred_edition_id = COALESCE(excluded.preferred_edition_id, wishlist_items.preferred_edition_id),
                    allow_smart_upgrade = excluded.allow_smart_upgrade,
                    status = 'active',
                    notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE wishlist_items.notes END,
                    updated_at = excluded.updated_at
                """,
                (
                    cg_id,
                    preferred_edition_id,
                    1 if allow_smart_upgrade else 0,
                    notes or "",
                    added_via or "web",
                    now_iso,
                    now_iso,
                ),
            )
            wi_row = conn.execute(
                "SELECT * FROM wishlist_items WHERE canonical_game_id = ?",
                (cg_id,),
            ).fetchone()
            assert wi_row is not None
            wi_id = int(wi_row["id"])

            for pt in parsed_targets:
                conn.execute(
                    """
                    INSERT INTO wishlist_platform_targets (
                        wishlist_item_id, platform_family, allowed_storefronts,
                        target_price_usd, is_active, muted_owned_conflict,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wishlist_item_id, platform_family) DO UPDATE SET
                        allowed_storefronts = excluded.allowed_storefronts,
                        target_price_usd = excluded.target_price_usd,
                        is_active = excluded.is_active,
                        muted_owned_conflict = excluded.muted_owned_conflict,
                        updated_at = excluded.updated_at
                    """,
                    (
                        wi_id,
                        pt["platform_family"],
                        json.dumps(pt["allowed_storefronts"]),
                        pt["target_price_usd"],
                        1 if pt["is_active"] else 0,
                        1 if pt["muted_owned_conflict"] else 0,
                        now_iso,
                        now_iso,
                    ),
                )

            return self._hydrate_item(conn, scope, wi_row, conflict=conflict)

    def list_items(self, scope: UserScope, *, status: str = "active") -> list[WishlistItemDetail]:
        scope.require_active()
        cleaned_status = (status or "active").strip().lower()
        if cleaned_status != "all" and cleaned_status not in VALID_WISHLIST_STATUSES:
            raise ValidationError(f"Invalid wishlist status '{cleaned_status}'")

        with self.db_manager.library_connection(scope) as conn:
            if cleaned_status == "all":
                rows = conn.execute("SELECT * FROM wishlist_items ORDER BY updated_at DESC, id DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM wishlist_items WHERE status = ? ORDER BY updated_at DESC, id DESC",
                    (cleaned_status,),
                ).fetchall()
            return [self._hydrate_item(conn, scope, r) for r in rows]

    def get_item(self, scope: UserScope, item_id: int) -> WishlistItemDetail:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Wishlist item {item_id} not found")
            return self._hydrate_item(conn, scope, row)

    def update_item(
        self,
        scope: UserScope,
        item_id: int,
        *,
        allow_smart_upgrade: bool | None = None,
        preferred_edition_id: int | None = None,
        status: str | None = None,
        notes: str | None = None,
    ) -> WishlistItemDetail:
        scope.require_active()
        if status is not None and status not in VALID_WISHLIST_STATUSES:
            raise ValidationError(f"Invalid wishlist status '{status}'")

        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            existing = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            if existing is None:
                raise NotFoundError(f"Wishlist item {item_id} not found")

            if preferred_edition_id is not None:
                ed = conn.execute(
                    "SELECT id FROM game_editions WHERE id = ? AND canonical_game_id = ?",
                    (preferred_edition_id, existing["canonical_game_id"]),
                ).fetchone()
                if ed is None:
                    raise ValidationError("preferred_edition_id does not belong to this game")

            updates: dict[str, Any] = {"updated_at": now_iso}
            if allow_smart_upgrade is not None:
                updates["allow_smart_upgrade"] = 1 if allow_smart_upgrade else 0
            if preferred_edition_id is not None:
                updates["preferred_edition_id"] = preferred_edition_id
            if status is not None:
                updates["status"] = status
            if notes is not None:
                updates["notes"] = notes

            assigns = ", ".join(f"{col} = ?" for col in updates)
            conn.execute(
                f"UPDATE wishlist_items SET {assigns} WHERE id = ?",
                (*updates.values(), item_id),
            )
            updated = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            return self._hydrate_item(conn, scope, updated)

    def update_platforms(
        self,
        scope: UserScope,
        item_id: int,
        *,
        platform_targets: list[str | dict[str, Any]] | None = None,
        remove_platforms: list[str] | None = None,
        allow_smart_upgrade: bool | None = None,
        force_track_owned: bool = False,
    ) -> WishlistItemDetail:
        scope.require_active()
        current = self.get_item(scope, item_id)
        conflict = self.check_ownership_conflicts(
            scope,
            current.canonical_game.title,
            library_game_id=current.canonical_game.library_game_id,
        )
        parsed_targets = self._parse_platform_targets(
            platform_targets,
            conflict,
            force_track_owned=force_track_owned,
        )
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            if allow_smart_upgrade is not None:
                conn.execute(
                    "UPDATE wishlist_items SET allow_smart_upgrade = ?, updated_at = ? WHERE id = ?",
                    (1 if allow_smart_upgrade else 0, now_iso, item_id),
                )
            for p in remove_platforms or []:
                fam = normalize_platform_family(p)
                conn.execute(
                    "DELETE FROM wishlist_platform_targets WHERE wishlist_item_id = ? AND platform_family = ?",
                    (item_id, fam),
                )
            for pt in parsed_targets:
                conn.execute(
                    """
                    INSERT INTO wishlist_platform_targets (
                        wishlist_item_id, platform_family, allowed_storefronts,
                        target_price_usd, is_active, muted_owned_conflict,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wishlist_item_id, platform_family) DO UPDATE SET
                        allowed_storefronts = excluded.allowed_storefronts,
                        target_price_usd = excluded.target_price_usd,
                        is_active = excluded.is_active,
                        muted_owned_conflict = excluded.muted_owned_conflict,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item_id,
                        pt["platform_family"],
                        json.dumps(pt["allowed_storefronts"]),
                        pt["target_price_usd"],
                        1 if pt["is_active"] else 0,
                        1 if pt["muted_owned_conflict"] else 0,
                        now_iso,
                        now_iso,
                    ),
                )
            row = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            return self._hydrate_item(conn, scope, row, conflict=conflict)

    def delete_item(self, scope: UserScope, item_id: int) -> None:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            cur = conn.execute("DELETE FROM wishlist_items WHERE id = ?", (item_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Wishlist item {item_id} not found")

    def record_manual_price(
        self,
        scope: UserScope,
        item_id: int,
        *,
        price_usd: float,
        platform: str = "PC",
        storefront: str = "manual",
        edition_name: str = "Standard Edition",
        edition_tier: str | None = None,
        regular_price_usd: float | None = None,
        store_url: str = "",
        store_sku_id: str | None = None,
        is_drm_free: bool = False,
        is_authorized_seller: bool = True,
        source: str = "manual",
    ) -> WishlistPriceRecordResult:
        scope.require_active()
        price_val = round(float(price_usd), 2)
        if price_val < 0:
            raise ValidationError("price_usd cannot be negative")
        reg_val = round(float(regular_price_usd), 2) if regular_price_usd is not None else None
        if reg_val is not None and reg_val < 0:
            raise ValidationError("regular_price_usd cannot be negative")

        fam = normalize_platform_family(platform)
        cleaned_sf = (storefront or "manual").strip().lower() or "manual"
        cleaned_ed = (edition_name or "Standard Edition").strip() or "Standard Edition"
        inferred_tier, inferred_rank = infer_edition_tier(cleaned_ed)
        eff_tier = (edition_tier or inferred_tier).strip().lower()
        eff_rank = EDITION_TIER_RANKS.get(eff_tier, inferred_rank)
        is_base = 1 if (eff_rank == 1 or "standard" in cleaned_ed.lower()) else 0
        now_iso = _utc_now_iso()

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            wi_row = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            if wi_row is None:
                raise NotFoundError(f"Wishlist item {item_id} not found")
            cg_id = int(wi_row["canonical_game_id"])

            conn.execute(
                """
                INSERT INTO game_editions (
                    canonical_game_id, edition_name, edition_tier, tier_rank,
                    is_base_edition, included_dlc_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
                ON CONFLICT(canonical_game_id, edition_name) DO UPDATE SET
                    edition_tier = excluded.edition_tier,
                    tier_rank = excluded.tier_rank,
                    is_base_edition = excluded.is_base_edition,
                    updated_at = excluded.updated_at
                """,
                (cg_id, cleaned_ed, eff_tier, eff_rank, is_base, now_iso, now_iso),
            )
            ed_row = conn.execute(
                "SELECT id FROM game_editions WHERE canonical_game_id = ? AND edition_name = ?",
                (cg_id, cleaned_ed),
            ).fetchone()
            assert ed_row is not None
            ed_id = int(ed_row["id"])

            sku_key = (store_sku_id or f"{fam.lower()}:{cleaned_ed.lower()}").strip()
            existing_sku = conn.execute(
                "SELECT * FROM edition_platform_skus WHERE edition_id = ? AND storefront = ? AND store_sku_id = ?",
                (ed_id, cleaned_sf, sku_key),
            ).fetchone()

            discount_pct = 0
            if reg_val and reg_val > 0 and price_val <= reg_val:
                discount_pct = max(0, int(round((1.0 - (price_val / reg_val)) * 100)))

            prev_low = (
                float(existing_sku["historical_low_usd"])
                if existing_sku is not None and existing_sku["historical_low_usd"] is not None
                else None
            )
            is_atl = prev_low is None or price_val <= prev_low + 0.001
            new_low = min(prev_low, price_val) if prev_low is not None else price_val

            conn.execute(
                """
                INSERT INTO edition_platform_skus (
                    edition_id, platform_family, platform_label, storefront, store_sku_id,
                    store_url, current_price_usd, regular_price_usd, discount_pct,
                    historical_low_usd, is_authorized_seller, is_drm_free,
                    last_checked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edition_id, storefront, store_sku_id) DO UPDATE SET
                    platform_family = excluded.platform_family,
                    platform_label = excluded.platform_label,
                    store_url = CASE WHEN excluded.store_url != '' THEN excluded.store_url ELSE edition_platform_skus.store_url END,
                    current_price_usd = excluded.current_price_usd,
                    regular_price_usd = COALESCE(excluded.regular_price_usd, edition_platform_skus.regular_price_usd),
                    discount_pct = excluded.discount_pct,
                    historical_low_usd = excluded.historical_low_usd,
                    is_authorized_seller = excluded.is_authorized_seller,
                    is_drm_free = excluded.is_drm_free,
                    last_checked_at = excluded.last_checked_at,
                    updated_at = excluded.updated_at
                """,
                (
                    ed_id,
                    fam,
                    platform.strip() or fam,
                    cleaned_sf,
                    sku_key,
                    store_url or "",
                    price_val,
                    reg_val,
                    discount_pct,
                    new_low,
                    1 if is_authorized_seller else 0,
                    1 if is_drm_free else 0,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            sku_row = conn.execute(
                "SELECT id FROM edition_platform_skus WHERE edition_id = ? AND storefront = ? AND store_sku_id = ?",
                (ed_id, cleaned_sf, sku_key),
            ).fetchone()
            assert sku_row is not None
            sku_id = int(sku_row["id"])

            _append_price_history(
                conn,
                sku_id=sku_id,
                storefront=cleaned_sf,
                price_usd=price_val,
                regular_price_usd=reg_val,
                discount_pct=discount_pct,
                is_all_time_low=is_atl,
                source=source,
                recorded_at=now_iso,
            )
            conn.execute("UPDATE wishlist_items SET updated_at = ? WHERE id = ?", (now_iso, item_id))
            wi_refreshed = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            item_detail = self._hydrate_item(conn, scope, wi_refreshed)

        return WishlistPriceRecordResult(
            sku_id=sku_id,
            price_usd=price_val,
            is_all_time_low=is_atl,
            historical_low_usd=new_low,
            item=item_detail,
        )

    def evaluate_deals(
        self,
        scope: UserScope,
        *,
        wishlist_item_id: int | None = None,
        platform: str | None = None,
        smart_upgrades_only: bool = False,
        include_above_target: bool = False,
    ) -> WishlistDealEvaluation:
        scope.require_active()
        if wishlist_item_id is not None:
            single = self.get_item(scope, wishlist_item_id)
            items = [single] if single.status == "active" else []
        else:
            items = self.list_items(scope, status="active")

        deals: list[WishlistDeal] = []
        plat_fam_filter = normalize_platform_family(platform) if platform and platform.strip() else None

        for item in items:
            if not item.targets or not item.skus:
                continue
            ed_by_id = {ed.id: ed for ed in item.editions}
            for target in item.targets:
                if not target.is_active or target.muted_owned_conflict:
                    continue
                if plat_fam_filter and target.platform_family != plat_fam_filter:
                    continue

                base_prices = [
                    float(s.current_price_usd)
                    for s in item.skus
                    if s.current_price_usd is not None
                    and _matches_platform_target(s, target)
                    and ed_by_id.get(s.edition_id) is not None
                    and ed_by_id[s.edition_id].tier_rank == 1
                ]
                base_price_now = min(base_prices) if base_prices else None

                for sku in item.skus:
                    if sku.current_price_usd is None or not _matches_platform_target(sku, target):
                        continue
                    price_f = float(sku.current_price_usd)
                    if price_f <= 0.0:
                        continue
                    if not include_above_target and price_f > target.target_price_usd:
                        continue
                    ed = ed_by_id.get(sku.edition_id)
                    if ed is None:
                        continue
                    if (
                        not item.allow_smart_upgrade
                        and ed.tier_rank > 1
                        and item.preferred_edition_id != ed.id
                    ):
                        continue

                    is_smart = bool(item.allow_smart_upgrade and ed.tier_rank > 1)
                    if smart_upgrades_only and not is_smart:
                        continue
                    is_atl = bool(sku.historical_low_usd is not None and price_f <= sku.historical_low_usd + 0.01)

                    headline = (
                        f"SMART UPGRADE: {ed.edition_name} is ${price_f:.2f} "
                        f"(meets your ${target.target_price_usd:.2f} Base target!)"
                        if is_smart
                        else f"TARGET MET: {ed.edition_name} is ${price_f:.2f} (target ${target.target_price_usd:.2f})"
                    )
                    deals.append(
                        WishlistDeal(
                            wishlist_item_id=item.id,
                            canonical_game_id=item.canonical_game_id,
                            game_title=item.canonical_game.title,
                            cover_url=item.canonical_game.cover_url,
                            edition_id=ed.id,
                            edition_name=ed.edition_name,
                            edition_tier=ed.edition_tier,
                            tier_rank=ed.tier_rank,
                            sku_id=sku.id,
                            platform_family=sku.platform_family,
                            platform_label=sku.platform_label,
                            storefront=sku.storefront,
                            store_url=sku.store_url,
                            current_price_usd=round(price_f, 2),
                            regular_price_usd=sku.regular_price_usd,
                            discount_pct=sku.discount_pct,
                            target_price_usd=round(target.target_price_usd, 2),
                            savings_below_target_usd=round(target.target_price_usd - price_f, 2),
                            current_base_edition_price_usd=base_price_now,
                            is_smart_upgrade=is_smart,
                            is_all_time_low=is_atl,
                            is_drm_free=sku.is_drm_free,
                            deal_headline=headline,
                        )
                    )

        deals.sort(key=lambda d: (-int(d.is_smart_upgrade), -d.tier_rank, d.current_price_usd))
        return WishlistDealEvaluation(
            total_deals=len(deals),
            smart_upgrades_count=sum(1 for d in deals if d.is_smart_upgrade),
            all_time_lows_count=sum(1 for d in deals if d.is_all_time_low),
            deals=tuple(deals),
        )

    def mark_purchased(
        self,
        scope: UserScope,
        item_id: int,
        *,
        platform: str = "PC",
        edition_name: str | None = None,
        price_paid_usd: float | None = None,
        completion_status: str = "Plan to Play",
    ) -> WishlistPurchaseResult:
        """Idempotently convert a wishlist item into an owned library game."""
        scope.require_active()
        if price_paid_usd is not None and float(price_paid_usd) < 0:
            raise ValidationError("price_paid_usd cannot be negative")

        current = self.get_item(scope, item_id)
        existing_lib_id = current.canonical_game.library_game_id
        if existing_lib_id is not None:
            existing_game = self.library_service.find_game(scope, existing_lib_id)
            if existing_game is not None:
                if current.status != "purchased":
                    current = self.update_item(scope, item_id, status="purchased")
                return WishlistPurchaseResult(
                    item=current,
                    library_game_id=existing_game.id,
                    already_purchased=True,
                )

        ed_str = f" {edition_name.strip()}" if edition_name and edition_name.strip() else ""
        price_str = f" for ${float(price_paid_usd):.2f}" if price_paid_usd is not None else ""
        notes = f"Purchased{ed_str}{price_str}".strip()
        if current.canonical_game.developer or current.canonical_game.publisher:
            notes += f" | Dev: {current.canonical_game.developer} Pub: {current.canonical_game.publisher}"

        game, created = self.library_service.create_or_get_game_by_external_id(
            scope,
            external_id=f"wishlist:{item_id}",
            title=current.canonical_game.title,
            platform=(platform or "PC").strip() or "PC",
            source="Wishlist",
            completion_status=completion_status,
            ownership_status="Owned",
            genres=list(current.canonical_game.genres),
            release_year=current.canonical_game.release_year,
            critic_score=current.canonical_game.critic_score,
            cover_url=current.canonical_game.cover_url,
            notes=notes,
        )

        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                "UPDATE wishlist_items SET status = 'purchased', updated_at = ? WHERE id = ?",
                (now_iso, item_id),
            )
            conn.execute(
                "UPDATE canonical_games SET library_game_id = ?, updated_at = ? WHERE id = ?",
                (game.id, now_iso, current.canonical_game_id),
            )
            wi_row = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (item_id,)).fetchone()
            refreshed = self._hydrate_item(conn, scope, wi_row)

        return WishlistPurchaseResult(
            item=refreshed,
            library_game_id=game.id,
            already_purchased=not created,
        )

    def get_price_history(
        self,
        scope: UserScope,
        item_id: int,
        *,
        limit: int = 50,
    ) -> WishlistHistoryDetail:
        scope.require_active()
        item = self.get_item(scope, item_id)
        capped = max(1, min(200, int(limit)))
        history_by_sku: dict[int, list[PriceHistoryEntry]] = {}
        with self.db_manager.library_connection(scope) as conn:
            for sku in item.skus:
                rows = conn.execute(
                    "SELECT * FROM price_history WHERE sku_id = ? ORDER BY recorded_at DESC, id DESC LIMIT ?",
                    (sku.id, capped),
                ).fetchall()
                history_by_sku[sku.id] = [row_to_price_history(r) for r in rows]
        return WishlistHistoryDetail(item=item, price_history_by_sku=history_by_sku)

    def find_item_by_title(self, scope: UserScope, title: str) -> WishlistItemDetail | None:
        scope.require_active()
        cleaned = (title or "").strip()
        if not cleaned:
            return None
        norm = normalize_title(cleaned)
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute(
                """
                SELECT wi.*
                FROM wishlist_items wi
                JOIN canonical_games cg ON cg.id = wi.canonical_game_id
                WHERE LOWER(cg.title) = LOWER(?) OR cg.normalized_title = ?
                ORDER BY wi.id DESC
                LIMIT 1
                """,
                (cleaned, norm),
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_item(conn, scope, row)

    def search_local_canonical(
        self,
        scope: UserScope,
        query: str,
        *,
        limit: int = 5,
    ) -> list[tuple[CanonicalGame, tuple[GameEdition, ...]]]:
        scope.require_active()
        q = (query or "").strip()
        if not q:
            return []
        norm_q = normalize_title(q)
        capped = max(1, min(20, int(limit)))
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute(
                """
                SELECT * FROM canonical_games
                WHERE LOWER(title) LIKE ? OR normalized_title LIKE ?
                ORDER BY CASE WHEN LOWER(title) = LOWER(?) THEN 0 ELSE 1 END, id DESC
                LIMIT ?
                """,
                (f"%{q.lower()}%", f"%{norm_q or q.lower()}%", q, capped),
            ).fetchall()
            out: list[tuple[CanonicalGame, tuple[GameEdition, ...]]] = []
            for cg_row in rows:
                cg = row_to_canonical(cg_row)
                ed_rows = conn.execute(
                    "SELECT * FROM game_editions WHERE canonical_game_id = ? ORDER BY tier_rank ASC, id ASC",
                    (cg.id,),
                ).fetchall()
                eds: list[GameEdition] = []
                for er in ed_rows:
                    sku_rows = conn.execute(
                        "SELECT * FROM edition_platform_skus WHERE edition_id = ? ORDER BY id ASC",
                        (er["id"],),
                    ).fetchall()
                    eds.append(row_to_edition(er, tuple(row_to_sku(sr) for sr in sku_rows)))
                out.append((cg, tuple(eds)))
            return out

    def find_local_library_cover(self, scope: UserScope, title: str) -> str | None:
        scope.require_active()
        cleaned = (title or "").strip()
        if not cleaned:
            return None
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute(
                """
                SELECT cover_url FROM games
                WHERE cover_url IS NOT NULL AND TRIM(cover_url) != ''
                  AND (LOWER(title) = LOWER(?) OR LOWER(title) LIKE LOWER(?) || '%')
                ORDER BY id ASC
                LIMIT 1
                """,
                (cleaned, cleaned),
            ).fetchone()
            return str(row["cover_url"]) if row and row["cover_url"] else None

    def update_canonical_cover_if_missing(
        self,
        scope: UserScope,
        canonical_game_id: int,
        cover_url: str,
        *,
        allow_upgrade_steam_600x900: bool = False,
    ) -> bool:
        scope.require_active()
        cleaned = (cover_url or "").strip()
        if not cleaned:
            return False
        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            row = conn.execute(
                "SELECT cover_url FROM canonical_games WHERE id = ?",
                (canonical_game_id,),
            ).fetchone()
            if row is None:
                return False
            cur = (row["cover_url"] or "").strip()
            should_set = not cur
            if (
                not should_set
                and allow_upgrade_steam_600x900
                and cur.endswith("/library_600x900.jpg")
                and bool(re.search(r"/[0-9a-f]{40}/", cleaned))
            ):
                should_set = True
            if not should_set:
                return False
            conn.execute(
                "UPDATE canonical_games SET cover_url = ?, updated_at = ? WHERE id = ?",
                (cleaned, now_iso, canonical_game_id),
            )
            return True

    def upsert_discovered_edition(
        self,
        scope: UserScope,
        *,
        canonical_game_id: int,
        edition_name: str,
        edition_tier: str | None = None,
        tier_rank: int | None = None,
        is_base_edition: bool | None = None,
        cover_url: str | None = None,
    ) -> GameEdition:
        scope.require_active()
        cleaned_ed = (edition_name or "Standard Edition").strip() or "Standard Edition"
        inf_tier, inf_rank = infer_edition_tier(cleaned_ed)
        eff_tier = (edition_tier or inf_tier).strip().lower()
        eff_rank = int(tier_rank) if tier_rank is not None else EDITION_TIER_RANKS.get(eff_tier, inf_rank)
        eff_base = (
            (1 if is_base_edition else 0)
            if is_base_edition is not None
            else (1 if (eff_rank == 1 or "standard" in cleaned_ed.lower()) else 0)
        )
        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO game_editions (
                    canonical_game_id, edition_name, edition_tier, tier_rank,
                    is_base_edition, included_dlc_notes, cover_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)
                ON CONFLICT(canonical_game_id, edition_name) DO UPDATE SET
                    edition_tier = excluded.edition_tier,
                    tier_rank = excluded.tier_rank,
                    is_base_edition = excluded.is_base_edition,
                    cover_url = COALESCE(game_editions.cover_url, excluded.cover_url),
                    updated_at = excluded.updated_at
                """,
                (
                    canonical_game_id,
                    cleaned_ed,
                    eff_tier,
                    eff_rank,
                    eff_base,
                    cover_url,
                    now_iso,
                    now_iso,
                ),
            )
            ed_row = conn.execute(
                "SELECT * FROM game_editions WHERE canonical_game_id = ? AND edition_name = ?",
                (canonical_game_id, cleaned_ed),
            ).fetchone()
            assert ed_row is not None
            sku_rows = conn.execute(
                "SELECT * FROM edition_platform_skus WHERE edition_id = ? ORDER BY id ASC",
                (ed_row["id"],),
            ).fetchall()
            return row_to_edition(ed_row, tuple(row_to_sku(sr) for sr in sku_rows))

    def upsert_discovered_sku(
        self,
        scope: UserScope,
        *,
        edition_id: int,
        platform_family: str,
        platform_label: str,
        storefront: str,
        store_sku_id: str,
        store_url: str = "",
        current_price_usd: float | None = None,
        regular_price_usd: float | None = None,
        discount_pct: int = 0,
        historical_low_usd: float | None = None,
        is_authorized_seller: bool = True,
        is_drm_free: bool = False,
        last_checked_at: str | None = None,
        source: str = "wishlist_sync",
        itad_plain: str | None = None,
    ) -> EditionPlatformSku:
        scope.require_active()
        fam = normalize_platform_family(platform_family)
        cleaned_sf = (storefront or "manual").strip().lower() or "manual"
        cleaned_sku = (store_sku_id or "").strip()
        if not cleaned_sku:
            raise ValidationError("store_sku_id is required")

        cur_val = round(float(current_price_usd), 2) if current_price_usd is not None else None
        if cur_val is not None and cur_val < 0:
            raise ValidationError("current_price_usd cannot be negative")
        reg_val = round(float(regular_price_usd), 2) if regular_price_usd is not None else None
        if reg_val is not None and reg_val < 0:
            raise ValidationError("regular_price_usd cannot be negative")
        ext_low = round(float(historical_low_usd), 2) if historical_low_usd is not None else None
        now_iso = _utc_now_iso()
        checked_iso = last_checked_at or (now_iso if cur_val is not None else None)

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM edition_platform_skus WHERE edition_id = ? AND storefront = ? AND store_sku_id = ?",
                (edition_id, cleaned_sf, cleaned_sku),
            ).fetchone()
            prev_low = (
                float(existing["historical_low_usd"])
                if existing is not None and existing["historical_low_usd"] is not None
                else None
            )

            low_candidates = [v for v in (prev_low, ext_low, cur_val) if v is not None and v > 0]
            if not low_candidates and cur_val is not None:
                low_candidates = [v for v in (prev_low, ext_low, cur_val) if v is not None]
            new_low = min(low_candidates) if low_candidates else prev_low

            eff_disc = int(discount_pct or 0)
            if eff_disc == 0 and cur_val is not None and reg_val and reg_val > 0 and cur_val < reg_val:
                eff_disc = max(0, int(round((1.0 - (cur_val / reg_val)) * 100)))

            conn.execute(
                """
                INSERT INTO edition_platform_skus (
                    edition_id, platform_family, platform_label, storefront, store_sku_id,
                    itad_plain, store_url, current_price_usd, regular_price_usd, discount_pct,
                    historical_low_usd, is_authorized_seller, is_drm_free,
                    last_checked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edition_id, storefront, store_sku_id) DO UPDATE SET
                    platform_family = excluded.platform_family,
                    platform_label = excluded.platform_label,
                    itad_plain = COALESCE(excluded.itad_plain, edition_platform_skus.itad_plain),
                    store_url = CASE WHEN excluded.store_url != '' THEN excluded.store_url ELSE edition_platform_skus.store_url END,
                    current_price_usd = COALESCE(excluded.current_price_usd, edition_platform_skus.current_price_usd),
                    regular_price_usd = COALESCE(excluded.regular_price_usd, edition_platform_skus.regular_price_usd),
                    discount_pct = CASE WHEN excluded.current_price_usd IS NOT NULL THEN excluded.discount_pct ELSE edition_platform_skus.discount_pct END,
                    historical_low_usd = COALESCE(excluded.historical_low_usd, edition_platform_skus.historical_low_usd),
                    is_authorized_seller = excluded.is_authorized_seller,
                    is_drm_free = excluded.is_drm_free,
                    last_checked_at = COALESCE(excluded.last_checked_at, edition_platform_skus.last_checked_at),
                    updated_at = excluded.updated_at
                """,
                (
                    edition_id,
                    fam,
                    (platform_label or fam).strip(),
                    cleaned_sf,
                    cleaned_sku,
                    itad_plain,
                    store_url or "",
                    cur_val,
                    reg_val,
                    eff_disc,
                    new_low,
                    1 if is_authorized_seller else 0,
                    1 if is_drm_free else 0,
                    checked_iso,
                    now_iso,
                    now_iso,
                ),
            )
            sku_row = conn.execute(
                "SELECT * FROM edition_platform_skus WHERE edition_id = ? AND storefront = ? AND store_sku_id = ?",
                (edition_id, cleaned_sf, cleaned_sku),
            ).fetchone()
            assert sku_row is not None
            sku_id = int(sku_row["id"])
            if cur_val is not None:
                is_atl = prev_low is None or cur_val <= prev_low + 0.001
                _append_price_history(
                    conn,
                    sku_id=sku_id,
                    storefront=cleaned_sf,
                    price_usd=cur_val,
                    regular_price_usd=reg_val,
                    discount_pct=eff_disc,
                    is_all_time_low=is_atl,
                    source=source,
                    recorded_at=checked_iso or now_iso,
                )
            return row_to_sku(sku_row)

    def record_sku_price(
        self,
        scope: UserScope,
        *,
        sku_id: int,
        price_usd: float,
        regular_price_usd: float | None = None,
        discount_pct: int | None = None,
        historical_low_usd: float | None = None,
        store_url: str | None = None,
        source: str = "wishlist_sync",
        recorded_at: str | None = None,
    ) -> WishlistPriceRecordResult:
        scope.require_active()
        price_val = round(float(price_usd), 2)
        if price_val < 0:
            raise ValidationError("price_usd cannot be negative")
        reg_val = round(float(regular_price_usd), 2) if regular_price_usd is not None else None
        if reg_val is not None and reg_val < 0:
            raise ValidationError("regular_price_usd cannot be negative")
        ext_low = round(float(historical_low_usd), 2) if historical_low_usd is not None else None

        now_iso = _utc_now_iso()
        rec_iso = recorded_at or now_iso

        with self.db_manager.library_connection(scope, immediate=True) as conn:
            sku_row = conn.execute("SELECT * FROM edition_platform_skus WHERE id = ?", (sku_id,)).fetchone()
            if sku_row is None:
                raise NotFoundError(f"SKU {sku_id} not found")
            ed_row = conn.execute(
                "SELECT canonical_game_id FROM game_editions WHERE id = ?",
                (sku_row["edition_id"],),
            ).fetchone()
            assert ed_row is not None
            wi_row = conn.execute(
                "SELECT * FROM wishlist_items WHERE canonical_game_id = ?",
                (ed_row["canonical_game_id"],),
            ).fetchone()
            if wi_row is None:
                raise NotFoundError(f"Wishlist item for SKU {sku_id} not found")

            prev_low = (
                float(sku_row["historical_low_usd"])
                if sku_row["historical_low_usd"] is not None
                else None
            )
            low_candidates = [v for v in (prev_low, ext_low, price_val) if v is not None and v > 0]
            if not low_candidates:
                low_candidates = [v for v in (prev_low, ext_low, price_val) if v is not None]
            new_low = min(low_candidates)
            is_atl = prev_low is None or price_val <= prev_low + 0.001

            eff_reg = reg_val if reg_val is not None else (
                float(sku_row["regular_price_usd"]) if sku_row["regular_price_usd"] is not None else None
            )
            if discount_pct is not None:
                eff_disc = max(0, int(discount_pct))
            elif eff_reg and eff_reg > 0 and price_val <= eff_reg:
                eff_disc = max(0, int(round((1.0 - (price_val / eff_reg)) * 100)))
            else:
                eff_disc = 0

            conn.execute(
                """
                UPDATE edition_platform_skus
                SET current_price_usd = ?,
                    regular_price_usd = COALESCE(?, regular_price_usd),
                    discount_pct = ?,
                    historical_low_usd = ?,
                    store_url = CASE WHEN ? != '' THEN ? ELSE store_url END,
                    last_checked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    price_val,
                    reg_val,
                    eff_disc,
                    new_low,
                    store_url or "",
                    store_url or "",
                    rec_iso,
                    now_iso,
                    sku_id,
                ),
            )
            _append_price_history(
                conn,
                sku_id=sku_id,
                storefront=sku_row["storefront"],
                price_usd=price_val,
                regular_price_usd=eff_reg,
                discount_pct=eff_disc,
                is_all_time_low=is_atl,
                source=source,
                recorded_at=rec_iso,
            )
            conn.execute(
                "UPDATE wishlist_items SET updated_at = ? WHERE id = ?",
                (now_iso, wi_row["id"]),
            )
            refreshed_wi = conn.execute("SELECT * FROM wishlist_items WHERE id = ?", (wi_row["id"],)).fetchone()
            item_detail = self._hydrate_item(conn, scope, refreshed_wi)

        return WishlistPriceRecordResult(
            sku_id=sku_id,
            price_usd=price_val,
            is_all_time_low=is_atl,
            historical_low_usd=new_low,
            item=item_detail,
        )

    def has_active_wishlist_items(self, scope: UserScope) -> bool:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            row = conn.execute("SELECT 1 FROM wishlist_items WHERE status = 'active' LIMIT 1").fetchone()
            return row is not None

    def list_active_skus_for_sync(self, scope: UserScope) -> list[EditionPlatformSku]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM edition_platform_skus s
                JOIN game_editions e ON s.edition_id = e.id
                JOIN wishlist_items w ON e.canonical_game_id = w.canonical_game_id
                WHERE w.status = 'active'
                ORDER BY s.id ASC
                """
            ).fetchall()
            return [row_to_sku(r) for r in rows]

    def list_push_subscriptions(self, scope: UserScope) -> list[PushSubscriptionRecord]:
        scope.require_active()
        with self.db_manager.library_connection(scope) as conn:
            rows = conn.execute(
                "SELECT * FROM push_subscriptions WHERE is_active = 1 ORDER BY id ASC"
            ).fetchall()
            return [row_to_push_subscription(r) for r in rows]

    def upsert_push_subscription(
        self,
        scope: UserScope,
        *,
        endpoint: str,
        p256dh_key: str,
        auth_key: str,
        user_agent: str = "",
        device_label: str = "",
    ) -> PushSubscriptionRecord:
        scope.require_active()
        now_iso = _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO push_subscriptions (
                    endpoint, p256dh_key, auth_key, user_agent, device_label, is_active, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    p256dh_key = excluded.p256dh_key,
                    auth_key = excluded.auth_key,
                    user_agent = excluded.user_agent,
                    device_label = CASE WHEN excluded.device_label != '' THEN excluded.device_label ELSE push_subscriptions.device_label END,
                    is_active = 1,
                    last_used_at = excluded.last_used_at
                """,
                (endpoint, p256dh_key, auth_key, user_agent[:256], device_label[:120], now_iso, now_iso),
            )
            row = conn.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint = ?",
                (endpoint,),
            ).fetchone()
            assert row is not None
            return row_to_push_subscription(row)

    def delete_push_subscription(self, scope: UserScope, endpoint: str) -> bool:
        scope.require_active()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            cur = conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
            return cur.rowcount > 0

    def select_unalerted_deals(
        self,
        scope: UserScope,
        deals: tuple[WishlistDeal, ...],
    ) -> list[WishlistDeal]:
        scope.require_active()
        if not deals:
            return []
        selected: list[WishlistDeal] = []
        seen_keys: set[tuple[int, str]] = set()
        with self.db_manager.library_connection(scope) as conn:
            for deal in deals:
                key = (deal.wishlist_item_id, deal.platform_family)
                if key in seen_keys:
                    continue
                target_row = conn.execute(
                    """
                    SELECT last_alerted_price_usd, last_alerted_edition_id
                    FROM wishlist_platform_targets
                    WHERE wishlist_item_id = ? AND platform_family = ?
                    """,
                    (deal.wishlist_item_id, deal.platform_family),
                ).fetchone()
                if target_row is None:
                    continue
                last_price = target_row["last_alerted_price_usd"]
                last_ed_id = target_row["last_alerted_edition_id"]
                should_alert = False
                if last_price is None:
                    should_alert = True
                elif float(deal.current_price_usd) < float(last_price) - 0.005:
                    should_alert = True
                elif deal.is_smart_upgrade:
                    last_tier_rank = 1
                    if last_ed_id is not None:
                        ed_row = conn.execute(
                            "SELECT tier_rank FROM game_editions WHERE id = ?",
                            (last_ed_id,),
                        ).fetchone()
                        if ed_row is not None:
                            last_tier_rank = int(ed_row["tier_rank"] or 1)
                    if deal.tier_rank > last_tier_rank:
                        should_alert = True
                if should_alert:
                    seen_keys.add(key)
                    selected.append(deal)
        return selected

    def mark_target_alerted(
        self,
        scope: UserScope,
        *,
        wishlist_item_id: int,
        platform_family: str,
        price_usd: float,
        edition_id: int | None,
        alerted_at: str | None = None,
    ) -> None:
        scope.require_active()
        now_iso = alerted_at or _utc_now_iso()
        with self.db_manager.library_connection(scope, immediate=True) as conn:
            conn.execute(
                """
                UPDATE wishlist_platform_targets
                SET last_alerted_at = ?,
                    last_alerted_price_usd = ?,
                    last_alerted_edition_id = ?,
                    updated_at = ?
                WHERE wishlist_item_id = ? AND platform_family = ?
                """,
                (now_iso, round(float(price_usd), 2), edition_id, now_iso, wishlist_item_id, platform_family),
            )
