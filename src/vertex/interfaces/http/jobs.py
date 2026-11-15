"""Durable sync trigger and per-user job health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from vertex.infrastructure.jobs import JobRecord
from vertex.infrastructure.scope import UserScope
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_read_scope,
    require_sync_scope,
)
from vertex.interfaces.http.schemas import (
    JobResponse,
    JobsOverviewResponse,
    ProviderStatusResponse,
)

router = APIRouter(tags=["jobs"])


def _to_job_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.id,
        kind=job.kind,
        provider=job.provider,
        state=job.state,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        scheduled_at=job.scheduled_at,
        available_at=job.available_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        last_error_code=job.last_error_code,
        last_error=job.last_error,
        result=job.result,
    )


@router.post("/api/sync/steam", response_model=JobResponse, status_code=202)
def enqueue_steam_sync(
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> JobResponse:
    job = services.platform_sync.enqueue_steam_sync(scope)
    return _to_job_response(job)


@router.get("/api/jobs", response_model=JobsOverviewResponse)
def list_jobs(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> JobsOverviewResponse:
    jobs = services.job_queue.list_user_jobs(scope, limit=25)
    providers = services.job_queue.get_provider_status_summary(scope)
    return JobsOverviewResponse(
        providers=[ProviderStatusResponse(**p) for p in providers],
        recent_jobs=[_to_job_response(j) for j in jobs],
    )
