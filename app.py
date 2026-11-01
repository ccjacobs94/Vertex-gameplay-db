"""
FastAPI Web Server + Background Auto-Updater for Game Hub.
Accessible over Local Network (LAN) and Tailscale.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware

import mcp_server
import system_db
import db
from context import get_current_user_id, reset_current_user_id, set_current_user_id
from web_auth import (
    SESSION_COOKIE_NAME,
    _is_direct_local_or_tailscale_request,
    get_auth_username,
    handle_login_get,
    handle_login_post,
    handle_logout,
    is_local_bypass_enabled,
    is_public_path,
    is_request_authenticated,
    is_web_auth_enabled,
    verify_web_session,
)
from db import (
    DEFAULT_DB_PATH,
    USERS_DB_DIR,
    add_manual_game,
    delete_game,
    dismiss_reconcile_group,
    get_game_achievements,
    get_game_by_id,
    get_habits_analytics,
    get_library_stats,
    get_pending_playtime_prompts,
    get_reconcile_candidates,
    init_db,
    query_games,
    reconcile_games,
    reset_reconcile_dismissals,
    resolve_playtime_prompt,
    unlink_game,
    update_game,
)
from sync_engine import (
    enrich_missing_metadata,
    search_external_metadata,
    sync_all_platforms,
    sync_game_achievements_on_demand,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gamehub.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
SYNC_INTERVAL_HOURS = float(os.environ.get("SYNC_INTERVAL_HOURS", "6"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8096"))

_user_sync_locks: Dict[int, asyncio.Lock] = {}
_sync_semaphore = asyncio.Semaphore(5)


def _get_user_sync_lock(user_id: Any = None) -> asyncio.Lock:
    raw_uid = user_id if user_id is not None else get_current_user_id()
    if raw_uid is None or raw_uid == "":
        key: Any = 0
    elif isinstance(raw_uid, int):
        key = raw_uid
    elif isinstance(raw_uid, str) and raw_uid.isdigit():
        key = int(raw_uid)
    else:
        key = raw_uid
    lock = _user_sync_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _user_sync_locks[key] = lock
    return lock


async def _periodic_sync_loop() -> None:
    """Background loop that automatically syncs Steam, Xbox, and PSN every N hours."""
    await asyncio.sleep(3)
    while True:
        try:
            lock = _get_user_sync_lock()
            async with lock:
                async with _sync_semaphore:
                    logger.info("Running scheduled multi-platform library sync...")
                    results = await sync_all_platforms()
                    logger.info("Scheduled sync finished: %s", results)
        except Exception as exc:
            logger.error("Error in scheduled sync loop: %s", exc)
        await asyncio.sleep(max(SYNC_INTERVAL_HOURS, 0.5) * 3600)


async def _run_mcp_http_server(host: str, port: int) -> None:
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from mcp.server.transport_security import TransportSecuritySettings

    # Disable local DNS rebinding protection since Cloudflare Tunnel terminates domain traffic
    sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    # Stateless: each request runs in its own context so current_user_id (set per bearer token)
    # can never leak across tenants through a long-lived shared MCP session.
    mcp_app = mcp_server.mcp.streamable_http_app(
        host=host, streamable_http_path="/mcp", transport_security=sec, stateless_http=True
    )
    sse_app = mcp_server.mcp.sse_app(transport_security=sec)
    for route in sse_app.routes:
        mcp_app.routes.append(route)

    from oauth_provider import (
        _get_base_domain,
        extract_bearer_token,
        get_oauth_routes,
        verify_bearer_token,
    )

    _initialized_mcp_users: set = set()
    _REDACTED_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}

    class RequestLoggerAndAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            safe_headers = {
                k: ("<redacted>" if k.lower() in _REDACTED_HEADERS else v)
                for k, v in request.headers.items()
            }
            path = request.url.path
            if path.startswith("/oauth/"):
                logger.info("--> MCP OAUTH REQ: %s %s | Headers: %s", request.method, path, safe_headers)
            else:
                body = await request.body()
                logger.info(
                    "--> MCP REQ: %s %s | Headers: %s | Body: %s",
                    request.method,
                    path,
                    safe_headers,
                    body.decode("utf-8", errors="ignore")[:300],
                )

            # Enforce per-user Bearer token authentication on MCP transport endpoints
            require_auth = os.environ.get("MCP_REQUIRE_AUTH", "true").lower() not in ("0", "false", "no")
            is_sse_root = path == "/" and "text/event-stream" in request.headers.get("accept", "")
            is_protected_mcp_path = path.startswith("/mcp") or path.startswith("/sse") or path.startswith("/messages") or is_sse_root

            user_ctx_token = None
            if request.method != "OPTIONS" and require_auth and is_protected_mcp_path:
                token = extract_bearer_token(request)
                user_id = verify_bearer_token(token)
                if not user_id:
                    base_domain = _get_base_domain()
                    logger.warning("<-- MCP AUTH REJECTED (401): %s %s", request.method, path)
                    return JSONResponse(
                        {"error": "unauthorized", "error_description": "Valid Bearer token required"},
                        status_code=401,
                        headers={
                            "WWW-Authenticate": f'Bearer resource_metadata="{base_domain}/.well-known/oauth-protected-resource"',
                            "Access-Control-Allow-Origin": "*",
                        },
                    )
                # Scope every DB query made by MCP tools to this user's games.db
                user_ctx_token = set_current_user_id(user_id)
                if user_id not in _initialized_mcp_users:
                    init_db()  # create/migrate this user's games.db on first use
                    _initialized_mcp_users.add(user_id)

            try:
                response = await call_next(request)
            finally:
                if user_ctx_token is not None:
                    reset_current_user_id(user_ctx_token)
            logger.info("<-- MCP RESP: %s %s -> %s", request.method, path, response.status_code)
            return response

    mcp_app.add_middleware(RequestLoggerAndAuthMiddleware)
    mcp_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def root_handler(request):
        # If client connects to root with SSE headers, delegate to SSE handler
        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            for r in sse_app.routes:
                if getattr(r, "path", "") == "/sse":
                    return await r.endpoint(request)
        return JSONResponse({
            "name": "GameHub-Library-Assistant",
            "status": "online",
            "mcp_streamable_http": "/mcp",
            "mcp_sse": "/sse",
            "capabilities": ["tools", "resources", "prompts"]
        })

    for oauth_route in get_oauth_routes():
        mcp_app.routes.insert(0, oauth_route)

    # Sign-in form and submission for /oauth/authorize when the user has no Vertex web session yet
    mcp_app.routes.insert(0, Route("/login", handle_login_get, methods=["GET"]))
    mcp_app.routes.insert(0, Route("/login", handle_login_post, methods=["POST"]))

    mcp_app.routes.insert(0, Route("/", root_handler, methods=["GET", "HEAD", "OPTIONS"]))

    config = uvicorn.Config(
        mcp_app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    system_db.init_system_db()
    default_user = get_auth_username()
    system_db.ensure_user(default_user)
    # If the default user's library doesn't exist yet, seed it from DEFAULT_DB_PATH
    default_user_db = Path(USERS_DB_DIR) / default_user / "games.db"
    if not default_user_db.exists() and Path(DEFAULT_DB_PATH).exists():
        default_user_db.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        try:
            shutil.copy2(DEFAULT_DB_PATH, default_user_db)
            logger.info("Migrated legacy database from %s to %s", DEFAULT_DB_PATH, default_user_db)
        except Exception as e:
            logger.warning("Could not seed default user database: %s", e)
    sync_task = asyncio.create_task(_periodic_sync_loop())
    mcp_task = asyncio.create_task(_run_mcp_http_server("0.0.0.0", MCP_PORT))
    logger.info("Started MCP Streamable HTTP server on port %s with CORS & OAuth discovery", MCP_PORT)
    yield
    sync_task.cancel()
    mcp_task.cancel()


app = FastAPI(
    title="Unified Game Library Hub",
    description="Cross-platform game library & metrics tracker across Xbox, PC, PS4, and Nintendo Switch",
    version="1.6.0",
    lifespan=lifespan,
)

class WebAuthAndCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method != "OPTIONS" and not is_public_path(path):
            if not is_request_authenticated(request):
                if path.startswith("/api/"):
                    return JSONResponse(
                        {"error": "unauthorized", "detail": "Authentication required"},
                        status_code=401,
                    )
                next_target = path
                if request.url.query:
                    next_target = f"{path}?{request.url.query}"
                return RedirectResponse(url=f"/login?next={quote(next_target)}", status_code=303)

        # Scope authenticated requests to the user's games.db
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        username = verify_web_session(session_id)
        if not username and is_local_bypass_enabled() and _is_direct_local_or_tailscale_request(request):
            username = get_auth_username()
        user_id = system_db.get_user_id_by_username(username) if username else None
        user_ctx_token = set_current_user_id(user_id) if user_id else None

        try:
            response = await call_next(request)
        finally:
            if user_ctx_token is not None:
                reset_current_user_id(user_ctx_token)

        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.add_middleware(WebAuthAndCacheMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ManualGameCreate(BaseModel):
    title: str = Field(..., min_length=1)
    platform: str = Field(default="Nintendo Switch")
    completion_status: str = Field(default="Not Played")
    ownership_status: str = Field(default="Owned")
    is_game_pass: bool = Field(default=False)
    playtime_hours: float = Field(default=0.0, ge=0.0)
    user_rating: Optional[int] = Field(default=None, ge=1, le=10)
    critic_score: Optional[int] = Field(default=None, ge=0, le=100)
    release_year: Optional[int] = None
    genres: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    cover_url: Optional[str] = None
    notes: str = ""
    favorite: bool = False


class GameUpdateRequest(BaseModel):
    title: Optional[str] = None
    platform: Optional[str] = None
    playtime_hours: Optional[float] = Field(default=None, ge=0.0)
    completion_status: Optional[str] = None
    ownership_status: Optional[str] = None
    is_game_pass: Optional[bool] = None
    achievements_unlocked: Optional[int] = Field(default=None, ge=0)
    achievements_total: Optional[int] = Field(default=None, ge=0)
    user_rating: Optional[int] = Field(default=None, ge=1, le=10)
    critic_score: Optional[int] = Field(default=None, ge=0, le=100)
    release_year: Optional[int] = None
    cover_url: Optional[str] = None
    notes: Optional[str] = None
    favorite: Optional[bool] = None
    genres: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class ReconcileConfirmRequest(BaseModel):
    game_ids: List[int] = Field(..., min_length=2)
    primary_id: Optional[int] = None
    custom_title: Optional[str] = None
    completion_status: Optional[str] = None


class ReconcileDismissRequest(BaseModel):
    game_ids: List[int] = Field(..., min_length=2)


class PlaytimePromptResolveRequest(BaseModel):
    action: str = Field(..., pattern="^(accept|dismiss|mute)$")


class UserConnectionsUpdateRequest(BaseModel):
    steam_id: Optional[str] = None
    xbl_api_key: Optional[str] = None
    psn_npsso: Optional[str] = None


class CreateTokenRequest(BaseModel):
    label: Optional[str] = Field(default="Personal Access Token")


def _get_active_user(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """Return (user_id, username) for the active request."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    username = verify_web_session(session_id)
    if not username and is_local_bypass_enabled() and _is_direct_local_or_tailscale_request(request):
        username = get_auth_username()
    user_id = get_current_user_id()
    if not user_id and username:
        user_id = system_db.get_user_id_by_username(username)
        if not user_id:
            user_id = system_db.ensure_user(username)
    return user_id, username


