"""
FastAPI Web Server + Background Auto-Updater for Game Hub.
Accessible over Local Network (LAN) and Tailscale.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import inspect
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware

import billing
import mcp_server
import passwords
import signup
import system_db
import db
import web_push
import wishlist_sync
from context import get_current_user_id, reset_current_user_id, set_current_user_id
import web_auth
from web_auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    _is_direct_local_or_tailscale_request,
    get_auth_username,
    get_auth_password,
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
    resolve_xbox_gamertag,
    search_external_metadata,
    sync_all_platforms,
    sync_game_achievements_on_demand,
    sync_xbox,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
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


async def run_scheduled_sync_iteration() -> Dict[str, Any]:
    """Execute a single scheduled sync iteration across all active users."""
    active_uids = system_db.list_active_user_ids()
    if not active_uids:
        default_user = get_auth_username()
        active_uids = [default_user]

    results: Dict[str, Any] = {}
    for uid in active_uids:
        lock = _get_user_sync_lock(uid)
        async with lock:
            async with _sync_semaphore:
                token = set_current_user_id(uid)
                try:
                    init_db()
                    logger.info("Running scheduled sync for user %s...", uid)
                    user_results = await sync_all_platforms()
                    results[uid] = user_results
                    logger.info("Scheduled sync finished for user %s: %s", uid, user_results)
                except Exception as exc:
                    logger.error("Scheduled sync error for user %s: %s", uid, exc)
                    results[uid] = {"error": str(exc)}
                finally:
                    reset_current_user_id(token)
    return results


async def _periodic_sync_loop() -> None:
    """Background loop that automatically syncs Steam, Xbox, and PSN every N hours."""
    await asyncio.sleep(3)
    while True:
        try:
            await run_scheduled_sync_iteration()
        except Exception as exc:
            logger.error("Error in scheduled sync loop: %s", exc)
        await asyncio.sleep(max(SYNC_INTERVAL_HOURS, 0.5) * 3600)


WISHLIST_SYNC_INTERVAL_HOURS = float(os.environ.get("WISHLIST_SYNC_INTERVAL_HOURS", "24"))


async def _periodic_wishlist_sync_loop() -> None:
    """Background loop that updates wishlist prices and checks deals every 24 hours."""
    await asyncio.sleep(15)
    while True:
        try:
            active_users = system_db.list_active_user_ids()
            if not active_users:
                default_user = get_auth_username()
                active_users = [default_user]
            for uid in active_users:
                token = set_current_user_id(uid)
                try:
                    init_db()
                    user_db = _get_active_user_db_path(uid)
                    db_path = str(user_db)
                    await wishlist_sync.sync_wishlist_prices_for_db(db_path=db_path)
                except Exception as exc:
                    logger.warning("Wishlist price sync failed for user %s: %s", uid, exc)
                finally:
                    reset_current_user_id(token)
        except Exception as exc:
            logger.error("Error in scheduled wishlist sync loop: %s", exc)
        await asyncio.sleep(max(WISHLIST_SYNC_INTERVAL_HOURS, 1.0) * 3600)


async def _run_mcp_http_server(host: str, port: int) -> None:
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from mcp.server.transport_security import TransportSecuritySettings

    allowed_mcp_hosts = ["localhost", "127.0.0.1", "::1"]
    custom_hosts = os.environ.get("MCP_ALLOWED_HOSTS") or os.environ.get("VERTEX_ALLOWED_HOSTS") or ""
    if custom_hosts:
        for ch in custom_hosts.split(","):
            if ch.strip() and ch.strip() not in allowed_mcp_hosts:
                allowed_mcp_hosts.append(ch.strip())

    sec = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_mcp_hosts,
    )
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
        allow_credentials=False,
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


def clean_unconfigured_subaccount_libraries(default_user: str, db_path: Optional[str] = None) -> List[str]:
    """
    Ensure subaccounts without personal credentials do not retain games accidentally
    leaked from server-owner environment defaults. Returns list of cleaned user_ids.
    """
    cleaned = []
    try:
        active_uids = system_db.list_active_user_ids(db_path=db_path)
    except Exception:
        return cleaned

    for uid in active_uids:
        if uid == default_user:
            continue
        try:
            uconns = system_db.get_user_connections(uid, db_path=db_path)
            has_own_creds = bool(uconns.get("steam_id") or uconns.get("xbl_api_key") or uconns.get("xbox_gamertag") or uconns.get("psn_npsso"))
            if not has_own_creds:
                user_db_file = Path(db.USERS_DB_DIR) / uid / "games.db"
                if user_db_file.exists():
                    with db.get_connection(str(user_db_file)) as uconn:
                        synced_count = uconn.execute("SELECT count(*) FROM games WHERE source != 'Manual'").fetchone()[0]
                        manual_count = uconn.execute("SELECT count(*) FROM games WHERE source = 'Manual'").fetchone()[0]
                        if synced_count > 0 and manual_count == 0:
                            logger.info("Purging leaked server-owner games from unconfigured subaccount %s", uid)
                            uconn.execute("DELETE FROM games")
                            uconn.execute("DELETE FROM sync_logs")
                            uconn.execute("DELETE FROM game_achievements")
                            uconn.execute("DELETE FROM playtime_prompts")
                            uconn.execute("DELETE FROM reconcile_dismissals")
                            uconn.commit()
                            cleaned.append(uid)
        except Exception as exc:
            logger.warning("Could not check/clean subaccount database for %s: %s", uid, exc)
    return cleaned


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DEFAULT_DB_PATH)
    system_db.init_system_db()
    default_user = get_auth_username()
    system_db.ensure_bootstrap_admin(default_user)
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
    clean_unconfigured_subaccount_libraries(default_user)
    sync_task = asyncio.create_task(_periodic_sync_loop())
    wishlist_task = asyncio.create_task(_periodic_wishlist_sync_loop())
    mcp_task = asyncio.create_task(_run_mcp_http_server("0.0.0.0", MCP_PORT))
    logger.info("Started MCP Streamable HTTP server on port %s with CORS & OAuth discovery", MCP_PORT)
    yield
    sync_task.cancel()
    wishlist_task.cancel()
    mcp_task.cancel()



app = FastAPI(
    title="Unified Game Library Hub",
    description="Cross-platform game library & metrics tracker across Xbox, PC, PS4, and Nintendo Switch",
    version="1.7.1",
    lifespan=lifespan,
)

def is_allowed_host(host_header: str) -> bool:
    if not host_header:
        return False
    h = host_header.split(":")[0].strip().lower()
    if not h:
        return False
    if h in ("localhost", "127.0.0.1", "::1", "testserver"):
        return True
    if h.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    allowed_env = os.environ.get("VERTEX_ALLOWED_HOSTS", "").strip()
    if allowed_env:
        for pattern in allowed_env.split(","):
            p = pattern.strip().lower()
            if p == "*" or p == h:
                return True
            if p.startswith("*.") and h.endswith(p[1:]):
                return True
    pub_url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if pub_url:
        try:
            pub_host = urlparse(pub_url).hostname
            if pub_host and h == pub_host.lower():
                return True
        except Exception:
            pass
    return False


class WebAuthAndCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if not is_allowed_host(host):
            return Response("Invalid Host header", status_code=400)

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
        if not username and not is_web_auth_enabled():
            username = get_auth_username()
        user = system_db.get_user_by_username(username) if username else None
        user_id = user["id"] if user else None

        if user and user.get("status") != "active":
            if user.get("status_reason") in ("awaiting_payment", "subscription_lapsed"):
                allowed = (
                    path.startswith("/billing")
                    or path.startswith("/static/")
                    or path in ("/logout", "/manifest.webmanifest", "/sw.js")
                )
                if not allowed:
                    if path.startswith("/api/"):
                        return JSONResponse(
                            {"error": "subscription_required", "detail": "Active subscription required"},
                            status_code=402,
                        )
                    return RedirectResponse(url="/billing", status_code=303)
            else:
                if not is_public_path(path):
                    if path.startswith("/api/"):
                        return JSONResponse(
                            {"error": "account_inactive", "detail": "Account is inactive or pending"},
                            status_code=403,
                        )
                    return RedirectResponse(url="/login", status_code=303)

        user_ctx_token = set_current_user_id(user_id) if user_id else None
        if user_id:
            try:
                init_db()
            except Exception:
                pass

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
    xbox_gamertag: Optional[str] = None
    psn_npsso: Optional[str] = None


class ConnectionTestRequest(BaseModel):
    platform: str
    credential: str


class CreateTokenRequest(BaseModel):
    label: Optional[str] = Field(default="Personal Access Token")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: Optional[str] = None
    email: Optional[str] = None
    role: str = Field(default="user", pattern="^(admin|user)$")


class AdminUserUpdateRequest(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(active|pending|inactive|disabled)$")
    role: Optional[str] = Field(default=None, pattern="^(admin|user)$")
    status_reason: Optional[str] = None


class AdminResetPasswordRequest(BaseModel):
    new_password: Optional[str] = None


class AdminSettingsUpdateRequest(BaseModel):
    registration_policy: str = Field(..., pattern="^(disabled|invite|approval|open)$")


class AdminInviteCreateRequest(BaseModel):
    note: Optional[str] = ""
    ttl_days: int = Field(default=7, ge=1, le=365)



def _get_active_user(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """Return (user_id, username) for the active request."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    username = verify_web_session(session_id)
    if not username and is_local_bypass_enabled() and _is_direct_local_or_tailscale_request(request):
        username = get_auth_username()
    if not username and not is_web_auth_enabled():
        username = get_auth_username()
    user_id = get_current_user_id()
    if not user_id and username:
        user = system_db.get_user_by_username(username)
        if not user or user.get("status") != "active":
            return None, None
        user_id = user["id"]
    return user_id, username


