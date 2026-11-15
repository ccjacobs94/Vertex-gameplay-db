"""Steam Web API platform adapter (fixture-testable, never touches personal DBs directly)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from vertex.integrations.platforms.base import (
    AdapterCapabilities,
    PlatformAdapterError,
    PlatformGameSnapshot,
)


class SteamAdapter:
    """Translates Steam Web API responses into normalized PlatformGameSnapshot items."""

    BASE_URL = "https://api.steampowered.com"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider="steam",
            display_name="Steam",
            supports_playtime=True,
            supports_achievements=True,
            supports_game_pass=False,
            auth_modes=("api_key+steam_id",),
        )

    async def resolve_steam_id(
        self,
        *,
        raw_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> str:
        cleaned = (raw_identifier or "").strip().rstrip("/")
        if "/" in cleaned:
            cleaned = cleaned.rsplit("/", 1)[-1].strip()
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
        body = resp.json().get("response", {})
        if body.get("success") != 1 or not body.get("steamid"):
            raise PlatformAdapterError(
                code="invalid_identifier",
                message=f"Could not resolve Steam vanity URL '{cleaned}'",
                retryable=False,
            )
        return str(body["steamid"])

    def _check_http_status(self, resp: httpx.Response, context: str) -> None:
        if resp.status_code in (401, 403):
            raise PlatformAdapterError(
                code="auth_failed",
                message=f"{context} rejected configured Steam API key (HTTP {resp.status_code})",
                retryable=False,
            )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After") or "60")
            raise PlatformAdapterError(
                code="rate_limited",
                message=f"{context} rate limited requests",
                retryable=True,
                throttle_seconds=max(1, min(600, retry_after)),
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
        raw_games = resp.json().get("response", {}).get("games", [])
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
