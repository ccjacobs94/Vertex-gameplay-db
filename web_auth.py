"""
Web UI Session & Login Authentication for Vertex (Game Hub).
Provides persistent SQLite-backed session cookies, a dark-themed login UI with
Android/Chrome password-manager autofill support, and optional local/Tailscale IP bypass.
"""

import html
import ipaddress
import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import quote, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import passwords
import system_db
from db import get_connection

logger = logging.getLogger("gamehub.web_auth")

SESSION_COOKIE_NAME = "vertex_session"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 3600  # 90 days for installed mobile PWA persistence

# Public paths that must NEVER redirect to /login so Chrome's WebAPK installer,
# Service Worker updater, and OAuth MCP clients can always fetch metadata and icons cleanly.
PUBLIC_PATH_PREFIXES = (
    "/login",
    "/logout",
    "/signup",
    "/billing/webhook",
    "/manifest.webmanifest",
    "/sw.js",
    "/static/icons/",
    "/static/vertex_logo",
    "/oauth/",
    "/.well-known/",
    "/mcp",
    "/sse",
    "/messages",
    "/healthz",
)


def get_auth_username() -> str:
    return os.environ.get("VERTEX_USERNAME", "chameleon94").strip() or "chameleon94"


def get_auth_password() -> str:
    """Returns VERTEX_PASSWORD if explicitly configured."""
    return os.environ.get("VERTEX_PASSWORD", "").strip()


def is_web_auth_enabled() -> bool:
    edition = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
    if edition == "hosted":
        return True
    reg_policy = system_db.get_setting(
        "registration_policy", os.environ.get("VERTEX_REGISTRATION", "disabled")
    )
    if reg_policy != "disabled":
        return True
    flag = os.environ.get("VERTEX_REQUIRE_AUTH", "true").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(get_auth_password())


def is_local_bypass_enabled() -> bool:
    """
    If VERTEX_AUTH_BYPASS_LOCAL=true, direct requests from private
    LAN / Tailscale IPs without a reverse-proxy domain header can bypass the login prompt,
    provided this is a single-user self-hosted instance. Defaults to false.
    """
    flag = os.environ.get("VERTEX_AUTH_BYPASS_LOCAL", "false").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    edition = system_db.get_setting("edition", os.environ.get("VERTEX_EDITION", "selfhosted"))
    if edition != "selfhosted":
        return False
    try:
        if system_db.count_active_users() > 1:
            return False
    except Exception:
        pass
    return True


def create_web_session(username: str, user_agent: str = "") -> str:
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + SESSION_MAX_AGE_SECONDS

    # Store exclusively in system_db (with foreign key cascade to user)
    user_id = system_db.get_user_id_by_username(username)
    if not user_id:
        raise ValueError(f"Cannot create web session: user '{username}' does not exist")
    with system_db.get_system_connection() as conn:
        conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (now,))
        conn.execute(
            """
            INSERT INTO web_sessions (session_id, user_id, username, created_at, expires_at, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, username, now, expires_at, (user_agent or "")[:255]),
        )
    return session_id


def verify_web_session(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None

    try:
        system_db.init_system_db()
        now = time.time()
        with system_db.get_system_connection() as conn:
            row = conn.execute(
                """
                SELECT ws.session_id, ws.user_id, ws.username, ws.expires_at, u.username as u_username, u.status, u.status_reason
                FROM web_sessions ws
                LEFT JOIN users u ON ws.user_id = u.id
                WHERE ws.session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] < now:
                conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))
                return None
            # If user does not exist in users table (e.g. deleted), purge session and fail closed
            if not row["u_username"] and not system_db.get_user_by_username(str(row["username"])):
                conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))
                return None
            # Allow billing-only sessions for awaiting_payment and subscription_lapsed
            if row["status"] and row["status"] != "active":
                if row["status_reason"] not in ("awaiting_payment", "subscription_lapsed"):
                    conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))
                    return None
            return str(row["u_username"] or row["username"])
    except Exception:
        return None


def revoke_web_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    try:
        system_db.init_system_db()
        with system_db.get_system_connection() as conn:
            conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))
    except Exception:
        pass


def revoke_sessions_for_username(username: str) -> None:
    if not username:
        return
    try:
        system_db.init_system_db()
        uid = system_db.get_user_id_by_username(username)
        with system_db.get_system_connection() as conn:
            if uid:
                conn.execute(
                    "DELETE FROM web_sessions WHERE user_id = ? OR LOWER(username) = LOWER(?)",
                    (uid, username),
                )
            else:
                conn.execute("DELETE FROM web_sessions WHERE LOWER(username) = LOWER(?)", (username,))
    except Exception:
        pass



def is_public_path(path: str) -> bool:
    for prefix in PUBLIC_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return True
    return False


