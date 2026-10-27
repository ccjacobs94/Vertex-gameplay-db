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

from db import get_connection

logger = logging.getLogger("gamehub.web_auth")

SESSION_COOKIE_NAME = "vertex_session"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 3600  # 90 days for installed mobile PWA persistence

# Public paths that must NEVER redirect to /login so Chrome's WebAPK installer,
# Service Worker updater, and OAuth MCP clients can always fetch metadata and icons cleanly.
PUBLIC_PATH_PREFIXES = (
    "/login",
    "/logout",
    "/manifest.webmanifest",
    "/sw.js",
    "/static/icons/",
    "/static/vertex_logo",
    "/oauth/",
    "/.well-known/",
)


def get_auth_username() -> str:
    return os.environ.get("VERTEX_USERNAME", "chris").strip() or "chris"


def get_auth_password() -> str:
    """
    Returns VERTEX_PASSWORD if explicitly configured, falling back to MCP_CLIENT_SECRET.
    """
    pw = os.environ.get("VERTEX_PASSWORD", "").strip()
    if pw:
        return pw
    return os.environ.get("MCP_CLIENT_SECRET", "").strip()


def is_web_auth_enabled() -> bool:
    flag = os.environ.get("VERTEX_REQUIRE_AUTH", "true").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(get_auth_password())


def is_local_bypass_enabled() -> bool:
    """
    If VERTEX_AUTH_BYPASS_LOCAL=true (the default), direct requests from private
    LAN / Tailscale IPs without a reverse-proxy domain header can bypass the login prompt,
    while https://vertex.crjacobs.cc (via Caddy / Cloudflare) always enforces login.
    """
    flag = os.environ.get("VERTEX_AUTH_BYPASS_LOCAL", "true").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _init_web_sessions_table() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                session_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                user_agent TEXT DEFAULT ''
            );
            """
        )


def create_web_session(username: str, user_agent: str = "") -> str:
    _init_web_sessions_table()
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + SESSION_MAX_AGE_SECONDS
    with get_connection() as conn:
        # Opportunistically prune expired sessions
        conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (now,))
        conn.execute(
            """
            INSERT INTO web_sessions (session_id, username, created_at, expires_at, user_agent)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, username, now, expires_at, (user_agent or "")[:255]),
        )
    return session_id


def verify_web_session(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    _init_web_sessions_table()
    now = time.time()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM web_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < now:
            conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))
            return None
        return row["username"]


def revoke_web_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    _init_web_sessions_table()
    with get_connection() as conn:
        conn.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))


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
    # Also accept a valid Bearer token for API CLI scripts
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        expected = get_auth_password()
        if expected and secrets.compare_digest(token, expected):
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

    expected_user = get_auth_username()
    expected_pass = get_auth_password()

    user_ok = secrets.compare_digest(username.lower(), expected_user.lower())
    pass_ok = bool(expected_pass) and secrets.compare_digest(password, expected_pass)

    if not (user_ok and pass_ok):
        logger.warning(
            "Failed web login attempt for username=%r from IP=%s",
            username,
            request.client.host if request.client else "unknown",
        )
        return render_login_page(
            next_url=next_url,
            error_msg="Invalid username or password.",
            default_username=username or expected_user,
        )

    session_id = create_web_session(
        username=expected_user,
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
    logger.info("Successful web login for user=%r", expected_user)
    return response


async def handle_logout(request: Request) -> Response:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    revoke_web_session(session_id)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