def _require_admin(request: Request) -> Tuple[str, str]:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = system_db.get_user(user_id)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user_id, username or user.get("username", "")



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


@app.get("/signup")
async def signup_page(request: Request) -> Response:
    return await signup.handle_signup_get(request)


@app.post("/signup")
async def signup_submit(request: Request) -> Response:
    return await signup.handle_signup_post(request)


def _get_session_user(request: Request) -> Optional[Dict[str, Any]]:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    username = verify_web_session(session_id)
    if not username and is_local_bypass_enabled() and _is_direct_local_or_tailscale_request(request):
        username = get_auth_username()
    if not username:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            from oauth_provider import verify_bearer_token
            uid = verify_bearer_token(token)
            if uid:
                return system_db.get_user(uid)
    if not username:
        return None
    return system_db.get_user_by_username(username)


def _require_hosted_edition() -> None:
    edition = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
    if edition != "hosted":
        raise HTTPException(status_code=404, detail="Billing is only available on hosted edition")


@app.get("/billing")
async def billing_page(request: Request) -> Response:
    _require_hosted_edition()
    user = _get_session_user(request)
    if not user:
        return RedirectResponse(url="/login?next=%2Fbilling", status_code=303)
    subscription = system_db.get_user_subscription(user["id"])
    error_msg = request.query_params.get("error", "")
    return billing.render_billing_page(user, subscription, error_msg=error_msg)


@app.post("/billing/checkout")
async def billing_checkout(request: Request) -> Response:
    _require_hosted_edition()
    user = _get_session_user(request)
    if not user:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse(url="/login?next=%2Fbilling", status_code=303)

    provider = billing.get_billing_provider()
    base_url = str(request.base_url).rstrip("/")
    return_url = f"{base_url}/billing"
    try:
        checkout_url = provider.create_checkout_url(user, return_url=return_url)
    except billing.BillingNotConfiguredError as exc:
        logger.error("Checkout failed: %s", exc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "billing_not_configured", "detail": str(exc)}, status_code=503)
        return billing.render_billing_page(
            user, system_db.get_user_subscription(user["id"]), error_msg=str(exc)
        )
    except Exception as exc:
        logger.error("Checkout creation error: %s", exc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "checkout_error", "detail": str(exc)}, status_code=500)
        return billing.render_billing_page(
            user,
            system_db.get_user_subscription(user["id"]),
            error_msg=f"Could not initialize checkout: {exc}",
        )

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"url": checkout_url})
    return RedirectResponse(url=checkout_url, status_code=303)


