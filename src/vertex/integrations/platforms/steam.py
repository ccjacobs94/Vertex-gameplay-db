"""Steam Web API platform adapter (fixture-testable, never touches personal DBs directly)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from vertex.integrations.platforms.base import (
    AchievementSnapshot,
    AdapterCapabilities,
    PlatformAdapterError,
    PlatformGameSnapshot,
)


def clean_steam_input(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip().split("?")[0].split("#")[0].strip()
    text = re.sub(
        r"^https?://(?:www\.)?steamcommunity\.com/(?:id|profiles)/",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.rstrip("/").strip()


class SteamAdapter:
    """Translates Steam Web API responses into normalized PlatformGameSnapshot items."""

    BASE_URL = "https://api.steampowered.com"

    def __init__(self) -> None:
        self._schema_cache: dict[int, dict[str, dict[str, Any]]] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider="steam",
            display_name="Steam",
            supports_playtime=True,
            supports_achievements=True,
            supports_game_pass=False,
            auth_modes=("api_key+steam_id",),
            supports_telemetry=False,
        )

    async def resolve_steam_id(
        self,
        *,
        raw_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> str:
        cleaned = clean_steam_input(raw_identifier)
        if cleaned.isdigit() and len(cleaned) >= 16:
            return cleaned
        if not cleaned:
            raise PlatformAdapterError(
                code="missing_credentials",
                message="Steam ID is required",
                retryable=False,
            )

        try:
            resp = await client.get(
                f"{self.BASE_URL}/ISteamUser/ResolveVanityURL/v1/",
                params={"key": api_key, "vanityurl": cleaned, "format": "json"},
            )
        except httpx.TimeoutException as exc:
            raise PlatformAdapterError(
                code="timeout",
                message="Timed out contacting Steam ResolveVanityURL",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"Network error resolving Steam vanity URL: {exc}",
                retryable=True,
            ) from exc

        self._check_http_status(resp, "Steam ResolveVanityURL")
        body = self._parse_response_json(resp, "Steam ResolveVanityURL")
        if body.get("success") != 1 or not body.get("steamid"):
            raise PlatformAdapterError(
                code="invalid_identifier",
                message=f"Could not resolve Steam vanity URL '{cleaned}'",
                retryable=False,
            )
        return str(body["steamid"])

    def _parse_retry_after(self, raw_header: str | None) -> int:
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

    def _parse_response_json(self, resp: httpx.Response, context: str) -> dict[str, object]:
        try:
            data = resp.json()
        except Exception as exc:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"{context} returned invalid JSON",
                retryable=True,
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("response"), dict):
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"{context} returned unexpected payload structure",
                retryable=True,
            )
        return data["response"]

    def _check_http_status(self, resp: httpx.Response, context: str) -> None:
        if resp.status_code in (401, 403):
            raise PlatformAdapterError(
                code="auth_failed",
                message=f"{context} rejected configured Steam API key (HTTP {resp.status_code})",
                retryable=False,
            )
        if resp.status_code == 429:
            retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
            raise PlatformAdapterError(
                code="rate_limited",
                message=f"{context} rate limited requests",
                retryable=True,
                throttle_seconds=retry_after,
            )
        if resp.status_code >= 500:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"{context} returned HTTP {resp.status_code}",
                retryable=True,
            )
        if resp.status_code >= 400:
            raise PlatformAdapterError(
                code="upstream_bad_request",
                message=f"{context} returned HTTP {resp.status_code}",
                retryable=False,
            )

    async def fetch_library(
        self,
        *,
        user_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> list[PlatformGameSnapshot]:
        if not api_key or not user_identifier:
            raise PlatformAdapterError(
                code="missing_credentials",
                message="Steam ID and Steam Web API key are both required",
                retryable=False,
            )

        steam_id = await self.resolve_steam_id(
            raw_identifier=user_identifier,
            api_key=api_key,
            client=client,
        )
        try:
            resp = await client.get(
                f"{self.BASE_URL}/IPlayerService/GetOwnedGames/v1/",
                params={
                    "key": api_key,
                    "steamid": steam_id,
                    "include_appinfo": 1,
                    "include_played_free_games": 1,
                    "format": "json",
                },
            )
        except httpx.TimeoutException as exc:
            raise PlatformAdapterError(
                code="timeout",
                message="Timed out fetching owned games from Steam",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"Steam transport failure: {exc}",
                retryable=True,
            ) from exc

        self._check_http_status(resp, "Steam GetOwnedGames")
        body = self._parse_response_json(resp, "Steam GetOwnedGames")
        raw_games = body.get("games") or []
        if not isinstance(raw_games, list):
            raise PlatformAdapterError(
                code="upstream_error",
                message="Steam GetOwnedGames returned non-list games field",
                retryable=True,
            )
        snapshots: list[PlatformGameSnapshot] = []
        for g in raw_games:
            appid = g.get("appid")
            name = (g.get("name") or "").strip()
            if not appid or not name:
                continue
            mins = max(0, int(g.get("playtime_forever") or 0))
            rtime = int(g.get("rtime_last_played") or 0)
            last_played = (
                datetime.fromtimestamp(rtime, tz=timezone.utc).isoformat()
                if rtime > 0
                else None
            )
            snapshots.append(
                PlatformGameSnapshot(
                    external_id=f"steam:{appid}",
                    title=name,
                    platform="PC (Steam)",
                    source="Steam",
                    playtime_minutes=mins,
                    last_played=last_played,
                    cover_url=(
                        f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg"
                    ),
                )
            )
        return snapshots

    async def fetch_game_schema(
        self,
        *,
        appid: int,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> dict[str, dict[str, Any]]:
        if appid in self._schema_cache:
            return self._schema_cache[appid]
        try:
            resp = await client.get(
                f"{self.BASE_URL}/ISteamUserStats/GetSchemaForGame/v2/",
                params={"key": api_key, "appid": str(appid), "l": "english"},
            )
            if resp.status_code == 200:
                data = resp.json()
                schema_achs = (
                    (data.get("game") or {}).get("availableGameStats") or {}
                ).get("achievements") or []
                schema_map = {
                    str(a["name"]): {
                        "displayName": a.get("displayName") or a["name"],
                        "description": a.get("description") or "",
                        "icon": a.get("icon"),
                        "icongray": a.get("icongray"),
                        "hidden": bool(a.get("hidden")),
                    }
                    for a in schema_achs
                    if isinstance(a, dict) and a.get("name")
                }
                self._schema_cache[appid] = schema_map
                return schema_map
        except Exception:
            pass
        return {}

    async def fetch_achievements(
        self,
        *,
        steam_id: str,
        appid: int,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> list[AchievementSnapshot]:
        if not api_key or not steam_id:
            return []
        try:
            resp = await client.get(
                f"{self.BASE_URL}/ISteamUserStats/GetPlayerAchievements/v1/",
                params={"key": api_key, "steamid": steam_id, "appid": str(appid), "l": "english"},
            )
        except httpx.HTTPError:
            return []

        if resp.status_code != 200:
            return []
        try:
            payload = resp.json()
        except Exception:
            return []
        playerstats = payload.get("playerstats") if isinstance(payload, dict) else None
        if not isinstance(playerstats, dict) or not playerstats.get("success"):
            return []
        raw_achs = playerstats.get("achievements") or []
        if not isinstance(raw_achs, list) or not raw_achs:
            return []

        schema_map = await self.fetch_game_schema(appid=appid, api_key=api_key, client=client)
        out: list[AchievementSnapshot] = []
        for a in raw_achs:
            if not isinstance(a, dict):
                continue
            api_name = str(a.get("apiname") or "").strip()
            if not api_name:
                continue
            sch = schema_map.get(api_name, {})
            name = str(a.get("name") or sch.get("displayName") or api_name).strip()
            desc = str(a.get("description") or sch.get("description") or "").strip()
            utime = int(a.get("unlocktime") or 0)
            unlock_time = (
                datetime.fromtimestamp(utime, tz=timezone.utc).isoformat() if utime > 0 else None
            )
            out.append(
                AchievementSnapshot(
                    external_ach_id=api_name,
                    name=name,
                    description=desc,
                    icon_url=sch.get("icon"),
                    icon_gray_url=sch.get("icongray"),
                    unlocked=a.get("achieved") == 1,
                    unlock_time=unlock_time,
                    is_hidden=bool(sch.get("hidden")),
                )
            )
        return out

    async def verify_connection(
        self,
        *,
        credential: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        cleaned = clean_steam_input(credential)
        if not cleaned:
            return {
                "status": "error",
                "platform": "steam",
                "message": "Steam ID or Profile URL cannot be empty",
            }
        if api_key:
            try:
                steam_id = await self.resolve_steam_id(
                    raw_identifier=cleaned,
                    api_key=api_key,
                    client=client,
                )
                r_player = await client.get(
                    f"{self.BASE_URL}/ISteamUser/GetPlayerSummaries/v2/",
                    params={"key": api_key, "steamids": steam_id},
                )
                if r_player.status_code == 200:
                    players = (r_player.json().get("response") or {}).get("players") or []
                    if players:
                        p = players[0]
                        persona_name = str(p.get("personaname") or steam_id)
                        avatar = str(p.get("avatarfull") or p.get("avatarmedium") or "")
                        return {
                            "status": "ok",
                            "platform": "steam",
                            "steam_id": steam_id,
                            "persona_name": persona_name,
                            "avatar_url": avatar,
                            "message": f"Steam account verified successfully as {persona_name}",
                        }
                return {
                    "status": "ok",
                    "platform": "steam",
                    "steam_id": steam_id,
                    "persona_name": steam_id,
                    "avatar_url": "",
                    "message": f"Steam ID verified as {steam_id}",
                }
            except PlatformAdapterError as exc:
                return {"status": "error", "platform": "steam", "message": exc.safe_message}
            except Exception as exc:
                return {"status": "error", "platform": "steam", "message": f"Steam verification error: {exc}"}

        if cleaned.isdigit() and len(cleaned) >= 16:
            return {
                "status": "ok",
                "platform": "steam",
                "steam_id": cleaned,
                "persona_name": cleaned,
                "avatar_url": "",
                "message": "SteamID recorded (live persona lookup requires Steam Web API key)",
            }
        return {
            "status": "error",
            "platform": "steam",
            "message": "Custom profile URLs require a Steam Web API key. Please provide a 17-digit numeric SteamID64.",
        }
