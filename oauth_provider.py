"""
Lightweight RFC 8414 / RFC 9728 OAuth 2.0 & OAuth 2.1 Provider for Game Hub MCP Server.
Allows Google Gemini Web, Claude, and other OAuth-mandated MCP clients to connect securely.
"""

import base64
import hashlib
import html
import logging
import os
import secrets
import time
from typing import Dict, Optional
from urllib.parse import urlencode, unquote, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from db import get_connection

logger = logging.getLogger("gamehub.oauth")


def _get_base_domain() -> str:
    return os.environ.get("MCP_PUBLIC_DOMAIN", "http://localhost:8096").rstrip("/")


def _get_client_id() -> str:
    return os.environ.get("MCP_CLIENT_ID", "game-hub-mcp").strip() or "game-hub-mcp"


def _get_client_secret() -> str:
    return os.environ.get("MCP_CLIENT_SECRET", "").strip()


# In-memory storage for short-lived authorization codes
_auth_codes: Dict[str, dict] = {}


def _init_oauth_table() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                access_token TEXT PRIMARY KEY,
                refresh_token TEXT UNIQUE,
                client_id TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )


def _store_token_pair(access_token: str, refresh_token: str, client_id: str) -> None:
    _init_oauth_table()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO oauth_tokens (access_token, refresh_token, client_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (access_token, refresh_token, client_id, time.time()),
        )


def _verify_refresh_token(refresh_token: str) -> Optional[str]:
    if not refresh_token:
        return None
    _init_oauth_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT client_id FROM oauth_tokens WHERE refresh_token = ?",
            (refresh_token,),
        ).fetchone()
        return row["client_id"] if row else None


def verify_bearer_token(token: str) -> bool:
    """Verify an incoming Bearer token against SQLite oauth_tokens or MCP_CLIENT_SECRET."""
    if not token:
        return False
    secret = _get_client_secret()
    if secret and secrets.compare_digest(token, secret):
        return True
    _init_oauth_table()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM oauth_tokens WHERE access_token = ?",
            (token,),
        ).fetchone()
        return row is not None


def extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def is_valid_redirect_uri(redirect_uri: str) -> bool:
    try:
        parsed = urlparse(redirect_uri)
    except Exception:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return True
    return False


def verify_code_challenge(verifier: str, challenge: str, method: str = "S256") -> bool:
    if not challenge:
        return True
    if not verifier:
        return False
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    if method == "S256":
        calculated = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        return secrets.compare_digest(calculated, challenge.rstrip("="))
    return False


async def protected_resource_metadata(request: Request) -> JSONResponse:
    """RFC 9728 OAuth 2.0 Protected Resource Metadata"""
    base_domain = _get_base_domain()
    return JSONResponse(
        {
            "resource": f"{base_domain}/mcp",
            "authorization_servers": [base_domain],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["gamehub:read", "gamehub:write", "openid", "profile", "email"],
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=3600",
            "Content-Type": "application/json;charset=UTF-8",
        },
    )


async def authorization_server_metadata(request: Request) -> JSONResponse:
    """RFC 8414 OAuth 2.0 Authorization Server Metadata"""
    base_domain = _get_base_domain()
    return JSONResponse(
        {
            "issuer": base_domain,
            "authorization_endpoint": f"{base_domain}/oauth/authorize",
            "token_endpoint": f"{base_domain}/oauth/token",
            "userinfo_endpoint": f"{base_domain}/oauth/userinfo",
            "registration_endpoint": f"{base_domain}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": [
                "authorization_code",
                "refresh_token",
                "client_credentials",
            ],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post",
                "client_secret_basic",
                "none",
            ],
            "code_challenge_methods_supported": ["S256", "plain"],
            "scopes_supported": ["gamehub:read", "gamehub:write", "openid", "profile", "email"],
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=3600",
            "Content-Type": "application/json;charset=UTF-8",
        },
    )


async def dynamic_client_registration(request: Request) -> JSONResponse:
    """RFC 7591 OAuth 2.0 Dynamic Client Registration (public client metadata only; never leaks secret)."""
    return JSONResponse(
        {
            "client_id": _get_client_id(),
            "client_id_issued_at": int(time.time()),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "application/json;charset=UTF-8",
        },
    )


async def userinfo_endpoint(request: Request) -> JSONResponse:
    """OpenID Connect UserInfo endpoint for Google Account Linking"""
    token = extract_bearer_token(request)
    if _get_client_secret() and not verify_bearer_token(token):
        return JSONResponse(
            {"error": "invalid_token", "error_description": "Missing or invalid Bearer token"},
            status_code=401,
            headers={"Access-Control-Allow-Origin": "*"},
        )
    hostname = urlparse(_get_base_domain()).hostname or "localhost"
    return JSONResponse(
        {
            "sub": "game_hub_owner",
            "name": "Game Hub Owner",
            "email": f"owner@{hostname}",
            "email_verified": True,
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "application/json;charset=UTF-8",
        },
    )