def _get_active_user_db_path(user_id: Optional[str] = None) -> Path:
    uid = user_id or get_current_user_id()
    if uid:
        return Path(db.USERS_DB_DIR) / str(uid) / "games.db"
    return Path(db.DEFAULT_DB_PATH)


@app.get("/login")
async def login_page(request: Request) -> Response:
    return await handle_login_get(request)


@app.post("/login")
async def login_submit(request: Request) -> Response:
    return await handle_login_post(request)


@app.get("/logout")
@app.post("/logout")
async def logout_action(request: Request) -> Response:
    return await handle_logout(request)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest")
async def pwa_manifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/api/games")
async def api_list_games(
    search: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    genre: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    ownership: Optional[str] = Query(default=None),
    is_game_pass: Optional[bool] = Query(default=None),
    favorite_only: bool = Query(default=False),
    sort_by: str = Query(default="playtime_desc"),
    limit: int = Query(default=500, le=5000),
    include_unconsolidated: bool = Query(default=False),
) -> Dict[str, Any]:
    games = query_games(
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        ownership=ownership,
        is_game_pass=is_game_pass,
        favorite_only=favorite_only,
        sort_by=sort_by,
        limit=limit,
        include_unconsolidated=include_unconsolidated,
    )
    return {"count": len(games), "games": games}


