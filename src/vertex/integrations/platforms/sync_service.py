"""Coordinates durable platform sync jobs across adapters, catalog, library, and activity."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from vertex.activity.service import ActivityService
from vertex.catalog.service import CatalogService
from vertex.identity.models import DecryptedConnections
from vertex.identity.service import IdentityService
from vertex.infrastructure.errors import ScopeViolationError, ValidationError
from vertex.infrastructure.jobs import JobQueue, JobRecord
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.platforms.base import (
    AchievementSnapshot,
    PlatformAdapterError,
    PlatformGameSnapshot,
    sanitize_error_message,
)
from vertex.integrations.platforms.gog import GOGAdapter, GogTokenBundle
from vertex.integrations.platforms.psn import PSNAdapter
from vertex.integrations.platforms.steam import SteamAdapter, clean_steam_input
from vertex.integrations.platforms.xbox import XboxAdapter
from vertex.library.models import GameAchievementsDetail, GameRecord
from vertex.library.service import LibraryService


def _achievement_to_dict(ach: AchievementSnapshot) -> dict[str, Any]:
    return {
        "external_ach_id": ach.external_ach_id,
        "name": ach.name,
        "description": ach.description,
        "icon_url": ach.icon_url,
        "icon_gray_url": ach.icon_gray_url,
        "unlocked": ach.unlocked,
        "unlock_time": ach.unlock_time,
        "reward_type": ach.reward_type,
        "reward_value": ach.reward_value,
        "rarity_pct": ach.rarity_pct,
        "is_hidden": ach.is_hidden,
    }


def _strip_prefix(value: str, prefix: str) -> str:
    base = prefix.rstrip(":_")
    for sep in (":", "_"):
        candidate = f"{base}{sep}"
        if value.startswith(candidate):
            return value[len(candidate) :]
    return value


class PlatformSyncService:
    """Enqueues and executes provider sync jobs through shared domain services."""

    SUPPORTED_PROVIDERS = ("steam", "xbox", "psn", "gog")

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
        xbox_adapter: XboxAdapter | None = None,
        psn_adapter: PSNAdapter | None = None,
        gog_adapter: GOGAdapter | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.job_queue = job_queue
        self.identity_service = identity_service
        self.catalog_service = catalog_service
        self.library_service = library_service
        self.activity_service = activity_service
        self.steam_adapter = steam_adapter or SteamAdapter()
        self.xbox_adapter = xbox_adapter or XboxAdapter()
        self.psn_adapter = psn_adapter or PSNAdapter()
        self.gog_adapter = gog_adapter or GOGAdapter(
            client_id=db_manager.settings.gog_client_id,
            client_secret=db_manager.settings.gog_client_secret,
        )
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

    def enqueue_xbox_sync(self, scope: UserScope) -> JobRecord:
        scope.require_active()
        return self.job_queue.enqueue(
            scope,
            kind="xbox_sync",
            provider="xbox",
            payload={"user_id": scope.user_id},
            dedup_key=f"xbox_sync:{scope.user_id}",
            max_attempts=3,
        )

    def enqueue_psn_sync(self, scope: UserScope) -> JobRecord:
        scope.require_active()
        return self.job_queue.enqueue(
            scope,
            kind="psn_sync",
            provider="psn",
            payload={"user_id": scope.user_id},
            dedup_key=f"psn_sync:{scope.user_id}",
            max_attempts=3,
        )

    def enqueue_gog_sync(self, scope: UserScope) -> JobRecord:
        scope.require_active()
        return self.job_queue.enqueue(
            scope,
            kind="gog_sync",
            provider="gog",
            payload={"user_id": scope.user_id},
            dedup_key=f"gog_sync:{scope.user_id}",
            max_attempts=3,
        )

    def enqueue_sync_for_provider(self, scope: UserScope, provider: str) -> JobRecord:
        normalized = provider.strip().lower()
        if normalized == "steam":
            return self.enqueue_steam_sync(scope)
        if normalized == "xbox":
            return self.enqueue_xbox_sync(scope)
        if normalized == "psn":
            return self.enqueue_psn_sync(scope)
        if normalized == "gog":
            return self.enqueue_gog_sync(scope)
        raise ValidationError(f"Unsupported sync provider: {provider}")

    def configured_providers(self, creds: DecryptedConnections) -> list[str]:
        providers: list[str] = []
        if (creds.steam_id or "").strip() and (creds.steam_api_key or "").strip():
            providers.append("steam")
        if (
            (creds.xbox_gamertag or "").strip() or (creds.xbox_xuid or "").strip()
        ) and (creds.xbl_api_key or "").strip():
            providers.append("xbox")
        if (creds.psn_npsso or "").strip():
            providers.append("psn")
        if (
            (creds.gog_access_token or "").strip()
            or (creds.gog_refresh_token or "").strip()
            or (creds.gog_username or "").strip()
            or (creds.gog_user_id or "").strip()
        ):
            providers.append("gog")
        return providers

    def enqueue_all_configured_syncs(self, scope: UserScope) -> list[JobRecord]:
        scope.require_active()
        creds = self.identity_service.get_decrypted_connections(scope)
        jobs: list[JobRecord] = []
        for provider in self.configured_providers(creds):
            jobs.append(self.enqueue_sync_for_provider(scope, provider))
        return jobs

    def enqueue_scheduled_sync_iteration(self) -> int:
        """Enqueue platform syncs for all active users with configured providers."""
        if not self.db_manager.settings.outbound_sync_enabled:
            return 0
        queued = 0
        for scope in self.identity_service.list_active_user_scopes():
            try:
                queued += len(self.enqueue_all_configured_syncs(scope))
            except Exception:
                continue
        return queued

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
        if job.kind == "xbox_sync":
            return await self._execute_xbox_sync(job, scope, worker_id)
        if job.kind == "psn_sync":
            return await self._execute_psn_sync(job, scope, worker_id)
        if job.kind == "gog_sync":
            return await self._execute_gog_sync(job, scope, worker_id)

        return self.job_queue.fail_job(
            job.id,
            worker_id,
            error_code="unknown_job_kind",
            error_message=f"Unsupported job kind '{job.kind}'",
            retryable=False,
        )

    def _apply_snapshots_and_observations(
        self,
        scope: UserScope,
        *,
        job: JobRecord,
        provider: str,
        snapshots: list[PlatformGameSnapshot],
        now_iso: str,
    ) -> tuple[list[tuple[PlatformGameSnapshot, GameRecord, bool]], int, int, int]:
        applied: list[tuple[PlatformGameSnapshot, GameRecord, bool]] = []
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
                allow_cover_upgrade=bool(snap.cover_is_poster),
                release_year=snap.release_year,
                critic_score=snap.critic_score,
                is_game_pass=snap.is_game_pass,
                is_play_anywhere=snap.is_play_anywhere,
                achievements_unlocked=snap.achievements_unlocked,
                achievements_total=snap.achievements_total,
            )
            if created:
                added += 1
            else:
                updated += 1

            # Deterministic job-scoped idempotency key ensures retries of the same job
            # never record duplicate cumulative observations or sessions.
            idempotency_key = f"{provider}_sync:job:{job.id}:ext:{snap.external_id}:mins:{snap.playtime_minutes}"
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
            played_delta = outcome.session is not None
            if played_delta:
                sessions_changed += 1
            applied.append((snap, game, played_delta))

        return applied, added, updated, sessions_changed

    def _fail_sync_job(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
        *,
        source_label: str,
        exc: Exception,
    ) -> JobRecord | None:
        if isinstance(exc, PlatformAdapterError):
            self.library_service.record_sync_log(
                scope,
                source=source_label,
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
        safe_msg = sanitize_error_message(str(exc))
        self.library_service.record_sync_log(
            scope,
            source=source_label,
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

    def _reject_disabled_sync(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
        *,
        source_label: str,
    ) -> JobRecord | None:
        self.library_service.record_sync_log(
            scope,
            source=source_label,
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

    async def _execute_steam_sync(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
    ) -> JobRecord | None:
        if not self.db_manager.settings.outbound_sync_enabled:
            return self._reject_disabled_sync(job, scope, worker_id, source_label="Steam")

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

            now_iso = datetime.now(timezone.utc).isoformat()
            achievements_synced = 0
            async with self.client_factory() as client:
                resolved_steam_id = await self.steam_adapter.resolve_steam_id(
                    raw_identifier=steam_id,
                    api_key=api_key,
                    client=client,
                )
                snapshots = await self.steam_adapter.fetch_library(
                    user_identifier=resolved_steam_id,
                    api_key=api_key,
                    client=client,
                )

                applied, added, updated, sessions_changed = self._apply_snapshots_and_observations(
                    scope,
                    job=job,
                    provider="steam",
                    snapshots=snapshots,
                    now_iso=now_iso,
                )

                # Opportunistically enrich achievements for played games missing achievement rows or newly played.
                candidates = [
                    (snap, game)
                    for snap, game, played_delta in applied
                    if snap.playtime_minutes > 0 and (game.achievements_synced_at is None or played_delta)
                ][:20]
                for snap, game in candidates:
                    appid_str = _strip_prefix(snap.external_id, "steam_")
                    if not appid_str.isdigit():
                        continue
                    try:
                        achs = await self.steam_adapter.fetch_achievements(
                            steam_id=resolved_steam_id,
                            appid=int(appid_str),
                            api_key=api_key,
                            client=client,
                        )
                        if achs:
                            self.library_service.upsert_game_achievements(
                                scope,
                                game_id=game.id,
                                source="Steam",
                                platform=game.platform,
                                achievements=[_achievement_to_dict(a) for a in achs],
                            )
                            achievements_synced += 1
                    except Exception:
                        continue

            summary = {
                "provider": "steam",
                "games_synced": len(snapshots),
                "added": added,
                "updated": updated,
                "sessions_changed": sessions_changed,
                "achievements_synced": achievements_synced,
            }
            self.library_service.record_sync_log(
                scope,
                source="Steam",
                status="success",
                games_synced=len(snapshots),
                message=f"Synced {len(snapshots)} Steam games (+{added} new)",
            )
            return self.job_queue.complete_job(job.id, worker_id, result=summary)
        except Exception as exc:
            return self._fail_sync_job(job, scope, worker_id, source_label="Steam", exc=exc)

    async def _execute_xbox_sync(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
    ) -> JobRecord | None:
        if not self.db_manager.settings.outbound_sync_enabled:
            return self._reject_disabled_sync(job, scope, worker_id, source_label="Xbox")

        try:
            creds = self.identity_service.get_decrypted_connections(scope)
            api_key = (creds.xbl_api_key or "").strip()
            gamertag = (creds.xbox_gamertag or "").strip() or None
            xuid = (creds.xbox_xuid or "").strip() or None
            if not api_key or (not gamertag and not xuid):
                raise PlatformAdapterError(
                    code="missing_credentials",
                    message="Configure Xbox Gamertag/XUID and OpenXBL API key before syncing",
                    retryable=False,
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            achievements_synced = 0
            async with self.client_factory() as client:
                snapshots, resolved_xuid = await self.xbox_adapter.fetch_library_with_xuid(
                    api_key=api_key,
                    xbox_gamertag=gamertag,
                    xbox_xuid=xuid,
                    client=client,
                )
                effective_xuid = resolved_xuid or xuid
                if resolved_xuid and resolved_xuid != creds.xbox_xuid:
                    self.identity_service.save_resolved_provider_state(
                        scope,
                        xbox_xuid=resolved_xuid,
                    )

                applied, added, updated, sessions_changed = self._apply_snapshots_and_observations(
                    scope,
                    job=job,
                    provider="xbox",
                    snapshots=snapshots,
                    now_iso=now_iso,
                )

                candidates = [
                    (snap, game)
                    for snap, game, played_delta in applied
                    if (
                        game.achievements_synced_at is None
                        or played_delta
                        or (snap.achievements_unlocked or 0) > game.achievements_unlocked
                    )
                    and ((snap.achievements_total or 0) > 0 or snap.playtime_minutes > 0)
                ][:25]
                for snap, game in candidates:
                    title_id = _strip_prefix(snap.external_id, "xbox_")
                    if not title_id:
                        continue
                    try:
                        achs = await self.xbox_adapter.fetch_achievements(
                            api_key=api_key,
                            title_id=title_id,
                            xuid=effective_xuid,
                            client=client,
                        )
                        if achs:
                            self.library_service.upsert_game_achievements(
                                scope,
                                game_id=game.id,
                                source="Xbox",
                                platform=game.platform,
                                achievements=[_achievement_to_dict(a) for a in achs],
                            )
                            achievements_synced += 1
                    except Exception:
                        continue

            summary = {
                "provider": "xbox",
                "games_synced": len(snapshots),
                "added": added,
                "updated": updated,
                "sessions_changed": sessions_changed,
                "achievements_synced": achievements_synced,
            }
            self.library_service.record_sync_log(
                scope,
                source="Xbox",
                status="success",
                games_synced=len(snapshots),
                message=f"Synced {len(snapshots)} Xbox games (+{added} new)",
            )
            return self.job_queue.complete_job(job.id, worker_id, result=summary)
        except Exception as exc:
            return self._fail_sync_job(job, scope, worker_id, source_label="Xbox", exc=exc)

    async def _execute_psn_sync(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
    ) -> JobRecord | None:
        if not self.db_manager.settings.outbound_sync_enabled:
            return self._reject_disabled_sync(job, scope, worker_id, source_label="PSN")

        try:
            creds = self.identity_service.get_decrypted_connections(scope)
            npsso = (creds.psn_npsso or "").strip()
            if not npsso:
                raise PlatformAdapterError(
                    code="missing_credentials",
                    message="Configure a PlayStation NPSSO token before syncing",
                    retryable=False,
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            snapshots, trophy_map = await self.psn_adapter.fetch_library_with_trophies(npsso=npsso)

            applied, added, updated, sessions_changed = self._apply_snapshots_and_observations(
                scope,
                job=job,
                provider="psn",
                snapshots=snapshots,
                now_iso=now_iso,
            )

            achievements_synced = 0
            for snap, game, _ in applied:
                trophies = trophy_map.get(snap.external_id)
                if not trophies:
                    continue
                try:
                    self.library_service.upsert_game_achievements(
                        scope,
                        game_id=game.id,
                        source="PSN",
                        platform=game.platform,
                        achievements=[_achievement_to_dict(a) for a in trophies],
                    )
                    achievements_synced += 1
                except Exception:
                    continue

            summary = {
                "provider": "psn",
                "games_synced": len(snapshots),
                "added": added,
                "updated": updated,
                "sessions_changed": sessions_changed,
                "achievements_synced": achievements_synced,
            }
            self.library_service.record_sync_log(
                scope,
                source="PSN",
                status="success",
                games_synced=len(snapshots),
                message=f"Synced {len(snapshots)} PlayStation games (+{added} new)",
            )
            return self.job_queue.complete_job(job.id, worker_id, result=summary)
        except Exception as exc:
            return self._fail_sync_job(job, scope, worker_id, source_label="PSN", exc=exc)

    async def _execute_gog_sync(
        self,
        job: JobRecord,
        scope: UserScope,
        worker_id: str,
    ) -> JobRecord | None:
        if not self.db_manager.settings.outbound_sync_enabled:
            return self._reject_disabled_sync(job, scope, worker_id, source_label="GOG")

        try:
            creds = self.identity_service.get_decrypted_connections(scope)
            if not any(
                (v or "").strip()
                for v in (
                    creds.gog_access_token,
                    creds.gog_refresh_token,
                    creds.gog_username,
                    creds.gog_user_id,
                )
            ):
                raise PlatformAdapterError(
                    code="missing_credentials",
                    message="Configure GOG Galaxy OAuth tokens or public username before syncing",
                    retryable=False,
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            achievements_synced = 0
            async with self.client_factory() as client:
                gog_result = await self.gog_adapter.fetch_library_detailed(
                    access_token=creds.gog_access_token,
                    refresh_token=creds.gog_refresh_token,
                    user_id=creds.gog_user_id,
                    username=creds.gog_username,
                    client=client,
                )
                snapshots = gog_result.snapshots
                active_tokens: GogTokenBundle | None = gog_result.tokens
                if active_tokens is not None:
                    self.identity_service.save_resolved_provider_state(
                        scope,
                        gog_user_id=active_tokens.user_id,
                        gog_username=active_tokens.username,
                        gog_access_token=active_tokens.access_token,
                        gog_refresh_token=active_tokens.refresh_token,
                    )

                applied, added, updated, sessions_changed = self._apply_snapshots_and_observations(
                    scope,
                    job=job,
                    provider="gog",
                    snapshots=snapshots,
                    now_iso=now_iso,
                )

                effective_access = active_tokens.access_token if active_tokens else creds.gog_access_token
                effective_refresh = active_tokens.refresh_token if active_tokens else creds.gog_refresh_token
                effective_uid = (active_tokens.user_id if active_tokens else None) or creds.gog_user_id
                if effective_access and effective_uid:
                    candidates = [
                        (snap, game)
                        for snap, game, played_delta in applied
                        if game.achievements_synced_at is None
                        or played_delta
                        or (snap.achievements_total or 0) > 0
                    ][:25]
                    for snap, game in candidates:
                        product_id = _strip_prefix(snap.external_id, "gog_")
                        if not product_id:
                            continue
                        try:
                            achs, maybe_bundle = await self.gog_adapter.fetch_achievements(
                                access_token=effective_access,
                                refresh_token=effective_refresh,
                                user_id=effective_uid,
                                product_id=product_id,
                                client=client,
                            )
                            if maybe_bundle is not None:
                                active_tokens = maybe_bundle
                                effective_access = maybe_bundle.access_token
                                effective_refresh = maybe_bundle.refresh_token
                                effective_uid = maybe_bundle.user_id or effective_uid
                                self.identity_service.save_resolved_provider_state(
                                    scope,
                                    gog_user_id=maybe_bundle.user_id,
                                    gog_username=maybe_bundle.username,
                                    gog_access_token=maybe_bundle.access_token,
                                    gog_refresh_token=maybe_bundle.refresh_token,
                                )
                            if achs:
                                self.library_service.upsert_game_achievements(
                                    scope,
                                    game_id=game.id,
                                    source="GOG",
                                    platform=game.platform,
                                    achievements=[_achievement_to_dict(a) for a in achs],
                                )
                                achievements_synced += 1
                        except Exception:
                            continue

            summary = {
                "provider": "gog",
                "games_synced": len(snapshots),
                "added": added,
                "updated": updated,
                "sessions_changed": sessions_changed,
                "achievements_synced": achievements_synced,
            }
            self.library_service.record_sync_log(
                scope,
                source="GOG",
                status="success",
                games_synced=len(snapshots),
                message=f"Synced {len(snapshots)} GOG games (+{added} new)",
            )
            return self.job_queue.complete_job(job.id, worker_id, result=summary)
        except Exception as exc:
            return self._fail_sync_job(job, scope, worker_id, source_label="GOG", exc=exc)

    async def sync_game_achievements_on_demand(
        self,
        scope: UserScope,
        game_id: int,
        *,
        force: bool = True,
    ) -> dict[str, Any]:
        """Fetch achievements on demand for a game and its canonical-linked provider copies."""
        scope.require_active()
        detail = self.library_service.get_game_achievements(scope, game_id)
        if not self.db_manager.settings.outbound_sync_enabled:
            return {
                "detail": detail,
                "synced_copies": 0,
                "synced_providers": [],
                "warnings": ["Outbound network sync is disabled on this instance"] if force else [],
            }

        linked = self.library_service.get_linked_games(scope, game_id)
        copies_to_sync = [
            copy
            for copy in linked
            if copy.source in ("Steam", "Xbox", "PSN", "GOG")
            and (force or copy.achievements_synced_at is None)
        ]
        if not force and not copies_to_sync:
            return {
                "detail": detail,
                "synced_copies": 0,
                "synced_providers": [],
                "warnings": [],
            }
        if force and not copies_to_sync:
            return {
                "detail": detail,
                "synced_copies": 0,
                "synced_providers": [],
                "warnings": ["No Steam, Xbox, PSN, or GOG linked copy found for this game"],
            }

        creds = self.identity_service.get_decrypted_connections(scope)
        synced_copies = 0
        synced_providers: list[str] = []
        warnings: list[str] = []

        async with self.client_factory() as client:
            resolved_steam_id: str | None = None
            gog_access = creds.gog_access_token
            gog_refresh = creds.gog_refresh_token
            gog_uid = creds.gog_user_id

            for copy in copies_to_sync:
                try:
                    achs: list[AchievementSnapshot] = []
                    if copy.source == "Steam":
                        if not creds.steam_id or not creds.steam_api_key:
                            warnings.append("Steam credentials not configured")
                            continue
                        appid_str = _strip_prefix(copy.external_id, "steam_")
                        if not appid_str.isdigit():
                            warnings.append(f"Invalid Steam AppID '{copy.external_id}'")
                            continue
                        if resolved_steam_id is None:
                            resolved_steam_id = await self.steam_adapter.resolve_steam_id(
                                raw_identifier=creds.steam_id,
                                api_key=creds.steam_api_key,
                                client=client,
                            )
                        achs = await self.steam_adapter.fetch_achievements(
                            steam_id=resolved_steam_id,
                            appid=int(appid_str),
                            api_key=creds.steam_api_key,
                            client=client,
                        )

                    elif copy.source == "Xbox":
                        if not creds.xbl_api_key:
                            warnings.append("Xbox OpenXBL API key not configured")
                            continue
                        title_id = _strip_prefix(copy.external_id, "xbox_")
                        if not title_id:
                            continue
                        xuid = creds.xbox_xuid
                        if not xuid and creds.xbox_gamertag:
                            gt = await self.xbox_adapter.resolve_gamertag(
                                gamertag=creds.xbox_gamertag,
                                api_key=creds.xbl_api_key,
                                client=client,
                            )
                            if gt and gt.get("xuid"):
                                xuid = gt["xuid"]
                                self.identity_service.save_resolved_provider_state(
                                    scope,
                                    xbox_xuid=xuid,
                                )
                        achs = await self.xbox_adapter.fetch_achievements(
                            api_key=creds.xbl_api_key,
                            title_id=title_id,
                            xuid=xuid,
                            client=client,
                        )

                    elif copy.source == "PSN":
                        if not creds.psn_npsso:
                            warnings.append("PlayStation NPSSO token not configured")
                            continue
                        achs = await self.psn_adapter.fetch_achievements_for_title(
                            npsso=creds.psn_npsso,
                            title=copy.title,
                        )

                    elif copy.source == "GOG":
                        if not gog_access and not gog_refresh:
                            warnings.append("GOG Galaxy OAuth tokens required for GOG achievements")
                            continue
                        product_id = _strip_prefix(copy.external_id, "gog_")
                        if not product_id:
                            continue
                        achs, maybe_bundle = await self.gog_adapter.fetch_achievements(
                            access_token=gog_access,
                            refresh_token=gog_refresh,
                            user_id=gog_uid,
                            product_id=product_id,
                            client=client,
                        )
                        if maybe_bundle is not None:
                            gog_access = maybe_bundle.access_token
                            gog_refresh = maybe_bundle.refresh_token
                            gog_uid = maybe_bundle.user_id or gog_uid
                            self.identity_service.save_resolved_provider_state(
                                scope,
                                gog_user_id=maybe_bundle.user_id,
                                gog_username=maybe_bundle.username,
                                gog_access_token=maybe_bundle.access_token,
                                gog_refresh_token=maybe_bundle.refresh_token,
                            )

                    if achs:
                        self.library_service.upsert_game_achievements(
                            scope,
                            game_id=copy.id,
                            source=copy.source,
                            platform=copy.platform,
                            achievements=[_achievement_to_dict(a) for a in achs],
                        )
                        synced_copies += 1
                        if copy.source not in synced_providers:
                            synced_providers.append(copy.source)
                    elif force:
                        warnings.append(f"{copy.source}: No achievement schema returned for {copy.title}")
                except PlatformAdapterError as exc:
                    if force:
                        warnings.append(f"{copy.source}: {exc.safe_message}")
                except Exception as exc:
                    if force:
                        warnings.append(f"{copy.source}: {sanitize_error_message(str(exc))}")

        updated_detail: GameAchievementsDetail = self.library_service.get_game_achievements(scope, game_id)
        return {
            "detail": updated_detail,
            "synced_copies": synced_copies,
            "synced_providers": synced_providers,
            "warnings": warnings,
        }

    async def verify_connection(
        self,
        scope: UserScope,
        *,
        provider: str,
        credential: str | None = None,
        secondary_credential: str | None = None,
    ) -> dict[str, Any]:
        """Verify provider credentials (supplied inline or from saved user connections)."""
        scope.require_active()
        normalized = provider.strip().lower()
        if not self.db_manager.settings.outbound_sync_enabled:
            return {
                "status": "error",
                "platform": normalized,
                "message": "Outbound network sync is disabled on this instance",
            }

        creds = self.identity_service.get_decrypted_connections(scope)
        async with self.client_factory() as client:
            if normalized == "steam":
                cleaned_input = clean_steam_input(credential or creds.steam_id or "")
                api_key = (secondary_credential or creds.steam_api_key or "").strip()
                return await self.steam_adapter.verify_connection(
                    credential=cleaned_input,
                    api_key=api_key,
                    client=client,
                )

            if normalized == "xbox":
                cand = (
                    (credential or "").strip()
                    or (creds.xbox_gamertag or "").strip()
                    or (creds.xbl_api_key or "").strip()
                    or (creds.xbox_xuid or "").strip()
                )
                server_xbl = (
                    (secondary_credential or "").strip()
                    or (creds.xbl_api_key or "").strip()
                    or (self.db_manager.settings.xbl_api_key or "").strip()
                )
                return await self.xbox_adapter.verify_connection(
                    credential=cand,
                    server_xbl_key=server_xbl,
                    client=client,
                )

            if normalized == "psn":
                cand = (credential or creds.psn_npsso or "").strip()
                return await self.psn_adapter.verify_connection(credential=cand)

            if normalized == "gog":
                raw_cand = (credential or "").strip()
                if not raw_cand:
                    if creds.gog_access_token or creds.gog_refresh_token:
                        raw_cand = json.dumps(
                            {
                                "access_token": creds.gog_access_token,
                                "refresh_token": creds.gog_refresh_token,
                                "user_id": creds.gog_user_id,
                                "username": creds.gog_username,
                            }
                        )
                    else:
                        raw_cand = (creds.gog_username or creds.gog_user_id or "").strip()
                return await self.gog_adapter.verify_connection(
                    credential=raw_cand,
                    client=client,
                )

            if normalized == "homeassistant":
                ha_url = (credential or creds.ha_url or "").strip().rstrip("/")
                ha_token = (secondary_credential or creds.ha_token or "").strip()
                if not ha_url or not ha_token:
                    return {
                        "status": "error",
                        "platform": "homeassistant",
                        "message": "Configure both Home Assistant URL and Long-Lived Access Token",
                    }
                try:
                    resp = await client.get(
                        f"{ha_url}/api/",
                        headers={"Authorization": f"Bearer {ha_token}"},
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        return {
                            "status": "ok",
                            "platform": "homeassistant",
                            "message": "Home Assistant API connection verified",
                        }
                    return {
                        "status": "error",
                        "platform": "homeassistant",
                        "message": f"Home Assistant returned HTTP {resp.status_code}",
                    }
                except Exception as exc:
                    return {
                        "status": "error",
                        "platform": "homeassistant",
                        "message": sanitize_error_message(f"Home Assistant connection error: {exc}"),
                    }

        raise ValidationError(f"Unsupported connection provider: {provider}")
