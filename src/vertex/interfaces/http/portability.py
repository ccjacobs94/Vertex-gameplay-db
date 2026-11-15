"""Versioned JSON export/import and validated legacy SQLite import endpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse

from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_write_scope,
)
from vertex.interfaces.http.schemas import ImportReportResponse, JsonImportRequest

router = APIRouter(prefix="/api/portability", tags=["portability"])


@router.get("/export.json")
def export_json(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> JSONResponse:
    data = services.portability.export_json(scope)
    return JSONResponse(
        content=data,
        headers={
            "Content-Disposition": f'attachment; filename="vertex-library-{scope.username}.json"',
            "Cache-Control": "no-store, private",
        },
    )


@router.post("/import/json", response_model=ImportReportResponse)
def import_json(
    body: JsonImportRequest,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> ImportReportResponse:
    rep = services.portability.import_json(
        scope,
        body.payload,
        mode=body.mode,
        dry_run=body.dry_run,
    )
    d = asdict(rep)
    d["warnings"] = list(rep.warnings)
    return ImportReportResponse(**d)


@router.post("/import/legacy-sqlite", response_model=ImportReportResponse)
async def import_legacy_sqlite(
    file: UploadFile = File(...),
    mode: str = Query(default="merge", pattern="^(merge|replace)$"),
    dry_run: bool = Query(default=False),
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> ImportReportResponse:
    raw_bytes = await file.read(services.settings.max_import_bytes + 1)
    rep = services.portability.import_legacy_sqlite_bytes(
        scope,
        raw_bytes,
        mode=mode,
        dry_run=dry_run,
    )
    d: dict[str, Any] = asdict(rep)
    d["warnings"] = list(rep.warnings)
    return ImportReportResponse(**d)
