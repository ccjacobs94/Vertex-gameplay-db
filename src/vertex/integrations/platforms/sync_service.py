"""Coordinates durable platform sync jobs across adapters, catalog, library, and activity."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from vertex.activity.service import ActivityService
from vertex.catalog.service import CatalogService
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import ScopeViolationError
from vertex.infrastructure.jobs import JobQueue, JobRecord
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.platforms.base import PlatformAdapterError, sanitize_error_message
from vertex.integrations.platforms.steam import SteamAdapter
from vertex.library.service import LibraryService


class PlatformSyncService:
    """Enqueues and executes provider sync jobs through shared domain services."""

    def __init__(
        self,
        *,
        db_manager: DatabaseManager,
        job_queue: JobQueue,
        identity_service: IdentityService,
        catalog_service: CatalogService,
        library_service: LibraryService,
        activity_service: ActivityService,
        steam_adapter: SteamAdapter | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.job_queue = job_queue
        self.identity_service = identity_service
        self.catalog_service = catalog_service
        self.library_service = library_service
        self.activity_service = activity_service
        self.steam_adapter = steam_adapter or SteamAdapter()
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=25.0))

    def enqueue_steam_sync(self, scope: UserScope) -> JobRecord:
        scope.require_active()
        return self.job_queue.enqueue(
            scope,
            kind="steam_sync",
            provider="steam",
            payload={"user_id": scope.user_id},
            dedup_key=f"steam_sync:{scope.user_id}",
            max_attempts=3,
        )

    async def process_next_job(self, worker_id: str = "worker-1") -> JobRecord | None:
        lease_seconds = self.db_manager.settings.worker_lease_seconds
        job = self.job_queue.acquire_next_lease(worker_id, lease_seconds=lease_seconds)
        if job is None:
            return None

        # Re-check user account state immediately before execution.
        try:
            with self.db_manager.system_connection() as conn:
                urow = conn.execute(
                    "SELECT id, username, role, status FROM users WHERE id = ?",
                    (job.owner_user_id,),
                ).fetchone()
            if urow is None or urow["status"] != "active":
                self.job_queue.cancel_job(job.id, "Owner account is no longer active")
                return None
            scope = UserScope(
                user_id=urow["id"],
                username=urow["username"],
                role=urow["role"],
                status=urow["status"],
            )
        except ScopeViolationError as exc:
            self.job_queue.cancel_job(job.id, str(exc))
            return None

        if job.kind == "steam_sync":
            return await self._execute_steam_sync(job, scope, worker_id)

        return self.job_queue.fail_job(
            job.id,
            worker_id,
            error_code="unknown_job_kind",
            error_message=f"Unsupported job kind '{job.kind}'",
            retryable=False,
        )

    async def _execute_steam_sync(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
    ) -> JobRecord | None:
        if not self.db_manager.settings.outbound_sync_enabled:
            self.library_service.record_sync_log(
                scope,
                source="Steam",
                status="skipped",
                games_synced=0,
                message="Outbound platform sync is disabled",
            )
            return self.job_queue.fail_job(
                job.id,
                worker_id,
                error_code="outbound_sync_disabled",
                error_message="Outbound network sync is disabled on this instance",
                retryable=False,
            )

        try:
            creds = self.identity_service.get_decrypted_connections(scope)
            steam_id = (creds.steam_id or "").strip()
            api_key = (creds.steam_api_key or "").strip()
            if not steam_id or not api_key:
                raise PlatformAdapterError(
                    code="missing_credentials",
                    message="Configure both Steam ID and Steam Web API key before syncing",
                    retryable=False,
                )

            async with self.client_factory() as client:
                snapshots = await self.steam_adapter.fetch_library(
                    user_identifier=steam_id,
                    api_key=api_key,
                    client=client,
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            added = 0
            updated = 0
            sessions_changed = 0
            for snap in snapshots:
                self.catalog_service.upsert_public_metadata(
                    title=snap.title,
                    source=snap.source,
                    cover_url=snap.cover_url,
                    genres=list(snap.genres),
                    tags=list(snap.tags),
                    retrieved_at=now_iso,
                )
                game, created = self.library_service.apply_platform_snapshot(
                    scope,
                    external_id=snap.external_id,
                    title=snap.title,
                    platform=snap.platform,
                    source=snap.source,
                    playtime_minutes=snap.playtime_minutes,
                    last_played=snap.last_played,
                    genres=list(snap.genres),
                    tags=list(snap.tags),
                    cover_url=snap.cover_url,
                    is_game_pass=snap.is_game_pass,
                    is_play_anywhere=snap.is_play_anywhere,
                )
                if created:
                    added += 1
                else:
                    updated += 1

                # Deterministic job-scoped idempotency key ensures retries of the same job
                # never record duplicate cumulative observations or sessions.
                idempotency_key = f"steam_sync:job:{job.id}:ext:{snap.external_id}:mins:{snap.playtime_minutes}"
                outcome = self.activity_service.record_cumulative_observation(
                    scope,
                    game_id=game.id,
                    source=snap.source,
                    platform=snap.platform,
                    observed_minutes=snap.playtime_minutes,
                    observed_at=now_iso,
                    idempotency_key=idempotency_key,
                    precision_level="estimated",
                )
                if outcome.session is not None:
                    sessions_changed += 1

            summary = {
                "provider": "steam",
                "games_synced": len(snapshots),
                "added": added,
                "updated": updated,
                "sessions_changed": sessions_changed,
            }
            self.library_service.record_sync_log(
                scope,
                source="Steam",
                status="success",
                games_synced=len(snapshots),
                message=f"Synced {len(snapshots)} Steam games (+{added} new)",
            )
            return self.job_queue.complete_job(job.id, worker_id, result=summary)

        except PlatformAdapterError as exc:
            self.library_service.record_sync_log(
                scope,
                source="Steam",
                status="error",
                games_synced=0,
                message=exc.safe_message,
            )
            return self.job_queue.fail_job(
                job.id,
                worker_id,
                error_code=exc.code,
                error_message=exc.safe_message,
                retryable=exc.retryable,
                throttle_seconds=exc.throttle_seconds,
            )
        except Exception as exc:
            safe_msg = sanitize_error_message(str(exc))
            self.library_service.record_sync_log(
                scope,
                source="Steam",
                status="error",
                games_synced=0,
                message=safe_msg,
            )
            return self.job_queue.fail_job(
                job.id,
                worker_id,
                error_code="unexpected_error",
                error_message=safe_msg,
                retryable=True,
            )