@app.get("/api/stats")
async def api_get_stats() -> Dict[str, Any]:
    stats = get_library_stats()
    stats["configured_sources"] = {
        "steam": bool(os.environ.get("STEAM_API_KEY") and os.environ.get("STEAM_ID")),
        "xbox": bool(os.environ.get("XBL_API_KEY")),
        "psn": bool(os.environ.get("PSN_NPSSO")),
    }
    stats["sync_interval_hours"] = SYNC_INTERVAL_HOURS
    stats["auth_enabled"] = is_web_auth_enabled()
    return stats


@app.get("/api/analytics")
async def api_get_analytics(
    search: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    genre: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    ownership: Optional[str] = Query(default=None),
    is_game_pass: Optional[bool] = Query(default=None),
    favorite_only: bool = Query(default=False),
) -> Dict[str, Any]:
    return get_habits_analytics(
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        ownership=ownership,
        is_game_pass=is_game_pass,
        favorite_only=favorite_only,
    )


@app.post("/api/games")
async def api_create_game(payload: ManualGameCreate) -> Dict[str, Any]:
    game = add_manual_game(
        title=payload.title.strip(),
        platform=payload.platform.strip(),
        completion_status=payload.completion_status,
        ownership_status=payload.ownership_status,
        is_game_pass=payload.is_game_pass,
        playtime_hours=payload.playtime_hours,
        user_rating=payload.user_rating,
        critic_score=payload.critic_score,
        release_year=payload.release_year,
        genres=payload.genres,
        tags=payload.tags,
        cover_url=payload.cover_url,
        notes=payload.notes,
        favorite=payload.favorite,
    )
    return {"status": "ok", "game": game}