def _render_consent_page(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    error_msg: str = "",
) -> HTMLResponse:
    error_html = (
        f"<div style='background:#451a1a;border:1px solid #dc2626;color:#fca5a5;padding:10px 12px;"
        f"border-radius:8px;font-size:0.85rem;margin-bottom:16px;'>{html.escape(error_msg)}</div>"
        if error_msg
        else ""
    )
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Authorize Game Hub MCP</title>
</head>
<body style="background:#0b0f19;color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box;">
  <form method="POST" action="/oauth/authorize" style="background:#131b2e;border:1px solid #26334d;border-radius:12px;padding:32px;max-width:400px;width:100%;box-shadow:0 10px 25px rgba(0,0,0,0.4);">
    <div style="font-size:2.2rem;margin-bottom:8px;text-align:center;">🎮</div>
    <h3 style="margin:0 0 8px 0;text-align:center;">Authorize Game Hub MCP</h3>
    <p style="color:#94a3b8;font-size:0.88rem;line-height:1.45;text-align:center;margin-bottom:20px;">
      Client <strong>{html.escape(client_id)}</strong> is requesting access to your Game Hub library.
    </p>
    {error_html}
    <input type="hidden" name="client_id" value="{html.escape(client_id)}">
    <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}">
    <input type="hidden" name="state" value="{html.escape(state)}">
    <input type="hidden" name="code_challenge" value="{html.escape(code_challenge)}">
    <input type="hidden" name="code_challenge_method" value="{html.escape(code_challenge_method)}">
    <label style="display:block;font-size:0.82rem;color:#cbd5e1;margin-bottom:6px;">MCP Client Secret (from <code>.env</code>)</label>
    <input type="password" name="access_key" required autofocus placeholder="Enter MCP_CLIENT_SECRET"
      style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#f8fafc;font-size:0.95rem;box-sizing:border-box;margin-bottom:16px;">
    <button type="submit" style="width:100%;padding:11px;border-radius:8px;border:none;background:#38bdf8;color:#0f172a;font-weight:600;font-size:0.95rem;cursor:pointer;">
      Approve &amp; Connect
    </button>
  </form>
</body>
</html>"""
    return HTMLResponse(html_content, status_code=200 if not error_msg else 401)


async def authorize_endpoint(request: Request):
    """
    Handles GET/POST /oauth/authorize.
    Requires the owner to authenticate with MCP_CLIENT_SECRET before issuing an authorization code.
    """
    secret = _get_client_secret()
    if not secret:
        return HTMLResponse(
            "<p style='font-family:sans-serif;color:#ef4444;padding:24px;'>"
            "OAuth is disabled because <code>MCP_CLIENT_SECRET</code> is not set in <code>.env</code>.</p>",
            status_code=403,
        )

    client_id = request.query_params.get("client_id") or _get_client_id()
    redirect_uri = request.query_params.get("redirect_uri") or ""
    state = request.query_params.get("state") or ""
    code_challenge = request.query_params.get("code_challenge") or ""
    code_challenge_method = request.query_params.get("code_challenge_method") or "S256"
    submitted_key = ""

    if request.method == "POST":
        try:
            form = await request.form()
            form_dict = dict(form)
            client_id = str(form_dict.get("client_id") or client_id)
            redirect_uri = str(form_dict.get("redirect_uri") or redirect_uri)
            state = str(form_dict.get("state") or state)
            code_challenge = str(form_dict.get("code_challenge") or code_challenge)
            code_challenge_method = str(form_dict.get("code_challenge_method") or code_challenge_method)
            submitted_key = str(form_dict.get("access_key") or "").strip()
        except Exception:
            pass

    if not redirect_uri or not is_valid_redirect_uri(redirect_uri):
        return HTMLResponse(
            "<p style='font-family:sans-serif;color:#ef4444;padding:24px;'>Invalid or missing redirect_uri parameter in OAuth request.</p>",
            status_code=400,
        )

    if request.method != "POST":
        return _render_consent_page(client_id, redirect_uri, state, code_challenge, code_challenge_method)

    if not submitted_key or not secrets.compare_digest(submitted_key, secret):
        logger.warning("Rejected OAuth authorization attempt with invalid secret for client '%s'", client_id)
        return _render_consent_page(
            client_id,
            redirect_uri,
            state,
            code_challenge,
            code_challenge_method,
            error_msg="Invalid MCP_CLIENT_SECRET. Please check your .env file.",
        )

    code = "gh_code_" + secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires_at": time.time() + 600,
    }

    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    target_url = redirect_uri + sep + urlencode(params)
    logger.info("Authorized OAuth for client '%s'", client_id)

    escaped_target = html.escape(target_url)
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0;url={escaped_target}">
  <title>Connecting Game Hub...</title>
</head>
<body style="background:#0b0f19;color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;">
  <div style="background:#131b2e;border:1px solid #26334d;border-radius:12px;padding:32px;text-align:center;max-width:400px;">
    <div style="font-size:2.5rem;margin-bottom:12px;">🎮</div>
    <h3 style="margin:0 0 8px 0;">Authorized!</h3>
    <p style="color:#94a3b8;font-size:0.9rem;line-height:1.4;">Redirecting back to your AI client...</p>
    <p style="font-size:0.8rem;margin-top:16px;"><a href="{escaped_target}" style="color:#38bdf8;text-decoration:none;">Click here if not redirected automatically</a></p>
  </div>
</body>
</html>"""

    response = HTMLResponse(html_content, status_code=302)
    response.headers["Location"] = target_url
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


