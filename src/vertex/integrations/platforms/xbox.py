"""OpenXBL (xbl.io) platform adapter for Xbox & PC Game Pass (fixture-testable)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from vertex.integrations.platforms.base import (
    AchievementSnapshot,
    AdapterCapabilities,
    PlatformAdapterError,
    PlatformGameSnapshot,
)

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


def _parse_retry_after(raw_header: str | None) -> int:
    val = (raw_header or "").strip()
    if not val:
        return 60
    try:
        return max(1, min(600, int(val)))
    except ValueError:
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = int((dt - datetime.now(timezone.utc)).total_seconds())
            return max(1, min(600, delta))
        except Exception:
            return 60


def _parse_iso_optional(raw: Any) -> str | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


class XboxAdapter:
    """Translates OpenXBL v2 API responses into normalized snapshots, achievements, and presence."""

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider="xbox",
            display_name="Xbox / Game Pass",
            supports_playtime=True,
            supports_achievements=True,
            supports_game_pass=True,
            auth_modes=("api_key", "gamertag+server_key", "xuid+server_key"),
            supports_telemetry=True,
        )

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "X-Authorization": api_key,
            "Accept": "application/json",
            "Accept-Language": "en-US",
        }

    def _check_status(self, resp: httpx.Response, context: str) -> None:
        if resp.status_code in (401, 403):
            raise PlatformAdapterError(
                code="auth_failed",
                message=f"{context} rejected configured OpenXBL API key (HTTP {resp.status_code})",
                retryable=False,
            )
        if resp.status_code == 429:
            raise PlatformAdapterError(
                code="rate_limited",
                message=f"{context} rate limited requests",
                retryable=True,
                throttle_seconds=_parse_retry_after(resp.headers.get("Retry-After")),
            )
        if resp.status_code >= 500:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"{context} returned HTTP {resp.status_code}",
                retryable=True,
            )

    def _unwrap_titles(self, payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
        xuid: str | None = None
        titles: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, dict):
                xuid = str(content.get("xuid") or "") or None
                raw_t = content.get("titles") or []
            else:
                xuid = str(payload.get("xuid") or "") or None
                raw_t = payload.get("titles") or []
            if isinstance(raw_t, list):
                titles = [t for t in raw_t if isinstance(t, dict)]
        elif isinstance(payload, list):
            titles = [t for t in payload if isinstance(t, dict)]
        return xuid, titles

    async def resolve_gamertag(
        self,
        *,
        gamertag: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> dict[str, str] | None:
        clean_gt = (gamertag or "").strip()
        if not clean_gt or not api_key:
            return None

        encoded_gt = quote(clean_gt)
        headers = self._headers(api_key)
        endpoints = (
            f"https://api.xbl.io/v2/friends/search?gt={encoded_gt}",
            f"https://xbl.io/api/v2/friends/search?gt={encoded_gt}",
            f"https://api.xbl.io/v2/player/gamertag/{encoded_gt}",
            f"https://xbl.io/api/v2/player/gamertag/{encoded_gt}",
        )

        for ep in endpoints:
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.HTTPError:
                continue
            self._check_status(resp, "OpenXBL gamertag lookup")
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            profile_users: list[Any] = []
            if isinstance(data, dict):
                profile_users = (
                    data.get("profileUsers")
                    or data.get("people")
                    or (data.get("content") or {}).get("profileUsers")
                    or (data.get("content") or {}).get("people")
                    or []
                )
                if not profile_users and ("id" in data or "xuid" in data):
                    profile_users = [data]
            elif isinstance(data, list):
                profile_users = data

            if profile_users and isinstance(profile_users[0], dict):
                u = profile_users[0]
                xuid = str(u.get("id") or u.get("hostId") or u.get("xuid") or "").strip()
                if not xuid:
                    continue
                settings = {
                    s.get("id"): str(s.get("value") or "")
                    for s in (u.get("settings") or [])
                    if isinstance(s, dict)
                }
                return {
                    "xuid": xuid,
                    "gamertag": settings.get("Gamertag")
                    or settings.get("ModernGamertag")
                    or str(u.get("gamertag") or clean_gt),
                    "gamerscore": settings.get("Gamerscore") or str(u.get("gamerscore") or "0"),
                    "avatar_url": settings.get("GameDisplayPicRaw") or str(u.get("displayPicRaw") or ""),
                }
        return None

    async def resolve_account_xuid(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> dict[str, str] | None:
        headers = self._headers(api_key)
        for ep in ("https://xbl.io/api/v2/account", "https://api.xbl.io/v2/account"):
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.HTTPError:
                continue
            self._check_status(resp, "OpenXBL account lookup")
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            users = (data.get("content") or {}).get("profileUsers") or data.get("profileUsers") or []
            if users and isinstance(users[0], dict):
                u = users[0]
                xuid = str(u.get("id") or u.get("hostId") or "").strip()
                settings = {
                    s.get("id"): str(s.get("value") or "")
                    for s in (u.get("settings") or [])
                    if isinstance(s, dict)
                }
                return {
                    "xuid": xuid,
                    "gamertag": settings.get("Gamertag") or str(u.get("hostId") or "Xbox Gamer"),
                    "gamerscore": settings.get("Gamerscore") or "0",
                    "avatar_url": settings.get("GameDisplayPicRaw") or "",
                }
        return None

    async def fetch_batch_minutes_played(
        self,
        *,
        api_key: str,
        xuid: str,
        title_ids: list[str],
        client: httpx.AsyncClient,
    ) -> dict[str, float]:
        if not xuid or not title_ids:
            return {}
        headers = {**self._headers(api_key), "Content-Type": "application/json"}
        playtime_map: dict[str, float] = {}
        chunk_size = 250
        for idx in range(0, len(title_ids), chunk_size):
            chunk = title_ids[idx : idx + chunk_size]
            endpoints = (
                "https://xbl.io/api/v2/player/stats",
                f"https://xbl.io/api/v2/achievements/stats/{xuid}",
                f"https://api.xbl.io/v2/achievements/stats/{xuid}",
            )
            for ep in endpoints:
                try:
                    if ep.endswith("/player/stats"):
                        resp = await client.post(
                            ep,
                            headers=headers,
                            json={
                                "xuids": [str(xuid)],
                                "stats": [{"name": "MinutesPlayed", "titleId": tid} for tid in chunk],
                            },
                        )
                    else:
                        resp = await client.post(
                            ep,
                            headers=headers,
                            json={"titleIds": [int(tid) if tid.isdigit() else tid for tid in chunk]},
                        )
                except httpx.HTTPError:
                    continue
                if resp.status_code in (401, 403, 429):
                    self._check_status(resp, "OpenXBL stats")
                if resp.status_code != 200:
                    continue
                try:
                    payload = resp.json()
                except Exception:
                    continue
                colls = (
                    (payload.get("content") or {}).get("statlistscollection")
                    or payload.get("statlistscollection")
                    or []
                )
                if isinstance(colls, dict):
                    colls = [colls]
                for col in colls:
                    if not isinstance(col, dict):
                        continue
                    for st in col.get("stats") or []:
                        if not isinstance(st, dict):
                            continue
                        tid = str(st.get("titleid") or st.get("titleId") or "")
                        val = st.get("value")
                        s_name = str(st.get("name") or "minutesplayed").lower()
                        if tid and s_name == "minutesplayed" and val is not None and str(val).strip() != "":
                            try:
                                playtime_map[tid] = float(val)
                            except (ValueError, TypeError):
                                pass
                if playtime_map:
                    break
        return playtime_map

    async def fetch_library_with_xuid(
        self,
        *,
        api_key: str,
        xbox_gamertag: str | None = None,
        xbox_xuid: str | None = None,
        client: httpx.AsyncClient,
    ) -> tuple[list[PlatformGameSnapshot], str | None]:
        if not api_key:
            raise PlatformAdapterError(
                code="missing_credentials",
                message="OpenXBL API key (or server XBL_API_KEY with Gamertag) is required",
                retryable=False,
            )

        headers = self._headers(api_key)
        xuid = (xbox_xuid or "").strip() or None
        gamertag = (xbox_gamertag or "").strip() or None

        if not xuid and gamertag:
            gt_info = await self.resolve_gamertag(
                gamertag=gamertag,
                api_key=api_key,
                client=client,
            )
            if gt_info and gt_info.get("xuid"):
                xuid = gt_info["xuid"]

        ach_endpoints: list[str] = []
        if xuid:
            ach_endpoints.append(f"https://api.xbl.io/v2/achievements/player/{xuid}")
            ach_endpoints.append(f"https://xbl.io/api/v2/achievements/player/{xuid}")
        if not gamertag:
            ach_endpoints.append("https://xbl.io/api/v2/achievements")

        titles: list[dict[str, Any]] = []
        any_upstream_error: PlatformAdapterError | None = None
        any_200 = False

        for ep in ach_endpoints:
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.TimeoutException:
                any_upstream_error = PlatformAdapterError(
                    code="timeout",
                    message="Timed out contacting OpenXBL achievements endpoint",
                    retryable=True,
                )
                continue
            except httpx.HTTPError as exc:
                any_upstream_error = PlatformAdapterError(
                    code="upstream_error",
                    message=f"OpenXBL transport error: {exc}",
                    retryable=True,
                )
                continue

            self._check_status(resp, "OpenXBL achievements")
            if resp.status_code == 200:
                any_200 = True
                try:
                    ep_xuid, ep_titles = self._unwrap_titles(resp.json())
                    if ep_xuid and not xuid:
                        xuid = ep_xuid
                    if ep_titles:
                        titles = ep_titles
                        break
                except Exception:
                    any_upstream_error = PlatformAdapterError(
                        code="upstream_error",
                        message="OpenXBL returned invalid JSON",
                        retryable=True,
                    )

        title_endpoints: list[str] = []
        if xuid:
            title_endpoints.append(f"https://api.xbl.io/v2/player/{xuid}/titles")
            title_endpoints.append(f"https://xbl.io/api/v2/player/{xuid}/titles")
        if not gamertag:
            title_endpoints.append("https://xbl.io/api/v2/titles")

        gamepass_map: dict[str, bool] = {}
        for ep in title_endpoints:
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.HTTPError:
                continue
            self._check_status(resp, "OpenXBL titles")
            if resp.status_code != 200:
                continue
            any_200 = True
            try:
                ep_xuid, ep_titles = self._unwrap_titles(resp.json())
            except Exception:
                continue
            if ep_xuid and not xuid:
                xuid = ep_xuid
            for qt in ep_titles:
                q_tid = qt.get("titleId")
                gp_info = qt.get("gamePass")
                if q_tid and isinstance(gp_info, dict) and gp_info.get("isGamePass") is not None:
                    gamepass_map[str(q_tid)] = bool(gp_info.get("isGamePass"))
            if not titles and ep_titles:
                titles = ep_titles
            if ep_titles:
                break

        if not any_200 and any_upstream_error is not None:
            raise any_upstream_error

        if not xuid and not gamertag:
            acc_info = await self.resolve_account_xuid(api_key=api_key, client=client)
            if acc_info and acc_info.get("xuid"):
                xuid = acc_info["xuid"]

        all_title_ids = [str(t.get("titleId")) for t in titles if t.get("titleId")]
        playtime_map: dict[str, float] = {}
        if xuid and all_title_ids:
            playtime_map = await self.fetch_batch_minutes_played(
                api_key=api_key,
                xuid=xuid,
                title_ids=all_title_ids,
                client=client,
            )

        snapshots: list[PlatformGameSnapshot] = []
        for t in titles:
            title_id = t.get("titleId")
            name = str(t.get("name") or "").strip()
            if not title_id or not name:
                continue
            title_type = str(t.get("type") or "Game").lower()
            if title_type not in ("game", "dvr", ""):
                continue

            devices = [str(d) for d in (t.get("devices") or [])]
            has_pc = any(d in ("Win32", "PC") for d in devices)
            has_console = any(d in ("XboxSeries", "XboxOne", "Xbox360") for d in devices)
            is_xpa = has_pc and has_console
            if is_xpa:
                platform = "Xbox / PC"
            elif has_pc:
                platform = "PC (Xbox)"
            else:
                platform = "Xbox"

            ach = t.get("achievement") if isinstance(t.get("achievement"), dict) else {}
            unlocked = int(ach.get("currentAchievements") or 0)
            total_ach = int(ach.get("totalAchievements") or 0)

            th = t.get("titleHistory") if isinstance(t.get("titleHistory"), dict) else {}
            last_played = _parse_iso_optional(th.get("lastTimePlayed") or t.get("lastTimePlayed"))

            poster_url: str | None = None
            boxart_url: str | None = None
            for img in t.get("images") or []:
                if not isinstance(img, dict):
                    continue
                itype = img.get("type")
                iurl = img.get("url")
                if itype == "Poster" and iurl:
                    poster_url = str(iurl)
                    break
                if itype == "BoxArt" and iurl and not boxart_url:
                    boxart_url = str(iurl)

            cover_url = poster_url or boxart_url or (str(t["displayImage"]) if t.get("displayImage") else None)
            mins_float = playtime_map.get(str(title_id), float(t.get("minutesPlayed") or 0.0))
            mins = max(0, int(round(mins_float)))

            gp_obj = t.get("gamePass")
            is_gp = gamepass_map.get(
                str(title_id),
                bool(gp_obj.get("isGamePass")) if isinstance(gp_obj, dict) else False,
            )

            snapshots.append(
                PlatformGameSnapshot(
                    external_id=f"xbox:{title_id}",
                    title=name,
                    platform=platform,
                    source="Xbox",
                    playtime_minutes=mins,
                    last_played=last_played,
                    cover_url=cover_url,
                    cover_is_poster=bool(poster_url),
                    is_game_pass=is_gp,
                    is_play_anywhere=is_xpa,
                    achievements_unlocked=unlocked,
                    achievements_total=total_ach,
                )
            )
        return snapshots, xuid

    async def fetch_library(
        self,
        *,
        user_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> list[PlatformGameSnapshot]:
        snaps, _ = await self.fetch_library_with_xuid(
            api_key=api_key,
            xbox_xuid=user_identifier if user_identifier.isdigit() else None,
            xbox_gamertag=None if user_identifier.isdigit() else user_identifier,
            client=client,
        )
        return snaps

    async def fetch_achievements(
        self,
        *,
        api_key: str,
        title_id: str,
        xuid: str | None = None,
        client: httpx.AsyncClient,
    ) -> list[AchievementSnapshot]:
        if not api_key or not title_id:
            return []
        headers = self._headers(api_key)
        endpoints: list[str] = []
        if xuid:
            endpoints.append(f"https://api.xbl.io/v2/achievements/player/{xuid}/{title_id}")
            endpoints.append(f"https://xbl.io/api/v2/achievements/player/{xuid}/{title_id}")
        endpoints.append(f"https://xbl.io/api/v2/achievements/title/{title_id}")

        data: dict[str, Any] | None = None
        for ep in endpoints:
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.HTTPError:
                continue
            if resp.status_code in (401, 403, 429):
                self._check_status(resp, "OpenXBL game achievements")
            if resp.status_code == 200:
                try:
                    parsed = resp.json()
                    if isinstance(parsed, dict):
                        data = parsed
                        break
                except Exception:
                    continue
        if not data:
            return []

        raw_achs = (data.get("content") or {}).get("achievements") or data.get("achievements") or []
        if not isinstance(raw_achs, list):
            return []

        out: list[AchievementSnapshot] = []
        for a in raw_achs:
            if not isinstance(a, dict):
                continue
            ach_id = str(a.get("id") or "").strip()
            if not ach_id:
                continue
            name = str(a.get("name") or ach_id).strip()
            desc = str(a.get("description") or a.get("lockedDescription") or "").strip()
            icon_url: str | None = None
            for m in a.get("mediaAssets") or []:
                if isinstance(m, dict) and m.get("type") == "Icon" and m.get("url"):
                    icon_url = str(m["url"])
                    break
            unlocked = str(a.get("progressState") or "").lower() == "achieved"
            prog = a.get("progression") if isinstance(a.get("progression"), dict) else {}
            unlock_time = _parse_iso_optional(prog.get("timeUnlocked")) if unlocked else None

            gamerscore: str | None = None
            for rew in a.get("rewards") or []:
                if isinstance(rew, dict) and rew.get("type") == "Gamerscore" and rew.get("value") is not None:
                    gamerscore = str(rew["value"])
                    break

            rarity_obj = a.get("rarity") if isinstance(a.get("rarity"), dict) else {}
            rarity_pct: float | None = None
            if rarity_obj.get("currentPercentage") is not None:
                try:
                    rarity_pct = float(rarity_obj["currentPercentage"])
                except (ValueError, TypeError):
                    pass

            out.append(
                AchievementSnapshot(
                    external_ach_id=ach_id,
                    name=name,
                    description=desc,
                    icon_url=icon_url,
                    unlocked=unlocked,
                    unlock_time=unlock_time,
                    reward_type="Gamerscore" if gamerscore is not None else None,
                    reward_value=gamerscore,
                    rarity_pct=rarity_pct,
                    is_hidden=bool(a.get("isSecret")),
                )
            )
        return out

    async def fetch_presence(
        self,
        *,
        api_key: str,
        xuid: str | None,
        client: httpx.AsyncClient,
    ) -> Any:
        headers = self._headers(api_key)
        endpoints: list[str] = []
        if xuid:
            endpoints.append(f"https://api.xbl.io/v2/presence/{xuid}")
            endpoints.append(f"https://xbl.io/api/v2/presence/{xuid}")
        endpoints.append("https://api.xbl.io/v2/presence")
        endpoints.append("https://xbl.io/api/v2/presence")

        for ep in endpoints:
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.HTTPError:
                continue
            if resp.status_code in (401, 403, 429):
                self._check_status(resp, "OpenXBL presence")
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    continue
        return None

    async def fetch_recent_achievements_for_sweep(
        self,
        *,
        api_key: str,
        xuid: str,
        client: httpx.AsyncClient,
        now: datetime | None = None,
        max_age_hours: float = 24.0,
    ) -> list[dict[str, Any]]:
        headers = self._headers(api_key)
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=max_age_hours)
        for ep in (
            f"https://api.xbl.io/v2/achievements/player/{xuid}",
            f"https://xbl.io/api/v2/achievements/player/{xuid}",
        ):
            try:
                resp = await client.get(ep, headers=headers)
            except httpx.HTTPError:
                continue
            if resp.status_code in (401, 403, 429):
                self._check_status(resp, "OpenXBL daily sweep")
            if resp.status_code == 200:
                try:
                    _, titles = self._unwrap_titles(resp.json())
                except Exception:
                    continue
                recent: list[dict[str, Any]] = []
                for t in titles:
                    tid = str(t.get("titleId") or "").strip()
                    name = str(t.get("name") or "").strip()
                    if not tid or is_system_app(tid, name):
                        continue
                    th = t.get("titleHistory") if isinstance(t.get("titleHistory"), dict) else {}
                    raw_last = th.get("lastTimePlayed") or t.get("lastTimePlayed")
                    iso_last = _parse_iso_optional(raw_last)
                    if not iso_last:
                        continue
                    if datetime.fromisoformat(iso_last) < cutoff:
                        continue
                    enriched = dict(t)
                    enriched["titleId"] = tid
                    enriched["lastTimePlayed"] = iso_last
                    recent.append(enriched)
                return recent
        return []

    async def verify_connection(
        self,
        *,
        credential: str,
        server_xbl_key: str,
        client: httpx.AsyncClient,
        credential_is_api_key: bool = False,
    ) -> dict[str, Any]:
        cleaned = (credential or "").strip()
        if not cleaned:
            return {
                "status": "error",
                "platform": "xbox",
                "message": "Xbox Gamertag or OpenXBL API key cannot be empty",
            }
        if not credential_is_api_key and server_xbl_key:
            try:
                gt_res = await self.resolve_gamertag(
                    gamertag=cleaned,
                    api_key=server_xbl_key,
                    client=client,
                )
                if gt_res and gt_res.get("xuid"):
                    return {
                        "status": "ok",
                        "platform": "xbox",
                        "gamertag": gt_res["gamertag"],
                        "xuid": gt_res["xuid"],
                        "gamerscore": gt_res["gamerscore"],
                        "avatar_url": gt_res.get("avatar_url", ""),
                        "message": f"Xbox account verified successfully as {gt_res['gamertag']} ({gt_res['gamerscore']} G)",
                    }
            except PlatformAdapterError as exc:
                if exc.code in ("rate_limited", "timeout", "upstream_error", "auth_failed"):
                    return {"status": "error", "platform": "xbox", "message": exc.safe_message}
            except Exception:
                pass

        try:
            acc = await self.resolve_account_xuid(api_key=cleaned, client=client)
            if acc is not None:
                return {
                    "status": "ok",
                    "platform": "xbox",
                    "gamertag": acc["gamertag"],
                    "xuid": acc["xuid"],
                    "gamerscore": acc["gamerscore"],
                    "avatar_url": acc["avatar_url"],
                    "message": f"Xbox account verified successfully as {acc['gamertag']} ({acc['gamerscore']} G)",
                }
        except PlatformAdapterError as exc:
            if not credential_is_api_key and server_xbl_key:
                return {
                    "status": "error",
                    "platform": "xbox",
                    "message": f"Could not find Xbox Gamertag '{cleaned}'. Please check spelling or privacy settings.",
                }
            return {"status": "error", "platform": "xbox", "message": exc.safe_message}

        return {
            "status": "error",
            "platform": "xbox",
            "message": (
                f"Could not find Xbox Gamertag '{cleaned}'"
                if (not credential_is_api_key and server_xbl_key)
                else "Invalid OpenXBL API key or unreachable Xbox account"
            ),
        }
