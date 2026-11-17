"""Legacy original-UX compatibility HTTP router backed by v2 modular services."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import html
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import db
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import httpx
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from vertex.identity.models import ALLOWED_PAT_SCOPES, InviteRecord, UserScope
from vertex.infrastructure.errors import VertexError
from vertex.interfaces.http.auth import _set_session_cookie
from vertex.interfaces.http.deps import (
    AppServices,
    get_services,
    require_admin_scope,
    require_read_scope,
    require_session_scope,
    require_sync_scope,
    require_write_scope,
    resolve_client_ip,
)
from vertex.interfaces.http.wishlist import _dump_targets, _serialize_item

router = APIRouter(include_in_schema=False)

_VALID_REGISTRATION_POLICIES = frozenset({"disabled", "invite"})


def _ensure_user_lib_path(scope: UserScope, services: AppServices) -> str:
    services.db_manager.ensure_user_library(scope)
    return str(services.db_manager.user_db_path(scope))


def _sanitize_next_url(next_url: str) -> str:
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    if next_url.startswith("/login") or next_url.startswith("/logout"):
        return "/"
    return next_url


def _resolve_base_url(request: Request) -> str:
    base_url = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host")
    if proto and host:
        base_url = f"{proto}://{host}".rstrip("/")
    return base_url


def _iso_to_epoch(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _mask_configured(configured: bool) -> str | None:
    return "Configured (enter new to change)" if configured else None


def _public_role(role: str) -> str:
    return "admin" if role == "admin" else "user"


def _internal_role(role: str) -> str:
    cleaned = (role or "").strip().lower()
    if cleaned == "user":
        return "member"
    return cleaned


def _public_status(status_val: str) -> str:
    if status_val == "suspended":
        return "inactive"
    return status_val


def _internal_status(status_val: str) -> str:
    cleaned = (status_val or "").strip().lower()
    if cleaned == "inactive":
        return "suspended"
    return cleaned


def _get_reg_policy(services: AppServices) -> str:
    with services.db_manager.system_connection() as conn:
        row = conn.execute(
            "SELECT value FROM instance_settings WHERE key = 'registration_policy'"
        ).fetchone()
    if row is not None and row["value"] in _VALID_REGISTRATION_POLICIES:
        return str(row["value"])
    default_env = os.environ.get("VERTEX_REGISTRATION", "disabled").strip().lower()
    return default_env if default_env in _VALID_REGISTRATION_POLICIES else "disabled"


def _set_reg_policy(services: AppServices, policy: str) -> str:
    cleaned = (policy or "").strip().lower()
    if cleaned not in _VALID_REGISTRATION_POLICIES:
        raise HTTPException(
            status_code=400,
            detail="Only 'disabled' and 'invite' registration policies are supported.",
        )
    now_iso = datetime.now(timezone.utc).isoformat()
    with services.db_manager.system_connection(immediate=True) as conn:
        conn.execute(
            """
            INSERT INTO instance_settings (key, value, updated_at)
            VALUES ('registration_policy', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (cleaned, now_iso),
        )
    return cleaned


def _user_stats(scope: UserScope, services: AppServices) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    stats = db.get_library_stats(db_path=user_db_path)
    summary = services.identity.get_connections_summary(scope)
    stats["configured_sources"] = {
        "steam": bool(summary.steam_id and (summary.steam_api_key_configured or services.settings.steam_api_key)),
        "xbox": bool(summary.xbox_gamertag or summary.xbl_api_key_configured or services.settings.xbl_api_key),
        "psn": bool(summary.psn_configured),
        "gog": bool(summary.gog_token_configured or summary.gog_user_id or summary.gog_username),
    }
    stats["sync_interval_hours"] = max(1, round(services.settings.scheduled_sync_interval_seconds / 3600))
    stats["auth_enabled"] = True
    stats["is_admin"] = scope.role == "admin"
    stats["edition"] = "selfhosted"
    stats["registration_policy"] = _get_reg_policy(services)
    return stats


def _serialize_connections(scope: UserScope, services: AppServices) -> dict[str, Any]:
    summary = services.identity.get_connections_summary(scope)
    account = services.identity.get_account(scope)
    return {
        "steam_id": summary.steam_id or "",
        "has_steam_api_key": summary.steam_api_key_configured,
        "steam_api_key_masked": _mask_configured(summary.steam_api_key_configured),
        "xbox_gamertag": summary.xbox_gamertag or "",
        "xbox_xuid": summary.xbox_xuid or "",
        "has_xbl_api_key": summary.xbl_api_key_configured,
        "xbl_api_key_masked": _mask_configured(summary.xbl_api_key_configured),
        "has_psn_npsso": summary.psn_configured,
        "psn_npsso_masked": _mask_configured(summary.psn_configured),
        "gog_user_id": summary.gog_user_id or "",
        "gog_username": summary.gog_username or "",
        "has_gog_token": summary.gog_token_configured,
        "gog_token_masked": _mask_configured(summary.gog_token_configured),
        "ha_url": summary.ha_url or "",
        "ha_entity_id": summary.ha_entity_id or "",
        "has_ha_token": summary.ha_token_configured,
        "ha_token_masked": _mask_configured(summary.ha_token_configured),
        "onboarding_completed": bool(account.onboarding_completed),
    }


def _normalize_provider_name(raw: str | None) -> str:
    val = (raw or "").strip().lower()
    if val in ("xbl", "xbox"):
        return "xbox"
    if val in ("playstation", "psn", "ps4", "ps5"):
        return "psn"
    if val in ("gog", "gog galaxy", "pc (gog)"):
        return "gog"
    if val in ("ha", "homeassistant", "home_assistant"):
        return "homeassistant"
    return val or "steam"


