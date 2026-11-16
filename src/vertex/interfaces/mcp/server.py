"""Authenticated Streamable HTTP MCP server exposing all 23 tools and 2 resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from vertex.catalog.taxonomy import GENRES, TAGS, normalize_genre, normalize_tag
from vertex.config import Settings
from vertex.identity.models import UserScope
from vertex.infrastructure.errors import AuthenticationError, AuthorizationError
from vertex.interfaces.http.deps import AppServices

MCP_TOOL_NAMES: tuple[str, ...] = (
    "get_gaming_profile_and_metrics",
    "search_game_library",
    "get_what_to_play_recommendations",
    "update_game_genres",
    "update_game_progress",
    "update_game_ownership",
    "add_game_to_library",
    "find_duplicate_cross_platform_games",
    "reconcile_cross_platform_games",
    "enrich_library_genres",
    "get_game_achievements_detail",
    "respond_to_playtime_prompt",
    "search_game_candidates",
    "add_to_wishlist",
    "update_wishlist_platforms",
    "update_item_price",
    "get_wishlist_deals",
    "mark_as_purchased",
    "get_session_history",
    "get_play_sessions",
    "get_session_analytics",
    "update_session_notes",
    "import_homeassistant_sessions",
)

MCP_RESOURCE_URIS: tuple[str, ...] = (
    "vertex://wishlist/active",
    "vertex://wishlist/{id}/history",
)

DEFAULT_ALLOWED_MCP_ORIGINS: tuple[str, ...] = (
    "https://claude.ai",
    "https://claude.com",
    "https://chatgpt.com",
    "https://chat.openai.com",
)

_PROMPT_TAG_RE = re.compile(
    r"</?(?:system|assistant|user|instructions|tool_call|im_start|im_end)[^>]*>", re.I
)
_CTRL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_MOOD_KEYWORDS: dict[str, set[str]] = {
    "cozy": {"cozy", "casual", "relaxing", "slice of life", "farming", "life sim", "simulation"},
    "action": {"action", "combat", "hack and slash", "shooter", "fps", "fast-paced"},
    "rpg": {"rpg", "role-playing", "jrpg", "crpg", "story rich", "turn-based"},
    "story": {"narrative", "story rich", "adventure", "visual novel", "interactive fiction"},
    "short": {"short", "indie", "puzzle", "arcade", "platformer"},
    "strategy": {"strategy", "tactics", "4x", "turn-based", "grand strategy", "roguelike"},
    "multiplayer": {"multiplayer", "co-op", "online co-op", "pvp", "party"},
}


def _sanitize_text(val: str) -> str:
    cleaned = _PROMPT_TAG_RE.sub("", val)
    cleaned = _CTRL_CHAR_RE.sub(" ", cleaned)
    return cleaned


def _sanitize_output(obj: Any) -> Any:
    """Convert dataclasses/collections into prompt-injection-sanitized plain JSON data."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _sanitize_output(asdict(obj))
    if isinstance(obj, str):
        return _sanitize_text(obj)
    if isinstance(obj, dict):
        return {str(k): _sanitize_output(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_sanitize_output(v) for v in obj]
    return obj


def _json_dumps_safe(obj: Any) -> str:
    return json.dumps(_sanitize_output(obj), indent=2, sort_keys=False)


def _extract_hostname(host_header: str) -> str | None:
    raw = (host_header or "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return None
        return raw[1:end].lower()
    if ":" in raw:
        return raw.rsplit(":", 1)[0].lower()
    return raw.lower()


def get_allowed_mcp_hosts(settings: Settings) -> set[str]:
    hosts = {"localhost", "127.0.0.1", "::1", "testserver", "game-hub", "game-hub-qa"}
    for h in settings.allowed_hosts:
        clean = _extract_hostname(h)
        if clean and clean != "*":
            hosts.add(clean)
    return hosts


def get_allowed_mcp_origins(settings: Settings) -> set[str]:
    origins = set(DEFAULT_ALLOWED_MCP_ORIGINS)
    for host in ("localhost", "127.0.0.1", "[::1]", "testserver", "game-hub", "game-hub-qa"):
        origins.add(f"http://{host}")
        origins.add(f"https://{host}")
    for h in settings.allowed_hosts:
        clean = _extract_hostname(h)
        if clean and clean != "*":
            origins.add(f"http://{clean}")
            origins.add(f"https://{clean}")
    for extra in settings.mcp_allowed_origins:
        clean = extra.strip().rstrip("/").lower()
        if clean:
            origins.add(clean)
    return origins


def _is_origin_allowed(origin_header: str, settings: Settings) -> bool:
    raw = (origin_header or "").strip().rstrip("/").lower()
    if not raw:
        return False
    allowed = get_allowed_mcp_origins(settings)
    if raw in allowed:
        return True
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return False
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if f"{scheme}://{host}" in allowed:
        return True
    if host in {"localhost", "127.0.0.1", "::1", "testserver"} and scheme in {"http", "https"}:
        return True
    if host in get_allowed_mcp_hosts(settings) and scheme in {"http", "https"}:
        return True
    return False


class VertexPatTokenVerifier(TokenVerifier):
    """Validates Bearer Personal Access Tokens against IdentityService on every HTTP request."""

    def __init__(self, services: AppServices) -> None:
        self.services = services

    async def verify_token(self, token: str) -> AccessToken | None:
        raw = (token or "").strip()
        if not raw:
            return None
        try:
            _, pat = self.services.identity.resolve_pat(raw)
        except (AuthenticationError, AuthorizationError):
            return None
        expires_at: int | None = None
        if pat.expires_at:
            try:
                dt = datetime.fromisoformat(pat.expires_at.replace("Z", "+00:00"))
                expires_at = int(dt.timestamp())
            except ValueError:
                expires_at = None
        return AccessToken(
            token=raw,
            client_id=f"pat:{pat.id}",
            scopes=list(pat.scopes),
            expires_at=expires_at,
        )


class McpSecurityMiddleware:
    """Enforces Host (400), Origin (403), trailing-slash normalization, and MCP session owner binding."""

    def __init__(self, app: ASGIApp, services: AppServices) -> None:
        self.app = app
        self.services = services
        # Maps mcp-session-id -> owner user_id to prevent cross-user session hijacking
        self._session_owners: dict[str, str] = {}

    async def _send_json_error(
        self, scope: Scope, receive: Receive, send: Send, status_code: int, detail: str
    ) -> None:
        resp = JSONResponse({"detail": detail}, status_code=status_code)
        await resp(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        # 1. Host header validation (400)
        host_val = headers.get("host", "")
        hostname = _extract_hostname(host_val)
        allowed_hosts = get_allowed_mcp_hosts(self.services.settings)
        if not hostname or hostname not in allowed_hosts:
            await self._send_json_error(scope, receive, send, 400, "Invalid Host header")
            return

        # 2. Origin header validation when present (403)
        origin_val = headers.get("origin")
        if origin_val is not None and not _is_origin_allowed(origin_val, self.services.settings):
            await self._send_json_error(scope, receive, send, 403, "Untrusted Origin header")
            return

        # 3. Cross-user MCP session hijack prevention (if PAT + mcp-session-id both present)
        req_session_id = (headers.get("mcp-session-id") or "").strip()
        auth_header = headers.get("authorization", "")
        req_user_id: str | None = None
        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header[7:].strip()
            if raw_token:
                try:
                    user_scope, _ = self.services.identity.resolve_pat(raw_token)
                    req_user_id = user_scope.user_id
                except (AuthenticationError, AuthorizationError):
                    req_user_id = None

        if req_session_id and req_user_id:
            bound_owner = self._session_owners.get(req_session_id)
            if bound_owner is not None and bound_owner != req_user_id:
                await self._send_json_error(
                    scope,
                    receive,
                    send,
                    403,
                    "Session belongs to a different authenticated user",
                )
                return

        # Normalize /mcp/ -> /mcp without issuing 307 redirect
        path = scope.get("path", "")
        if path == "/mcp/":
            scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and req_user_id:
                for k, v in message.get("headers") or []:
                    if k.decode("latin-1").lower() == "mcp-session-id":
                        sid = v.decode("latin-1").strip()
                        if sid and sid not in self._session_owners:
                            self._session_owners[sid] = req_user_id
            await send(message)

        await self.app(scope, receive, send_wrapper)  # type: ignore[arg-type]


def _resolve_scope(services: AppServices, *, required_scope: str) -> UserScope:
    """Re-verify the active request PAT and required scope against IdentityService."""
    access = get_access_token()
    if access is None or not access.token:
        raise PermissionError("Missing Bearer personal access token")
    try:
        scope, _ = services.identity.resolve_pat(access.token, required_scope=required_scope)
        return scope
    except (AuthenticationError, AuthorizationError) as exc:
        raise PermissionError(str(exc)) from exc


def _resolve_wishlist_item_id(
    services: AppServices,
    scope: UserScope,
    *,
    wishlist_item_id: int | None = None,
    title: str | None = None,
) -> int:
    if wishlist_item_id is not None:
        item_id = int(wishlist_item_id)
        services.wishlist.get_item(scope, item_id)
        return item_id
    if title and title.strip():
        found = services.wishlist.find_item_by_title(scope, title.strip())
        if found is None:
            raise LookupError(f"Wishlist item '{title}' not found")
        return found.id
    raise ValueError("Either wishlist_item_id or title is required")


def build_mcp_server(services: AppServices) -> MCPServer:
    """Construct an authenticated MCPServer wired to all 23 tools and 2 resources."""
    bind = services.settings.mcp_bind_host
    issuer_host = "localhost" if bind in {"0.0.0.0", "::", ""} else bind
    issuer_url = f"http://{issuer_host}:{services.settings.mcp_port}"
    mcp = MCPServer(
        name="Vertex Game Hub",
        instructions=(
            "Personal multi-platform game library, playtime analytics, wishlist price tracking, "
            "and gaming assistant scoped strictly to the authenticated user."
        ),
        token_verifier=VertexPatTokenVerifier(services),
        auth=AuthSettings(
            issuer_url=issuer_url,  # type: ignore[arg-type]
            resource_server_url=issuer_url,  # type: ignore[arg-type]
            required_scopes=["read:library"],
            validate_token_resource=False,
        ),
    )

    # ------------------------------------------------------------------
    # Library & Analytics Tools (1 - 12)
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_gaming_profile_and_metrics() -> str:
        """Return overall gaming profile, platform breakdown, completion metrics, and recent sessions."""
        scope = _resolve_scope(services, required_scope="read:library")
        summary = services.library.get_summary_stats(scope)
        lib_analytics = services.analytics.get_library_analytics(scope)
        sess_analytics = services.analytics.get_session_analytics(scope, days=30)
        return _json_dumps_safe(
            {
                "username": scope.username,
                "summary": summary,
                "library_analytics": lib_analytics,
                "recent_session_analytics_30d": sess_analytics,
            }
        )

    @mcp.tool()
    def search_game_library(
        query: str | None = None,
        platform: str | None = None,
        completion_status: str | None = None,
        genre: str | None = None,
        tag: str | None = None,
        ownership_status: str | None = None,
        favorite_only: bool = False,
        min_playtime_hours: float | None = None,
        max_playtime_hours: float | None = None,
        sort_by: str = "last_played",
        limit: int = 25,
    ) -> str:
        """Search and filter the authenticated user's game library."""
        scope = _resolve_scope(services, required_scope="read:library")
        cap = max(1, min(100, limit))
        games = services.library.list_games(
            scope,
            search=query or "",
            platform=platform or "",
            status=completion_status or "",
            favorite_only=favorite_only,
            sort_by=sort_by,
        )
        genre_l = (genre or "").strip().lower()
        tag_l = (tag or "").strip().lower()
        own_l = (ownership_status or "").strip().lower()

        filtered: list[dict[str, Any]] = []
        for g in games:
            if genre_l and not any(x.lower() == genre_l for x in g.genres):
                continue
            if tag_l and not any(x.lower() == tag_l for x in g.tags):
                continue
            if own_l and g.ownership_status.lower() != own_l:
                continue
            hours = round(g.playtime_minutes / 60.0, 2)
            if min_playtime_hours is not None and hours < min_playtime_hours:
                continue
            if max_playtime_hours is not None and hours > max_playtime_hours:
                continue
            row = asdict(g)
            row["playtime_hours"] = hours
            filtered.append(row)
            if len(filtered) >= cap:
                break
        return _json_dumps_safe({"count": len(filtered), "games": filtered})

    @mcp.tool()
    def get_what_to_play_recommendations(
        mood: str | None = None,
        max_hours: float | None = None,
        platform: str | None = None,
        include_game_pass: bool = True,
        limit: int = 8,
    ) -> str:
        """Recommend games from the user's backlog matching optional mood, time budget, and platform."""
        scope = _resolve_scope(services, required_scope="read:library")
        cap = max(1, min(25, limit))
        all_games = services.library.list_games(
            scope, platform=platform or "", sort_by="last_played"
        )
        backlog_statuses = {"Not Played", "Unplayed", "Plan to Play", "Playing", "On Hold"}
        mood_l = (mood or "").strip().lower()
        mood_tokens = _MOOD_KEYWORDS.get(mood_l, {mood_l} if mood_l else set())

        scored: list[tuple[float, dict[str, Any]]] = []
        for g in all_games:
            if g.completion_status not in backlog_statuses:
                continue
            if not include_game_pass and g.is_game_pass and g.ownership_status != "Owned":
                continue
            hours = round(g.playtime_minutes / 60.0, 2)
            if max_hours is not None and hours > max_hours:
                continue

            score = 0.0
            if g.completion_status == "Playing":
                score += 4.0
            elif g.completion_status in {"Plan to Play", "Not Played", "Unplayed"}:
                score += 2.5
            if g.favorite:
                score += 3.0
            if g.user_rating:
                score += float(g.user_rating) * 0.5
            if g.ownership_status == "Owned":
                score += 1.0

            labels = {x.lower() for x in (*g.genres, *g.tags)}
            haystack = " ".join([*labels, g.notes.lower(), g.title.lower()])
            matched_mood: list[str] = []
            if mood_tokens:
                for tok in mood_tokens:
                    if tok in labels or tok in haystack:
                        score += 5.0
                        matched_mood.append(tok)

            entry = asdict(g)
            entry["playtime_hours"] = hours
            entry["recommendation_score"] = round(score, 2)
            entry["matched_mood_keywords"] = matched_mood
            scored.append((score, entry))

        scored.sort(key=lambda pair: (-pair[0], pair[1]["title"].lower()))
        picks = [item for _, item in scored[:cap]]
        return _json_dumps_safe(
            {
                "mood": mood,
                "platform": platform,
                "max_hours": max_hours,
                "recommendations": picks,
            }
        )

    @mcp.tool()
    def update_game_genres(
        game_id: int,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        mode: str = "merge",
    ) -> str:
        """Update genres/tags on a game using controlled taxonomy (merge mode by default)."""
        scope = _resolve_scope(services, required_scope="write:library")
        gid = int(game_id)
        existing = services.library.get_game(scope, gid)
        mode_clean = (mode or "merge").strip().lower()
        if mode_clean not in {"merge", "replace"}:
            raise ValueError("mode must be 'merge' or 'replace'")

        dropped_genres: list[str] = []
        dropped_tags: list[str] = []

        next_genres: list[str] | None = None
        if genres is not None:
            valid_g: list[str] = []
            allowed_g = set(GENRES)
            for raw_g in genres:
                norm = normalize_genre(raw_g)
                if norm and norm in allowed_g:
                    valid_g.append(norm)
                elif str(raw_g).strip():
                    dropped_genres.append(str(raw_g).strip())
            if mode_clean == "merge":
                combined = list(existing.genres)
                for g in valid_g:
                    if g not in combined:
                        combined.append(g)
                next_genres = combined
            else:
                next_genres = list(dict.fromkeys(valid_g))

        next_tags: list[str] | None = None
        if tags is not None:
            valid_t: list[str] = []
            allowed_t = set(TAGS)
            for raw_t in tags:
                norm = normalize_tag(raw_t)
                if norm and norm in allowed_t:
                    valid_t.append(norm)
                elif str(raw_t).strip():
                    dropped_tags.append(str(raw_t).strip())
            if mode_clean == "merge":
                combined_t = list(existing.tags)
                for t in valid_t:
                    if t not in combined_t:
                        combined_t.append(t)
                next_tags = combined_t
            else:
                next_tags = list(dict.fromkeys(valid_t))

        updated = services.library.update_game(
            scope,
            gid,
            genres=next_genres,
            tags=next_tags,
            replace_labels=(mode_clean == "replace"),
        )
        return _json_dumps_safe(
            {
                "game": updated,
                "mode": mode_clean,
                "ignored_unknown_genres": dropped_genres,
                "ignored_unknown_tags": dropped_tags,
            }
        )

    @mcp.tool()
    def update_game_progress(
        game_id: int,
        completion_status: str | None = None,
        user_rating: int | float | None = None,
        notes: str | None = None,
        favorite: bool | None = None,
        unlock_status: bool = False,
    ) -> str:
        """Update user-owned completion progress, rating, notes, and favorite flag."""
        scope = _resolve_scope(services, required_scope="write:library")
        status_locked = False if unlock_status else None
        rating_int = int(round(user_rating)) if user_rating is not None else None
        updated = services.library.update_game(
            scope,
            int(game_id),
            completion_status=completion_status,
            status_locked=status_locked,
            user_rating=rating_int,
            notes=notes,
            favorite=favorite,
        )
        return _json_dumps_safe({"game": updated})

    @mcp.tool()
    def update_game_ownership(
        game_id: int,
        ownership_status: str,
        is_game_pass: bool | None = None,
    ) -> str:
        """Update a game's ownership status (Owned, Game Pass, Wishlisted, etc.) and Game Pass flag."""
        scope = _resolve_scope(services, required_scope="write:library")
        updated = services.library.update_game(
            scope,
            int(game_id),
            ownership_status=ownership_status,
            is_game_pass=is_game_pass,
        )
        return _json_dumps_safe({"game": updated})

    @mcp.tool()
    def add_game_to_library(
        title: str,
        platform: str,
        completion_status: str = "Plan to Play",
        ownership_status: str = "Owned",
        playtime_hours: float = 0.0,
        user_rating: int | float | None = None,
        notes: str = "",
        favorite: bool = False,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        is_game_pass: bool = False,
    ) -> str:
        """Add a manual game entry to the authenticated user's library."""
        scope = _resolve_scope(services, required_scope="write:library")
        rating_int = int(round(user_rating)) if user_rating is not None else None
        created = services.library.add_manual_game(
            scope,
            title=title,
            platform=platform,
            completion_status=completion_status,
            ownership_status=ownership_status,
            playtime_hours=max(0.0, float(playtime_hours)),
            user_rating=rating_int,
            notes=notes,
            favorite=favorite,
            genres=genres,
            tags=tags,
            is_game_pass=is_game_pass,
        )
        return _json_dumps_safe({"game": created})

    @mcp.tool()
    def find_duplicate_cross_platform_games() -> str:
        """Identify candidate duplicate games owned across multiple platforms for reconciliation."""
        scope = _resolve_scope(services, required_scope="read:library")
        candidates = services.library.get_reconcile_candidates(scope)
        return _json_dumps_safe({"groups": candidates, "count": len(candidates)})

    @mcp.tool()
    def reconcile_cross_platform_games(game_ids: list[int]) -> str:
        """Link/merge cross-platform copies of a game into one canonical reconciliation group."""
        scope = _resolve_scope(services, required_scope="write:library")
        ids = [int(gid) for gid in game_ids]
        canonical_id = services.library.merge_games(scope, ids)
        return _json_dumps_safe({"canonical_id": canonical_id, "merged_game_ids": ids})

    @mcp.tool()
    async def enrich_library_genres(
        limit: int = 50,
        force: bool = False,
    ) -> str:
        """Merge public catalog genres/tags into existing library games without replacing user edits."""
        scope = _resolve_scope(services, required_scope="write:library")
        summary = await services.catalog.enrich_library(
            scope,
            services.library,
            limit=max(1, min(500, limit)),
            force=force,
        )
        return _json_dumps_safe(summary)

    @mcp.tool()
    async def get_game_achievements_detail(
        game_id: int,
        sync_if_empty: bool = False,
    ) -> str:
        """Return unlocked and locked achievement details for a game in the user's library."""
        scope = _resolve_scope(services, required_scope="read:library")
        gid = int(game_id)
        ach = services.library.get_game_achievements(scope, gid)
        if sync_if_empty and not ach.achievements:
            try:
                await services.platform_sync.sync_game_achievements_on_demand(scope, gid)
                ach = services.library.get_game_achievements(scope, gid)
            except Exception:
                pass
        return _json_dumps_safe(ach)

    @mcp.tool()
    def respond_to_playtime_prompt(
        prompt_id: int,
        action: str = "accept",
        completion_status: str | None = None,
        user_rating: int | float | None = None,
        notes: str | None = None,
    ) -> str:
        """Resolve a pending playtime milestone / completion prompt."""
        scope = _resolve_scope(services, required_scope="write:library")
        result = services.activity.resolve_playtime_prompt(
            scope,
            int(prompt_id),
            action=action,
        )
        if any(x is not None for x in (completion_status, user_rating, notes)):
            rating_int = int(round(user_rating)) if user_rating is not None else None
            services.library.update_game(
                scope,
                result.game_id,
                completion_status=completion_status,
                user_rating=rating_int,
                notes=notes,
            )
        return _json_dumps_safe(result)

    # ------------------------------------------------------------------
    # Wishlist & Price Tracking Tools (13 - 18)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def search_game_candidates(
        query: str,
        limit: int = 10,
    ) -> str:
        """Search local catalog and storefront candidates with ownership/Game Pass conflict warnings."""
        scope = _resolve_scope(services, required_scope="read:library")
        result = await services.wishlist_sync.search_candidates(scope, query, limit=limit)
        return _json_dumps_safe(result)

    @mcp.tool()
    async def add_to_wishlist(
        title: str,
        platform_targets: list[dict[str, Any]] | None = None,
        release_year: int | None = None,
        developer: str = "",
        publisher: str = "",
        cover_url: str | None = None,
        genres: list[str] | None = None,
        allow_smart_upgrade: bool = True,
        notes: str = "",
        force_track_owned: bool = False,
        discover_prices: bool = False,
    ) -> str:
        """Add a game to the user's multi-platform price watchlist with ownership guard."""
        scope = _resolve_scope(services, required_scope="write:library")
        targets = platform_targets if platform_targets else [{"platform": "PC"}]
        detail = services.wishlist.add_item(
            scope,
            title=title,
            platform_targets=targets,
            release_year=release_year,
            developer=developer,
            publisher=publisher,
            cover_url=cover_url,
            genres=genres,
            allow_smart_upgrade=allow_smart_upgrade,
            notes=notes,
            added_via="mcp",
            force_track_owned=force_track_owned,
        )
        sync_summary: dict[str, Any] | None = None
        if discover_prices:
            sync_summary = await services.wishlist_sync.sync_wishlist_for_user(
                scope,
                wishlist_item_id=detail.id,
                force=True,
            )
            detail = services.wishlist.get_item(scope, detail.id)
        return _json_dumps_safe({"wishlist_item": detail, "price_sync": sync_summary})

    @mcp.tool()
    def update_wishlist_platforms(
        wishlist_item_id: int | None = None,
        title: str | None = None,
        platform_targets: list[dict[str, Any]] | None = None,
        remove_platforms: list[str] | None = None,
        allow_smart_upgrade: bool | None = None,
        force_track_owned: bool = False,
    ) -> str:
        """Update platform targets, target prices, and smart upgrade settings on a wishlist item."""
        scope = _resolve_scope(services, required_scope="write:library")
        item_id = _resolve_wishlist_item_id(
            services, scope, wishlist_item_id=wishlist_item_id, title=title
        )
        updated = services.wishlist.update_platforms(
            scope,
            item_id,
            platform_targets=platform_targets,
            remove_platforms=remove_platforms,
            allow_smart_upgrade=allow_smart_upgrade,
            force_track_owned=force_track_owned,
        )
        return _json_dumps_safe({"wishlist_item": updated})

    @mcp.tool()
    def update_item_price(
        price_usd: float,
        wishlist_item_id: int | None = None,
        title: str | None = None,
        platform: str = "PC",
        storefront: str = "manual",
        edition_name: str = "Standard Edition",
        edition_tier: str | None = None,
        regular_price_usd: float | None = None,
        store_url: str = "",
        store_sku_id: str | None = None,
        is_drm_free: bool = False,
        is_authorized_seller: bool = True,
    ) -> str:
        """Record a price quote for a wishlisted game and evaluate deal/smart-upgrade alerts."""
        scope = _resolve_scope(services, required_scope="write:library")
        item_id = _resolve_wishlist_item_id(
            services, scope, wishlist_item_id=wishlist_item_id, title=title
        )
        res = services.wishlist.record_manual_price(
            scope,
            item_id,
            price_usd=price_usd,
            platform=platform,
            storefront=storefront,
            edition_name=edition_name,
            edition_tier=edition_tier,
            regular_price_usd=regular_price_usd,
            store_url=store_url,
            store_sku_id=store_sku_id,
            is_drm_free=is_drm_free,
            is_authorized_seller=is_authorized_seller,
            source="mcp",
        )
        return _json_dumps_safe(res)

    @mcp.tool()
    def get_wishlist_deals(
        wishlist_item_id: int | None = None,
        platform: str | None = None,
        smart_upgrades_only: bool = False,
        include_above_target: bool = False,
    ) -> str:
        """Evaluate active wishlist deals, historical lows, and smart-upgrade recommendations."""
        scope = _resolve_scope(services, required_scope="read:library")
        eval_res = services.wishlist.evaluate_deals(
            scope,
            wishlist_item_id=int(wishlist_item_id) if wishlist_item_id is not None else None,
            platform=platform,
            smart_upgrades_only=smart_upgrades_only,
            include_above_target=include_above_target,
        )
        return _json_dumps_safe(eval_res)

    @mcp.tool()
    def mark_as_purchased(
        wishlist_item_id: int | None = None,
        title: str | None = None,
        platform: str = "PC",
        edition_name: str | None = None,
        price_paid_usd: float | None = None,
        completion_status: str = "Plan to Play",
    ) -> str:
        """Mark a wishlist item as purchased and transition it into the user's owned game library."""
        scope = _resolve_scope(services, required_scope="write:library")
        item_id = _resolve_wishlist_item_id(
            services, scope, wishlist_item_id=wishlist_item_id, title=title
        )
        result = services.wishlist.mark_purchased(
            scope,
            item_id,
            platform=platform,
            edition_name=edition_name,
            price_paid_usd=price_paid_usd,
            completion_status=completion_status,
        )
        return _json_dumps_safe(result)

    # ------------------------------------------------------------------
    # Play Sessions & Home Assistant Tools (19 - 23)
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_session_history(
        game_id: int | None = None,
        platform: str | None = None,
        source: str | None = None,
        days: int = 30,
        limit: int = 50,
    ) -> str:
        """Retrieve recent play session history within the requested lookback window."""
        scope = _resolve_scope(services, required_scope="read:library")
        since = (
            (datetime.now(UTC) - timedelta(days=max(1, days)))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        cap = max(1, min(200, limit))
        sessions = services.activity.list_sessions(
            scope,
            game_id=int(game_id) if game_id is not None else None,
            limit=max(cap, 200),
        )
        plat_l = (platform or "").strip().lower()
        src_l = (source or "").strip().lower()
        filtered = [
            s
            for s in sessions
            if (not s.started_at or s.started_at >= since)
            and (not plat_l or s.platform.lower() == plat_l)
            and (not src_l or s.source.lower() == src_l)
        ][:cap]
        return _json_dumps_safe({"days": days, "count": len(filtered), "sessions": filtered})

    @mcp.tool()
    def get_play_sessions(
        game_id: int | None = None,
        platform: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> str:
        """Query detailed play sessions with optional game, platform, and ISO date range filters."""
        scope = _resolve_scope(services, required_scope="read:library")
        cap = max(1, min(200, limit))
        sessions = services.activity.list_sessions(
            scope,
            game_id=int(game_id) if game_id is not None else None,
            limit=max(cap, 200),
        )
        plat_l = (platform or "").strip().lower()
        filtered = []
        for s in sessions:
            if plat_l and s.platform.lower() != plat_l:
                continue
            if start_date and s.started_at and s.started_at < start_date:
                continue
            if end_date and s.started_at and s.started_at > end_date:
                continue
            filtered.append(s)
            if len(filtered) >= cap:
                break
        return _json_dumps_safe({"count": len(filtered), "sessions": filtered})

    @mcp.tool()
    def get_session_analytics(days: int = 30) -> str:
        """Return session streaks, playtime breakdown by platform/source, and daily ledger stats."""
        scope = _resolve_scope(services, required_scope="read:library")
        bounded_days = max(1, min(365, days))
        analytics = services.analytics.get_session_analytics(scope, days=bounded_days)
        ledger = services.activity.get_daily_ledger(scope)
        return _json_dumps_safe(
            {"days": bounded_days, "analytics": analytics, "daily_ledger": ledger[:bounded_days]}
        )

    @mcp.tool()
    def update_session_notes(
        session_id: int,
        notes: str,
        lock_session: bool = True,
    ) -> str:
        """Edit user notes on a play session and lock it by default against sync coalescing."""
        scope = _resolve_scope(services, required_scope="write:library")
        updated = services.activity.update_session(
            scope,
            int(session_id),
            notes=notes,
            is_locked=lock_session,
        )
        return _json_dumps_safe({"session": updated})

    @mcp.tool()
    async def import_homeassistant_sessions(
        csv_content: str | None = None,
        hours: int = 24,
        sync_live: bool = False,
    ) -> str:
        """Import play sessions from a Home Assistant CSV export or live HA history connection."""
        scope = _resolve_scope(services, required_scope="write:library")
        if csv_content and csv_content.strip():
            result = services.homeassistant.import_csv(scope, csv_content=csv_content)
            return _json_dumps_safe(result)
        if sync_live:
            days_back = max(1, min(30, (max(1, hours) + 23) // 24))
            result = await services.homeassistant.sync_history(scope, days_back=days_back)
            return _json_dumps_safe(result)
        raise ValueError("Provide either csv_content or sync_live=True")

    # ------------------------------------------------------------------
    # MCP Resources (2)
    # ------------------------------------------------------------------

    @mcp.resource("vertex://wishlist/active", mime_type="application/json")
    def read_active_wishlist_resource() -> str:
        """Return all active wishlist items, targets, and current price summaries for the user."""
        scope = _resolve_scope(services, required_scope="read:library")
        items = services.wishlist.list_items(scope, status="active")
        return _json_dumps_safe({"status": "active", "count": len(items), "items": items})

    @mcp.resource("vertex://wishlist/{id}/history", mime_type="application/json")
    def read_wishlist_price_history_resource(id: str) -> str:
        """Return historical price snapshots across storefronts/editions for one wishlist item."""
        scope = _resolve_scope(services, required_scope="read:library")
        try:
            item_id = int(str(id).strip())
        except ValueError as exc:
            raise ValueError(f"Wishlist id '{id}' must be an integer") from exc
        history = services.wishlist.get_price_history(scope, item_id, limit=100)
        return _json_dumps_safe(history)

    return mcp


def build_mcp_streamable_app(mcp_server: MCPServer, services: AppServices) -> ASGIApp:
    """Wrap the MCPServer Streamable HTTP ASGI app with Host/Origin/session security."""
    inner = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=False,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return McpSecurityMiddleware(inner, services)


def create_mcp_app(
    settings: Settings | None = None,
    *,
    services: AppServices | None = None,
) -> Starlette:
    """Create a standalone Starlette ASGI app for the dedicated private MCP listener port."""
    from vertex.main import build_app_services

    cfg = settings or Settings.from_env()
    app_services = services or build_app_services(cfg)
    mcp_server = build_mcp_server(app_services)
    secured_mcp_app = build_mcp_streamable_app(mcp_server, app_services)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with mcp_server.session_manager.run():
            yield

    async def healthz(_request):
        return JSONResponse({"status": "ok", "service": "vertex-mcp", "transport": "streamable-http"})

    async def legacy_sse_retired(_request):
        return JSONResponse(
            {
                "detail": "Legacy unauthenticated SSE transport is retired. Use Streamable HTTP at /mcp with Authorization: Bearer <PAT>."
            },
            status_code=410,
        )

    app = Starlette(lifespan=lifespan)
    app.add_route("/healthz", healthz, methods=["GET"])
    app.add_route("/sse", legacy_sse_retired, methods=["GET", "POST"])
    app.add_route("/messages", legacy_sse_retired, methods=["GET", "POST"])
    app.router.routes.append(Mount("/", app=secured_mcp_app))
    return app
