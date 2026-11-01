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

import system_db
from web_auth import SESSION_COOKIE_NAME, render_login_page, verify_web_session

logger = logging.getLogger("gamehub.oauth")


def _get_base_domain() -> str:
    return os.environ.get("MCP_PUBLIC_DOMAIN", "http://localhost:8096").rstrip("/")


def _get_client_id() -> str:
    return os.environ.get("MCP_CLIENT_ID", "game-hub-mcp").strip() or "game-hub-mcp"


def _get_client_secret() -> str:
    return os.environ.get("MCP_CLIENT_SECRET", "").strip()


# In-memory storage for short-lived authorization codes
_auth_codes: Dict[str, dict] = {}


def _init_oauth_table(db_path: Optional[str] = None) -> None:
    """Ensure system.db (including oauth_tokens.user_id) exists and is migrated."""
    system_db.init_system_db(db_path or system_db.DEFAULT_SYSTEM_DB_PATH)


def _store_token_pair(
    access_token: str,
    refresh_token: str,
    client_id: str,
    user_id: str,
    db_path: Optional[str] = None,
) -> None:
    if not user_id:
        raise ValueError("user_id is required to issue an OAuth token")
    path = db_path or system_db.DEFAULT_SYSTEM_DB_PATH
    _init_oauth_table(path)
    with system_db.get_system_connection(path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO oauth_tokens (access_token, refresh_token, client_id, user_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (access_token, refresh_token, client_id, user_id, time.time()),
        )


def _verify_refresh_token(refresh_token: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Return {"client_id", "user_id"} for a valid, user-bound refresh token, else None."""
    if not refresh_token:
        return None
    path = db_path or system_db.DEFAULT_SYSTEM_DB_PATH
    _init_oauth_table(path)
    with system_db.get_system_connection(path) as conn:
        row = conn.execute(
            "SELECT client_id, user_id FROM oauth_tokens WHERE refresh_token = ?",
            (refresh_token,),
        ).fetchone()
        if not row or not row["user_id"]:
            return None
        return {"client_id": row["client_id"], "user_id": str(row["user_id"])}


def verify_bearer_token(token: str, db_path: Optional[str] = None) -> Optional[str]:
    """Return the user_id bound to an access token, or None if invalid.

    Tokens without a user binding or bound to an inactive user are rejected.
    """
    if not token:
        return None
    path = db_path or system_db.DEFAULT_SYSTEM_DB_PATH
    _init_oauth_table(path)
    with system_db.get_system_connection(path) as conn:
        row = conn.execute(
            "SELECT user_id FROM oauth_tokens WHERE access_token = ?",
            (token,),
        ).fetchone()
        if not row or not row["user_id"]:
            return None
        uid = str(row["user_id"])
        user = system_db.get_user(uid, db_path=path)
        if not user or user.get("status") != "active":
            return None
        return uid


def _resolve_session_user_id(request: Request, db_path: Optional[str] = None) -> Optional[str]:
    """Map the Vertex web session cookie to an active system.db user id (None if not signed in or not active)."""
    username = verify_web_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not username:
        return None
    user = system_db.get_user_by_username(username, db_path=db_path)
    if user and user.get("status") != "active":
        return None
    user_id = user["id"] if user else system_db.ensure_user(username, db_path=db_path)
    return user_id


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
    user_id = verify_bearer_token(token)
    if not user_id:
        return JSONResponse(
            {"error": "invalid_token", "error_description": "Missing or invalid Bearer token"},
            status_code=401,
            headers={"Access-Control-Allow-Origin": "*"},
        )
    hostname = urlparse(_get_base_domain()).hostname or "localhost"
    username = system_db.get_username_by_user_id(user_id) or user_id
    return JSONResponse(
        {
            "sub": user_id,
            "name": username,
            "email": f"{username}@{hostname}",
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
    username: str = "",
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
      Client <strong>{html.escape(client_id)}</strong> is requesting access to the Game Hub library of <strong>{html.escape(username)}</strong>.
    </p>
    {error_html}
    <input type="hidden" name="client_id" value="{html.escape(client_id)}">
    <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}">
    <input type="hidden" name="state" value="{html.escape(state)}">
    <input type="hidden" name="code_challenge" value="{html.escape(code_challenge)}">
    <input type="hidden" name="code_challenge_method" value="{html.escape(code_challenge_method)}">
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
    Requires the user to be signed in to their Vertex web session (they are prompted to sign in
    otherwise); the issued authorization code is bound to that user's id.
    """
    client_id = request.query_params.get("client_id") or _get_client_id()
    redirect_uri = request.query_params.get("redirect_uri") or ""
    state = request.query_params.get("state") or ""
    code_challenge = request.query_params.get("code_challenge") or ""
    code_challenge_method = request.query_params.get("code_challenge_method") or "S256"

    if request.method == "POST":
        try:
            form = await request.form()
            form_dict = dict(form)
            client_id = str(form_dict.get("client_id") or client_id)
            redirect_uri = str(form_dict.get("redirect_uri") or redirect_uri)
            state = str(form_dict.get("state") or state)
            code_challenge = str(form_dict.get("code_challenge") or code_challenge)
            code_challenge_method = str(form_dict.get("code_challenge_method") or code_challenge_method)
        except Exception:
            pass

    if not redirect_uri or not is_valid_redirect_uri(redirect_uri):
        return HTMLResponse(
            "<p style='font-family:sans-serif;color:#ef4444;padding:24px;'>Invalid or missing redirect_uri parameter in OAuth request.</p>",
            status_code=400,
        )

    user_id = _resolve_session_user_id(request)
    if not user_id:
        # Not signed in: prompt for a Vertex login, then return to this authorization request.
        resume = "/oauth/authorize"
        if request.url.query:
            resume += "?" + request.url.query
        return render_login_page(next_url=resume)

    if request.method != "POST":
        return _render_consent_page(
            client_id,
            redirect_uri,
            state,
            code_challenge,
            code_challenge_method,
            username=system_db.get_username_by_user_id(user_id) or "",
        )

    code = "gh_code_" + secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client_id,
        "user_id": user_id,
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
    logger.info("Authorized OAuth for client '%s' (user %s)", client_id, user_id)

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
        _store_token_pair(access_token, refresh_token, resolved_client, record["user_id"])

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
        refresh_record = _verify_refresh_token(incoming_refresh)
        if not refresh_record:
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
        _store_token_pair(
            access_token, refresh_token, refresh_record["client_id"], refresh_record["user_id"]
        )
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
