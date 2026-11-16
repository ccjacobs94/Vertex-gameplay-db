"""Live presence telemetry, targeted close reconciliation, Xbox RTA stream, and daily sweep."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from vertex.activity.models import ActiveTelemetrySession
from vertex.activity.service import ActivityService
from vertex.identity.models import DecryptedConnections
from vertex.identity.service import IdentityService
from vertex.infrastructure.scope import UserScope
from vertex.infrastructure.sqlite import DatabaseManager
from vertex.integrations.platforms.base import AchievementSnapshot, sanitize_error_message
from vertex.integrations.platforms.gog import GOGAdapter, GogActivePresence, GogTokenBundle
from vertex.integrations.platforms.xbox import XboxAdapter
from vertex.library.service import LibraryService

logger = logging.getLogger("vertex.telemetry")

SYSTEM_APPS: set[str] = {
    "750323071",   # Xbox Home / Dashboard
    "328178078",   # Microsoft Edge
    "1683392974",  # Blu-ray Player
}

SYSTEM_APP_NAMES: set[str] = {
    "xbox home",
    "home",
    "dashboard",
    "microsoft edge",
    "edge",
    "blu-ray player",
    "netflix",
    "youtube",
    "spotify",
    "disney+",
    "hulu",
    "prime video",
    "twitch",
    "settings",
    "microsoft store",
    "xbox accessories",
    "captures",
    "media player",
    "tv & movies",
}


def is_system_app(title_id: Any, title_name: str | None = None) -> bool:
    tid_str = str(title_id or "").strip()
    if tid_str in SYSTEM_APPS:
        return True
    if title_name:
        clean_name = title_name.strip().lower()
        if clean_name in SYSTEM_APP_NAMES or any(
            clean_name.startswith(app) for app in SYSTEM_APP_NAMES
        ):
            return True
    return False


def _parse_iso(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    cleaned = raw.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def extract_active_xbox_title(presence_payload: Any) -> dict[str, str | None] | None:
    """Parse OpenXBL / RTA presence payload into active non-system game metadata."""
    if not presence_payload:
        return None

    payload = presence_payload
    if isinstance(payload, dict) and "content" in payload:
        payload = payload["content"]

    records = payload if isinstance(payload, list) else [payload]
    for entry in records:
        if not isinstance(entry, dict):
            continue
        if "content" in entry and isinstance(entry["content"], dict):
            entry = entry["content"]
        if str(entry.get("state") or "").lower() == "offline":
            continue

        for device in entry.get("devices") or []:
            if not isinstance(device, dict):
                continue
            for title in device.get("titles") or []:
                if not isinstance(title, dict):
                    continue
                state = str(title.get("state") or title.get("activityState") or "").capitalize()
                t_id = str(title.get("id") or title.get("titleId") or "").strip()
                t_name = str(title.get("name") or title.get("titleName") or "").strip()
                if state == "Active" and t_id and not is_system_app(t_id, t_name):
                    act = title.get("activity") if isinstance(title.get("activity"), dict) else {}
                    rich = str(act.get("richPresence") or title.get("richPresence") or "").strip() or None
                    return {
                        "title_id": t_id,
                        "title": t_name or f"Xbox Title {t_id}",
                        "rich_presence": rich,
                    }

        for title in entry.get("titles") or []:
            if not isinstance(title, dict):
                continue
            state = str(title.get("state") or title.get("activityState") or "").capitalize()
            t_id = str(title.get("id") or title.get("titleId") or "").strip()
            t_name = str(title.get("name") or title.get("titleName") or "").strip()
            if state == "Active" and t_id and not is_system_app(t_id, t_name):
                act = title.get("activity") if isinstance(title.get("activity"), dict) else {}
                rich = str(act.get("richPresence") or title.get("richPresence") or "").strip() or None
                return {
                    "title_id": t_id,
                    "title": t_name or f"Xbox Title {t_id}",
                    "rich_presence": rich,
                }

        title_id = entry.get("titleId")
        title_name = entry.get("titleName") or entry.get("name")
        status = str(entry.get("state") or entry.get("userPresence") or "").lower()
        if title_id and status in ("online", "active") and not is_system_app(title_id, title_name):
            return {
                "title_id": str(title_id),
                "title": str(title_name or f"Xbox Title {title_id}"),
                "rich_presence": None,
            }

    return None


class TelemetryService:
    """Multi-user live presence polling, flush-buffered session reconciliation, and daily sweep."""

    def __init__(
        self,
        *,
        db_manager: DatabaseManager,
        identity_service: IdentityService,
        library_service: LibraryService,
        activity_service: ActivityService,
        xbox_adapter: XboxAdapter | None = None,
        gog_adapter: GOGAdapter | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        flush_delay_seconds: float | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.identity_service = identity_service
        self.library_service = library_service
        self.activity_service = activity_service
        self.xbox_adapter = xbox_adapter or XboxAdapter()
        self.gog_adapter = gog_adapter or GOGAdapter(
            client_id=db_manager.settings.gog_client_id,
            client_secret=db_manager.settings.gog_client_secret,
        )
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=20.0))
        self.flush_delay_seconds = (
            float(flush_delay_seconds)
            if flush_delay_seconds is not None
            else float(db_manager.settings.telemetry_flush_delay_seconds)
        )

    def get_status(self, scope: UserScope) -> dict[str, Any]:
        scope.require_active()
        creds = self.identity_service.get_decrypted_connections(scope)
        active_sessions = self.activity_service.list_active_telemetry_sessions(scope)
        xbl_key = (creds.xbl_watcher_api_key or creds.xbl_api_key or "").strip()
        xbox_configured = bool(xbl_key and (creds.xbox_xuid or creds.xbox_gamertag))
        gog_configured = bool(
            (creds.gog_access_token or creds.gog_refresh_token)
            and (creds.gog_user_id or creds.gog_access_token)
        )
        return {
            "telemetry_enabled": self.db_manager.settings.telemetry_enabled,
            "outbound_sync_enabled": self.db_manager.settings.outbound_sync_enabled,
            "poll_interval_seconds": self.db_manager.settings.telemetry_poll_interval_seconds,
            "daily_sweep_interval_seconds": self.db_manager.settings.daily_sweep_interval_seconds,
            "flush_delay_seconds": self.flush_delay_seconds,
            "providers": {
                "xbox": xbox_configured,
                "xbox_rta": bool(creds.xbl_watcher_api_key and (creds.xbox_xuid or creds.xbox_gamertag)),
                "gog": gog_configured,
            },
            "active_sessions": active_sessions,
        }

    def _xbox_telemetry_key(self, creds: DecryptedConnections) -> str:
        return (creds.xbl_watcher_api_key or creds.xbl_api_key or "").strip()

    async def _resolve_xbox_xuid(
        self,
        scope: UserScope,
        creds: DecryptedConnections,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> str | None:
        if creds.xbox_xuid:
            return creds.xbox_xuid.strip()
        if creds.xbox_gamertag:
            gt = await self.xbox_adapter.resolve_gamertag(
                gamertag=creds.xbox_gamertag,
                api_key=api_key,
                client=client,
            )
            if gt and gt.get("xuid"):
                self.identity_service.save_resolved_provider_state(scope, xbox_xuid=gt["xuid"])
                return gt["xuid"]
        acc = await self.xbox_adapter.resolve_account_xuid(api_key=api_key, client=client)
        if acc and acc.get("xuid"):
            self.identity_service.save_resolved_provider_state(scope, xbox_xuid=acc["xuid"])
            return acc["xuid"]
        return None

    async def poll_user(
        self,
        scope: UserScope,
        *,
        now: datetime | None = None,
        sleep_before_flush: bool = True,
    ) -> dict[str, Any]:
        """Poll Xbox and GOG live presence for one user and reconcile transitions."""
        scope.require_active()
        current_dt = now or datetime.now(timezone.utc)
        if not self.db_manager.settings.outbound_sync_enabled:
            return {
                "status": "skipped",
                "events": [],
                "active_sessions": self.activity_service.list_active_telemetry_sessions(scope),
                "errors": ["Outbound network sync is disabled on this instance"],
            }

        creds = self.identity_service.get_decrypted_connections(scope)
        events: list[dict[str, Any]] = []
        errors: list[str] = []

        async with self.client_factory() as client:
            xbl_key = self._xbox_telemetry_key(creds)
            if xbl_key and (creds.xbox_xuid or creds.xbox_gamertag):
                try:
                    xuid = await self._resolve_xbox_xuid(scope, creds, xbl_key, client)
                    presence_payload = await self.xbox_adapter.fetch_presence(
                        api_key=xbl_key,
                        xuid=xuid,
                        client=client,
                    )
                    ev = await self.apply_xbox_presence_payload(
                        scope,
                        presence_payload,
                        api_key=xbl_key,
                        xuid=xuid,
                        detection_method="presence_poll",
                        now=current_dt,
                        sleep_before_flush=sleep_before_flush,
                        client=client,
                    )
                    if ev:
                        events.extend(ev)
                except Exception as exc:
                    errors.append(f"Xbox telemetry: {sanitize_error_message(str(exc))}")

            if creds.gog_access_token or creds.gog_refresh_token:
                try:
                    gog_ev = await self._poll_gog_user(
                        scope,
                        creds,
                        client=client,
                        now=current_dt,
                        sleep_before_flush=sleep_before_flush,
                    )
                    if gog_ev:
                        events.extend(gog_ev)
                except Exception as exc:
                    errors.append(f"GOG telemetry: {sanitize_error_message(str(exc))}")

        return {
            "status": "ok" if not errors else ("partial" if events else "error"),
            "events": events,
            "active_sessions": self.activity_service.list_active_telemetry_sessions(scope),
            "errors": errors,
        }

    async def apply_xbox_presence_payload(
        self,
        scope: UserScope,
        presence_payload: Any,
        *,
        api_key: str | None = None,
        xuid: str | None = None,
        detection_method: str = "rta_websocket",
        now: datetime | None = None,
        sleep_before_flush: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        """Process one Xbox presence payload through the durable active-session state machine."""
        scope.require_active()
        current_dt = now or datetime.now(timezone.utc)
        now_iso = current_dt.isoformat()
        creds = self.identity_service.get_decrypted_connections(scope)
        key = (api_key or self._xbox_telemetry_key(creds)).strip()
        target_xuid = (xuid or creds.xbox_xuid or "").strip() or None
        active = self.activity_service.get_active_telemetry_session(scope, "xbox")
        detected = extract_active_xbox_title(presence_payload)

        async def _run_with_client(http: httpx.AsyncClient) -> list[dict[str, Any]]:
            events: list[dict[str, Any]] = []
            if detected:
                tid = detected["title_id"] or ""
                title = detected["title"] or f"Xbox Title {tid}"
                rich = detected.get("rich_presence")
                if active is None:
                    started = await self._start_xbox_session(
                        scope,
                        title_id=tid,
                        title=title,
                        rich_presence=rich,
                        api_key=key,
                        xuid=target_xuid,
                        detection_method=detection_method,
                        now_iso=now_iso,
                        client=http,
                    )
                    events.append({"provider": "xbox", "action": "started", "title": started.title, "game_id": started.game_id})
                elif active.external_title_id == tid:
                    self.activity_service.touch_active_telemetry_session(
                        scope,
                        "xbox",
                        last_seen_at=now_iso,
                        rich_presence=rich,
                    )
                    events.append({"provider": "xbox", "action": "touched", "title": active.title, "game_id": active.game_id})
                else:
                    closed = await self._close_xbox_session(
                        scope,
                        active,
                        api_key=key,
                        xuid=target_xuid,
                        ended_at=current_dt,
                        sleep_before_flush=sleep_before_flush,
                        client=http,
                    )
                    events.append(closed)
                    started = await self._start_xbox_session(
                        scope,
                        title_id=tid,
                        title=title,
                        rich_presence=rich,
                        api_key=key,
                        xuid=target_xuid,
                        detection_method=detection_method,
                        now_iso=now_iso,
                        client=http,
                    )
                    events.append({"provider": "xbox", "action": "started", "title": started.title, "game_id": started.game_id})
            elif active is not None:
                closed = await self._close_xbox_session(
                    scope,
                    active,
                    api_key=key,
                    xuid=target_xuid,
                    ended_at=current_dt,
                    sleep_before_flush=sleep_before_flush,
                    client=http,
                )
                events.append(closed)
            return events

        if client is not None:
            return await _run_with_client(client)
        async with self.client_factory() as own_client:
            return await _run_with_client(own_client)

    async def _start_xbox_session(
        self,
        scope: UserScope,
        *,
        title_id: str,
        title: str,
        rich_presence: str | None,
        api_key: str,
        xuid: str | None,
        detection_method: str,
        now_iso: str,
        client: httpx.AsyncClient,
    ) -> ActiveTelemetrySession:
        ext_id = f"xbox:{title_id}"
        game = self.library_service.find_game_by_external_id(scope, ext_id)
        if game is None:
            game = self.library_service.find_game_by_external_id(scope, f"xbox_{title_id}")
        if game is None:
            game, _ = self.library_service.apply_platform_snapshot(
                scope,
                external_id=ext_id,
                title=title,
                platform="Xbox",
                source="Xbox",
                playtime_minutes=0,
                last_played=now_iso,
            )

        baseline_mins = float(game.playtime_minutes)
        if api_key and xuid:
            try:
                stats_map = await self.xbox_adapter.fetch_batch_minutes_played(
                    api_key=api_key,
                    xuid=xuid,
                    title_ids=[title_id],
                    client=client,
                )
                if title_id in stats_map:
                    baseline_mins = max(baseline_mins, float(stats_map[title_id]))
            except Exception:
                pass

        baseline_int = int(round(baseline_mins))
        self.activity_service.seed_baseline_if_absent(
            scope,
            game_id=game.id,
            source="Xbox",
            baseline_minutes=baseline_int,
            observed_at=now_iso,
        )

        return self.activity_service.upsert_active_telemetry_session(
            scope,
            provider="xbox",
            external_title_id=title_id,
            game_id=game.id,
            title=game.title,
            platform=game.platform,
            source="xbox_telemetry",
            started_at=now_iso,
            last_seen_at=now_iso,
            baseline_minutes=baseline_int,
            baseline_achievements=game.achievements_unlocked,
            rich_presence=rich_presence,
            detection_method=detection_method,
        )

    async def _close_xbox_session(
        self,
        scope: UserScope,
        active: ActiveTelemetrySession,
        *,
        api_key: str,
        xuid: str | None,
        ended_at: datetime,
        sleep_before_flush: bool,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        self.activity_service.delete_active_telemetry_session(scope, "xbox")
        start_dt = _parse_iso(active.started_at)
        end_dt = max(ended_at, start_dt)
        wall_clock_minutes = max(0.0, round((end_dt - start_dt).total_seconds() / 60.0, 2))

        if sleep_before_flush and self.flush_delay_seconds > 0:
            await asyncio.sleep(self.flush_delay_seconds)

        baseline_mins = float(active.baseline_minutes)
        updated_minutes = baseline_mins
        if api_key and xuid:
            try:
                stats_map = await self.xbox_adapter.fetch_batch_minutes_played(
                    api_key=api_key,
                    xuid=xuid,
                    title_ids=[active.external_title_id],
                    client=client,
                )
                if active.external_title_id in stats_map:
                    updated_minutes = float(stats_map[active.external_title_id])
            except Exception:
                pass

        cloud_delta = max(0.0, round(updated_minutes - baseline_mins, 2))
        achievements_earned = 0
        if api_key:
            try:
                achs = await self.xbox_adapter.fetch_achievements(
                    api_key=api_key,
                    title_id=active.external_title_id,
                    xuid=xuid,
                    client=client,
                )
                if achs:
                    res = self.library_service.upsert_game_achievements(
                        scope,
                        game_id=active.game_id,
                        source="Xbox",
                        platform=active.platform,
                        achievements=[_achievement_to_dict(a) for a in achs],
                    )
                    window_hits = self._count_window_unlocks(achs, start_dt, end_dt)
                    count_delta = max(0, int(res["unlocked"]) - active.baseline_achievements)
                    achievements_earned = max(window_hits, count_delta)
            except Exception:
                pass

        active_minutes = cloud_delta if cloud_delta > 0 else wall_clock_minutes
        idle_minutes = (
            max(0.0, round(wall_clock_minutes - active_minutes, 2))
            if cloud_delta > 0 and wall_clock_minutes > 0
            else 0.0
        )

        if cloud_delta >= 1.0 or wall_clock_minutes >= 0.5 or achievements_earned > 0:
            final_duration = max(1, int(round(active_minutes)))
            recorded_end = end_dt if end_dt > start_dt else start_dt + timedelta(seconds=max(1, final_duration))
            outcome = self.activity_service.record_interval_observation(
                scope,
                game_id=active.game_id,
                source="xbox_telemetry",
                platform=active.platform,
                started_at=start_dt.isoformat(),
                ended_at=recorded_end.isoformat(),
                duration_minutes=final_duration,
                precision_level="exact",
                notes=active.rich_presence,
                achievements_unlocked=achievements_earned,
                active_minutes=active_minutes,
                idle_minutes=idle_minutes,
                external_session_ref=f"xbox:{active.external_title_id}:{active.started_at}",
                idempotency_key=f"telemetry:xbox:{active.external_title_id}:{active.started_at}:{end_dt.isoformat()}",
            )
            if cloud_delta > 0:
                existing_game = self.library_service.get_game(scope, active.game_id)
                self.library_service.apply_platform_snapshot(
                    scope,
                    external_id=existing_game.external_id,
                    title=active.title,
                    platform=active.platform,
                    source="Xbox",
                    playtime_minutes=int(round(updated_minutes)),
                    last_played=recorded_end.isoformat(),
                )
            return {
                "provider": "xbox",
                "action": "closed",
                "title": active.title,
                "game_id": active.game_id,
                "duration_minutes": final_duration,
                "active_minutes": active_minutes,
                "idle_minutes": idle_minutes,
                "achievements_unlocked": achievements_earned,
                "session_id": outcome.session.id if outcome.session else None,
                "status": outcome.status,
            }

        return {
            "provider": "xbox",
            "action": "discarded_short",
            "title": active.title,
            "game_id": active.game_id,
            "duration_minutes": 0,
        }

    async def _poll_gog_user(
        self,
        scope: UserScope,
        creds: DecryptedConnections,
        *,
        client: httpx.AsyncClient,
        now: datetime,
        sleep_before_flush: bool,
    ) -> list[dict[str, Any]]:
        now_iso = now.isoformat()
        detected, refreshed = await self.gog_adapter.fetch_presence(
            access_token=creds.gog_access_token,
            refresh_token=creds.gog_refresh_token,
            user_id=creds.gog_user_id,
            client=client,
        )
        effective_access = refreshed.access_token if refreshed else creds.gog_access_token
        effective_refresh = refreshed.refresh_token if refreshed else creds.gog_refresh_token
        effective_uid = (refreshed.user_id if refreshed else None) or creds.gog_user_id
        effective_username = (refreshed.username if refreshed else None) or creds.gog_username
        if refreshed is not None:
            self.identity_service.save_resolved_provider_state(
                scope,
                gog_user_id=refreshed.user_id,
                gog_username=refreshed.username,
                gog_access_token=refreshed.access_token,
                gog_refresh_token=refreshed.refresh_token,
            )

        bundle = (
            GogTokenBundle(
                access_token=effective_access,
                refresh_token=effective_refresh,
                user_id=effective_uid or "",
                username=effective_username,
            )
            if effective_access and effective_uid
            else None
        )

        active = self.activity_service.get_active_telemetry_session(scope, "gog")
        events: list[dict[str, Any]] = []

        if detected:
            if active is None:
                started, bundle = await self._start_gog_session(
                    scope,
                    presence=detected,
                    bundle=bundle,
                    now_iso=now_iso,
                    client=client,
                )
                events.append({"provider": "gog", "action": "started", "title": started.title, "game_id": started.game_id})
            elif active.external_title_id == detected.game_id:
                self.activity_service.touch_active_telemetry_session(
                    scope,
                    "gog",
                    last_seen_at=now_iso,
                    rich_presence=detected.rich_presence,
                )
                events.append({"provider": "gog", "action": "touched", "title": active.title, "game_id": active.game_id})
            else:
                closed, bundle = await self._close_gog_session(
                    scope,
                    active,
                    bundle=bundle,
                    ended_at=now,
                    sleep_before_flush=sleep_before_flush,
                    client=client,
                )
                events.append(closed)
                started, bundle = await self._start_gog_session(
                    scope,
                    presence=detected,
                    bundle=bundle,
                    now_iso=now_iso,
                    client=client,
                )
                events.append({"provider": "gog", "action": "started", "title": started.title, "game_id": started.game_id})
        elif active is not None:
            closed, bundle = await self._close_gog_session(
                scope,
                active,
                bundle=bundle,
                ended_at=now,
                sleep_before_flush=sleep_before_flush,
                client=client,
            )
            events.append(closed)

        return events

    async def _start_gog_session(
        self,
        scope: UserScope,
        *,
        presence: GogActivePresence,
        bundle: GogTokenBundle | None,
        now_iso: str,
        client: httpx.AsyncClient,
    ) -> tuple[ActiveTelemetrySession, GogTokenBundle | None]:
        pid = presence.game_id
        ext_id = f"gog:{pid}"
        game = self.library_service.find_game_by_external_id(scope, ext_id)
        if game is None:
            game = self.library_service.find_game_by_external_id(scope, f"gog_{pid}")
        if game is None:
            title = presence.game_title
            if not title or title == f"GOG Game {pid}":
                fetched_title = await self.gog_adapter.fetch_product_title(product_id=pid, client=client)
                if fetched_title:
                    title = fetched_title
            game, _ = self.library_service.apply_platform_snapshot(
                scope,
                external_id=ext_id,
                title=title or f"GOG Game {pid}",
                platform="PC (GOG)",
                source="GOG",
                playtime_minutes=0,
                last_played=now_iso,
            )

        baseline_mins = int(game.playtime_minutes)
        current_bundle = bundle
        if current_bundle is not None:
            try:
                cloud_mins, _, next_bundle = await self.gog_adapter.fetch_game_sessions(
                    bundle=current_bundle,
                    product_id=pid,
                    client=client,
                )
                if next_bundle.access_token != current_bundle.access_token:
                    self.identity_service.save_resolved_provider_state(
                        scope,
                        gog_user_id=next_bundle.user_id,
                        gog_username=next_bundle.username,
                        gog_access_token=next_bundle.access_token,
                        gog_refresh_token=next_bundle.refresh_token,
                    )
                current_bundle = next_bundle
                baseline_mins = max(baseline_mins, int(cloud_mins))
            except Exception:
                pass

        self.activity_service.seed_baseline_if_absent(
            scope,
            game_id=game.id,
            source="GOG",
            baseline_minutes=baseline_mins,
            observed_at=now_iso,
        )
        session = self.activity_service.upsert_active_telemetry_session(
            scope,
            provider="gog",
            external_title_id=pid,
            game_id=game.id,
            title=game.title,
            platform=game.platform,
            source="gog_telemetry",
            started_at=now_iso,
            last_seen_at=now_iso,
            baseline_minutes=baseline_mins,
            baseline_achievements=game.achievements_unlocked,
            rich_presence=presence.rich_presence,
            detection_method="gog_presence_poll",
        )
        return session, current_bundle

    async def _close_gog_session(
        self,
        scope: UserScope,
        active: ActiveTelemetrySession,
        *,
        bundle: GogTokenBundle | None,
        ended_at: datetime,
        sleep_before_flush: bool,
        client: httpx.AsyncClient,
    ) -> tuple[dict[str, Any], GogTokenBundle | None]:
        self.activity_service.delete_active_telemetry_session(scope, "gog")
        start_dt = _parse_iso(active.started_at)
        end_dt = max(ended_at, start_dt)
        wall_clock_minutes = max(0.0, round((end_dt - start_dt).total_seconds() / 60.0, 2))

        if sleep_before_flush and self.flush_delay_seconds > 0:
            await asyncio.sleep(self.flush_delay_seconds)

        baseline_mins = float(active.baseline_minutes)
        updated_minutes = baseline_mins
        current_bundle = bundle
        if current_bundle is not None:
            try:
                cloud_mins, _, next_bundle = await self.gog_adapter.fetch_game_sessions(
                    bundle=current_bundle,
                    product_id=active.external_title_id,
                    client=client,
                )
                if next_bundle.access_token != current_bundle.access_token:
                    self.identity_service.save_resolved_provider_state(
                        scope,
                        gog_user_id=next_bundle.user_id,
                        gog_username=next_bundle.username,
                        gog_access_token=next_bundle.access_token,
                        gog_refresh_token=next_bundle.refresh_token,
                    )
                current_bundle = next_bundle
                updated_minutes = float(cloud_mins)
            except Exception:
                pass

        cloud_delta = max(0.0, round(updated_minutes - baseline_mins, 2))
        achievements_earned = 0
        if current_bundle is not None:
            try:
                achs, maybe_bundle = await self.gog_adapter.fetch_achievements(
                    access_token=current_bundle.access_token,
                    refresh_token=current_bundle.refresh_token,
                    user_id=current_bundle.user_id,
                    product_id=active.external_title_id,
                    client=client,
                )
                if maybe_bundle is not None:
                    if maybe_bundle.access_token != current_bundle.access_token:
                        self.identity_service.save_resolved_provider_state(
                            scope,
                            gog_user_id=maybe_bundle.user_id,
                            gog_username=maybe_bundle.username,
                            gog_access_token=maybe_bundle.access_token,
                            gog_refresh_token=maybe_bundle.refresh_token,
                        )
                    current_bundle = maybe_bundle
                if achs:
                    res = self.library_service.upsert_game_achievements(
                        scope,
                        game_id=active.game_id,
                        source="GOG",
                        platform=active.platform,
                        achievements=[_achievement_to_dict(a) for a in achs],
                    )
                    window_hits = self._count_window_unlocks(achs, start_dt, end_dt)
                    count_delta = max(0, int(res["unlocked"]) - active.baseline_achievements)
                    achievements_earned = max(window_hits, count_delta)
            except Exception:
                pass

        active_minutes = cloud_delta if cloud_delta > 0 else wall_clock_minutes
        idle_minutes = (
            max(0.0, round(wall_clock_minutes - active_minutes, 2))
            if cloud_delta > 0 and wall_clock_minutes > 0
            else 0.0
        )

        if cloud_delta >= 1.0 or wall_clock_minutes >= 0.5 or achievements_earned > 0:
            final_duration = max(1, int(round(active_minutes)))
            recorded_end = end_dt if end_dt > start_dt else start_dt + timedelta(seconds=max(1, final_duration))
            outcome = self.activity_service.record_interval_observation(
                scope,
                game_id=active.game_id,
                source="gog_telemetry",
                platform=active.platform,
                started_at=start_dt.isoformat(),
                ended_at=recorded_end.isoformat(),
                duration_minutes=final_duration,
                precision_level="exact",
                notes=active.rich_presence,
                achievements_unlocked=achievements_earned,
                active_minutes=active_minutes,
                idle_minutes=idle_minutes,
                external_session_ref=f"gog:{active.external_title_id}:{active.started_at}",
                idempotency_key=f"telemetry:gog:{active.external_title_id}:{active.started_at}:{end_dt.isoformat()}",
            )
            if cloud_delta > 0:
                existing_game = self.library_service.get_game(scope, active.game_id)
                self.library_service.apply_platform_snapshot(
                    scope,
                    external_id=existing_game.external_id,
                    title=active.title,
                    platform=active.platform,
                    source="GOG",
                    playtime_minutes=int(round(updated_minutes)),
                    last_played=recorded_end.isoformat(),
                )
            return (
                {
                    "provider": "gog",
                    "action": "closed",
                    "title": active.title,
                    "game_id": active.game_id,
                    "duration_minutes": final_duration,
                    "active_minutes": active_minutes,
                    "idle_minutes": idle_minutes,
                    "achievements_unlocked": achievements_earned,
                    "session_id": outcome.session.id if outcome.session else None,
                    "status": outcome.status,
                },
                current_bundle,
            )

        return (
            {
                "provider": "gog",
                "action": "discarded_short",
                "title": active.title,
                "game_id": active.game_id,
                "duration_minutes": 0,
            },
            current_bundle,
        )

    @staticmethod
    def _count_window_unlocks(
        achievements: list[AchievementSnapshot],
        start_dt: datetime,
        end_dt: datetime,
    ) -> int:
        window_start = start_dt - timedelta(minutes=5)
        window_end = end_dt + timedelta(minutes=5)
        hits = 0
        for ach in achievements:
            if not ach.unlocked or not ach.unlock_time:
                continue
            try:
                u_dt = _parse_iso(ach.unlock_time)
            except Exception:
                continue
            if window_start <= u_dt <= window_end:
                hits += 1
        return hits

    async def poll_all_users(
        self,
        *,
        now: datetime | None = None,
        sleep_before_flush: bool = True,
    ) -> int:
        if not self.db_manager.settings.outbound_sync_enabled:
            return 0
        total_events = 0
        for scope in self.identity_service.list_active_user_scopes():
            try:
                res = await self.poll_user(
                    scope,
                    now=now,
                    sleep_before_flush=sleep_before_flush,
                )
                total_events += len(res.get("events") or [])
            except Exception:
                continue
        return total_events

    async def run_daily_sweep_for_user(
        self,
        scope: UserScope,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Reconcile offline/handheld/short sessions missed by live telemetry."""
        scope.require_active()
        current_dt = now or datetime.now(timezone.utc)
        now_iso = current_dt.isoformat()
        date_bucket = current_dt.date().isoformat()
        if not self.db_manager.settings.outbound_sync_enabled:
            return {
                "status": "skipped",
                "reconciled_count": 0,
                "titles_checked": 0,
                "providers": [],
                "errors": ["Outbound network sync is disabled on this instance"],
            }

        creds = self.identity_service.get_decrypted_connections(scope)
        reconciled_count = 0
        titles_checked = 0
        providers_checked: list[str] = []
        errors: list[str] = []

        async with self.client_factory() as client:
            xbl_key = self._xbox_telemetry_key(creds)
            if xbl_key and (creds.xbox_xuid or creds.xbox_gamertag):
                try:
                    xuid = await self._resolve_xbox_xuid(scope, creds, xbl_key, client)
                    if xuid:
                        providers_checked.append("xbox")
                        recent_titles = await self.xbox_adapter.fetch_recent_achievements_for_sweep(
                            api_key=xbl_key,
                            xuid=xuid,
                            client=client,
                        )
                        titles_checked += len(recent_titles)
                        if recent_titles:
                            playtime_map = await self.xbox_adapter.fetch_batch_minutes_played(
                                api_key=xbl_key,
                                xuid=xuid,
                                title_ids=[str(t["titleId"]) for t in recent_titles],
                                client=client,
                            )
                            for t_info in recent_titles:
                                tid = str(t_info["titleId"])
                                mins = playtime_map.get(tid)
                                if mins is None:
                                    continue
                                mins_int = int(round(mins))
                                ext_id = f"xbox:{tid}"
                                legacy_game = self.library_service.find_game_by_external_id(scope, f"xbox_{tid}")
                                if legacy_game is not None and self.library_service.find_game_by_external_id(scope, ext_id) is None:
                                    ext_id = legacy_game.external_id
                                game, _ = self.library_service.apply_platform_snapshot(
                                    scope,
                                    external_id=ext_id,
                                    title=str(t_info.get("name") or f"Xbox Title {tid}"),
                                    platform="Xbox",
                                    source="Xbox",
                                    playtime_minutes=mins_int,
                                    last_played=t_info.get("lastTimePlayed") or now_iso,
                                )
                                outcome = self.activity_service.record_cumulative_observation(
                                    scope,
                                    game_id=game.id,
                                    source="Xbox",
                                    platform=game.platform,
                                    observed_minutes=mins_int,
                                    observed_at=t_info.get("lastTimePlayed") or now_iso,
                                    idempotency_key=f"daily_sweep:xbox:{tid}:{date_bucket}:{mins_int}",
                                    precision_level="estimated",
                                )
                                if outcome.session is not None:
                                    reconciled_count += 1
                except Exception as exc:
                    errors.append(f"Xbox sweep: {sanitize_error_message(str(exc))}")

            if creds.gog_access_token or creds.gog_refresh_token or creds.gog_username:
                try:
                    providers_checked.append("gog")
                    gog_result = await self.gog_adapter.fetch_library_detailed(
                        access_token=creds.gog_access_token,
                        refresh_token=creds.gog_refresh_token,
                        user_id=creds.gog_user_id,
                        username=creds.gog_username,
                        client=client,
                    )
                    if gog_result.tokens is not None:
                        self.identity_service.save_resolved_provider_state(
                            scope,
                            gog_user_id=gog_result.tokens.user_id,
                            gog_username=gog_result.tokens.username,
                            gog_access_token=gog_result.tokens.access_token,
                            gog_refresh_token=gog_result.tokens.refresh_token,
                        )
                    played_snaps = [s for s in gog_result.snapshots if s.playtime_minutes > 0]
                    titles_checked += len(played_snaps)
                    for snap in played_snaps:
                        game, _ = self.library_service.apply_platform_snapshot(
                            scope,
                            external_id=snap.external_id,
                            title=snap.title,
                            platform=snap.platform,
                            source="GOG",
                            playtime_minutes=snap.playtime_minutes,
                            last_played=snap.last_played or now_iso,
                        )
                        outcome = self.activity_service.record_cumulative_observation(
                            scope,
                            game_id=game.id,
                            source="GOG",
                            platform=game.platform,
                            observed_minutes=snap.playtime_minutes,
                            observed_at=snap.last_played or now_iso,
                            idempotency_key=f"daily_sweep:gog:{snap.external_id}:{date_bucket}:{snap.playtime_minutes}",
                            precision_level="estimated",
                        )
                        if outcome.session is not None:
                            reconciled_count += 1
                except Exception as exc:
                    errors.append(f"GOG sweep: {sanitize_error_message(str(exc))}")

        return {
            "status": "ok" if not errors else ("partial" if providers_checked else "error"),
            "reconciled_count": reconciled_count,
            "titles_checked": titles_checked,
            "providers": providers_checked,
            "errors": errors,
        }

    async def run_daily_sweep_all_users(self, *, now: datetime | None = None) -> int:
        if not self.db_manager.settings.outbound_sync_enabled:
            return 0
        total_reconciled = 0
        for scope in self.identity_service.list_active_user_scopes():
            try:
                res = await self.run_daily_sweep_for_user(scope, now=now)
                total_reconciled += int(res.get("reconciled_count") or 0)
            except Exception:
                continue
        return total_reconciled


