"""GOG Galaxy platform adapter: library, playtime, achievements, and live presence."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from vertex.catalog.taxonomy import validate_genres
from vertex.integrations.platforms.base import (
    AchievementSnapshot,
    AdapterCapabilities,
    PlatformAdapterError,
    PlatformGameSnapshot,
)

DEFAULT_GOG_CLIENT_ID = "46899977096215655"
DEFAULT_GOG_CLIENT_SECRET = (
    "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
)
GOG_REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"


@dataclass(frozen=True, slots=True)
class GogTokenBundle:
    access_token: str
    refresh_token: str | None = None
    user_id: str | None = None
    username: str | None = None


@dataclass(frozen=True, slots=True)
class GogLibraryResult:
    snapshots: list[PlatformGameSnapshot]
    tokens: GogTokenBundle | None = None


@dataclass(frozen=True, slots=True)
class GogActivePresence:
    game_id: str
    game_title: str | None = None
    rich_presence: str | None = None
    status: str = "playing"


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


def _parse_gog_timestamp(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        if raw <= 0:
            return None
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        ts = int(text)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts > 0 else None
    try:
        # Handle +0000 offset format returned by gameplay.gog.com
        clean = text.replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", clean):
            clean = f"{clean[:-2]}:{clean[-2:]}"
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _normalize_gog_cover(raw_image: Any) -> str | None:
    if not raw_image:
        return None
    s = str(raw_image).strip()
    if not s:
        return None
    if s.startswith("//"):
        s = f"https:{s}"
    if not s.startswith(("http://", "https://")):
        return None
    if not re.search(r"\.(jpg|jpeg|png|webp)(?:\?.*)?$", s, re.IGNORECASE):
        s = f"{s}_glx_vertical_cover.jpg"
    return s


def extract_gog_auth_code(raw: str) -> str | None:
    text = (raw or "").strip()
    if "code=" in text and text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        codes = parse_qs(parsed.query).get("code")
        if codes and codes[0].strip():
            return codes[0].strip()
    return None


class GOGAdapter:
    """Translates GOG Galaxy OAuth, library, playtime, achievement, and presence APIs."""

    AUTH_URL = "https://auth.gog.com"
    EMBED_URL = "https://embed.gog.com"
    API_URL = "https://api.gog.com"
    GAMEPLAY_URL = "https://gameplay.gog.com"
    PRESENCE_URL = "https://presence.gog.com"
    WEB_URL = "https://www.gog.com"

    def __init__(
        self,
        *,
        client_id: str = DEFAULT_GOG_CLIENT_ID,
        client_secret: str = DEFAULT_GOG_CLIENT_SECRET,
    ) -> None:
        self.client_id = client_id or DEFAULT_GOG_CLIENT_ID
        self.client_secret = client_secret or DEFAULT_GOG_CLIENT_SECRET

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider="gog",
            display_name="GOG Galaxy",
            supports_playtime=True,
            supports_achievements=True,
            supports_game_pass=False,
            auth_modes=("oauth_refresh_token", "oauth_code", "public_username"),
            supports_telemetry=True,
        )

    def _check_status(self, resp: httpx.Response, context: str, *, allow_404: bool = False) -> None:
        if allow_404 and resp.status_code == 404:
            return
        if resp.status_code in (401, 403):
            raise PlatformAdapterError(
                code="auth_failed",
                message=f"{context} rejected GOG credentials (HTTP {resp.status_code})",
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

    async def exchange_auth_code(
        self,
        *,
        code: str,
        client: httpx.AsyncClient,
    ) -> GogTokenBundle:
        try:
            resp = await client.get(
                f"{self.AUTH_URL}/token",
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": GOG_REDIRECT_URI,
                },
            )
        except httpx.TimeoutException as exc:
            raise PlatformAdapterError(
                code="timeout",
                message="Timed out exchanging GOG authorization code",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"Network error contacting GOG OAuth: {exc}",
                retryable=True,
            ) from exc

        if resp.status_code in (400, 401, 403):
            raise PlatformAdapterError(
                code="auth_failed",
                message=f"GOG rejected authorization code (HTTP {resp.status_code})",
                retryable=False,
            )
        self._check_status(resp, "GOG OAuth token exchange")
        data = resp.json()
        acc = str(data.get("access_token") or "").strip()
        if not acc:
            raise PlatformAdapterError(
                code="auth_failed",
                message="GOG OAuth did not return an access_token",
                retryable=False,
            )
        return GogTokenBundle(
            access_token=acc,
            refresh_token=str(data.get("refresh_token") or "").strip() or None,
            user_id=str(data.get("user_id") or "").strip() or None,
        )

    async def refresh_access_token(
        self,
        *,
        refresh_token: str,
        client: httpx.AsyncClient,
    ) -> GogTokenBundle:
        try:
            resp = await client.get(
                f"{self.AUTH_URL}/token",
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        except httpx.TimeoutException as exc:
            raise PlatformAdapterError(
                code="timeout",
                message="Timed out refreshing GOG OAuth token",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"Network error refreshing GOG OAuth token: {exc}",
                retryable=True,
            ) from exc

        if resp.status_code in (400, 401, 403):
            raise PlatformAdapterError(
                code="auth_failed",
                message=f"GOG refresh token expired or invalid (HTTP {resp.status_code})",
                retryable=False,
            )
        self._check_status(resp, "GOG OAuth refresh")
        data = resp.json()
        acc = str(data.get("access_token") or "").strip()
        if not acc:
            raise PlatformAdapterError(
                code="auth_failed",
                message="GOG token refresh returned empty access_token",
                retryable=False,
            )
        return GogTokenBundle(
            access_token=acc,
            refresh_token=str(data.get("refresh_token") or refresh_token).strip() or refresh_token,
            user_id=str(data.get("user_id") or "").strip() or None,
        )

    async def ensure_token_bundle(
        self,
        *,
        access_token: str | None,
        refresh_token: str | None,
        user_id: str | None,
        username: str | None,
        client: httpx.AsyncClient,
    ) -> GogTokenBundle | None:
        acc = (access_token or "").strip()
        ref = (refresh_token or "").strip()
        uid = (user_id or "").strip() or None
        uname = (username or "").strip() or None
        if not acc and ref:
            code = extract_gog_auth_code(ref)
            if code:
                bundle = await self.exchange_auth_code(code=code, client=client)
            else:
                bundle = await self.refresh_access_token(refresh_token=ref, client=client)
            acc = bundle.access_token
            ref = bundle.refresh_token or ref
            uid = bundle.user_id or uid
        if not acc:
            return None

        if not uid or not uname:
            ident_uid, ident_uname, maybe_bundle = await self.resolve_user_identity(
                access_token=acc,
                refresh_token=ref or None,
                client=client,
            )
            if maybe_bundle is not None:
                acc = maybe_bundle.access_token
                ref = maybe_bundle.refresh_token or ref
                uid = maybe_bundle.user_id or uid
            uid = uid or ident_uid
            uname = uname or ident_uname

        return GogTokenBundle(
            access_token=acc,
            refresh_token=ref or None,
            user_id=uid,
            username=uname,
        )

    async def _authed_get(
        self,
        url: str,
        *,
        bundle: GogTokenBundle,
        client: httpx.AsyncClient,
        params: dict[str, Any] | None = None,
        context: str = "GOG API",
        allow_404: bool = False,
    ) -> tuple[httpx.Response, GogTokenBundle]:
        headers = {"Authorization": f"Bearer {bundle.access_token}", "Accept": "application/json"}
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise PlatformAdapterError(
                code="timeout",
                message=f"Timed out contacting {context}",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformAdapterError(
                code="upstream_error",
                message=f"Network error contacting {context}: {exc}",
                retryable=True,
            ) from exc

        if resp.status_code == 401 and bundle.refresh_token:
            refreshed = await self.refresh_access_token(
                refresh_token=bundle.refresh_token,
                client=client,
            )
            new_bundle = GogTokenBundle(
                access_token=refreshed.access_token,
                refresh_token=refreshed.refresh_token or bundle.refresh_token,
                user_id=refreshed.user_id or bundle.user_id,
                username=bundle.username,
            )
            headers["Authorization"] = f"Bearer {new_bundle.access_token}"
            try:
                resp = await client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise PlatformAdapterError(
                    code="upstream_error",
                    message=f"Network error retrying {context}: {exc}",
                    retryable=True,
                ) from exc
            self._check_status(resp, context, allow_404=allow_404)
            return resp, new_bundle

        self._check_status(resp, context, allow_404=allow_404)
        return resp, bundle

    async def resolve_user_identity(
        self,
        *,
        access_token: str,
        refresh_token: str | None = None,
        client: httpx.AsyncClient,
    ) -> tuple[str | None, str | None, GogTokenBundle | None]:
        bundle = GogTokenBundle(access_token=access_token, refresh_token=refresh_token)
        try:
            resp, updated = await self._authed_get(
                f"{self.EMBED_URL}/userData.json",
                bundle=bundle,
                client=client,
                context="GOG userData",
                allow_404=True,
            )
        except PlatformAdapterError:
            return None, None, None
        if resp.status_code != 200:
            return None, None, updated
        try:
            data = resp.json()
        except Exception:
            return None, None, updated
        if not isinstance(data, dict):
            return None, None, updated
        uid = str(data.get("userId") or data.get("user_id") or "").strip() or None
        uname = str(data.get("username") or data.get("name") or "").strip() or None
        return uid, uname, updated

    async def fetch_public_profile_stats(
        self,
        *,
        username: str,
        user_id: str | None,
        client: httpx.AsyncClient,
        bundle: GogTokenBundle | None = None,
    ) -> tuple[dict[str, dict[str, Any]], GogTokenBundle | None]:
        """Fetch bulk playtime/last_session stats from GOG profile stats endpoint if available."""
        clean_user = (username or "").strip()
        if not clean_user:
            return {}, bundle

        stats_by_pid: dict[str, dict[str, Any]] = {}
        page = 1
        max_pages = 25
        while page <= max_pages:
            url = f"{self.WEB_URL}/u/{clean_user}/games/stats"
            params = {"sort": "total_playtime", "order": "desc", "page": str(page)}
            try:
                if bundle is not None:
                    resp, bundle = await self._authed_get(
                        url,
                        bundle=bundle,
                        client=client,
                        params=params,
                        context="GOG profile stats",
                        allow_404=True,
                    )
                else:
                    resp = await client.get(url, params=params)
            except Exception:
                break

            if resp.status_code != 200:
                break
            try:
                data = resp.json()
            except Exception:
                break
            if not isinstance(data, dict):
                break
            embedded = data.get("_embedded") if isinstance(data.get("_embedded"), dict) else {}
            items = embedded.get("items") if isinstance(embedded.get("items"), list) else []
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                game_obj = item.get("game") if isinstance(item.get("game"), dict) else item
                pid = str(game_obj.get("id") or item.get("id") or "").strip()
                if not pid:
                    continue
                raw_stats = item.get("stats")
                user_stat: dict[str, Any] = {}
                if isinstance(raw_stats, dict):
                    if user_id and isinstance(raw_stats.get(user_id), dict):
                        user_stat = raw_stats[user_id]
                    else:
                        first_dict = next(
                            (v for v in raw_stats.values() if isinstance(v, dict)),
                            {},
                        )
                        user_stat = first_dict
                playtime = user_stat.get("playtime")
                if playtime is None:
                    playtime = item.get("playtime") or item.get("time_sum") or 0
                last_sess = _parse_gog_timestamp(
                    user_stat.get("lastSessionDate")
                    or item.get("lastSessionDate")
                    or item.get("last_session_date")
                )
                stats_by_pid[pid] = {
                    "title": str(game_obj.get("title") or "").strip(),
                    "image": game_obj.get("image"),
                    "playtime_minutes": max(0, int(round(float(playtime or 0)))),
                    "last_played": last_sess,
                }

            total_pages = int(data.get("pages") or data.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1
        return stats_by_pid, bundle

    async def fetch_game_sessions(
        self,
        *,
        bundle: GogTokenBundle,
        product_id: str,
        client: httpx.AsyncClient,
    ) -> tuple[int, str | None, GogTokenBundle]:
        """Fetch cumulative time_sum (minutes) & last_session_date for one GOG game."""
        uid = (bundle.user_id or "").strip()
        if not uid or not product_id:
            return 0, None, bundle

        resp, updated_bundle = await self._authed_get(
            f"{self.GAMEPLAY_URL}/games/{product_id}/users/{uid}/sessions",
            bundle=bundle,
            client=client,
            context="GOG game sessions",
            allow_404=True,
        )
        if resp.status_code != 200:
            return 0, None, updated_bundle
        try:
            data = resp.json()
        except Exception:
            return 0, None, updated_bundle
        if not isinstance(data, dict):
            return 0, None, updated_bundle
        time_sum = max(0, int(round(float(data.get("time_sum") or 0))))
        last_sess = _parse_gog_timestamp(data.get("last_session_date"))
        if not last_sess and isinstance(data.get("sessions"), list) and data["sessions"]:
            latest_ts = max(
                (_parse_gog_timestamp(s.get("session_date")) or "" for s in data["sessions"] if isinstance(s, dict)),
                default="",
            )
            last_sess = latest_ts or None
        return time_sum, last_sess, updated_bundle

    async def fetch_library_detailed(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        user_id: str | None = None,
        username: str | None = None,
        client: httpx.AsyncClient,
    ) -> GogLibraryResult:
        bundle = await self.ensure_token_bundle(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user_id,
            username=username,
            client=client,
        )
        clean_uname = (username or "").strip() or (bundle.username if bundle else None)

        if bundle is None and not clean_uname:
            raise PlatformAdapterError(
                code="missing_credentials",
                message="GOG OAuth token/code or public GOG username is required",
                retryable=False,
            )

        products: list[dict[str, Any]] = []
        if bundle is not None:
            page = 1
            while page <= 50:
                resp, bundle = await self._authed_get(
                    f"{self.EMBED_URL}/account/getFilteredProducts",
                    bundle=bundle,
                    client=client,
                    params={"mediaType": "1", "page": str(page)},
                    context="GOG library",
                    allow_404=True,
                )
                if resp.status_code != 200:
                    break
                try:
                    data = resp.json()
                except Exception as exc:
                    raise PlatformAdapterError(
                        code="upstream_error",
                        message="GOG getFilteredProducts returned invalid JSON",
                        retryable=True,
                    ) from exc
                page_items = data.get("products") or []
                if not isinstance(page_items, list) or not page_items:
                    break
                products.extend(p for p in page_items if isinstance(p, dict))
                total_pages = int(data.get("totalPages") or 1)
                if page >= total_pages:
                    break
                page += 1

            # Fallback to /user/data/games + /products?ids=... if getFilteredProducts returned empty
            if not products:
                owned_resp, bundle = await self._authed_get(
                    f"{self.EMBED_URL}/user/data/games",
                    bundle=bundle,
                    client=client,
                    context="GOG owned game IDs",
                    allow_404=True,
                )
                if owned_resp.status_code == 200:
                    try:
                        owned_ids = [
                            str(x)
                            for x in (owned_resp.json().get("owned") or [])
                            if x is not None
                        ]
                    except Exception:
                        owned_ids = []
                    for i in range(0, len(owned_ids), 50):
                        chunk = owned_ids[i : i + 50]
                        prod_resp = await client.get(
                            f"{self.API_URL}/products",
                            params={"ids": ",".join(chunk)},
                        )
                        if prod_resp.status_code == 200:
                            try:
                                plist = prod_resp.json()
                                if isinstance(plist, list):
                                    products.extend(p for p in plist if isinstance(p, dict))
                            except Exception:
                                pass

        eff_uid = (bundle.user_id if bundle else None) or (user_id or "").strip() or None
        stats_map, bundle = await self.fetch_public_profile_stats(
            username=clean_uname or "",
            user_id=eff_uid,
            client=client,
            bundle=bundle,
        )

        # If unauthenticated username-only sync was requested and profile stats succeeded, synthesize products
        if not products and stats_map:
            for pid, st in stats_map.items():
                if st.get("title"):
                    products.append(
                        {
                            "id": pid,
                            "title": st["title"],
                            "image": st.get("image"),
                            "isGame": True,
                        }
                    )

        if bundle is None and not products:
            raise PlatformAdapterError(
                code="auth_failed",
                message="Could not fetch GOG library (profile may be private; configure GOG OAuth token)",
                retryable=False,
            )

        # For authenticated accounts, query per-game sessions for any products missing stats_map playtime
        sem = asyncio.Semaphore(6)
        session_playtime: dict[str, tuple[int, str | None]] = {}

        if bundle is not None and bundle.user_id:
            missing_pids = [
                str(p["id"])
                for p in products
                if p.get("id") is not None and str(p["id"]) not in stats_map
            ]

            async def _pull_sess(pid: str) -> None:
                async with sem:
                    assert bundle is not None
                    mins, lp, _ = await self.fetch_game_sessions(
                        bundle=bundle,
                        product_id=pid,
                        client=client,
                    )
                    session_playtime[pid] = (mins, lp)

            if missing_pids:
                await asyncio.gather(*[_pull_sess(pid) for pid in missing_pids], return_exceptions=True)

        snapshots: list[PlatformGameSnapshot] = []
        seen_pids: set[str] = set()
        for p in products:
            pid = str(p.get("id") or "").strip()
            title = str(p.get("title") or "").strip()
            if not pid or not title or pid in seen_pids:
                continue
            if p.get("isGame") is False:
                continue
            seen_pids.add(pid)

            st_entry = stats_map.get(pid)
            if st_entry is not None:
                mins = int(st_entry["playtime_minutes"])
                last_played = st_entry["last_played"]
            elif pid in session_playtime:
                mins, last_played = session_playtime[pid]
            else:
                mins = max(0, int(p.get("playtime") or 0))
                last_played = _parse_gog_timestamp(p.get("lastSessionDate"))

            raw_genres: list[str] = []
            if isinstance(p.get("category"), str) and p["category"].strip():
                raw_genres.append(p["category"].strip())
            if isinstance(p.get("genres"), list):
                raw_genres.extend(str(g) for g in p["genres"] if g)
            clean_genres = tuple(validate_genres(raw_genres))

            images_obj = p.get("images") if isinstance(p.get("images"), dict) else {}
            cover_raw = (
                p.get("image")
                or images_obj.get("logo2x")
                or images_obj.get("background")
                or images_obj.get("icon")
            )

            snapshots.append(
                PlatformGameSnapshot(
                    external_id=f"gog:{pid}",
                    title=title,
                    platform="PC (GOG)",
                    source="GOG",
                    playtime_minutes=mins,
                    last_played=last_played,
                    cover_url=_normalize_gog_cover(cover_raw),
                    genres=clean_genres,
                )
            )

        return GogLibraryResult(snapshots=snapshots, tokens=bundle)

    async def fetch_library(
        self,
        *,
        user_identifier: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> list[PlatformGameSnapshot]:
        res = await self.fetch_library_detailed(
            refresh_token=api_key or None,
            username=user_identifier or None,
            client=client,
        )
        return res.snapshots

    async def fetch_achievements(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        user_id: str | None = None,
        product_id: str,
        client: httpx.AsyncClient,
    ) -> tuple[list[AchievementSnapshot], GogTokenBundle | None]:
        bundle = await self.ensure_token_bundle(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user_id,
            username=None,
            client=client,
        )
        if bundle is None or not bundle.user_id:
            return [], bundle

        items: list[dict[str, Any]] = []
        page_token: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": "1000"}
            if page_token:
                params["page_token"] = page_token
            resp, bundle = await self._authed_get(
                f"{self.GAMEPLAY_URL}/clients/{product_id}/users/{bundle.user_id}/achievements",
                bundle=bundle,
                client=client,
                params=params,
                context="GOG achievements",
                allow_404=True,
            )
            if resp.status_code != 200:
                break
            try:
                data = resp.json()
            except Exception:
                break
            if not isinstance(data, dict):
                break
            page_items = data.get("items") or []
            if not isinstance(page_items, list) or not page_items:
                break
            items.extend(it for it in page_items if isinstance(it, dict))
            next_tok = str(data.get("next_page_token") or "").strip()
            if not next_tok or next_tok == page_token:
                break
            page_token = next_tok

        out: list[AchievementSnapshot] = []
        for it in items:
            ach_id = str(it.get("achievement_id") or it.get("achievement_key") or "").strip()
            if not ach_id:
                continue
            name = str(it.get("name") or it.get("achievement_key") or ach_id).strip()
            desc = str(it.get("description") or "").strip()
            unlock_time = _parse_gog_timestamp(it.get("date_unlocked"))
            unlocked = unlock_time is not None
            rarity: float | None = None
            if it.get("rarity") is not None:
                try:
                    rarity = float(it["rarity"])
                except (ValueError, TypeError):
                    pass
            rarity_desc = str(it.get("rarity_level_description") or "").strip() or None
            rarity_slug = str(it.get("rarity_level_slug") or "").strip() or None
            out.append(
                AchievementSnapshot(
                    external_ach_id=ach_id,
                    name=name,
                    description=desc,
                    icon_url=it.get("image_url_unlocked"),
                    icon_gray_url=it.get("image_url_locked"),
                    unlocked=unlocked,
                    unlock_time=unlock_time,
                    reward_type=rarity_desc,
                    reward_value=rarity_slug,
                    rarity_pct=rarity,
                    is_hidden=not bool(it.get("visible", True)),
                )
            )
        return out, bundle

    async def fetch_presence(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        user_id: str | None = None,
        client: httpx.AsyncClient,
    ) -> tuple[GogActivePresence | None, GogTokenBundle | None]:
        bundle = await self.ensure_token_bundle(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user_id,
            username=None,
            client=client,
        )
        if bundle is None or not bundle.user_id:
            return None, bundle

        resp, bundle = await self._authed_get(
            f"{self.PRESENCE_URL}/statuses",
            bundle=bundle,
            client=client,
            params={"user_id": bundle.user_id},
            context="GOG presence",
            allow_404=True,
        )
        if resp.status_code != 200:
            return None, bundle
        try:
            data = resp.json()
        except Exception:
            return None, bundle
        return self.extract_active_presence(data, target_user_id=bundle.user_id), bundle

    def extract_active_presence(
        self, payload: Any, *, target_user_id: str | None = None
    ) -> GogActivePresence | None:
        if not isinstance(payload, dict):
            return None
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items and ("game_id" in payload or "presence" in payload):
            items = [payload]

        for entry in items:
            if not isinstance(entry, dict):
                continue
            uid = str(entry.get("user_id") or "").strip()
            if target_user_id and uid and uid != target_user_id:
                continue
            presence = str(entry.get("presence") or "").strip().lower()
            status = str(entry.get("status") or "").strip().lower()
            if presence == "offline" or status == "offline":
                continue
            gid = str(entry.get("game_id") or "").strip()
            if not gid or gid.lower() in ("0", "none", "null"):
                continue
            gtitle = str(entry.get("game_title") or entry.get("title") or "").strip() or None
            rich = str(entry.get("rich_presence") or "").strip() or None
            return GogActivePresence(
                game_id=gid,
                game_title=gtitle,
                rich_presence=rich,
                status=status or "playing",
            )
        return None

    async def fetch_product_title(
        self,
        *,
        product_id: str,
        client: httpx.AsyncClient,
    ) -> str | None:
        try:
            resp = await client.get(f"{self.API_URL}/products/{product_id}")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("title"):
                    return str(data["title"]).strip()
        except Exception:
            pass
        return None

    async def verify_connection(
        self,
        *,
        credential: str,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        cleaned = (credential or "").strip()
        if not cleaned:
            return {
                "status": "error",
                "platform": "gog",
                "message": "GOG OAuth token, authorization code, or username cannot be empty",
            }
        try:
            if cleaned.startswith("{"):
                parsed = json.loads(cleaned)
                bundle = await self.ensure_token_bundle(
                    access_token=parsed.get("access_token"),
                    refresh_token=parsed.get("refresh_token"),
                    user_id=parsed.get("user_id"),
                    username=parsed.get("username"),
                    client=client,
                )
                if bundle:
                    return {
                        "status": "ok",
                        "platform": "gog",
                        "user_id": bundle.user_id,
                        "username": bundle.username or bundle.user_id,
                        "message": f"GOG account verified as {bundle.username or bundle.user_id}",
                    }
            code = extract_gog_auth_code(cleaned)
            if code:
                bundle = await self.exchange_auth_code(code=code, client=client)
                uid, uname, _ = await self.resolve_user_identity(
                    access_token=bundle.access_token,
                    refresh_token=bundle.refresh_token,
                    client=client,
                )
                return {
                    "status": "ok",
                    "platform": "gog",
                    "user_id": uid or bundle.user_id,
                    "username": uname or uid or bundle.user_id,
                    "message": f"GOG OAuth authorization code verified as {uname or uid or bundle.user_id}",
                }

            # Try as refresh_token first; if rejected and looks like a username, check public profile
            if len(cleaned) > 32 or "." in cleaned or "_" in cleaned and len(cleaned) > 20:
                try:
                    bundle = await self.refresh_access_token(refresh_token=cleaned, client=client)
                    uid, uname, _ = await self.resolve_user_identity(
                        access_token=bundle.access_token,
                        refresh_token=bundle.refresh_token,
                        client=client,
                    )
                    return {
                        "status": "ok",
                        "platform": "gog",
                        "user_id": uid or bundle.user_id,
                        "username": uname or uid or bundle.user_id,
                        "message": f"GOG Galaxy token verified as {uname or uid or bundle.user_id}",
                    }
                except PlatformAdapterError as exc:
                    if not re.match(r"^[a-zA-Z0-9_.-]{2,32}$", cleaned):
                        return {"status": "error", "platform": "gog", "message": exc.safe_message}

            # Try verifying as access token via userData.json
            uid, uname, _ = await self.resolve_user_identity(access_token=cleaned, client=client)
            if uid or uname:
                return {
                    "status": "ok",
                    "platform": "gog",
                    "user_id": uid,
                    "username": uname or uid,
                    "message": f"GOG session verified as {uname or uid}",
                }

            # Public profile fallback
            stats, _ = await self.fetch_public_profile_stats(
                username=cleaned,
                user_id=None,
                client=client,
            )
            if stats:
                return {
                    "status": "ok",
                    "platform": "gog",
                    "username": cleaned,
                    "message": f"GOG public profile verified ({len(stats)} game stats visible)",
                }
            return {
                "status": "error",
                "platform": "gog",
                "message": "Could not verify GOG token or public profile",
            }
        except PlatformAdapterError as exc:
            return {"status": "error", "platform": "gog", "message": exc.safe_message}
        except Exception as exc:
            return {"status": "error", "platform": "gog", "message": f"GOG verification failed: {exc}"}