@app.patch("/api/games/{game_id}")
async def api_update_game(game_id: int, payload: GameUpdateRequest) -> Dict[str, Any]:
    existing = get_game_by_id(game_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Game not found")

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    updated = update_game(game_id, updates)
    return {"status": "ok", "game": updated}


@app.delete("/api/games/{game_id}")
async def api_delete_game(game_id: int) -> Dict[str, Any]:
    deleted = delete_game(game_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"status": "ok", "deleted_id": game_id}


@app.get("/api/games/{game_id}/achievements")
async def api_get_game_achievements(
    game_id: int,
    auto_sync: bool = Query(default=True),
) -> Dict[str, Any]:
    existing = get_game_by_id(game_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Game not found")

    linked_copies = existing.get("linked_games") or [existing]
    needs_sync = any(
        c.get("source") in ("Steam", "Xbox", "PSN") and not c.get("achievements_synced_at")
        for c in linked_copies
    )
    if auto_sync and needs_sync:
        try:
            return await sync_game_achievements_on_demand(game_id)
        except Exception as exc:
            logger.warning("Auto-sync achievements error for game %s: %s", game_id, exc)

    return get_game_achievements(game_id)


@app.post("/api/games/{game_id}/achievements/sync")
async def api_sync_game_achievements(game_id: int) -> Dict[str, Any]:
    existing = get_game_by_id(game_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Game not found")
    data = await sync_game_achievements_on_demand(game_id)
    refreshed_game = get_game_by_id(game_id)
    return {"status": "ok", "game": refreshed_game, "achievements": data}


@app.post("/api/sync")
async def api_trigger_sync() -> Dict[str, Any]:
    lock = _get_user_sync_lock()
    if lock.locked():
        return {"status": "running", "message": "A sync is already in progress."}
    async with lock:
        async with _sync_semaphore:
            results = await sync_all_platforms()
    return {"status": "completed", "results": results, "stats": get_library_stats()}


@app.post("/api/enrich-metadata")
async def api_enrich_metadata(
    limit: int = Query(default=500, ge=1, le=2000),
    force: bool = Query(default=False),
) -> Dict[str, Any]:
    lock = _get_user_sync_lock()
    if lock.locked():
        return {"status": "running", "message": "A sync or enrichment job is already in progress."}
    async with lock:
        async with _sync_semaphore:
            result = await enrich_missing_metadata(limit=limit, force=force)
    return {"status": "completed", "result": result, "stats": get_library_stats()}


@app.get("/api/search-external")
async def api_search_external(q: str = Query(..., min_length=2)) -> Dict[str, Any]:
    results = await search_external_metadata(q)
    return {"results": results}


@app.get("/api/reconcile/candidates")
async def api_get_reconcile_candidates(
    cross_platform_only: bool = Query(default=False),
) -> Dict[str, Any]:
    candidates = get_reconcile_candidates(cross_platform_only=cross_platform_only)
    return {"count": len(candidates), "candidates": candidates}


@app.post("/api/reconcile/confirm")
async def api_reconcile_confirm(payload: ReconcileConfirmRequest) -> Dict[str, Any]:
    try:
        game = reconcile_games(
            game_ids=payload.game_ids,
            primary_id=payload.primary_id,
            custom_title=payload.custom_title,
            completion_status=payload.completion_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "game": game, "stats": get_library_stats()}


@app.post("/api/reconcile/dismiss")
async def api_reconcile_dismiss(payload: ReconcileDismissRequest) -> Dict[str, Any]:
    dismissed = dismiss_reconcile_group(payload.game_ids)
    return {"status": "ok", "dismissed_pairs": dismissed}


@app.post("/api/reconcile/unlink/{game_id}")
async def api_reconcile_unlink(game_id: int) -> Dict[str, Any]:
    updated = unlink_game(game_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"status": "ok", "game": updated, "stats": get_library_stats()}


@app.post("/api/reconcile/reset-dismissals")
async def api_reconcile_reset_dismissals() -> Dict[str, Any]:
    cleared = reset_reconcile_dismissals()
    return {"status": "ok", "cleared": cleared}


@app.get("/api/playtime-prompts")
async def api_get_playtime_prompts() -> Dict[str, Any]:
    prompts = get_pending_playtime_prompts()
    return {"count": len(prompts), "prompts": prompts}


@app.post("/api/playtime-prompts/{prompt_id}/resolve")
async def api_resolve_playtime_prompt(
    prompt_id: int, payload: PlaytimePromptResolveRequest
) -> Dict[str, Any]:
    try:
        res = resolve_playtime_prompt(prompt_id, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not res:
        raise HTTPException(status_code=404, detail="Playtime prompt not found")
    return {"status": "ok", "result": res, "stats": get_library_stats()}


@app.get("/api/user/connections")
async def api_get_user_connections(request: Request) -> Dict[str, Any]:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    conns = system_db.get_user_connections(user_id)
    xbl_key = conns.get("xbl_api_key", "")
    psn_token = conns.get("psn_npsso", "")

    return {
        "user_id": user_id,
        "username": username,
        "steam_id": conns.get("steam_id", ""),
        "has_xbl_api_key": bool(xbl_key),
        "has_psn_npsso": bool(psn_token),
        "xbl_api_key_masked": (f"••••••••{xbl_key[-4:]}" if len(xbl_key) >= 4 else ("••••" if xbl_key else "")),
        "psn_npsso_masked": (f"••••••••{psn_token[-4:]}" if len(psn_token) >= 4 else ("••••" if psn_token else "")),
        "server_defaults": {
            "steam": bool(os.environ.get("STEAM_API_KEY") and os.environ.get("STEAM_ID")),
            "xbox": bool(os.environ.get("XBL_API_KEY")),
            "psn": bool(os.environ.get("PSN_NPSSO")),
        },
    }


@app.post("/api/user/connections")
async def api_update_user_connections(
    payload: UserConnectionsUpdateRequest, request: Request
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    # If key is "__CLEAR__", clear it; if empty string/None, preserve existing
    xbl = "" if payload.xbl_api_key == "__CLEAR__" else (payload.xbl_api_key if payload.xbl_api_key else None)
    psn = "" if payload.psn_npsso == "__CLEAR__" else (payload.psn_npsso if payload.psn_npsso else None)

    updated = system_db.set_user_connections(
        user_id=user_id,
        steam_id=payload.steam_id,
        xbl_api_key=xbl,
        psn_npsso=psn,
    )
    return {
        "status": "ok",
        "message": "Platform connections saved successfully",
        "connections": {
            "steam_id": updated.get("steam_id", ""),
            "has_xbl_api_key": bool(updated.get("xbl_api_key")),
            "has_psn_npsso": bool(updated.get("psn_npsso")),
        },
    }


@app.get("/api/user/mcp")
async def api_get_user_mcp(request: Request) -> Dict[str, Any]:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    base_domain = os.environ.get("MCP_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if not base_domain:
        base_domain = str(request.base_url).rstrip("/")
    tokens = system_db.list_user_oauth_tokens(user_id)
    return {
        "user_id": user_id,
        "username": username,
        "mcp_endpoint": f"{base_domain}/mcp",
        "sse_endpoint": f"{base_domain}/sse",
        "tokens": tokens,
    }


@app.post("/api/user/mcp/tokens")
async def api_create_user_mcp_token(
    payload: CreateTokenRequest, request: Request
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    token = system_db.create_personal_access_token(
        user_id, label=payload.label or "Personal Access Token"
    )
    return {
        "status": "ok",
        "token": token,
        "message": "Personal access token generated successfully. Copy it now, as it won't be shown again in full.",
    }


@app.delete("/api/user/mcp/tokens/{token}")
async def api_revoke_user_mcp_token(token: str, request: Request) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    revoked = system_db.revoke_oauth_token(user_id, token)
    if not revoked:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"status": "ok", "message": "Token revoked successfully"}


@app.get("/api/user/library/export")
async def api_export_library(request: Request) -> Response:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db_path = _get_active_user_db_path(user_id)
    if not user_db_path.exists():
        user_db_path.parent.mkdir(parents=True, exist_ok=True)
        init_db(str(user_db_path))

    tmp_export = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_export_path = tmp_export.name
    tmp_export.close()

    try:
        with sqlite3.connect(str(user_db_path)) as src, sqlite3.connect(tmp_export_path) as dst:
            src.backup(dst)
    except Exception as exc:
        if os.path.exists(tmp_export_path):
            os.remove(tmp_export_path)
        logger.error("Failed to backup library for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to export library database: {exc}")

    clean_user = re.sub(r"[^a-zA-Z0-9_-]", "", username or "user") or "user"
    filename = f"games-{clean_user}.db"
    return FileResponse(
        path=tmp_export_path,
        filename=filename,
        media_type="application/x-sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(os.remove, tmp_export_path),
    )


@app.post("/api/user/library/import")
async def api_import_library(
    request: Request,
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    filename = (file.filename or "").lower()
    if not any(filename.endswith(ext) for ext in (".db", ".sqlite", ".sqlite3")):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file must be a SQLite database (.db, .sqlite, .sqlite3)",
        )

    tmp_import = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_import_path = tmp_import.name

    MAX_DB_SIZE = 150 * 1024 * 1024  # 150 MB
    bytes_read = 0
    try:
        while chunk := await file.read(1024 * 1024):  # 1 MB chunks
            bytes_read += len(chunk)
            if bytes_read > MAX_DB_SIZE:
                raise HTTPException(status_code=400, detail="Database file exceeds 150 MB limit")
            tmp_import.write(chunk)
    finally:
        tmp_import.close()

    # Validate SQLite header and integrity
    try:
        with open(tmp_import_path, "rb") as f:
            header = f.read(16)
        if not header.startswith(b"SQLite format 3\x00"):
            raise ValueError("File is not a valid SQLite database header")

        with sqlite3.connect(tmp_import_path) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {integrity}")

            has_games = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'"
            ).fetchone()
            if not has_games:
                raise ValueError("Database does not contain the required 'games' table")

            game_count = conn.execute("SELECT count(*) FROM games").fetchone()[0]
    except Exception as exc:
        if os.path.exists(tmp_import_path):
            os.remove(tmp_import_path)
        raise HTTPException(status_code=400, detail=f"Invalid database file: {exc}")

    user_db_path = _get_active_user_db_path(user_id)
    user_db_path.parent.mkdir(parents=True, exist_ok=True)

    lock = _get_user_sync_lock(user_id)
    async with lock:
        # Create safety backup of existing DB if present
        if user_db_path.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup_path = user_db_path.with_name(f"games.db.bak-{ts}")
            try:
                shutil.copy2(user_db_path, backup_path)
                logger.info("Created backup of user %s database at %s", user_id, backup_path)
            except Exception as e:
                logger.warning("Could not backup existing database: %s", e)

        # Remove existing WAL/SHM files
        wal = Path(str(user_db_path) + "-wal")
        shm = Path(str(user_db_path) + "-shm")
        if wal.exists():
            try:
                wal.unlink()
            except OSError:
                pass
        if shm.exists():
            try:
                shm.unlink()
            except OSError:
                pass

        # Atomic move
        shutil.move(tmp_import_path, str(user_db_path))

        # Run migrations on the newly imported DB to ensure all columns exist
        init_db(str(user_db_path))

    return {
        "status": "ok",
        "message": "Library imported successfully",
        "imported_games": game_count,
        "stats": get_library_stats(),
    }


async def _forward_to_mcp_service(request: Request, target_path: str) -> Response:
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{MCP_PORT}", timeout=httpx.Timeout(60.0, read=None))
    url = httpx.URL(path=target_path, query=request.url.query.encode("utf-8") if request.url.query else None)

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    req_body = await request.body()
    try:
        req = client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=req_body,
        )
        resp = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.warning("MCP proxy forwarding error: %s", exc)
        return JSONResponse({"error": "mcp_service_unavailable", "detail": str(exc)}, status_code=503)

    resp_headers = dict(resp.headers)
    resp_headers.pop("content-encoding", None)
    resp_headers.pop("content-length", None)
    resp_headers.pop("transfer-encoding", None)

    async def stream_response():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_response(),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )


@app.api_route("/mcp{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
async def proxy_mcp(request: Request, path: str = ""):
    return await _forward_to_mcp_service(request, f"/mcp{path}")


@app.api_route("/sse{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"], include_in_schema=False)
async def proxy_sse(request: Request, path: str = ""):
    return await _forward_to_mcp_service(request, f"/sse{path}")


@app.api_route("/messages{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"], include_in_schema=False)
async def proxy_messages(request: Request, path: str = ""):
    return await _forward_to_mcp_service(request, f"/messages{path}")


from oauth_provider import get_oauth_routes
for oauth_route in get_oauth_routes():
    app.routes.append(oauth_route)

