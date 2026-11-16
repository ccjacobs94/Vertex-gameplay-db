"""Personal wishlist, target platforms, manual prices, deals, and purchase conversion HTTP router."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query

from vertex.infrastructure.scope import UserScope
from fastapi import APIRouter, Depends, HTTPException, Query, status

from vertex.identity.models import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_write_scope,
)
from vertex.interfaces.http.schemas import (
    JobResponse,
    WishlistCandidatesSearchResponse,
    WishlistSyncRequest,
)
from vertex.interfaces.http.schemas import (
    PriceHistoryEntryResponse,
    WishlistCreateRequest,
    WishlistDealEvaluationResponse,
    WishlistDealResponse,
    WishlistHistoryResponse,
    WishlistItemResponse,
    WishlistPlatformsUpdateRequest,
    WishlistPriceRecordRequest,
    WishlistPriceRecordResponse,
    WishlistPurchaseRequest,
    WishlistPurchaseResponse,
    WishlistUpdateRequest,
)
from vertex.wishlist.models import WishlistItemDetail

router = APIRouter(prefix="/api/wishlist", tags=["wishlist"])


def _serialize_item(item: WishlistItemDetail) -> WishlistItemResponse:
    d = asdict(item)
    d["canonical_game"]["genres"] = list(item.canonical_game.genres)
    d["targets"] = [
        {**asdict(t), "allowed_storefronts": list(t.allowed_storefronts)}
        for t in item.targets
    ]
    d["editions"] = [
        {**asdict(ed), "skus": [asdict(s) for s in ed.skus]}
        for ed in item.editions
    ]
    d["skus"] = [asdict(s) for s in item.skus]
    d["conflict"]["conflicted_platforms"] = list(item.conflict.conflicted_platforms)
    return WishlistItemResponse(**d)


def _dump_targets(raw: list[Any]) -> list[str | dict[str, Any]]:
    out: list[str | dict[str, Any]] = []
    for t in raw:
        if isinstance(t, str):
            out.append(t)
        elif hasattr(t, "model_dump"):
            out.append(t.model_dump())
        elif isinstance(t, dict):
            out.append(t)
    return out


@router.get("", response_model=list[WishlistItemResponse])
def list_wishlist(
    status: str = Query(default="active"),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[WishlistItemResponse]:
    return [_serialize_item(i) for i in services.wishlist.list_items(scope, status=status)]


@router.post("", response_model=WishlistItemResponse, status_code=201)
def add_wishlist_item(
    body: WishlistCreateRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> WishlistItemResponse:
    item = services.wishlist.add_item(
        scope,
        title=body.title,
        platform_targets=_dump_targets(body.platform_targets),
        release_year=body.release_year,
        developer=body.developer,
        publisher=body.publisher,
        cover_url=body.cover_url,
        genres=body.genres,
        critic_score=body.critic_score,
        allow_smart_upgrade=body.allow_smart_upgrade,
        preferred_edition_id=body.preferred_edition_id,
        notes=body.notes,
        added_via="web",
        force_track_owned=body.force_track_owned,
    )
    return _serialize_item(item)


@router.get("/candidates", response_model=WishlistCandidatesSearchResponse)
async def search_wishlist_candidates(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=25),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> WishlistCandidatesSearchResponse:
    result = await services.wishlist_sync.search_candidates(scope, q, limit=limit)
    return WishlistCandidatesSearchResponse(**result)


@router.post("/sync", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_wishlist_price_sync(
    body: WishlistSyncRequest | None = None,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> JobResponse:
    req = body or WishlistSyncRequest()
    if req.wishlist_item_id:
        services.wishlist.get_item(scope, req.wishlist_item_id)
    job = services.wishlist_sync.enqueue_wishlist_sync(
        scope,
        wishlist_item_id=req.wishlist_item_id,
        force=req.force,
        max_age_seconds=req.max_age_seconds,
    )
    return JobResponse.model_validate(job, from_attributes=True)


@router.get("/deals", response_model=WishlistDealEvaluationResponse)
def get_wishlist_deals(
    platform: str | None = Query(default=None),
    smart_upgrades_only: bool = Query(default=False),
    include_above_target: bool = Query(default=False),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> WishlistDealEvaluationResponse:
    ev = services.wishlist.evaluate_deals(
        scope,
        platform=platform,
        smart_upgrades_only=smart_upgrades_only,
        include_above_target=include_above_target,
    )
    return WishlistDealEvaluationResponse(
        total_deals=ev.total_deals,
        smart_upgrades_count=ev.smart_upgrades_count,
        all_time_lows_count=ev.all_time_lows_count,
        deals=[WishlistDealResponse(**asdict(d)) for d in ev.deals],
    )


@router.get("/{item_id}", response_model=WishlistItemResponse)
def get_wishlist_item(
    item_id: int,
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> WishlistItemResponse:
    return _serialize_item(services.wishlist.get_item(scope, item_id))


@router.patch("/{item_id}", response_model=WishlistItemResponse)
def update_wishlist_item(
    item_id: int,
    body: WishlistUpdateRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> WishlistItemResponse:
    item = services.wishlist.update_item(
        scope,
        item_id,
        allow_smart_upgrade=body.allow_smart_upgrade,
        preferred_edition_id=body.preferred_edition_id,
        status=body.status,
        notes=body.notes,
    )
    return _serialize_item(item)


@router.delete("/{item_id}", status_code=204)
def delete_wishlist_item(
    item_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> None:
    services.wishlist.delete_item(scope, item_id)


@router.patch("/{item_id}/platforms", response_model=WishlistItemResponse)
def update_wishlist_platforms(
    item_id: int,
    body: WishlistPlatformsUpdateRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> WishlistItemResponse:
    item = services.wishlist.update_platforms(
        scope,
        item_id,
        platform_targets=_dump_targets(body.platform_targets),
        remove_platforms=body.remove_platforms,
        allow_smart_upgrade=body.allow_smart_upgrade,
        force_track_owned=body.force_track_owned,
    )
    return _serialize_item(item)


@router.post("/{item_id}/price", response_model=WishlistPriceRecordResponse)
def record_wishlist_price(
    item_id: int,
    body: WishlistPriceRecordRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> WishlistPriceRecordResponse:
    res = services.wishlist.record_manual_price(
        scope,
        item_id,
        price_usd=body.price_usd,
        platform=body.platform,
        storefront=body.storefront,
        edition_name=body.edition_name,
        edition_tier=body.edition_tier,
        regular_price_usd=body.regular_price_usd,
        store_url=body.store_url,
        store_sku_id=body.store_sku_id,
        is_drm_free=body.is_drm_free,
        is_authorized_seller=body.is_authorized_seller,
        source="manual",
    )
    return WishlistPriceRecordResponse(
        sku_id=res.sku_id,
        price_usd=res.price_usd,
        is_all_time_low=res.is_all_time_low,
        historical_low_usd=res.historical_low_usd,
        item=_serialize_item(res.item),
    )


@router.post("/{item_id}/purchase", response_model=WishlistPurchaseResponse)
def purchase_wishlist_item(
    item_id: int,
    body: WishlistPurchaseRequest | None = None,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> WishlistPurchaseResponse:
    req = body or WishlistPurchaseRequest()
    res = services.wishlist.mark_purchased(
        scope,
        item_id,
        platform=req.platform,
        edition_name=req.edition_name,
        price_paid_usd=req.price_paid_usd,
        completion_status=req.completion_status,
    )
    return WishlistPurchaseResponse(
        item=_serialize_item(res.item),
        library_game_id=res.library_game_id,
        already_purchased=res.already_purchased,
    )


@router.get("/{item_id}/history", response_model=WishlistHistoryResponse)
def get_wishlist_history(
    item_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> WishlistHistoryResponse:
    hist = services.wishlist.get_price_history(scope, item_id, limit=limit)
    return WishlistHistoryResponse(
        item=_serialize_item(hist.item),
        price_history_by_sku={
            str(sku_id): [PriceHistoryEntryResponse(**asdict(entry)) for entry in entries]
            for sku_id, entries in hist.price_history_by_sku.items()
        },
    )