def _is_direct_local_or_tailscale_request(request: Request) -> bool:
    # If routed through Cloudflare or Caddy for a public domain, do not treat as local
    if request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for"):
        return False
    client_host = request.client.host if request.client else ""
    if not client_host:
        return False
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(client_host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def is_request_authenticated(request: Request) -> bool:
    if not is_web_auth_enabled():
        return True
    if is_local_bypass_enabled() and _is_direct_local_or_tailscale_request(request):
        return True
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if verify_web_session(session_id):
        return True
    # Also accept a valid Bearer token from OAuth / PAT
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        from oauth_provider import verify_bearer_token
        if verify_bearer_token(token):
            return True
    return False


def _is_https_request(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if "https" in forwarded_proto.lower():
        return True
    cf_visitor = request.headers.get("cf-visitor", "")
    if "https" in cf_visitor.lower():
        return True
    return False


def _sanitize_next_url(next_url: str) -> str:
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not next_url.startswith("/"):
        return "/"
    if next_url.startswith("/login") or next_url.startswith("/logout"):
        return "/"
    return next_url


def render_login_page(next_url: str = "/", error_msg: str = "", default_username: str = "") -> HTMLResponse:
    safe_next = html.escape(_sanitize_next_url(next_url))
    safe_user = html.escape(default_username or get_auth_username())
    error_html = (
        f"""<div class="login-error" role="alert">
              <span aria-hidden="true">⚠️</span>
              <span>{html.escape(error_msg)}</span>
            </div>"""
        if error_msg
        else ""
    )

    policy = system_db.get_registration_policy()
    signup_link_html = (
        """<div style="margin-top:0.85rem; font-size:0.84rem; text-align:center;">
             <span style="color:#94a3b8;">Don't have an account?</span>
             <a href="/signup" style="color:#38bdf8; text-decoration:none; font-weight:600; margin-left:0.35rem;">Create an account</a>
           </div>"""
        if policy != "disabled"
        else ""
    )

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#0b0f19">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Vertex">
  <title>Sign In | Vertex Game Nexus</title>
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <link rel="apple-touch-icon" sizes="180x180" href="/static/icons/apple-touch-icon.png">
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
      --accent-indigo: #6366f1;
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100dvh;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background:
        radial-gradient(circle at 20% 15%, rgba(56, 189, 248, 0.12), transparent 45%),
        radial-gradient(circle at 80% 85%, rgba(139, 92, 246, 0.12), transparent 45%),
        var(--bg-page);
      color: var(--text-primary);
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      padding: 1.25rem;
    }}
    .login-card {{
      width: 100%;
      max-width: 410px;
      background: rgba(19, 27, 46, 0.92);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border-subtle);
      border-radius: 16px;
      padding: 2.25rem 1.85rem;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.55);
    }}
    .brand-header {{
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      margin-bottom: 1.75rem;
    }}
    .brand-logo {{
      width: 60px;
      height: 60px;
      border-radius: 14px;
      margin-bottom: 0.85rem;
      filter: drop-shadow(0 0 18px rgba(56, 189, 248, 0.35));
    }}
    .brand-header h1 {{
      margin: 0;
      font-size: 1.65rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      background: linear-gradient(135deg, #f8fafc 0%, #38bdf8 55%, #a855f7 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .brand-header p {{
      margin: 0.35rem 0 0;
      font-size: 0.86rem;
      color: var(--text-secondary);
    }}
    .login-error {{
      display: flex;
      align-items: center;
      gap: 0.55rem;
      background: rgba(239, 68, 68, 0.14);
      border: 1px solid rgba(239, 68, 68, 0.45);
      color: #fca5a5;
      padding: 0.75rem 0.9rem;
      border-radius: 10px;
      font-size: 0.85rem;
      margin-bottom: 1.25rem;
    }}
    .field-group {{
      margin-bottom: 1.1rem;
    }}
    .field-group label {{
      display: block;
      font-size: 0.82rem;
      font-weight: 600;
      color: #cbd5e1;
      margin-bottom: 0.42rem;
    }}
    .field-group input {{
      width: 100%;
      padding: 0.78rem 0.95rem;
      border-radius: 10px;
      border: 1px solid #334155;
      background: var(--bg-input);
      color: var(--text-primary);
      font-size: 1rem;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }}
    .field-group input:focus {{
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2);
    }}
    .btn-submit {{
      width: 100%;
      margin-top: 0.5rem;
      padding: 0.85rem 1rem;
      border: none;
      border-radius: 10px;
      background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
      color: #ffffff;
      font-size: 0.98rem;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 6px 20px rgba(59, 130, 246, 0.35);
      transition: transform 0.12s ease, filter 0.15s ease;
    }}
    .btn-submit:hover {{
      filter: brightness(1.08);
    }}
    .btn-submit:active {{
      transform: scale(0.99);
    }}
    .login-footer {{
      margin-top: 1.35rem;
      text-align: center;
      font-size: 0.76rem;
      color: #64748b;
    }}
  </style>