@app.post("/billing/portal")
async def billing_portal(request: Request) -> Response:
    _require_hosted_edition()
    user = _get_session_user(request)
    if not user:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse(url="/login?next=%2Fbilling", status_code=303)

    provider = billing.get_billing_provider()
    base_url = str(request.base_url).rstrip("/")
    return_url = f"{base_url}/billing"
    try:
        portal_url = provider.create_portal_url(user, return_url=return_url)
    except billing.BillingNotConfiguredError as exc:
        logger.error("Portal failed: %s", exc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "billing_not_configured", "detail": str(exc)}, status_code=503)
        return billing.render_billing_page(
            user, system_db.get_user_subscription(user["id"]), error_msg=str(exc)
        )
    except Exception as exc:
        logger.error("Portal creation error: %s", exc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "portal_error", "detail": str(exc)}, status_code=500)
        return billing.render_billing_page(
            user,
            system_db.get_user_subscription(user["id"]),
            error_msg=f"Could not open billing portal: {exc}",
        )

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"url": portal_url})
    return RedirectResponse(url=portal_url, status_code=303)


@app.post("/billing/webhook")
async def billing_webhook(request: Request) -> Response:
    payload = await request.body()
    signature_header = request.headers.get("stripe-signature", "")
    provider = billing.get_billing_provider()
    try:
        event = provider.verify_and_parse_webhook(payload, signature_header)
    except billing.BillingNotConfiguredError as exc:
        logger.warning("Billing webhook rejected: billing not configured (%s)", exc)
        return JSONResponse({"error": "billing_not_configured"}, status_code=503)
    except Exception as exc:
        logger.error("Billing webhook signature error: %s", exc)
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    if event is None:
        return JSONResponse({"error": "invalid_signature"}, status_code=400)

    try:
        billing.apply_billing_event(event)
    except Exception as exc:
        logger.error("Failed to apply billing event %s: %s", event.event_id, exc)
        return JSONResponse({"error": "processing_failed", "detail": str(exc)}, status_code=500)

    return JSONResponse({"received": True, "event_id": event.event_id})



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


@app.get("/healthz")
async def api_healthz() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/api/stats")
async def api_get_stats(request: Request = None) -> Dict[str, Any]:
    stats = get_library_stats()
    stats["configured_sources"] = {
        "steam": bool(os.environ.get("STEAM_API_KEY") and os.environ.get("STEAM_ID")),
        "xbox": bool(os.environ.get("XBL_API_KEY")),
        "psn": bool(os.environ.get("PSN_NPSSO")),
    }
    stats["sync_interval_hours"] = SYNC_INTERVAL_HOURS
    stats["auth_enabled"] = is_web_auth_enabled()

    user_id, _ = _get_active_user(request) if request else (None, None)
    is_admin = False
    if user_id:
        user = system_db.get_user(user_id)
        if user and user.get("role") == "admin":
            is_admin = True
    stats["is_admin"] = is_admin
    stats["edition"] = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
    stats["registration_policy"] = system_db.get_setting(
        "registration_policy", os.environ.get("VERTEX_REGISTRATION", "disabled")
    )
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
async def api_trigger_sync(platform: Optional[str] = None) -> Dict[str, Any]:
    lock = _get_user_sync_lock()
    if lock.locked():
        return {"status": "running", "message": "A sync is already in progress."}
    async with lock:
        async with _sync_semaphore:
            if isinstance(platform, str) and platform.strip().lower() in ("xbox", "xbl"):
                results = [await sync_xbox()]
            else:
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


@app.get("/api/account")
async def api_get_account(request: Request) -> Dict[str, Any]:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = system_db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    edition = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
    reg_policy = system_db.get_setting(
        "registration_policy", os.environ.get("VERTEX_REGISTRATION", "disabled")
    )
    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user.get("email"),
        "role": user.get("role", "user"),
        "status": user.get("status", "active"),
        "status_reason": user.get("status_reason"),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
        "edition": edition,
        "registration_policy": reg_policy,
    }


@app.post("/api/account/password")
async def api_change_password(payload: ChangePasswordRequest, request: Request) -> Response:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = system_db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    verified = passwords.verify_password(payload.current_password, user.get("password_hash"))
    if not verified:
        bootstrap_user = get_auth_username()
        bootstrap_pass = get_auth_password()
        if (
            secrets.compare_digest(user["username"].lower(), bootstrap_user.lower())
            and bool(bootstrap_pass)
            and secrets.compare_digest(payload.current_password, bootstrap_pass)
        ):
            verified = True
        elif (
            secrets.compare_digest(user["username"].lower(), bootstrap_user.lower())
            and not bool(bootstrap_pass)
            and str(user.get("password_hash") or "").strip().lower() == "managed_by_web_auth"
        ):
            # Initial bootstrap setup: bootstrap admin establishing initial password when VERTEX_PASSWORD was omitted
            verified = True

    if not verified:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        system_db.set_user_password(user_id, payload.new_password, actor_id=user_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # Revoke sessions for this username and create fresh one
    web_auth.revoke_sessions_for_username(user["username"])
    new_sess = web_auth.create_web_session(
        user["username"], request.headers.get("user-agent", "")
    )
    resp = JSONResponse({"status": "success", "message": "Password updated successfully"})
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=new_sess,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=web_auth._is_https_request(request),
        samesite="lax",
        path="/",
    )
    return resp


@app.get("/api/admin/users")
async def api_admin_list_users(request: Request, status: Optional[str] = None) -> Dict[str, Any]:
    _require_admin(request)
    users = system_db.list_users(status=status)
    return {"users": users}