def _render_login_html(
    *,
    next_url: str = "/",
    error_msg: str = "",
    default_username: str = "",
    bootstrap_open: bool = False,
    reg_policy: str = "disabled",
    status_code: int = 200,
) -> HTMLResponse:
    safe_next = html.escape(_sanitize_next_url(next_url))
    safe_user = html.escape(default_username)
    error_html = (
        f'<div class="auth-alert auth-error" role="alert">{html.escape(error_msg)}</div>'
        if error_msg
        else ""
    )
    bootstrap_html = (
        '<div class="auth-alert auth-info">First-run setup: enter a username and password (10+ characters recommended, min 8) to create the initial administrator account.</div>'
        if bootstrap_open
        else ""
    )
    signup_html = (
        '<div class="auth-footer"><span>Need an account?</span> <a href="/signup">Create account</a></div>'
        if (bootstrap_open or reg_policy != "disabled")
        else ""
    )
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#0b0f19">
  <title>Sign In | Vertex Game Nexus</title>
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <style>
    :root {{
      color-scheme: dark;
      --bg-page: #0b0f19;
      --bg-card: #131b2e;
      --bg-input: #0b1120;
      --border-subtle: #26334d;
      --text-primary: #f1f5f9;
      --text-secondary: #94a3b8;
      --accent: #38bdf8;
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100dvh;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background:
        radial-gradient(circle at 20% 15%, rgba(56, 189, 248, 0.12), transparent 45%),
        radial-gradient(circle at 80% 85%, rgba(139, 92, 246, 0.12), transparent 45%),
        var(--bg-page);
      color: var(--text-primary);
      display: flex; flex-direction: column; justify-content: center; align-items: center;
      padding: 1.25rem;
    }}
    .login-card {{
      width: 100%; max-width: 410px;
      background: rgba(19, 27, 46, 0.94); backdrop-filter: blur(16px);
      border: 1px solid var(--border-subtle); border-radius: 16px;
      padding: 2.25rem 1.85rem; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.55);
    }}
    .brand-header {{ text-align: center; margin-bottom: 1.6rem; }}
    .brand-logo {{ width: 58px; height: 58px; border-radius: 14px; margin-bottom: 0.75rem; }}
    .brand-header h1 {{
      margin: 0; font-size: 1.7rem; font-weight: 800;
      background: linear-gradient(135deg, #f8fafc 0%, #38bdf8 55%, #a855f7 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .brand-header p {{ margin: 0.35rem 0 0; font-size: 0.85rem; color: var(--text-secondary); }}
    .auth-alert {{ padding: 0.75rem 0.9rem; border-radius: 10px; font-size: 0.85rem; margin-bottom: 1.1rem; }}
    .auth-error {{ background: rgba(239, 68, 68, 0.14); border: 1px solid rgba(239, 68, 68, 0.45); color: #fca5a5; }}
    .auth-info {{ background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.4); color: #bae6fd; }}
    .field-group {{ margin-bottom: 1rem; }}
    .field-group label {{ display: block; font-size: 0.82rem; font-weight: 600; color: #cbd5e1; margin-bottom: 0.35rem; }}
    .field-group input {{
      width: 100%; padding: 0.75rem 0.9rem; border-radius: 10px;
      border: 1px solid #334155; background: var(--bg-input); color: var(--text-primary); font-size: 0.98rem;
    }}
    .field-group input:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2); }}
    .btn-submit {{
      width: 100%; padding: 0.85rem 1rem; border: none; border-radius: 10px;
      font-size: 0.98rem; font-weight: 700; color: #0b0f19; cursor: pointer;
      background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
      margin-top: 0.35rem;
    }}
    .auth-footer {{ margin-top: 1rem; font-size: 0.85rem; text-align: center; color: var(--text-secondary); }}
    .auth-footer a {{ color: #38bdf8; text-decoration: none; font-weight: 600; margin-left: 0.25rem; }}
  </style>
</head>
<body>
  <main class="login-card">
    <div class="brand-header">
      <img src="/static/vertex_logo.svg" alt="Vertex" class="brand-logo">
      <h1>Vertex</h1>
      <p>Self-Hosted Game Library Nexus</p>
    </div>
    {bootstrap_html}
    {error_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{safe_next}">
      <div class="field-group">
        <label for="login-username">Username</label>
        <input id="login-username" type="text" name="username" value="{safe_user}" autocomplete="username" required autofocus>
      </div>
      <div class="field-group">
        <label for="login-password">Password</label>
        <input id="login-password" type="password" name="password" autocomplete="current-password" required>
      </div>
      <button type="submit" class="btn-submit">{'Create Admin & Sign In' if bootstrap_open else 'Sign In'}</button>
    </form>
    {signup_html}
  </main>
</body>
</html>"""
    return HTMLResponse(page, status_code=status_code)


def _render_signup_html(
    *,
    policy: str,
    bootstrap_open: bool,
    error_msg: str = "",
    default_username: str = "",
    default_invite: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    if not bootstrap_open and policy == "disabled" and not default_invite:
        closed_page = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Registration Closed | Vertex</title>
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <style>
    body { margin: 0; min-height: 100dvh; font-family: system-ui, -apple-system, sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; padding: 1.25rem; }
    .card { max-width: 400px; width: 100%; background: #131b2e; border: 1px solid #26334d; border-radius: 16px; padding: 2rem; text-align: center; }
    a { color: #38bdf8; text-decoration: none; font-weight: 600; }
  </style>
</head>
<body>
  <div class="card">
    <img src="/static/vertex_logo.svg" alt="Vertex" width="56" height="56" style="margin-bottom: 1rem;">
    <h1>Registration Closed</h1>
    <p style="color:#94a3b8;">Public account registration is disabled. Request an invite link from your administrator.</p>
    <a href="/login">&larr; Return to Sign In</a>
  </div>
</body>
</html>"""
        return HTMLResponse(closed_page, status_code=403)

    safe_user = html.escape(default_username)
    safe_invite = html.escape(default_invite)
    error_html = (
        f'<div class="auth-error" role="alert">{html.escape(error_msg)}</div>'
        if error_msg
        else ""
    )
    invite_field = (
        ""
        if bootstrap_open
        else f"""<div class="field-group">
        <label for="reg-invite">Invite Code</label>
        <input id="reg-invite" type="text" name="invite_code" value="{safe_invite}" placeholder="Enter invitation code" autocomplete="off" required>
      </div>"""
    )
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#0b0f19">
  <title>Create Account | Vertex Game Nexus</title>
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <style>
    :root {{ color-scheme: dark; --bg-page: #0b0f19; --bg-input: #0b1120; --border-subtle: #26334d; --text-primary: #f1f5f9; --text-secondary: #94a3b8; }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100dvh; font-family: system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at 20% 15%, rgba(56, 189, 248, 0.12), transparent 45%), var(--bg-page);
      color: var(--text-primary); display: flex; justify-content: center; align-items: center; padding: 1.25rem;
    }}
    .signup-card {{ width: 100%; max-width: 420px; background: rgba(19, 27, 46, 0.94); border: 1px solid var(--border-subtle); border-radius: 16px; padding: 2.15rem 1.85rem; }}
    .brand-header {{ text-align: center; margin-bottom: 1.4rem; }}
    .brand-header h1 {{ margin: 0; font-size: 1.55rem; }}
    .brand-header p {{ margin: 0.35rem 0 0; font-size: 0.85rem; color: var(--text-secondary); }}
    .auth-error {{ background: rgba(239, 68, 68, 0.14); border: 1px solid rgba(239, 68, 68, 0.45); color: #fca5a5; padding: 0.75rem 0.9rem; border-radius: 10px; font-size: 0.85rem; margin-bottom: 1rem; }}
    .field-group {{ margin-bottom: 1rem; }}
    .field-group label {{ display: block; font-size: 0.82rem; font-weight: 600; color: #cbd5e1; margin-bottom: 0.35rem; }}
    .field-group input {{ width: 100%; padding: 0.75rem 0.9rem; border-radius: 10px; border: 1px solid #334155; background: var(--bg-input); color: var(--text-primary); font-size: 0.95rem; }}
    .btn-submit {{ width: 100%; padding: 0.85rem 1rem; border: none; border-radius: 10px; font-size: 0.95rem; font-weight: 700; color: #0b0f19; cursor: pointer; background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%); }}
    .hp-field {{ position: absolute; left: -9999px; opacity: 0; pointer-events: none; }}
    .auth-footer {{ margin-top: 1rem; font-size: 0.84rem; text-align: center; color: var(--text-secondary); }}
    .auth-footer a {{ color: #38bdf8; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <main class="signup-card">
    <div class="brand-header">
      <img src="/static/vertex_logo.svg" alt="Vertex" width="52" height="52" style="margin-bottom:0.65rem;">
      <h1>{'Create Admin Account' if bootstrap_open else 'Create Account'}</h1>
      <p>Join your self-hosted Vertex library</p>
    </div>
    {error_html}
    <form method="post" action="/signup">
      <div class="hp-field" aria-hidden="true"><input type="text" name="website" tabindex="-1" autocomplete="off"></div>
      {invite_field}
      <div class="field-group">
        <label for="reg-username">Username</label>
        <input id="reg-username" type="text" name="username" value="{safe_user}" autocomplete="username" required>
      </div>
      <div class="field-group">
        <label for="reg-password">Password</label>
        <input id="reg-password" type="password" name="password" autocomplete="new-password" minlength="8" required>
      </div>
      <div class="field-group">
        <label for="reg-password-confirm">Confirm Password</label>
        <input id="reg-password-confirm" type="password" name="confirm_password" autocomplete="new-password" minlength="8" required>
      </div>
      <button type="submit" class="btn-submit">Create Account</button>
    </form>
    <div class="auth-footer">Already have an account? <a href="/login">Sign In</a></div>
  </main>
</body>
</html>"""
    return HTMLResponse(page, status_code=status_code)


# ── Web HTML Authentication (/login, /logout, /signup) ───────────────────────


@router.get("/login")
def login_get(
    request: Request,
    next: str = Query(default="/"),
    services: AppServices = Depends(get_services),
) -> Response:
    token = request.cookies.get(services.settings.cookie_name)
    if services.identity.resolve_session(token) is not None:
        return RedirectResponse(url=_sanitize_next_url(next), status_code=303)
    return _render_login_html(
        next_url=next,
        bootstrap_open=services.identity.is_bootstrap_open(),
        reg_policy=_get_reg_policy(services),
    )


@router.post("/login")
async def login_post(
    request: Request,
    services: AppServices = Depends(get_services),
) -> Response:
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    next_url = _sanitize_next_url(str(form.get("next") or "/"))
    bootstrap_open = services.identity.is_bootstrap_open()
    reg_policy = _get_reg_policy(services)

    if not username or not password:
        return _render_login_html(
            next_url=next_url,
            error_msg="Username and password are required.",
            default_username=username,
            bootstrap_open=bootstrap_open,
            reg_policy=reg_policy,
            status_code=400,
        )

    try:
        if bootstrap_open:
            _acc, token = services.identity.bootstrap_admin(
                username=username,
                password=password,
                user_agent=request.headers.get("user-agent", ""),
            )
        else:
            _scope, token = services.identity.login(
                username=username,
                password=password,
                client_ip=resolve_client_ip(request, services.settings),
                user_agent=request.headers.get("user-agent", ""),
            )
    except VertexError as exc:
        return _render_login_html(
            next_url=next_url,
            error_msg=str(exc),
            default_username=username,
            bootstrap_open=bootstrap_open,
            reg_policy=reg_policy,
            status_code=401,
        )

    resp = RedirectResponse(url=next_url, status_code=303)
    _set_session_cookie(resp, services, token)
    return resp


@router.get("/logout")
@router.post("/logout")
def logout_web(
    request: Request,
    services: AppServices = Depends(get_services),
) -> Response:
    token = request.cookies.get(services.settings.cookie_name)
    services.identity.logout_session(token)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(
        services.settings.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=services.settings.cookie_secure,
    )
    resp.headers["Clear-Site-Data"] = '"cache"'
    return resp


@router.get("/signup")
def signup_get(
    request: Request,
    invite: str = Query(default=""),
    services: AppServices = Depends(get_services),
) -> Response:
    policy = _get_reg_policy(services)
    clean_invite = invite.strip()
    if policy == "disabled" and not clean_invite:
        raise HTTPException(status_code=404, detail="Not Found")
    return _render_signup_html(
        policy=policy,
        bootstrap_open=services.identity.is_bootstrap_open(),
        default_invite=clean_invite,
    )


@router.post("/signup")
async def signup_post(
    request: Request,
    services: AppServices = Depends(get_services),
) -> Response:
    form = await request.form()
    if str(form.get("website") or "").strip():
        return RedirectResponse(url="/login", status_code=303)

    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    confirm = str(form.get("confirm_password") or password)
    invite_code = str(form.get("invite_code") or "").strip()
    bootstrap_open = services.identity.is_bootstrap_open()
    policy = _get_reg_policy(services)
    if policy == "disabled" and not invite_code:
        raise HTTPException(status_code=404, detail="Not Found")

    if password != confirm:
        return _render_signup_html(
            policy=policy,
            bootstrap_open=bootstrap_open,
            error_msg="Passwords do not match.",
            default_username=username,
            default_invite=invite_code,
            status_code=400,
        )

    try:
        if bootstrap_open:
            _acc, token = services.identity.bootstrap_admin(
                username=username,
                password=password,
                user_agent=request.headers.get("user-agent", ""),
            )
        else:
            if not invite_code:
                return _render_signup_html(
                    policy=policy,
                    bootstrap_open=False,
                    error_msg="An invitation code is required to register.",
                    default_username=username,
                    status_code=400,
                )
            _acc, token = services.identity.accept_invite(
                invite_code=invite_code,
                username=username,
                password=password,
                user_agent=request.headers.get("user-agent", ""),
            )
    except VertexError as exc:
        return _render_signup_html(
            policy=policy,
            bootstrap_open=bootstrap_open,
            error_msg=str(exc),
            default_username=username,
            default_invite=invite_code,
            status_code=400,
        )

    resp = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(resp, services, token)
    return resp


# ── Direct Legacy Endpoints (/api/user/*, /api/search-external, etc.) ────────


class LegacyConnectionUpdateBody(BaseModel):
    steam_id: str | None = None
    steam_api_key: str | None = None
    clear_steam_api_key: bool = False
    xbox_gamertag: str | None = None
    xbox_xuid: str | None = None
    xbl_api_key: str | None = None
    clear_xbl_api_key: bool = False
    xbl_watcher_api_key: str | None = None
    clear_xbl_watcher_api_key: bool = False
    psn_npsso: str | None = None
    clear_psn_npsso: bool = False
    gog_user_id: str | None = None
    gog_username: str | None = None
    gog_access_token: str | None = None
    gog_token: str | None = None
    gog_refresh_token: str | None = None
    clear_gog_tokens: bool = False
    ha_url: str | None = None
    ha_token: str | None = None
    clear_ha_token: bool = False
    ha_entity_id: str | None = None


class LegacyConnectionTestBody(BaseModel):
    platform: str
    credential: str = ""
    secondary_credential: str | None = None


class LegacyMcpTokenCreateBody(BaseModel):
    label: str | None = None
    name: str | None = None
    scopes: list[str] | None = None
    ttl_days: int | None = None


@router.get("/api/user/connections")
def legacy_get_user_connections(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    return _serialize_connections(scope, services)


@router.post("/api/user/connections")
def legacy_update_user_connections(
    body: LegacyConnectionUpdateBody,
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    gog_access = body.gog_access_token if body.gog_access_token is not None else body.gog_token
    services.identity.update_connections(
        scope,
        steam_id=body.steam_id,
        steam_api_key=body.steam_api_key,
        clear_steam_api_key=body.clear_steam_api_key,
        xbox_gamertag=body.xbox_gamertag,
        xbox_xuid=body.xbox_xuid,
        xbl_api_key=body.xbl_api_key,
        clear_xbl_api_key=body.clear_xbl_api_key,
        xbl_watcher_api_key=body.xbl_watcher_api_key,
        clear_xbl_watcher_api_key=body.clear_xbl_watcher_api_key,
        psn_npsso=body.psn_npsso,
        clear_psn_npsso=body.clear_psn_npsso,
        gog_user_id=body.gog_user_id,
        gog_username=body.gog_username,
        gog_access_token=gog_access,
        gog_refresh_token=body.gog_refresh_token,
        clear_gog_tokens=body.clear_gog_tokens,
        ha_url=body.ha_url,
        ha_token=body.ha_token,
        clear_ha_token=body.clear_ha_token,
        ha_entity_id=body.ha_entity_id,
    )
    return {"status": "ok", **_serialize_connections(scope, services)}


@router.post("/api/user/connections/test")
async def legacy_test_user_connection(
    body: LegacyConnectionTestBody,
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    provider = _normalize_provider_name(body.platform)
    raw = await services.platform_sync.verify_connection(
        scope,
        provider=provider,
        credential=body.credential,
        secondary_credential=body.secondary_credential,
    )
    return {
        **raw,
        "status": str(raw.get("status") or "error"),
        "platform": str(raw.get("platform") or provider),
        "message": str(raw.get("message") or ""),
    }


@router.post("/api/user/onboarding/complete")
def legacy_complete_onboarding(
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    account = services.identity.complete_onboarding(scope)
    return {"status": "ok", "onboarding_completed": bool(account.onboarding_completed)}


@router.get("/api/user/mcp")
def legacy_get_user_mcp(
    request: Request,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    pats = services.identity.list_pats(scope)
    tokens = [
        {
            "access_token": t.id,
            "id": t.id,
            "client_id": t.name,
            "token_preview": f"{t.token_prefix}...",
            "scopes": list(t.scopes),
            "created_at": _iso_to_epoch(t.created_at),
            "last_used_at": _iso_to_epoch(t.last_used_at),
        }
        for t in pats
        if not t.revoked_at
    ]
    return {
        "mcp_endpoint": f"{_resolve_base_url(request)}/mcp",
        "tokens": tokens,
    }


@router.post("/api/user/mcp/tokens")
def legacy_create_user_mcp_token(
    body: LegacyMcpTokenCreateBody,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    token_name = (body.label or body.name or "Personal Access Token").strip()
    created = services.identity.create_pat(
        scope,
        name=token_name,
        scopes=body.scopes or sorted(ALLOWED_PAT_SCOPES),
        ttl_days=body.ttl_days,
    )
    rec = created.record
    return {
        "status": "ok",
        "token": created.token,
        "access_token": rec.id,
        "id": rec.id,
        "client_id": rec.name,
        "token_preview": f"{rec.token_prefix}...",
        "created_at": _iso_to_epoch(rec.created_at),
    }


@router.delete("/api/user/mcp/tokens/{token_id}")
def legacy_revoke_user_mcp_token(
    token_id: str,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    services.identity.revoke_pat(scope, token_id)
    return {"status": "ok", "revoked_id": token_id}


@router.get("/api/user/library/export")
def legacy_export_library(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> FileResponse:
    user_db_path = Path(_ensure_user_lib_path(scope, services))
    tmp_export = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp_export.name
    tmp_export.close()
    try:
        with sqlite3.connect(str(user_db_path)) as src, sqlite3.connect(tmp_path) as dst:
            src.backup(dst)
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=f"Failed to export library database: {exc}")

    clean_user = re.sub(r"[^a-zA-Z0-9_-]", "", scope.username or "user") or "user"
    filename = f"games-{clean_user}.db"
    return FileResponse(
        path=tmp_path,
        filename=filename,
        media_type="application/x-sqlite3",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
        },
        background=BackgroundTask(os.remove, tmp_path),
    )


@router.post("/api/user/library/import")
async def legacy_import_library(
    file: UploadFile = File(...),
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    filename = (file.filename or "").lower()
    if not any(filename.endswith(ext) for ext in (".db", ".sqlite", ".sqlite3")):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file must be a SQLite database (.db, .sqlite, .sqlite3)",
        )
    raw_bytes = await file.read(services.settings.max_import_bytes + 1)
    if len(raw_bytes) > services.settings.max_import_bytes:
        raise HTTPException(status_code=400, detail="Database file exceeds size limit")
    rep = services.portability.import_legacy_sqlite_bytes(
        scope,
        raw_bytes,
        mode="replace",
        dry_run=False,
    )
    payload = asdict(rep)
    payload["warnings"] = list(rep.warnings)
    return {
        "status": "ok",
        "imported_games": rep.games_created + rep.games_updated,
        **payload,
    }


@router.get("/api/search-external")
async def legacy_search_external(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=15, ge=1, le=50),
    _scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    results = await services.catalog.search_catalog(q, limit=limit)
    return {
        "results": [
            {
                **asdict(item),
                "genres": list(item.genres),
            }
            for item in results
        ]
    }


@router.post("/api/enrich-metadata")
async def legacy_enrich_metadata(
    limit: int = Query(default=500, ge=1, le=2000),
    force: bool = Query(default=False),
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    report = await services.catalog.enrich_library(scope, services.library, limit=limit, force=force)
    return {
        "status": "completed",
        "result": asdict(report),
        "stats": _user_stats(scope, services),
    }


@router.get("/api/ui/admin/settings")
def legacy_get_admin_settings(
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    settings_map = services.identity.get_instance_settings(scope)
    return {
        **settings_map,
        "registration_policy": _get_reg_policy(services),
    }


@router.put("/api/ui/admin/settings")
def legacy_put_admin_settings(
    body: dict[str, str],
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    remaining: dict[str, str] = {}
    policy = _get_reg_policy(services)
    for k, v in body.items():
        if k == "registration_policy":
            policy = _set_reg_policy(services, v)
        else:
            remaining[k] = str(v)
    if remaining:
        services.identity.update_instance_settings(scope, remaining)
    settings_map = services.identity.get_instance_settings(scope)
    return {
        **settings_map,
        "registration_policy": policy,
    }


def validate_steam_openid_response(query_params: dict[str, str]) -> str | None:
    if query_params.get("openid.mode", "") in ("cancel", "error"):
        return None
    payload = {k: v for k, v in query_params.items() if k.startswith("openid.")}
    payload["openid.mode"] = "check_authentication"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post("https://steamcommunity.com/openid/login", data=payload)
            if resp.status_code == 200 and "is_valid:true" in resp.text:
                claimed = query_params.get("openid.claimed_id", "")
                match = re.search(r"https?://steamcommunity\.com/openid/id/(\d+)", claimed)
                if match:
                    return match.group(1)
    except Exception:
        return None
    return None


@router.get("/api/auth/steam/login")
def legacy_steam_openid_login(request: Request, return_to: str = "/") -> RedirectResponse:
    base_url = _resolve_base_url(request)
    safe_return = _sanitize_next_url(return_to)
    return_url = f"{base_url}/api/auth/steam/callback?return_to={quote(safe_return)}"
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_url,
        "openid.realm": f"{base_url}/",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return RedirectResponse(f"https://steamcommunity.com/openid/login?{urlencode(params)}", status_code=303)


@router.get("/api/auth/steam/callback")
def legacy_steam_openid_callback(
    request: Request,
    return_to: str = "/",
    services: AppServices = Depends(get_services),
) -> RedirectResponse:
    target = _sanitize_next_url(return_to)
    sep = "&" if "?" in target else "?"
    if request.query_params.get("openid.mode") in ("cancel", "error"):
        return RedirectResponse(f"{target}{sep}cancelled=1", status_code=303)
    steam_id = validate_steam_openid_response(dict(request.query_params))
    if not steam_id:
        return RedirectResponse(f"{target}{sep}error=steam_verification_failed", status_code=303)
    cookie_token = request.cookies.get(services.settings.cookie_name)
    scope = services.identity.resolve_session(cookie_token)
    if scope is not None:
        services.identity.update_connections(scope, steam_id=steam_id)
        return RedirectResponse(f"{target}{sep}steam_connected=1&steam_id={steam_id}", status_code=303)
    return RedirectResponse(f"/login?steam_id={steam_id}&steam_unlinked=1", status_code=303)


# ── UI-Rewritten Legacy Compatibility Routes (/api/ui/*) ─────────────────────


class LegacyGameCreateBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    platform: str = Field(..., min_length=1, max_length=100)
    completion_status: str = "Not Played"
    ownership_status: str = "Owned"
    is_game_pass: bool = False
    is_play_anywhere: bool = False
    playtime_hours: float = 0.0
    user_rating: int | None = None
    critic_score: int | None = None
    release_year: int | None = None
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    notes: str = ""
    favorite: bool = False


class LegacyGameUpdateBody(BaseModel):
    title: str | None = None
    platform: str | None = None
    completion_status: str | None = None
    status_locked: bool | None = None
    ownership_status: str | None = None
    ownership_locked: bool | None = None
    is_game_pass: bool | None = None
    is_play_anywhere: bool | None = None
    playtime_hours: float | None = None
    genres: list[str] | None = None
    tags: list[str] | None = None
    user_rating: int | None = None
    critic_score: int | None = None
    release_year: int | None = None
    cover_url: str | None = None
    notes: str | None = None
    favorite: bool | None = None
    playtime_prompt_muted: bool | None = None
    achievements_unlocked: int | None = None
    achievements_total: int | None = None


class LegacySessionPatchBody(BaseModel):
    notes: str | None = None
    duration_minutes: int | None = None
    started_at: str | None = None
    ended_at: str | None = None


class LegacyReconcileConfirmBody(BaseModel):
    game_ids: list[int] = Field(..., min_length=2)
    primary_id: int | None = None
    custom_title: str | None = None
    completion_status: str | None = None


class LegacyReconcileDismissBody(BaseModel):
    game_ids: list[int] = Field(..., min_length=2)


class LegacyPromptResolveBody(BaseModel):
    action: str = Field(..., pattern="^(set_playing|mute|dismiss)$")


class LegacyTimezoneBody(BaseModel):
    timezone: str = "auto"
    timezone_eras: list[dict[str, Any]] = Field(default_factory=list)


class LegacyPasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class LegacyAdminUserCreateBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    role: str = "user"
    password: str | None = None


class LegacyAdminUserPatchBody(BaseModel):
    role: str | None = None
    status: str | None = None
    status_reason: str | None = None


class LegacyAdminResetPasswordBody(BaseModel):
    new_password: str | None = None


class LegacyAdminInviteCreateBody(BaseModel):
    role: str = "member"
    note: str = ""
    ttl_days: int | None = None
    ttl_hours: int | None = None


class LegacyHaImportCsvBody(BaseModel):
    csv_content: str = Field(..., min_length=1)
    platform: str = "Xbox"
    max_gap_seconds: int = 1800


class LegacyHaSyncBody(BaseModel):
    ha_url: str | None = None
    ha_token: str | None = None
    entity_id: str | None = None
    platform: str = "Xbox"
    days_back: int = 7
    max_gap_seconds: int = 1800


class LegacyWishlistCreateBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    release_year: int | None = None
    developer: str = ""
    publisher: str = ""
    cover_url: str | None = None
    genres: list[str] = Field(default_factory=list)
    critic_score: int | None = None
    platform_targets: list[Any] = Field(default_factory=list)
    allow_smart_upgrade: bool = True
    preferred_edition_id: int | None = None
    notes: str = ""
    force_track_owned: bool = False


class LegacyWishlistUpdateBody(BaseModel):
    allow_smart_upgrade: bool | None = None
    preferred_edition_id: int | None = None
    status: str | None = None
    notes: str | None = None


class LegacyWishlistPlatformsBody(BaseModel):
    platform_targets: list[Any] = Field(default_factory=list)
    remove_platforms: list[str] = Field(default_factory=list)
    allow_smart_upgrade: bool | None = None
    force_track_owned: bool | None = None


class LegacyWishlistPurchaseBody(BaseModel):
    platform: str | None = None
    edition_name: str | None = None
    price_paid_usd: float | None = None
    completion_status: str = "Plan to Play"


class LegacyPushSubscribeBody(BaseModel):
    endpoint: str
    keys: dict[str, str]
    device_label: str = "Web Browser"


class LegacyPushUnsubscribeBody(BaseModel):
    endpoint: str


@router.get("/api/ui/stats")
def ui_get_stats(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    return _user_stats(scope, services)


@router.get("/api/ui/games")
def ui_list_games(
    search: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    genre: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    ownership: str | None = Query(default=None),
    is_game_pass: bool | None = Query(default=None),
    is_play_anywhere: bool | None = Query(default=None),
    favorite_only: bool = Query(default=False),
    sort_by: str = Query(default="playtime_desc"),
    limit: int = Query(default=500, ge=1, le=5000),
    include_unconsolidated: bool = Query(default=False),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    games = db.query_games(
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        ownership=ownership,
        is_game_pass=is_game_pass,
        is_play_anywhere=is_play_anywhere,
        favorite_only=favorite_only,
        sort_by=sort_by,
        limit=limit,
        include_unconsolidated=include_unconsolidated,
        db_path=user_db_path,
    )
    return {"count": len(games), "games": games}


@router.post("/api/ui/games")
def ui_create_game(
    body: LegacyGameCreateBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    game = db.add_manual_game(
        title=body.title.strip(),
        platform=body.platform.strip(),
        completion_status=body.completion_status,
        ownership_status=body.ownership_status,
        is_game_pass=body.is_game_pass,
        is_play_anywhere=body.is_play_anywhere,
        playtime_hours=body.playtime_hours,
        user_rating=body.user_rating,
        critic_score=body.critic_score,
        release_year=body.release_year,
        genres=body.genres,
        tags=body.tags,
        cover_url=body.cover_url,
        notes=body.notes,
        favorite=body.favorite,
        db_path=user_db_path,
    )
    return {"status": "ok", "game": game}


@router.patch("/api/ui/games/{game_id}")
def ui_update_game(
    game_id: int,
    body: LegacyGameUpdateBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    existing = db.get_game_by_id(game_id, db_path=user_db_path)
    if not existing:
        raise HTTPException(status_code=404, detail="Game not found")
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    updated = db.update_game(game_id, updates, db_path=user_db_path)
    return {"status": "ok", "game": updated}


@router.delete("/api/ui/games/{game_id}")
def ui_delete_game(
    game_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    deleted = db.delete_game(game_id, db_path=user_db_path)
    if not deleted:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"status": "ok", "deleted_id": game_id}


@router.get("/api/ui/games/{game_id}/achievements")
async def ui_get_game_achievements(
    game_id: int,
    auto_sync: bool = Query(default=True),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    if auto_sync:
        try:
            res = await services.platform_sync.sync_game_achievements_on_demand(
                scope,
                game_id,
                force=False,
            )
            return asdict(res["detail"])
        except Exception:
            pass
    return asdict(services.library.get_game_achievements(scope, game_id))


@router.post("/api/ui/games/{game_id}/achievements/sync")
async def ui_sync_game_achievements(
    game_id: int,
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    res = await services.platform_sync.sync_game_achievements_on_demand(
        scope,
        game_id,
        force=True,
    )
    return {
        "status": "ok",
        "game": db.get_game_by_id(game_id, db_path=user_db_path),
        "achievements": asdict(res["detail"]),
        "synced_copies": res["synced_copies"],
        "synced_providers": res["synced_providers"],
        "warnings": res["warnings"],
    }


@router.get("/api/ui/analytics")
def ui_get_analytics(
    search: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    genre: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    ownership: str | None = Query(default=None),
    is_game_pass: bool | None = Query(default=None),
    is_play_anywhere: bool | None = Query(default=None),
    favorite_only: bool = Query(default=False),
    client_timezone: str | None = Query(default=None),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    account = services.identity.get_account(scope)
    tz = account.timezone or "auto"
    if tz == "auto" and client_timezone:
        tz = client_timezone.strip()
    return db.get_habits_analytics(
        search=search,
        platform=platform,
        status=status,
        genre=genre,
        tag=tag,
        ownership=ownership,
        is_game_pass=is_game_pass,
        is_play_anywhere=is_play_anywhere,
        favorite_only=favorite_only,
        user_timezone=tz,
        user_timezone_eras=list(account.timezone_eras or []),
        db_path=user_db_path,
    )


@router.get("/api/ui/analytics/sessions")
def ui_get_sessions_analytics(
    days: int = Query(default=365, ge=1, le=730),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    title: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    client_timezone: str | None = Query(default=None),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    account = services.identity.get_account(scope)
    tz = account.timezone or "auto"
    if tz == "auto" and client_timezone:
        tz = client_timezone.strip()
    return db.get_sessions_analytics(
        days=days,
        limit=limit,
        offset=offset,
        title=title,
        platform=platform,
        user_timezone=tz,
        user_timezone_eras=list(account.timezone_eras or []),
        db_path=user_db_path,
    )


@router.get("/api/ui/sessions")
def ui_get_play_sessions(
    game_id: int | None = Query(default=None),
    platform: str | None = Query(default=None),
    days: int = Query(default=30, ge=0, le=3650),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    return db.get_play_sessions(
        game_id=game_id,
        platform=platform,
        days=days,
        limit=limit,
        offset=offset,
        db_path=user_db_path,
    )


@router.get("/api/ui/sessions/stats")
def ui_get_play_sessions_stats(
    days: int = Query(default=30, ge=1, le=3650),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    return db.get_play_sessions_stats(days=days, db_path=user_db_path)


@router.patch("/api/ui/sessions/{session_id}")
def ui_patch_play_session(
    session_id: int,
    body: LegacySessionPatchBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    if body.duration_minutes is not None and body.duration_minutes <= 0:
        raise HTTPException(status_code=400, detail="duration_minutes must be > 0")
    try:
        updated = db.update_play_session(
            session_id=session_id,
            notes=body.notes,
            duration_minutes=body.duration_minutes,
            started_at=body.started_at,
            ended_at=body.ended_at,
            db_path=user_db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Play session not found")
    return updated


@router.delete("/api/ui/sessions/{session_id}")
def ui_delete_play_session(
    session_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    if not db.delete_play_session(session_id, db_path=user_db_path):
        raise HTTPException(status_code=404, detail="Play session not found")
    return {"status": "ok", "deleted_id": session_id}


@router.get("/api/ui/reconcile/candidates")
def ui_reconcile_candidates(
    cross_platform_only: bool = Query(default=False),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    cands = db.get_reconcile_candidates(
        cross_platform_only=cross_platform_only,
        db_path=user_db_path,
    )
    return {"count": len(cands), "candidates": cands}


@router.post("/api/ui/reconcile/confirm")
def ui_reconcile_confirm(
    body: LegacyReconcileConfirmBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    try:
        merged = db.reconcile_games(
            game_ids=body.game_ids,
            primary_id=body.primary_id,
            custom_title=body.custom_title,
            completion_status=body.completion_status,
            db_path=user_db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "game": merged, "stats": _user_stats(scope, services)}


@router.post("/api/ui/reconcile/dismiss")
def ui_reconcile_dismiss(
    body: LegacyReconcileDismissBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    dismissed = db.dismiss_reconcile_group(body.game_ids, db_path=user_db_path)
    return {"status": "ok", "dismissed_pairs": dismissed}


@router.post("/api/ui/reconcile/unlink/{game_id}")
def ui_reconcile_unlink(
    game_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    updated = db.unlink_game(game_id, db_path=user_db_path)
    if not updated:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"status": "ok", "game": updated, "stats": _user_stats(scope, services)}


@router.post("/api/ui/reconcile/reset-dismissals")
def ui_reconcile_reset_dismissals(
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    user_db_path = _ensure_user_lib_path(scope, services)
    cleared = db.reset_reconcile_dismissals(db_path=user_db_path)
    return {"status": "ok", "cleared": cleared, "stats": _user_stats(scope, services)}


@router.get("/api/ui/playtime-prompts")
def ui_list_playtime_prompts(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    prompts = [asdict(p) for p in services.activity.list_playtime_prompts(scope)]
    return {"prompts": prompts}


@router.post("/api/ui/playtime-prompts/{prompt_id}/resolve")
def ui_resolve_playtime_prompt(
    prompt_id: int,
    body: LegacyPromptResolveBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    res = services.activity.resolve_playtime_prompt(scope, prompt_id, body.action)
    return {
        "status": "ok",
        **asdict(res),
        "stats": _user_stats(scope, services),
    }


@router.get("/api/ui/account")
def ui_get_account(
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    acc = services.identity.get_account(scope)
    return {
        **asdict(acc),
        "role": acc.role,
    }


@router.patch("/api/ui/account/timezone")
def ui_update_timezone(
    body: LegacyTimezoneBody,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    account = services.identity.update_timezone(
        scope,
        timezone_name=body.timezone,
        timezone_eras=body.timezone_eras,
    )
    services.activity.rebuild_daily_ledger(scope)
    return {
        "status": "ok",
        "timezone": account.timezone,
        "timezone_eras": account.timezone_eras,
    }


@router.post("/api/ui/account/password")
def ui_change_password(
    body: LegacyPasswordBody,
    request: Request,
    scope: UserScope = Depends(require_session_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    services.identity.change_own_password(
        scope,
        current_password=body.current_password,
        new_password=body.new_password,
        current_session_token=request.cookies.get(services.settings.cookie_name),
    )
    return {"status": "ok", "message": "Password updated successfully."}


def _generate_temp_password() -> str:
    return secrets.token_urlsafe(12) + "!1A"


@router.get("/api/ui/admin/users")
def ui_admin_list_users(
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    users = [
        {
            **asdict(u),
            "role": _public_role(u.role),
            "status": _public_status(u.status),
        }
        for u in services.identity.list_users(scope)
    ]
    return {"users": users}


@router.post("/api/ui/admin/users")
def ui_admin_create_user(
    body: LegacyAdminUserCreateBody,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    generated_pw: str | None = None
    password = (body.password or "").strip()
    if not password:
        generated_pw = _generate_temp_password()
        password = generated_pw
    acc = services.identity.admin_create_user(
        scope,
        username=body.username,
        password=password,
        role=_internal_role(body.role),
    )
    res: dict[str, Any] = {
        "status": "ok",
        "user": {
            **asdict(acc),
            "role": _public_role(acc.role),
            "status": _public_status(acc.status),
        },
    }
    if generated_pw:
        res["temporary_password"] = generated_pw
    return res


@router.patch("/api/ui/admin/users/{user_id}")
def ui_admin_update_user(
    user_id: str,
    body: LegacyAdminUserPatchBody,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    acc = services.identity.admin_update_user(
        scope,
        user_id,
        role=_internal_role(body.role) if body.role is not None else None,
        status=_internal_status(body.status) if body.status is not None else None,
        status_reason=body.status_reason,
    )
    return {
        "status": "ok",
        "user": {
            **asdict(acc),
            "role": _public_role(acc.role),
            "status": _public_status(acc.status),
        },
    }


@router.post("/api/ui/admin/users/{user_id}/reset-password")
def ui_admin_reset_password(
    user_id: str,
    body: LegacyAdminResetPasswordBody | None = None,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    req = body or LegacyAdminResetPasswordBody()
    temp_pw = (req.new_password or "").strip() or _generate_temp_password()
    acc = services.identity.admin_reset_password(scope, user_id, new_password=temp_pw)
    return {
        "status": "ok",
        "temporary_password": temp_pw,
        "user": {
            **asdict(acc),
            "role": _public_role(acc.role),
            "status": _public_status(acc.status),
        },
    }


@router.delete("/api/ui/admin/users/{user_id}")
def ui_admin_delete_user(
    user_id: str,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    services.identity.admin_delete_user(scope, user_id)
    return {"status": "ok", "deleted_id": user_id}


def _invite_status(inv: InviteRecord) -> str:
    if inv.revoked_at:
        return "revoked"
    if inv.used_by:
        return "used"
    if inv.expires_at:
        try:
            dt = datetime.fromisoformat(inv.expires_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= datetime.now(timezone.utc):
                return "expired"
        except ValueError:
            pass
    return "active"


@router.get("/api/ui/admin/invites")
def ui_admin_list_invites(
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    invites = [
        {
            **asdict(inv),
            "code_preview": f"{inv.code_hash[:10]}…",
            "status": _invite_status(inv),
        }
        for inv in services.identity.list_invites(scope)
    ]
    return {"invites": invites}


@router.post("/api/ui/admin/invites")
def ui_admin_create_invite(
    body: LegacyAdminInviteCreateBody,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    ttl_hours = body.ttl_hours if body.ttl_hours is not None else (body.ttl_days or 3) * 24
    ttl_hours = max(1, min(720, ttl_hours))
    created = services.identity.create_invite(
        scope,
        role=_internal_role(body.role),
        note=body.note,
        ttl_hours=ttl_hours,
    )
    return {
        "status": "ok",
        "code": created.code,
        "invite_url": f"/signup?invite={created.code}",
        "record": {
            **asdict(created.record),
            "code_preview": f"{created.record.code_hash[:10]}…",
            "status": "active",
        },
    }


@router.delete("/api/ui/admin/invites/{code_hash}")
def ui_admin_revoke_invite(
    code_hash: str,
    scope: UserScope = Depends(require_admin_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    services.identity.revoke_invite(scope, code_hash)
    return {"status": "ok", "revoked_hash": code_hash}


@router.post("/api/ui/sync")
async def ui_trigger_sync(
    platform: str | None = Query(default=None),
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    if platform and platform.strip():
        jobs = [services.platform_sync.enqueue_sync_for_provider(scope, _normalize_provider_name(platform))]
    else:
        jobs = services.platform_sync.enqueue_all_configured_syncs(scope)

    results: list[dict[str, Any]] = []
    for _ in range(len(jobs)):
        processed = await services.platform_sync.process_next_job(worker_id="ui-sync")
        if processed is None:
            break
        results.append(
            {
                "job_id": processed.id,
                "kind": processed.kind,
                "status": processed.status,
                "result": processed.result,
                "error": processed.error_message,
            }
        )
    return {
        "status": "completed",
        "results": results,
        "stats": _user_stats(scope, services),
    }


@router.post("/api/ui/integrations/homeassistant/import-csv")
def ui_ha_import_csv(
    body: LegacyHaImportCsvBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    res = services.homeassistant.import_csv(
        scope,
        csv_content=body.csv_content,
        platform=body.platform,
        max_gap_seconds=body.max_gap_seconds,
    )
    return {"status": "ok", "result": asdict(res)}


@router.post("/api/ui/integrations/homeassistant/sync")
async def ui_ha_sync(
    body: LegacyHaSyncBody | None = None,
    scope: UserScope = Depends(require_sync_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    req = body or LegacyHaSyncBody()
    creds = services.identity.get_decrypted_connections(scope)
    res = await services.homeassistant.sync_history(
        scope,
        ha_url=req.ha_url,
        ha_token=req.ha_token,
        entity_id=req.entity_id,
        platform=req.platform,
        days_back=req.days_back,
        max_gap_seconds=req.max_gap_seconds,
        creds=creds,
    )
    return {"status": "ok", "result": asdict(res)}


@router.get("/api/ui/wishlist")
def ui_list_wishlist(
    status: str = Query(default="active"),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> list[Any]:
    return [_serialize_item(item).model_dump() for item in services.wishlist.list_items(scope, status=status)]


@router.post("/api/ui/wishlist")
async def ui_add_wishlist_item(
    body: LegacyWishlistCreateBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    item = services.wishlist.add_item(
        scope,
        title=body.title,
        platform_targets=_dump_targets(body.platform_targets),
        release_year=body.release_year,
        developer=body.developer,
        publisher=body.publisher,
        cover_url=body.cover_url,
        genres=body.genres,
        critic_score=body.critic_score,
        allow_smart_upgrade=body.allow_smart_upgrade,
        preferred_edition_id=body.preferred_edition_id,
        notes=body.notes,
        added_via="web",
        force_track_owned=body.force_track_owned,
    )
    try:
        await services.wishlist_sync.sync_wishlist_for_user(scope, wishlist_item_id=item.id, force=True)
        item = services.wishlist.get_item(scope, item.id)
    except Exception:
        pass
    return _serialize_item(item).model_dump()


@router.get("/api/ui/wishlist/candidates")
async def ui_search_wishlist_candidates(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=25),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    return await services.wishlist_sync.search_candidates(scope, q, limit=limit)


@router.get("/api/ui/wishlist/deals")
def ui_get_wishlist_deals(
    platform: str | None = Query(default=None),
    smart_upgrades_only: bool = Query(default=False),
    include_above_target: bool = Query(default=False),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    ev = services.wishlist.evaluate_deals(
        scope,
        platform=platform,
        smart_upgrades_only=smart_upgrades_only,
        include_above_target=include_above_target,
    )
    return {
        "total_deals": ev.total_deals,
        "smart_upgrades_count": ev.smart_upgrades_count,
        "all_time_lows_count": ev.all_time_lows_count,
        "deals": [asdict(d) for d in ev.deals],
    }


@router.post("/api/ui/wishlist/sync")
async def ui_sync_wishlist_prices(
    wishlist_item_id: int | None = Query(default=None),
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    return await services.wishlist_sync.sync_wishlist_for_user(
        scope,
        wishlist_item_id=wishlist_item_id,
        force=True,
    )


@router.patch("/api/ui/wishlist/{item_id}")
def ui_update_wishlist_item(
    item_id: int,
    body: LegacyWishlistUpdateBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    item = services.wishlist.update_item(
        scope,
        item_id,
        allow_smart_upgrade=body.allow_smart_upgrade,
        preferred_edition_id=body.preferred_edition_id,
        status=body.status,
        notes=body.notes,
    )
    return _serialize_item(item).model_dump()


@router.delete("/api/ui/wishlist/{item_id}")
def ui_delete_wishlist_item(
    item_id: int,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    services.wishlist.delete_item(scope, item_id)
    return {"status": "ok", "deleted_id": item_id}


@router.patch("/api/ui/wishlist/{item_id}/platforms")
async def ui_update_wishlist_platforms(
    item_id: int,
    body: LegacyWishlistPlatformsBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    item = services.wishlist.update_platforms(
        scope,
        item_id,
        platform_targets=_dump_targets(body.platform_targets),
        remove_platforms=body.remove_platforms,
        allow_smart_upgrade=body.allow_smart_upgrade,
        force_track_owned=body.force_track_owned,
    )
    try:
        await services.wishlist_sync.sync_wishlist_for_user(scope, wishlist_item_id=item.id, force=True)
        item = services.wishlist.get_item(scope, item.id)
    except Exception:
        pass
    return _serialize_item(item).model_dump()


@router.post("/api/ui/wishlist/{item_id}/purchase")
def ui_purchase_wishlist_item(
    item_id: int,
    body: LegacyWishlistPurchaseBody | None = None,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    req = body or LegacyWishlistPurchaseBody()
    res = services.wishlist.mark_purchased(
        scope,
        item_id,
        platform=req.platform,
        edition_name=req.edition_name,
        price_paid_usd=req.price_paid_usd,
        completion_status=req.completion_status,
    )
    return {
        "item": _serialize_item(res.item).model_dump(),
        "library_game_id": res.library_game_id,
        "already_purchased": res.already_purchased,
    }


@router.get("/api/ui/wishlist/{item_id}/history")
def ui_get_wishlist_history(
    item_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    hist = services.wishlist.get_price_history(scope, item_id, limit=limit)
    return {
        "item": _serialize_item(hist.item).model_dump(),
        "price_history_by_sku": {
            str(sku_id): [asdict(entry) for entry in entries]
            for sku_id, entries in hist.price_history_by_sku.items()
        },
    }


@router.get("/api/ui/push/vapid-public-key")
def ui_get_vapid_public_key(
    _scope: UserScope = Depends(require_read_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, str]:
    if not services.web_push.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web Push notifications are disabled on this instance",
        )
    key = services.web_push.get_public_key()
    return {"publicKey": key, "public_key": key}


@router.post("/api/ui/push/subscribe")
def ui_subscribe_push(
    body: LegacyPushSubscribeBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    p256dh = body.keys.get("p256dh") or ""
    auth = body.keys.get("auth") or ""
    if not p256dh or not auth:
        raise HTTPException(status_code=422, detail="Subscription keys must include 'p256dh' and 'auth'")
    sub = services.web_push.subscribe(
        scope,
        endpoint=body.endpoint,
        p256dh_key=p256dh,
        auth_key=auth,
        device_label=body.device_label,
    )
    return {"status": "ok", "subscription": asdict(sub)}


@router.delete("/api/ui/push/subscribe")
def ui_unsubscribe_push(
    body: LegacyPushUnsubscribeBody,
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    removed = services.web_push.unsubscribe(scope, body.endpoint)
    return {"status": "ok", "removed": removed}


@router.post("/api/ui/push/test")
async def ui_test_push(
    scope: UserScope = Depends(require_write_scope),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    if not services.web_push.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web Push notifications are disabled on this instance",
        )
    return await services.web_push.send_test_notification(scope)