</head>
<body>
  <main class="login-card">
    <div class="brand-header">
      <img src="/static/vertex_logo.svg" alt="Vertex Logo" class="brand-logo" width="60" height="60">
      <h1>Vertex</h1>
      <p>Sign in to access your unified gaming universe</p>
    </div>
    {error_html}
    <form method="POST" action="/login">
      <input type="hidden" name="next" value="{safe_next}">
      <div class="field-group">
        <label for="login-username">Username</label>
        <input
          id="login-username"
          type="text"
          name="username"
          value="{safe_user}"
          autocomplete="username"
          autocapitalize="none"
          spellcheck="false"
          required
        >
      </div>
      <div class="field-group">
        <label for="login-password">Password</label>
        <input
          id="login-password"
          type="password"
          name="password"
          autocomplete="current-password"
          placeholder="Enter your Vertex password"
          autofocus
          required
        >
      </div>
      <button type="submit" class="btn-submit">Sign In</button>
    </form>
    {signup_link_html}
    <div class="login-footer">
      Protected session &bull; Remains signed in for 90 days on this device
    </div>
  </main>
  <script>
    if ("serviceWorker" in navigator) {{
      navigator.serviceWorker.register("/sw.js").catch(() => {{}});
    }}
  </script>
</body>
</html>"""

    return HTMLResponse(
        content=content,
        status_code=200 if not error_msg else 401,
        headers={"Cache-Control": "no-store"},
    )


async def handle_login_get(request: Request) -> Response:
    next_url = _sanitize_next_url(request.query_params.get("next", "/"))
    if is_request_authenticated(request):
        return RedirectResponse(url=next_url, status_code=303)
    return render_login_page(next_url=next_url)


async def handle_login_post(request: Request) -> Response:
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "").strip()
    next_url = _sanitize_next_url(str(form.get("next") or "/"))

    bootstrap_user = get_auth_username()
    bootstrap_pass = get_auth_password()
    fallback_allowed = os.environ.get(
        "VERTEX_ENV_PASSWORD_FALLBACK", "true"
    ).strip().lower() not in ("0", "false", "no")

    system_db.init_system_db()
    user = system_db.get_user_by_username(username)
    authenticated = False

    if user:
        if passwords.verify_password(password, user.get("password_hash")):
            authenticated = True
        elif (
            fallback_allowed
            and not str(user.get("password_hash") or "").startswith("scrypt$")
            and secrets.compare_digest(username.lower(), bootstrap_user.lower())
            and bool(bootstrap_pass)
            and secrets.compare_digest(password, bootstrap_pass)
        ):
            authenticated = True
            # Upgrade placeholder or legacy hash to scrypt
            system_db.set_user_password(user["id"], password)
            system_db.ensure_bootstrap_admin(user["username"])
    elif (
        secrets.compare_digest(username.lower(), bootstrap_user.lower())
        and bool(bootstrap_pass)
        and secrets.compare_digest(password, bootstrap_pass)
    ):
        # Initial provision of bootstrap admin
        uid = system_db.create_user(
            username=bootstrap_user,
            password=password,
            role="admin",
            status="active",
        )
        user = system_db.get_user(uid)
        authenticated = True

    if not authenticated or not user:
        logger.warning(
            "Failed web login attempt for username=%r from IP=%s",
            username,
            request.client.host if request.client else "unknown",
        )
        return render_login_page(
            next_url=next_url,
            error_msg="Invalid username or password.",
            default_username=username or bootstrap_user,
        )

    if user.get("status") != "active":
        if user.get("status_reason") in ("awaiting_payment", "subscription_lapsed"):
            system_db.record_login(user["id"])
            session_id = create_web_session(
                username=user["username"],
                user_agent=request.headers.get("user-agent", ""),
            )
            response = RedirectResponse(url="/billing", status_code=303)
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=session_id,
                max_age=SESSION_MAX_AGE_SECONDS,
                httponly=True,
                secure=_is_https_request(request),
                samesite="lax",
                path="/",
            )
            logger.info(
                "Billing-only web login for subscriber=%r (reason=%s)",
                user["username"],
                user.get("status_reason"),
            )
            return response

        logger.warning(
            "Login rejected for non-active user=%r (status=%s, reason=%s)",
            user["username"],
            user.get("status"),
            user.get("status_reason"),
        )
        return render_login_page(
            next_url=next_url,
            error_msg="This account is not active. Contact your administrator.",
            default_username=username,
        )


    system_db.record_login(user["id"])

    session_id = create_web_session(
        username=user["username"],
        user_agent=request.headers.get("user-agent", ""),
    )
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=_is_https_request(request),
        samesite="lax",
        path="/",
    )
    logger.info("Successful web login for user=%r", user["username"])
    return response



async def handle_logout(request: Request) -> Response:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    revoke_web_session(session_id)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
