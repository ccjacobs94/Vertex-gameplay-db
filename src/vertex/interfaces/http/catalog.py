"""Shared public catalog search and library metadata enrichment HTTP router."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_write_scope,
)
from vertex.interfaces.http.schemas import (
    CatalogEnrichmentReportResponse,
    CatalogEnrichRequest,
    CatalogSearchResultResponse,
)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/search", response_model=list[CatalogSearchResultResponse])
async def search_catalog(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[CatalogSearchResultResponse]:
    scope.require_active()
    items = await services.catalog.search_catalog(q, limit=limit)
    out: list[CatalogSearchResultResponse] = []
    for item in items:
        d = asdict(item)
        d["genres"] = list(item.genres)
        d["tags"] = list(item.tags)
        out.append(CatalogSearchResultResponse(**d))
    return out


@router.post("/enrich", response_model=CatalogEnrichmentReportResponse)
async def enrich_library(
    body: CatalogEnrichRequest | None = None,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> CatalogEnrichmentReportResponse:
    req = body or CatalogEnrichRequest()
    report = await services.catalog.enrich_library(
        scope,
        services.library,
        limit=req.limit,
        force=req.force,
    )
    return CatalogEnrichmentReportResponse(**asdict(report))