class XboxRtaClient:
    """OpenXBL Real-Time Activity (RTA) WebSocket client scoped to one user."""

    def __init__(
        self,
        *,
        scope: UserScope,
        watcher_api_key: str,
        xuid: str,
        telemetry_service: TelemetryService,
    ) -> None:
        self.scope = scope
        self.watcher_api_key = watcher_api_key.strip()
        self.xuid = xuid.strip()
        self.telemetry_service = telemetry_service
        self._running = False

    async def fetch_rta_nonce(self) -> str | None:
        if not self.telemetry_service.db_manager.settings.outbound_sync_enabled:
            return None
        headers = {
            "X-Authorization": self.watcher_api_key,
            "Accept": "application/json",
        }
        try:
            async with self.telemetry_service.client_factory() as client:
                resp = await client.get("https://api.xbl.io/v2/rta", headers=headers, timeout=15.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("nonce") or (data.get("content") or {}).get("nonce")
        except Exception as exc:
            logger.warning("Failed to fetch Xbox RTA nonce for user %s: %s", self.scope.username, exc)
        return None

    async def handle_rta_packet(self, raw_message: str) -> list[dict[str, Any]]:
        packet = json.loads(raw_message)
        if isinstance(packet, list) and len(packet) >= 4:
            return await self.telemetry_service.apply_xbox_presence_payload(
                self.scope,
                packet[3],
                api_key=self.watcher_api_key,
                xuid=self.xuid,
                detection_method="rta_websocket",
            )
        return []

    async def run(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.info("websockets library unavailable; falling back to Xbox presence polling")
            return

        self._running = True
        while self._running:
            nonce = await self.fetch_rta_nonce()
            if not nonce:
                await asyncio.sleep(60)
                continue
            ws_url = f"wss://rta.xboxlive.com/connect?nonce={nonce}"
            try:
                async with websockets.connect(
                    ws_url,
                    subprotocols=["rta.xboxlive.com.V2"],
                    ping_interval=30,
                    ping_timeout=15,
                ) as ws:
                    await ws.send(
                        json.dumps([1, 1, f"https://userpresence.xboxlive.com/users/xuid({self.xuid})"])
                    )
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            await self.handle_rta_packet(message)
                        except Exception as exc:
                            logger.debug("RTA packet handling error: %s", exc)
            except asyncio.CancelledError:
                self._running = False
                raise
            except Exception as exc:
                logger.warning("Xbox RTA WebSocket disconnected (%s); reconnecting in 30s", exc)
                await asyncio.sleep(30)

    def stop(self) -> None:
        self._running = False
