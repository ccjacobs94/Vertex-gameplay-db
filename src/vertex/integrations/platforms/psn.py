"""PlayStation Network (PSNAWP) platform adapter (injectable & fixture-testable)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

import httpx

from vertex.catalog.models import normalize_title
from vertex.integrations.platforms.base import (
    AchievementSnapshot,
    AdapterCapabilities,
    PlatformAdapterError,
    PlatformGameSnapshot,
)


def clean_psn_token(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("npsso"):
            return str(data["npsso"]).strip()
    except Exception:
        pass
    match = re.search(r'["\']?npsso["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]+)["\']?', text)
    if match:
        return match.group(1).strip()
    return text.strip("\"' ")


def _default_psn_client_factory(npsso: str) -> Any:
    from psnawp_api import PSNAWP

    return PSNAWP(npsso).me()


class PSNAdapter:
    """Translates PSNAWP client models into normalized snapshots and trophies."""

    def __init__(self, client_factory: Callable[[str], Any] | None = None) -> None:
        self.psn_client_factory = client_factory or _default_psn_client_factory

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider="psn",
            display_name="PlayStation Network",
            supports_playtime=True,
            supports_achievements=True,
            supports_game_pass=False,
            auth_modes=("npsso",),
            supports_telemetry=False,
        )

    def _map_psn_exception(self, exc: Exception) -> PlatformAdapterError:
        name = type(exc).__name__
        if any(
            k in name
            for k in ("Authentication", "InvalidToken", "Unauthorized", "Forbidden")
        ):
            return PlatformAdapterError(
                code="auth_failed",
                message=f"PSN rejected configured NPSSO token ({name}): {exc}",
                retryable=False,
            )
        if "TooManyRequests" in name:
            return PlatformAdapterError(
                code="rate_limited",
                message="PSN API rate limited requests",
                retryable=True,
                throttle_seconds=60,
            )
        if "NotFound" in name:
            return PlatformAdapterError(
                code="not_found",
                message=f"PSN resource not found: {exc}",
                retryable=False,
            )
        return PlatformAdapterError(
            code="upstream_error",
            message=f"PSN sync failed: {exc}",
            retryable=True,
        )

    def _format_trophies(self, trophies: Any) -> list[AchievementSnapshot]:
        formatted: list[AchievementSnapshot] = []
        for t in trophies:
            tid = str(getattr(t, "trophy_id", "") or "").strip()
            if not tid:
                continue
            tname = str(getattr(t, "trophy_name", "") or f"Trophy {tid}").strip()
            tdetail = str(getattr(t, "trophy_detail", "") or "").strip()
            ticon = getattr(t, "trophy_icon_url", None)
            earned = bool(getattr(t, "earned", False))
            edt = getattr(t, "earned_date_time", None)
            unlock_time = edt.isoformat() if edt else None
            ttype = getattr(t, "trophy_type", None)
            ttype_val = getattr(ttype, "value", ttype) if ttype is not None else None
            ttype_str = str(ttype_val).split(".")[-1].capitalize() if ttype_val else "Trophy"
            earn_rate = getattr(t, "trophy_earn_rate", None)
            rarity = float(earn_rate) if earn_rate is not None else None
            formatted.append(
                AchievementSnapshot(
                    external_ach_id=tid,
                    name=tname,
                    description=tdetail,
                    icon_url=str(ticon) if ticon else None,
                    unlocked=earned,
                    unlock_time=unlock_time,
                    reward_type=ttype_str,
                    reward_value=ttype_str.lower(),
                    rarity_pct=rarity,
                    is_hidden=bool(getattr(t, "trophy_hidden", False)),
                )
            )
        return formatted

    def _sync_library_blocking(
        self, npsso: str
    ) -> tuple[list[PlatformGameSnapshot], dict[str, list[AchievementSnapshot]]]:
        client = self.psn_client_factory(npsso)
        trophy_title_map: dict[str, Any] = {}
        trophy_map: dict[str, dict[str, int]] = {}
        try:
            for tr in client.trophy_titles():
                t_name = (getattr(tr, "title_name", None) or "").strip().lower()
                defined = getattr(tr, "defined_trophies", None)
                earned = getattr(tr, "earned_trophies", None)
                if t_name and defined and earned:
                    tot = sum(getattr(defined, k, 0) for k in ("bronze", "silver", "gold", "platinum"))
                    unl = sum(getattr(earned, k, 0) for k in ("bronze", "silver", "gold", "platinum"))
                    trophy_map[t_name] = {"unlocked": int(unl), "total": int(tot)}
                    trophy_title_map[t_name] = tr
        except Exception as exc:
            # Fail loudly on auth/rate limit; degrade cleanly on transient summary issues.
            mapped = self._map_psn_exception(exc)
            if mapped.code in ("auth_failed", "rate_limited"):
                raise mapped from exc

        snapshots: list[PlatformGameSnapshot] = []
        ach_by_ext_id: dict[str, list[AchievementSnapshot]] = {}
        for item in client.title_stats():
            title_id = getattr(item, "title_id", None)
            name = getattr(item, "name", None)
            if not title_id or not name:
                continue

            play_dur = getattr(item, "play_duration", None)
            mins = max(0, int(round(play_dur.total_seconds() / 60.0))) if play_dur else 0
            last_dt = getattr(item, "last_played_date_time", None)
            last_played = last_dt.isoformat() if last_dt else None
            cat = str(getattr(item, "category", "")).upper()
            platform = "PlayStation 5" if "PS5" in cat else "PlayStation 4"
            cover_url = getattr(item, "image_url", None)

            key_lower = str(name).strip().lower()
            tr_info = trophy_map.get(key_lower, {"unlocked": 0, "total": 0})
            ext_id = f"psn:{title_id}"

            snapshots.append(
                PlatformGameSnapshot(
                    external_id=ext_id,
                    title=str(name).strip(),
                    platform=platform,
                    source="PSN",
                    playtime_minutes=mins,
                    last_played=last_played,
                    cover_url=str(cover_url) if cover_url else None,
                    achievements_unlocked=tr_info["unlocked"],
                    achievements_total=tr_info["total"],
                )
            )

            tr_obj = trophy_title_map.get(key_lower)
            if tr_obj and tr_info["total"] > 0:
                np_comm_id = getattr(tr_obj, "np_communication_id", None)
                plat_set = getattr(tr_obj, "title_platform", None) or []
                target_plat = next(iter(plat_set)) if plat_set else None
                if np_comm_id and target_plat:
                    try:
                        trophies = list(client.trophies(np_comm_id, target_plat, include_progress=True))
                        formatted = self._format_trophies(trophies)
                        if formatted:
                            ach_by_ext_id[ext_id] = formatted
                    except Exception:
                        pass

        return snapshots, ach_by_ext_id

    async def fetch_library_with_trophies(
        self,
        *,
        npsso: str,
    ) -> tuple[list[PlatformGameSnapshot], dict[str, list[AchievementSnapshot]]]:
        cleaned = clean_psn_token(npsso)
        if not cleaned:
            raise PlatformAdapterError(
                code="missing_credentials",
                message="PSN NPSSO token is required",
                retryable=False,
            )
        try:
            return await asyncio.to_thread(self._sync_library_blocking, cleaned)
        except PlatformAdapterError:
            raise
        except Exception as exc:
            raise self._map_psn_exception(exc) from exc

    async def fetch_library(
        self,
        *,
        user_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> list[PlatformGameSnapshot]:
        del user_identifier, client
        snaps, _ = await self.fetch_library_with_trophies(npsso=api_key)
        return snaps

    def _fetch_single_achievements_blocking(
        self, npsso: str, title: str
    ) -> list[AchievementSnapshot]:
        client = self.psn_client_factory(npsso)
        target_name = (title or "").strip().lower()
        target_norm = normalize_title(target_name)
        for tr in client.trophy_titles():
            tr_name = (getattr(tr, "title_name", None) or "").strip().lower()
            if tr_name == target_name or (target_norm and normalize_title(tr_name) == target_norm):
                np_comm_id = getattr(tr, "np_communication_id", None)
                plat_set = getattr(tr, "title_platform", None) or []
                target_plat = next(iter(plat_set)) if plat_set else None
                if np_comm_id and target_plat:
                    trophies = list(client.trophies(np_comm_id, target_plat, include_progress=True))
                    return self._format_trophies(trophies)
                break
        return []

    async def fetch_achievements_for_title(
        self,
        *,
        npsso: str,
        title: str,
    ) -> list[AchievementSnapshot]:
        cleaned = clean_psn_token(npsso)
        if not cleaned or not title:
            return []
        try:
            return await asyncio.to_thread(self._fetch_single_achievements_blocking, cleaned, title)
        except Exception as exc:
            mapped = self._map_psn_exception(exc)
            if mapped.code == "auth_failed":
                raise mapped from exc
            return []

    def _verify_blocking(self, cleaned: str) -> dict[str, Any]:
        if len(cleaned) == 64 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
            try:
                me = self.psn_client_factory(cleaned)
                online_id = getattr(me, "online_id", "") or "PSN User"
                account_id = getattr(me, "account_id", "")
                return {
                    "status": "ok",
                    "platform": "psn",
                    "online_id": str(online_id),
                    "account_id": str(account_id),
                    "message": f"PlayStation Network account verified successfully as {online_id}",
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "platform": "psn",
                    "message": f"PlayStation NPSSO verification failed: {exc}",
                }
        if len(cleaned) <= 30 and re.match(r"^[a-zA-Z0-9_-]+$", cleaned):
            return {
                "status": "ok",
                "platform": "psn",
                "online_id": cleaned,
                "message": f"PlayStation Online ID set to {cleaned} (library sync requires 64-hex NPSSO token)",
            }
        return {
            "status": "error",
            "platform": "psn",
            "message": "Invalid PSN NPSSO token (must be 64 hexadecimal characters) or invalid PSN Online ID",
        }

    async def verify_connection(self, *, credential: str) -> dict[str, Any]:
        cleaned = clean_psn_token(credential)
        if not cleaned:
            return {
                "status": "error",
                "platform": "psn",
                "message": "PlayStation token or Online ID cannot be empty",
            }
        return await asyncio.to_thread(self._verify_blocking, cleaned)