async def token_endpoint(request: Request) -> JSONResponse:
    """
    Handles POST /oauth/token to exchange authorization code or credentials for an access token.
    """
    secret = _get_client_secret()
    if not secret:
        return JSONResponse(
            {"error": "access_denied", "error_description": "MCP_CLIENT_SECRET is not configured on this server"},
            status_code=403,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    content_type = request.headers.get("content-type", "")
    data = {}
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}

    client_id = str(data.get("client_id") or "")
    client_secret = str(data.get("client_secret") or "")

    # Inspect HTTP Basic Auth header if present
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            if ":" in decoded:
                u, p = decoded.split(":", 1)
                client_id = unquote(u)
                client_secret = unquote(p)
        except Exception:
            pass

    grant_type = data.get("grant_type", "authorization_code")
    logger.info("OAuth token request: grant_type=%s, client_id=%s", grant_type, client_id)

    if grant_type == "authorization_code":
        code = str(data.get("code") or "")
        if not code or code not in _auth_codes:
            logger.warning("Token exchange rejected: invalid or missing authorization code")
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"},
                status_code=400,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json;charset=UTF-8",
                },
            )

        record = _auth_codes.pop(code)
        if time.time() > record["expires_at"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Authorization code has expired"},
                status_code=400,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json;charset=UTF-8",
                },
            )

        code_verifier = str(data.get("code_verifier") or "")
        if record.get("code_challenge"):
            if not code_verifier or not verify_code_challenge(
                code_verifier, record["code_challenge"], record.get("code_challenge_method", "S256")
            ):
                logger.warning("Token exchange rejected: PKCE verification failed")
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "PKCE code_verifier check failed"},
                    status_code=400,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Content-Type": "application/json;charset=UTF-8",
                    },
                )

        access_token = "gh_mcp_" + secrets.token_urlsafe(32)
        refresh_token = "gh_ref_" + secrets.token_urlsafe(32)
        resolved_client = client_id or record.get("client_id") or _get_client_id()
        _store_token_pair(access_token, refresh_token, resolved_client)

        return JSONResponse(
            {
                "token_type": "Bearer",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": 3600,
                "scope": "gamehub:read gamehub:write",
            },
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json;charset=UTF-8",
            },
        )

    elif grant_type == "refresh_token":
        incoming_refresh = str(data.get("refresh_token") or "")
        resolved_client = _verify_refresh_token(incoming_refresh)
        if not resolved_client:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Invalid refresh token"},
                status_code=400,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json;charset=UTF-8",
                },
            )
        access_token = "gh_mcp_" + secrets.token_urlsafe(32)
        refresh_token = "gh_ref_" + secrets.token_urlsafe(32)
        _store_token_pair(access_token, refresh_token, resolved_client)
        return JSONResponse(
            {
                "token_type": "Bearer",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": 3600,
                "scope": "gamehub:read gamehub:write",
            },
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json;charset=UTF-8",
            },
        )

    elif grant_type == "client_credentials":
        if not client_secret or not secrets.compare_digest(client_secret, secret):
            logger.warning("Rejected client_credentials token request with invalid client_secret")
            return JSONResponse(
                {"error": "invalid_client", "error_description": "Invalid client_secret"},
                status_code=401,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json;charset=UTF-8",
                },
            )
        access_token = "gh_mcp_" + secrets.token_urlsafe(32)
        refresh_token = "gh_ref_" + secrets.token_urlsafe(32)
        _store_token_pair(access_token, refresh_token, client_id or _get_client_id())
        return JSONResponse(
            {
                "token_type": "Bearer",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": 3600,
                "scope": "gamehub:read gamehub:write",
            },
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json;charset=UTF-8",
            },
        )

    return JSONResponse(
        {"error": "unsupported_grant_type"},
        status_code=400,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "application/json;charset=UTF-8",
        },
    )


def get_oauth_routes():
    """Returns all RFC 9728 and RFC 8414 metadata and OAuth routes."""
    return [
        Route("/.well-known/oauth-protected-resource", protected_resource_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource/mcp/", protected_resource_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server", authorization_server_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server/mcp", authorization_server_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/openid-configuration", authorization_server_metadata, methods=["GET", "OPTIONS"]),
        Route("/oauth/authorize", authorize_endpoint, methods=["GET", "POST", "OPTIONS"]),
        Route("/oauth/token", token_endpoint, methods=["POST", "OPTIONS"]),
        Route("/oauth/userinfo", userinfo_endpoint, methods=["GET", "POST", "OPTIONS"]),
        Route("/oauth/register", dynamic_client_registration, methods=["POST", "OPTIONS"]),
    ]