@app.post("/api/admin/users")
async def api_admin_create_user(
    payload: AdminUserCreateRequest, request: Request
) -> Dict[str, Any]:
    actor_id, _ = _require_admin(request)
    pw = payload.password
    generated_pw = False
    if not pw:
        pw = secrets.token_urlsafe(16)
        generated_pw = True

    try:
        new_uid = system_db.create_user(
            username=payload.username,
            password=pw,
            email=payload.email,
            role=payload.role,
            status="active",
            actor_id=actor_id,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    user = system_db.get_user(new_uid)
    return {
        "status": "success",
        "user": user,
        "temporary_password": pw if generated_pw else None,
    }


@app.patch("/api/admin/users/{target_user_id}")
async def api_admin_update_user(
    target_user_id: str, payload: AdminUserUpdateRequest, request: Request
) -> Dict[str, Any]:
    actor_id, _ = _require_admin(request)
    target = system_db.get_user(target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        if payload.status is not None:
            status_val = payload.status
            reason = payload.status_reason
            if status_val == "disabled":
                status_val = "inactive"
                if not reason:
                    reason = "admin_suspended"
            system_db.set_user_status(
                target_user_id,
                status_val,
                reason=reason,
                actor_id=actor_id,
            )
            if status_val != "active":
                web_auth.revoke_sessions_for_username(target["username"])
        if payload.role is not None:
            system_db.set_user_role(target_user_id, payload.role, actor_id=actor_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    updated = system_db.get_user(target_user_id)
    return {"status": "success", "user": updated}


@app.delete("/api/admin/users/{target_user_id}")
async def api_admin_delete_user(
    target_user_id: str, request: Request
) -> Dict[str, Any]:
    actor_id, _ = _require_admin(request)
    target = system_db.get_user(target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        system_db.delete_user(target_user_id, actor_id=actor_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    return {
        "status": "success",
        "message": f"User '{target['username']}' and all associated data have been permanently deleted.",
        "deleted_user_id": target_user_id,
    }


@app.post("/api/admin/users/{target_user_id}/reset-password")
async def api_admin_reset_password(
    target_user_id: str, payload: AdminResetPasswordRequest, request: Request
) -> Dict[str, Any]:
    actor_id, _ = _require_admin(request)
    target = system_db.get_user(target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    pw = payload.new_password or secrets.token_urlsafe(16)

    try:
        system_db.set_user_password(target_user_id, pw, actor_id=actor_id)
        # Invalidate old sessions and PATs
        web_auth.revoke_sessions_for_username(target["username"])
        with system_db.get_system_connection() as conn:
            conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (target_user_id,))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    return {
        "status": "success",
        "message": f"Password reset for {target['username']}",
        "temporary_password": pw,
    }


@app.get("/api/admin/settings")
async def api_admin_get_settings(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    return {
        "edition": system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted")),
        "registration_policy": system_db.get_registration_policy(),
    }


@app.put("/api/admin/settings")
async def api_admin_update_settings(
    payload: AdminSettingsUpdateRequest, request: Request
) -> Dict[str, Any]:
    _require_admin(request)
    try:
        system_db.set_registration_policy(payload.registration_policy)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {
        "status": "success",
        "registration_policy": system_db.get_registration_policy(),
    }


@app.get("/api/admin/invites")
async def api_admin_list_invites(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    return {"invites": system_db.list_invites()}


@app.post("/api/admin/invites")
async def api_admin_create_invite(
    payload: AdminInviteCreateRequest, request: Request
) -> Dict[str, Any]:
    actor_id, _ = _require_admin(request)
    try:
        code = system_db.create_invite(
            created_by=actor_id,
            note=payload.note or "",
            ttl_days=payload.ttl_days,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {
        "status": "success",
        "code": code,
        "invite_url": f"/signup?invite={quote(code)}",
    }


@app.delete("/api/admin/invites/{code_hash}")
async def api_admin_revoke_invite(code_hash: str, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    revoked = system_db.revoke_invite(code_hash)
    if not revoked:
        raise HTTPException(status_code=404, detail="Invite not found or already consumed")
    return {"status": "success", "message": "Invite revoked"}



def clean_steam_input(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = raw.strip()
    # Strip URL prefixes if provided
    text = text.split("?")[0].split("#")[0].strip()
    text = re.sub(r"^https?://(?:www\.)?steamcommunity\.com/(?:id|profiles)/", "", text, flags=re.IGNORECASE)
    text = text.rstrip("/")
    return text.strip()


def clean_psn_token(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "npsso" in data:
            return str(data["npsso"]).strip()
    except Exception:
        pass
    m = re.search(r'["\']?npsso["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]+)["\']?', text)
    if m:
        return m.group(1).strip()
    return text.strip("\"' ")


def clean_xbl_key(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return raw.strip()


def _verify_steam_connection(credential: str) -> Dict[str, Any]:
    cleaned = clean_steam_input(credential)
    if not cleaned:
        return {"status": "error", "platform": "steam", "message": "Steam ID or Profile URL cannot be empty"}

    api_key = os.environ.get("STEAM_API_KEY", "").strip()
    steam_id = cleaned

    if api_key:
        try:
            with httpx.Client(timeout=15.0) as client:
                if not (cleaned.isdigit() and len(cleaned) >= 16):
                    r_vanity = client.get(
                        "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
                        params={"key": api_key, "vanityurl": cleaned},
                    )
                    if r_vanity.status_code == 200:
                        vdata = r_vanity.json().get("response", {})
                        if vdata.get("success") == 1 and vdata.get("steamid"):
                            steam_id = str(vdata["steamid"])
                        else:
                            return {
                                "status": "error",
                                "platform": "steam",
                                "message": f"Could not resolve Steam vanity URL '{cleaned}'",
                            }

                r_player = client.get(
                    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
                    params={"key": api_key, "steamids": steam_id},
                )
                if r_player.status_code == 200:
                    players = r_player.json().get("response", {}).get("players", [])
                    if players:
                        p = players[0]
                        persona_name = p.get("personaname", steam_id)
                        avatar = p.get("avatarfull") or p.get("avatarmedium") or ""
                        return {
                            "status": "ok",
                            "platform": "steam",
                            "steam_id": steam_id,
                            "persona_name": persona_name,
                            "avatar_url": avatar,
                            "message": f"Steam account verified successfully as {persona_name}",
                        }
        except Exception as exc:
            return {"status": "error", "platform": "steam", "message": f"Steam verification check error: {exc}"}

    if cleaned.isdigit() and len(cleaned) >= 16:
        return {
            "status": "ok",
            "platform": "steam",
            "steam_id": cleaned,
            "persona_name": cleaned,
            "avatar_url": "",
            "message": "SteamID recorded (live persona lookup requires server STEAM_API_KEY)",
        }
    return {
        "status": "error",
        "platform": "steam",
        "message": "Custom profile URLs require STEAM_API_KEY on the server to resolve. Please provide a 17-digit numeric SteamID64.",
    }


async def _verify_xbox_connection(credential: str) -> Dict[str, Any]:
    cleaned = clean_xbl_key(credential)
    if not cleaned:
        return {"status": "error", "platform": "xbox", "message": "Xbox Gamertag or OpenXBL API key cannot be empty"}

    server_xbl_key = os.environ.get("XBL_API_KEY", "").strip() or (system_db.get_setting("xbl_api_key") or "").strip()

    # 1. If server has XBL_API_KEY, try resolving credential as an Xbox Gamertag via friends search
    if server_xbl_key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as aclient:
                gt_res = await resolve_xbox_gamertag(aclient, server_xbl_key, cleaned)
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
        except Exception as exc:
            logger.debug("Server OpenXBL gamertag resolution error for %s: %s", cleaned, exc)

    # 2. Alternatively, verify credential directly as a personal OpenXBL API key
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://xbl.io/api/v2/account",
                headers={"X-Authorization": cleaned, "Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                profile_users = data.get("profileUsers", [])
                gamertag = "Xbox Gamer"
                gamerscore = "0"
                avatar_url = ""
                xuid = ""
                if profile_users:
                    u = profile_users[0]
                    xuid = str(u.get("id") or u.get("hostId") or "")
                    settings = {s.get("id"): s.get("value", "") for s in u.get("settings", [])}
                    gamertag = settings.get("Gamertag") or u.get("hostId") or gamertag
                    gamerscore = settings.get("Gamerscore", "0")
                    avatar_url = settings.get("GameDisplayPicRaw", "")
                return {
                    "status": "ok",
                    "platform": "xbox",
                    "gamertag": gamertag,
                    "xuid": xuid,
                    "gamerscore": gamerscore,
                    "avatar_url": avatar_url,
                    "message": f"Xbox account verified successfully as {gamertag} ({gamerscore} G)",
                }
            elif resp.status_code in (401, 403):
                if server_xbl_key:
                    return {
                        "status": "error",
                        "platform": "xbox",
                        "message": f"Could not find Xbox Gamertag '{cleaned}'. Please check the spelling or Xbox privacy settings.",
                    }
                return {"status": "error", "platform": "xbox", "message": "Invalid OpenXBL API key (unauthorized)"}
            else:
                if server_xbl_key:
                    return {
                        "status": "error",
                        "platform": "xbox",
                        "message": f"Could not verify Xbox Gamertag '{cleaned}' via OpenXBL (HTTP {resp.status_code})",
                    }
                return {
                    "status": "error",
                    "platform": "xbox",
                    "message": f"OpenXBL returned status {resp.status_code}",
                }
    except Exception as exc:
        if server_xbl_key:
            return {"status": "error", "platform": "xbox", "message": f"Xbox Gamertag lookup failed: {exc}"}
        return {"status": "error", "platform": "xbox", "message": f"Xbox verification check failed: {exc}"}


def _verify_psn_connection(credential: str) -> Dict[str, Any]:
    cleaned = clean_psn_token(credential)
    if not cleaned:
        return {"status": "error", "platform": "psn", "message": "PlayStation token or Online ID cannot be empty"}

    if len(cleaned) == 64 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        try:
            from psnawp_api import PSNAWP
            psnawp = PSNAWP(cleaned)
            me = psnawp.me()
            online_id = getattr(me, "online_id", "") or "PSN User"
            account_id = getattr(me, "account_id", "")
            return {
                "status": "ok",
                "platform": "psn",
                "online_id": online_id,
                "account_id": account_id,
                "message": f"PlayStation Network account verified successfully as {online_id}",
            }
        except Exception as exc:
            return {"status": "error", "platform": "psn", "message": f"PlayStation NPSSO verification failed: {exc}"}

    if len(cleaned) <= 30 and re.match(r"^[a-zA-Z0-9_-]+$", cleaned):
        return {
            "status": "ok",
            "platform": "psn",
            "online_id": cleaned,
            "message": f"PlayStation Online ID set to {cleaned}",
        }

    return {
        "status": "error",
        "platform": "psn",
        "message": "Invalid PSN NPSSO token (must be 64 hexadecimal characters) or invalid PSN Online ID",
    }


@app.get("/api/user/connections")
async def api_get_user_connections(request: Request) -> Dict[str, Any]:
    user_id, username = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    conns = system_db.get_user_connections(user_id)
    xbl_key = conns.get("xbl_api_key", "")
    psn_token = conns.get("psn_npsso", "")

    is_primary = (user_id == get_auth_username())
    onboarding_completed = bool(conns.get("onboarding_completed", False))

    if not onboarding_completed:
        # Check if the user already has games in their personal database (established account)
        try:
            user_db_path = _get_active_user_db_path(user_id)
            if user_db_path.exists():
                with sqlite3.connect(user_db_path) as uconn:
                    row = uconn.execute("SELECT count(*) FROM games").fetchone()
                    if row and row[0] > 0:
                        system_db.set_user_onboarding_completed(user_id, True)
                        onboarding_completed = True
        except Exception:
            pass

    return {
        "user_id": user_id,
        "username": username,
        "steam_id": conns.get("steam_id", ""),
        "has_xbl_api_key": bool(xbl_key),
        "xbox_gamertag": conns.get("xbox_gamertag", ""),
        "xbox_xuid": conns.get("xbox_xuid", ""),
        "has_psn_npsso": bool(psn_token),
        "onboarding_completed": onboarding_completed,
        "xbl_api_key_masked": (f"••••••••{xbl_key[-4:]}" if len(xbl_key) >= 4 else ("••••" if xbl_key else "")),
        "psn_npsso_masked": (f"••••••••{psn_token[-4:]}" if len(psn_token) >= 4 else ("••••" if psn_token else "")),
        "server_defaults": {
            "steam": bool(os.environ.get("STEAM_API_KEY") and (os.environ.get("STEAM_ID") if is_primary else False)),
            "xbox": bool(os.environ.get("XBL_API_KEY")),
            "psn": bool(os.environ.get("PSN_NPSSO") if is_primary else False),
        },
    }


@app.post("/api/user/connections")
async def api_update_user_connections(
    payload: UserConnectionsUpdateRequest, request: Request
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    steam = clean_steam_input(payload.steam_id) if payload.steam_id is not None else None

    # Handle xbox_gamertag and xbl_api_key
    raw_gt = payload.xbox_gamertag
    raw_xbl = payload.xbl_api_key

    xbox_gt = None
    if raw_gt is not None:
        xbox_gt = "" if raw_gt == "__CLEAR__" else raw_gt.strip()

    xbl = None
    if raw_xbl is not None:
        if raw_xbl == "__CLEAR__":
            xbl = ""
            if raw_gt is None:
                xbox_gt = ""
        else:
            cleaned_xbl = clean_xbl_key(raw_xbl)
            # If user entered a gamertag into the key field without separate xbox_gamertag
            if raw_gt is None and cleaned_xbl and (len(cleaned_xbl) <= 15 or " " in cleaned_xbl):
                xbox_gt = cleaned_xbl
                xbl = ""
            else:
                xbl = cleaned_xbl

    psn = "" if payload.psn_npsso == "__CLEAR__" else (clean_psn_token(payload.psn_npsso) if payload.psn_npsso else None)

    # Attempt to resolve xuid immediately if gamertag is configured and server has key
    resolved_xuid = None
    server_key = os.environ.get("XBL_API_KEY", "").strip() or xbl
    if xbox_gt and server_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                gt_data = await resolve_xbox_gamertag(client, server_key, xbox_gt)
                if gt_data and gt_data.get("xuid"):
                    resolved_xuid = gt_data["xuid"]
        except Exception:
            pass

    updated = system_db.set_user_connections(
        user_id=user_id,
        steam_id=steam,
        xbl_api_key=xbl,
        xbox_gamertag=xbox_gt,
        xbox_xuid=resolved_xuid,
        psn_npsso=psn,
    )
    return {
        "status": "ok",
        "message": "Platform connections saved successfully",
        "connections": {
            "steam_id": updated.get("steam_id", ""),
            "has_xbl_api_key": bool(updated.get("xbl_api_key")),
            "xbox_gamertag": updated.get("xbox_gamertag", ""),
            "xbox_xuid": updated.get("xbox_xuid", ""),
            "has_psn_npsso": bool(updated.get("psn_npsso")),
            "onboarding_completed": bool(updated.get("onboarding_completed", False)),
        },
    }


@app.post("/api/user/connections/test")
async def api_test_user_connection(payload: ConnectionTestRequest, request: Request) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    plat = payload.platform.strip().lower()
    cred = payload.credential.strip()

    if plat == "steam":
        res = _verify_steam_connection(cred)
    elif plat in ("xbox", "xbl"):
        res = _verify_xbox_connection(cred)
    elif plat == "psn":
        res = _verify_psn_connection(cred)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported verification platform: {plat}")

    if inspect.isawaitable(res):
        res = await res
    return res


@app.post("/api/user/onboarding/complete")
async def api_complete_user_onboarding(request: Request) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    system_db.set_user_onboarding_completed(user_id, True)
    return {"status": "ok", "onboarding_completed": True, "message": "Onboarding marked as completed"}


def validate_steam_openid_response(query_params: Dict[str, str]) -> Optional[str]:
    mode = query_params.get("openid.mode", "")
    if mode in ("cancel", "error"):
        return None

    # Re-validate with Steam using check_authentication
    validation_payload = {k: v for k, v in query_params.items() if k.startswith("openid.")}
    validation_payload["openid.mode"] = "check_authentication"

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post("https://steamcommunity.com/openid/login", data=validation_payload)
            if resp.status_code == 200 and "is_valid:true" in resp.text:
                claimed_id = query_params.get("openid.claimed_id", "")
                m = re.search(r"https?://steamcommunity\.com/openid/id/(\d+)", claimed_id)
                if m:
                    return m.group(1)
    except Exception as exc:
        logger.warning("Steam OpenID signature validation error: %s", exc)
    return None


@app.get("/api/auth/steam/login")
async def api_auth_steam_login(request: Request, return_to: str = "/") -> Response:
    base_url = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") and request.headers.get("x-forwarded-host"):
        base_url = f"{request.headers['x-forwarded-proto']}://{request.headers['x-forwarded-host']}".rstrip("/")

    return_url = f"{base_url}/api/auth/steam/callback?return_to={quote(return_to)}"
    realm_url = f"{base_url}/"

    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_url,
        "openid.realm": realm_url,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    steam_login_url = f"https://steamcommunity.com/openid/login?{urlencode(params)}"
    return RedirectResponse(url=steam_login_url, status_code=303)


@app.get("/api/auth/steam/callback")
async def api_auth_steam_callback(request: Request, return_to: str = "/") -> Response:
    target_return = return_to if return_to.startswith("/") else "/"
    sep = "&" if "?" in target_return else "?"

    if request.query_params.get("openid.mode") in ("cancel", "error"):
        return RedirectResponse(f"{target_return}{sep}cancelled=1", status_code=303)

    raw_params = dict(request.query_params)
    steam_id = validate_steam_openid_response(raw_params)
    if inspect.isawaitable(steam_id):
        steam_id = await steam_id

    if not steam_id:
        return RedirectResponse(f"{target_return}{sep}error=steam_verification_failed", status_code=303)

    user_id, _ = _get_active_user(request)
    if user_id:
        system_db.set_user_connections(user_id=user_id, steam_id=steam_id)
        return RedirectResponse(f"{target_return}{sep}steam_connected=1&steam_id={steam_id}", status_code=303)

    return RedirectResponse(f"/login?steam_id={steam_id}&steam_unlinked=1", status_code=303)


def exchange_xbox_oauth_code(code: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    try:
        with httpx.Client(timeout=20.0) as client:
            # 1. Exchange auth code for Microsoft OAuth access token
            token_resp = client.post(
                "https://login.live.com/oauth20_token.srf",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            if token_resp.status_code != 200:
                logger.warning("Microsoft OAuth token exchange error: %s", token_resp.text)
                return None
            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            # 2. Authenticate with Xbox Live User Auth
            xbl_resp = client.post(
                "https://user.auth.xboxlive.com/user/authenticate",
                json={
                    "RelyingParty": "http://auth.xboxlive.com",
                    "TokenType": "JWT",
                    "Properties": {
                        "AuthMethod": "RPS",
                        "SiteName": "user.auth.xboxlive.com",
                        "RpsTicket": f"d={access_token}",
                    },
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if xbl_resp.status_code != 200:
                logger.warning("Xbox Live user authentication error: %s", xbl_resp.text)
                return None
            xbl_data = xbl_resp.json()
            user_token = xbl_data.get("Token")
            user_hash = xbl_data.get("DisplayClaims", {}).get("xui", [{}])[0].get("uhs", "")

            # 3. Obtain XSTS token for Xbox Live services
            xsts_resp = client.post(
                "https://xsts.auth.xboxlive.com/xsts/authorize",
                json={
                    "RelyingParty": "http://xboxlive.com",
                    "TokenType": "JWT",
                    "Properties": {
                        "SandboxId": "RETAIL",
                        "UserTokens": [user_token],
                    },
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if xsts_resp.status_code != 200:
                logger.warning("Xbox Live XSTS authorization error: %s", xsts_resp.text)
                return None
            xsts_data = xsts_resp.json()
            xsts_token = xsts_data.get("Token")
            xui = xsts_data.get("DisplayClaims", {}).get("xui", [{}])[0]
            gamertag = xui.get("gtg", "Xbox Gamer")
            xuid = xui.get("xid", "")

            return {
                "status": "ok",
                "gamertag": gamertag,
                "xuid": xuid,
                "user_hash": user_hash,
                "xsts_token": xsts_token,
            }
    except Exception as exc:
        logger.warning("Xbox Live OAuth exchange error: %s", exc)
        return None


@app.get("/api/auth/xbox/login")
async def api_auth_xbox_login(request: Request, return_to: str = "/") -> Response:
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
    target_return = return_to if return_to.startswith("/") else "/"
    sep = "&" if "?" in target_return else "?"

    if not client_id:
        return RedirectResponse(f"{target_return}{sep}error=xbox_oauth_not_configured", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") and request.headers.get("x-forwarded-host"):
        base_url = f"{request.headers['x-forwarded-proto']}://{request.headers['x-forwarded-host']}".rstrip("/")

    redirect_uri = f"{base_url}/api/auth/xbox/callback"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "XboxLive.signin offline_access",
        "redirect_uri": redirect_uri,
        "state": target_return,
    }
    ms_auth_url = f"https://login.live.com/oauth20_authorize.srf?{urlencode(params)}"
    return RedirectResponse(url=ms_auth_url, status_code=303)


@app.get("/api/auth/xbox/callback")
async def api_auth_xbox_callback(
    request: Request,
    code: Optional[str] = None,
    error: Optional[str] = None,
    state: Optional[str] = None,
    return_to: Optional[str] = None,
) -> Response:
    target_return = (state or return_to or "/")
    if not target_return.startswith("/"):
        target_return = "/"
    sep = "&" if "?" in target_return else "?"

    if error or not code:
        return RedirectResponse(f"{target_return}{sep}error=xbox_auth_cancelled", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") and request.headers.get("x-forwarded-host"):
        base_url = f"{request.headers['x-forwarded-proto']}://{request.headers['x-forwarded-host']}".rstrip("/")
    redirect_uri = f"{base_url}/api/auth/xbox/callback"

    result = exchange_xbox_oauth_code(code, redirect_uri)
    if inspect.isawaitable(result):
        result = await result

    if not result or result.get("status") != "ok":
        return RedirectResponse(f"{target_return}{sep}error=xbox_auth_failed", status_code=303)

    user_id, _ = _get_active_user(request)
    gamertag = result.get("gamertag", "Xbox Gamer")
    if user_id:
        system_db.set_user_connections(user_id=user_id, xbl_api_key=result.get("xsts_token", ""))
        return RedirectResponse(f"{target_return}{sep}xbox_connected=1&gamertag={quote(gamertag)}", status_code=303)

    return RedirectResponse(f"/login?xbox_unlinked=1&gamertag={quote(gamertag)}", status_code=303)


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


# ==============================================================================
# Wishlist, Price Monitoring & Web Push REST APIs
# ==============================================================================

@app.get("/api/wishlist/candidates")
async def api_get_wishlist_candidates(
    q: str = Query(..., min_length=1),
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    res = await mcp_server.search_game_candidates(query=q)
    return res


@app.get("/api/wishlist")
async def api_list_wishlist(
    status: str = Query("active"),
    request: Request = None,
) -> List[Dict[str, Any]]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    return db.list_wishlist_items(status=status, db_path=str(user_db))


@app.post("/api/wishlist")
async def api_add_wishlist(
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    user_db = _get_active_user_db_path(user_id)
    db_path = str(user_db)

    item = db.add_wishlist_item(
        title=title,
        platform_targets=payload.get("platform_targets"),
        release_year=payload.get("release_year"),
        developer=payload.get("developer") or "",
        publisher=payload.get("publisher") or "",
        cover_url=payload.get("cover_url"),
        genres=payload.get("genres"),
        critic_score=payload.get("critic_score"),
        allow_smart_upgrade=payload.get("allow_smart_upgrade", True),
        notes=payload.get("notes") or "",
        added_via="web",
        force_track_owned=payload.get("force_track_owned", False),
        db_path=db_path,
    )

    try:
        await wishlist_sync.discover_skus_for_item(item["id"], db_path=db_path)
        refreshed = db.get_wishlist_item(item["id"], db_path=db_path)
        if refreshed:
            item = refreshed
    except Exception as exc:
        logger.warning("Auto-discovery failed on wishlist add for %s: %s", title, exc)

    return item


@app.get("/api/wishlist/deals")
async def api_get_wishlist_deals(
    platform: Optional[str] = Query(None),
    smart_upgrades_only: bool = Query(False),
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    db_path = str(user_db)
    deals = db.evaluate_wishlist_deals(db_path=db_path)
    if platform:
        fam = db.normalize_platform_family(platform)
        deals = [d for d in deals if d.get("platform_family") == fam or platform.lower() in (d.get("platform_label") or "").lower()]
    if smart_upgrades_only:
        deals = [d for d in deals if d.get("is_smart_upgrade")]
    return {
        "total_deals": len(deals),
        "smart_upgrades_count": sum(1 for d in deals if d.get("is_smart_upgrade")),
        "all_time_lows_count": sum(1 for d in deals if d.get("is_all_time_low")),
        "deals": deals,
    }


@app.get("/api/wishlist/{item_id}")
async def api_get_wishlist_item(
    item_id: int,
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    item = db.get_wishlist_item(item_id, db_path=str(user_db))
    if not item:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    return item


@app.patch("/api/wishlist/{item_id}")
async def api_update_wishlist_item(
    item_id: int,
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    updated = db.update_wishlist_item(
        item_id=item_id,
        allow_smart_upgrade=payload.get("allow_smart_upgrade"),
        preferred_edition_id=payload.get("preferred_edition_id"),
        status=payload.get("status"),
        notes=payload.get("notes"),
        db_path=str(user_db),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    return updated


@app.delete("/api/wishlist/{item_id}")
async def api_delete_wishlist_item(
    item_id: int,
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    success = db.delete_wishlist_item(item_id, db_path=str(user_db))
    if not success:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    return {"status": "deleted", "id": item_id}


@app.patch("/api/wishlist/{item_id}/platforms")
async def api_update_wishlist_platforms(
    item_id: int,
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    db_path = str(user_db)
    try:
        updated = db.update_wishlist_platforms(
            wishlist_item_id=item_id,
            platform_targets=payload.get("platform_targets"),
            remove_platforms=payload.get("remove_platforms"),
            allow_smart_upgrade=payload.get("allow_smart_upgrade"),
            force_track_owned=payload.get("force_track_owned", False),
            db_path=db_path,
        )
        try:
            await wishlist_sync.discover_skus_for_item(item_id, db_path=db_path)
            refreshed = db.get_wishlist_item(item_id, db_path=db_path)
            if refreshed:
                updated = refreshed
        except Exception as exc:
            logger.warning("Auto-discovery failed on wishlist platform update for %s: %s", item_id, exc)
        return updated
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/wishlist/{item_id}/price")
async def api_record_wishlist_price(
    item_id: int,
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    price_val = payload.get("price_usd")
    if price_val is None:
        raise HTTPException(status_code=400, detail="price_usd is required")
    platform = payload.get("platform") or "PC"
    user_db = _get_active_user_db_path(user_id)
    res = mcp_server.update_item_price(
        wishlist_item_id=item_id,
        price_usd=float(price_val),
        platform=platform,
        storefront=payload.get("storefront") or "manual",
        edition_name=payload.get("edition_name") or "Standard Edition",
        edition_tier=payload.get("edition_tier") or "base",
        regular_price_usd=payload.get("regular_price_usd"),
        store_url=payload.get("store_url") or "",
        store_sku_id=payload.get("store_sku_id"),
        db_path=str(user_db),
    )
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res


@app.post("/api/wishlist/{item_id}/purchase")
async def api_mark_wishlist_purchased(
    item_id: int,
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    platform = payload.get("platform") or "PC"
    user_db = _get_active_user_db_path(user_id)
    try:
        res = db.mark_wishlist_item_purchased(
            wishlist_item_id=item_id,
            platform=platform,
            edition_name=payload.get("edition_name"),
            price_paid_usd=payload.get("price_paid_usd"),
            completion_status=payload.get("completion_status") or "Plan to Play",
            db_path=str(user_db),
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/wishlist/{item_id}/history")
async def api_get_wishlist_history(
    item_id: int,
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    db_path = str(user_db)
    item = db.get_wishlist_item(item_id, db_path=db_path)
    if not item:
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    history_by_sku = {}
    for s in item.get("skus") or []:
        history_by_sku[s["id"]] = db.get_sku_price_history(s["id"], limit=50, db_path=db_path)
    return {
        "item": item,
        "price_history_by_sku": history_by_sku,
    }


@app.post("/api/wishlist/sync")
async def api_sync_wishlist_prices(request: Request = None) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_db = _get_active_user_db_path(user_id)
    db_path = str(user_db)
    res = await wishlist_sync.sync_wishlist_prices_for_db(db_path=db_path)
    return res


# --- Web Push VAPID Endpoints ---

@app.get("/api/push/vapid-public-key")
async def api_get_vapid_public_key() -> Dict[str, str]:
    keys = web_push.get_or_create_vapid_keys()
    return {"publicKey": keys["public_key"]}


_ALLOWED_PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    ".push.apple.com",
    "web.push.apple.com",
    ".notify.windows.com",
    "updates.push.services.mozilla.com",
    ".push.services.mozilla.com",
)


def is_valid_push_endpoint(endpoint: str) -> bool:
    if not endpoint:
        return False
    try:
        parsed = urlparse(endpoint)
    except Exception:
        return False

    if parsed.scheme.lower() != "https":
        return False

    host = (parsed.hostname or "").lower().strip()
    if not host:
        return False

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            return False
    except ValueError:
        pass

    allowed = False
    for suffix in _ALLOWED_PUSH_HOST_SUFFIXES:
        if suffix.startswith("."):
            if host.endswith(suffix):
                allowed = True
                break
        elif host == suffix:
            allowed = True
            break

    custom_hosts = os.environ.get("VERTEX_ALLOWED_PUSH_HOSTS", "").strip()
    if custom_hosts:
        for ch in custom_hosts.split(","):
            ch = ch.strip().lower()
            if ch and (host == ch or host.endswith("." + ch)):
                allowed = True
                break

    if not allowed:
        return False

    try:
        addr_info = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
                return False
    except Exception:
        pass

    return True


@app.post("/api/push/subscribe")
async def api_push_subscribe(
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="endpoint, keys.p256dh, and keys.auth are required")

    if not is_valid_push_endpoint(endpoint):
        raise HTTPException(
            status_code=400,
            detail="Invalid push service endpoint: must be a trusted public HTTPS push service",
        )

    sub = db.upsert_push_subscription(
        endpoint=endpoint,
        p256dh_key=p256dh,
        auth_key=auth,
        user_agent=request.headers.get("User-Agent", "") if request else "",
        device_label=payload.get("device_label") or "",
    )
    return {"status": "subscribed", "subscription": sub}


@app.delete("/api/push/subscribe")
async def api_push_unsubscribe(
    payload: Dict[str, Any],
    request: Request = None,
) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    endpoint = payload.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint is required")
    success = db.delete_push_subscription(endpoint)
    return {"status": "unsubscribed", "deleted": success}


@app.post("/api/push/test")
async def api_push_test(request: Request = None) -> Dict[str, Any]:
    user_id, _ = _get_active_user(request) if request else (None, None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    subs = db.list_active_push_subscriptions()
    if not subs:
        return {"status": "no_subscriptions", "message": "No active push subscriptions registered for this user"}

    keys = web_push.get_or_create_vapid_keys()
    claim_email = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@localhost.localdomain")
    test_payload = json.dumps({
        "title": "🔔 Vertex Web Push Connected",
        "body": "Wishlist and Smart Upgrade deal alerts will appear here when price drops occur!",
        "icon": "/static/icons/icon-192.png",
        "badge": "/static/icons/icon-192.png",
        "tag": "push-test",
        "data": {"url": "/#tab-wishlist"},
    })

    sent_count = 0
    for s in subs:
        sub_info = {
            "endpoint": s["endpoint"],
            "keys": {"p256dh": s["p256dh_key"], "auth": s["auth_key"]},
        }
        res = web_push._send_raw_push(
            subscription_info=sub_info,
            payload_json_str=test_payload,
            vapid_private_key=keys["private_key"],
            vapid_claims={"sub": claim_email},
        )
        if res.get("status") in ("sent", "mock_delivered"):
            sent_count += 1

    return {"status": "ok", "subscriptions_targeted": len(subs), "notifications_sent": sent_count}


from oauth_provider import get_oauth_routes
for oauth_route in get_oauth_routes():
    app.routes.append(oauth_route)


