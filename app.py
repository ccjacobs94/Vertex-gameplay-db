"""
FastAPI Web Server + Background Auto-Updater for Game Hub.
Accessible over Local Network (LAN) and Tailscale.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

import mcp_server
from db import (
    add_manual_game,
    delete_game,
    dismiss_reconcile_group,
    get_game_by_id,
    get_habits_analytics,
    get_library_stats,
    get_reconcile_candidates,
    init_db,
    query_games,
    reconcile_games,
    reset_reconcile_dismissals,
    unlink_game,
    update_game,
)
from sync_engine import enrich_missing_metadata, search_external_metadata, sync_all_platforms

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gamehub.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
SYNC_INTERVAL_HOURS = float(os.environ.get("SYNC_INTERVAL_HOURS", "6"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8096"))

_sync_lock = asyncio.Lock()


async def _periodic_sync_loop() -> None:
    """Background loop that automatically syncs Steam, Xbox, and PSN every N hours."""
    await asyncio.sleep(3)
    while True:
        try:
            async with _sync_lock:
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
    mcp_app = mcp_server.mcp.streamable_http_app(host=host, streamable_http_path="/mcp", transport_security=sec)
    sse_app = mcp_server.mcp.sse_app(transport_security=sec)
    for route in sse_app.routes:
        mcp_app.routes.append(route)

    from oauth_provider import (
        _get_base_domain,
        _get_client_secret,
        extract_bearer_token,
        get_oauth_routes,
        verify_bearer_token,
    )

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

            # Enforce Bearer token authentication on MCP transport endpoints when MCP_CLIENT_SECRET is set
            require_auth = os.environ.get("MCP_REQUIRE_AUTH", "true").lower() not in ("0", "false", "no")
            is_sse_root = path == "/" and "text/event-stream" in request.headers.get("accept", "")
            is_protected_mcp_path = path.startswith("/mcp") or path.startswith("/sse") or path.startswith("/messages") or is_sse_root

            if request.method != "OPTIONS" and require_auth and _get_client_secret() and is_protected_mcp_path:
                token = extract_bearer_token(request)
                if not verify_bearer_token(token):
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

            response = await call_next(request)
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
    sync_task = asyncio.create_task(_periodic_sync_loop())
    mcp_task = asyncio.create_task(_run_mcp_http_server("0.0.0.0", MCP_PORT))
    logger.info("Started MCP Streamable HTTP server on port %s with CORS & OAuth discovery", MCP_PORT)
    yield
    sync_task.cancel()
    mcp_task.cancel()


app = FastAPI(
    title="Unified Game Library Hub",
    description="Cross-platform game library & metrics tracker across Xbox, PC, PS4, and Nintendo Switch",
    version="1.0.0",
    lifespan=lifespan,
)

class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.add_middleware(NoCacheStaticMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ManualGameCreate(BaseModel):
    title: str = Field(..., min_length=1)
    platform: str = Field(default="Nintendo Switch")
    completion_status: str = Field(default="Not Played")
    playtime_hours: float = Field(default=0.0, ge=0.0)
    user_rating: Optional[int] = Field(default=None, ge=1, le=10)
    critic_score: Optional[int] = Field(default=None, ge=0, le=100)
    release_year: Optional[int] = None
    genres: List[str] = Field(default_factory=list)
    cover_url: Optional[str] = None
    notes: str = ""
    favorite: bool = False


class GameUpdateRequest(BaseModel):
    title: Optional[str] = None
    platform: Optional[str] = None
    playtime_hours: Optional[float] = Field(default=None, ge=0.0)
    completion_status: Optional[str] = None
    user_rating: Optional[int] = Field(default=None, ge=1, le=10)
    critic_score: Optional[int] = Field(default=None, ge=0, le=100)
    release_year: Optional[int] = None
    cover_url: Optional[str] = None
    notes: Optional[str] = None
    favorite: Optional[bool] = None
    genres: Optional[List[str]] = None


class ReconcileConfirmRequest(BaseModel):
    game_ids: List[int] = Field(..., min_length=2)
    primary_id: Optional[int] = None
    custom_title: Optional[str] = None
    completion_status: Optional[str] = None


class ReconcileDismissRequest(BaseModel):
    game_ids: List[int] = Field(..., min_length=2)


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
    return stats


@app.get("/api/analytics")
async def api_get_analytics(
    search: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    genre: Optional[str] = Query(default=None),
    favorite_only: bool = Query(default=False),
) -> Dict[str, Any]:
    return get_habits_analytics(
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        favorite_only=favorite_only,
    )


@app.post("/api/games")
async def api_create_game(payload: ManualGameCreate) -> Dict[str, Any]:
    game = add_manual_game(
        title=payload.title.strip(),
        platform=payload.platform.strip(),
        completion_status=payload.completion_status,
        playtime_hours=payload.playtime_hours,
        user_rating=payload.user_rating,
        critic_score=payload.critic_score,
        release_year=payload.release_year,
        genres=payload.genres,
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


@app.post("/api/sync")
async def api_trigger_sync() -> Dict[str, Any]:
    if _sync_lock.locked():
        return {"status": "running", "message": "A sync is already in progress."}
    async with _sync_lock:
        results = await sync_all_platforms()
    return {"status": "completed", "results": results, "stats": get_library_stats()}


@app.post("/api/enrich-metadata")
async def api_enrich_metadata(
    limit: int = Query(default=500, ge=1, le=2000),
    force: bool = Query(default=False),
) -> Dict[str, Any]:
    if _sync_lock.locked():
        return {"status": "running", "message": "A sync or enrichment job is already in progress."}
    async with _sync_lock:
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


from oauth_provider import get_oauth_routes
for oauth_route in get_oauth_routes():
    app.routes.append(oauth_route)

