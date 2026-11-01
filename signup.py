"""
Self-hosted user registration UI and endpoint handlers for Vertex (Game Hub).
Supports configurable registration policies (disabled, invite, approval, open),
bot honeypot protection, client rate limiting, and seamless post-registration login.
"""

import html
import logging
import os
import secrets
import time
from typing import Dict, List, Optional
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

import passwords
import system_db
import web_auth

logger = logging.getLogger("gamehub.signup")

# Simple in-memory rate limiting: IP -> list of attempt timestamps
_RATE_LIMIT_WINDOW = 3600  # 1 hour
_MAX_ATTEMPTS_PER_WINDOW = 10
_ip_attempts: Dict[str, List[float]] = {}


def _is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    timestamps = _ip_attempts.get(client_ip, [])
    # Prune expired
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= _MAX_ATTEMPTS_PER_WINDOW:
        _ip_attempts[client_ip] = timestamps
        return True
    timestamps.append(now)
    _ip_attempts[client_ip] = timestamps
    return False


def render_signup_page(
    policy: str,
    error_msg: str = "",
    default_username: str = "",
    default_email: str = "",
    default_invite: str = "",
) -> HTMLResponse:
    if policy == "disabled":
        content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Registration Closed | Vertex</title>
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <style>
    body {{
      margin: 0; min-height: 100dvh;
      font-family: system-ui, -apple-system, sans-serif;
      background: #0b0f19; color: #f1f5f9;
      display: flex; align-items: center; justify-content: center;
      padding: 1.25rem;
    }}
    .card {{
      max-width: 400px; width: 100%;
      background: #131b2e; border: 1px solid #26334d;
      border-radius: 16px; padding: 2rem; text-align: center;
    }}
    h1 {{ font-size: 1.4rem; margin: 0 0 0.5rem 0; color: #f8fafc; }}
    p {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 1.5rem; }}
    a {{ color: #38bdf8; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <img src="/static/vertex_logo.svg" alt="Vertex" width="56" height="56" style="margin-bottom: 1rem;">
    <h1>Registration Closed</h1>
    <p>Public account registration is currently disabled on this instance. Please contact the administrator for access.</p>
    <a href="/login">&larr; Return to Sign In</a>
  </div>
</body>
</html>"""
        return HTMLResponse(content, status_code=403)

    safe_user = html.escape(default_username)
    safe_email = html.escape(default_email)
    safe_invite = html.escape(default_invite)
    error_html = (
        f"""<div class="signup-error" role="alert">
              <span aria-hidden="true">⚠️</span>
              <span>{html.escape(error_msg)}</span>
            </div>"""
        if error_msg
        else ""
    )

    invite_field_html = ""
    if policy == "invite":
        invite_field_html = f"""
        <div class="field-group">
          <label for="reg-invite">Invite Code</label>
          <input
            id="reg-invite"
            type="text"
            name="invite_code"
            value="{safe_invite}"
            placeholder="Enter your invitation code"
            autocomplete="off"
            required
          >
          <span class="hint">An invite code is required to register on this instance.</span>
        </div>
        """

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#0b0f19">
  <title>Create Account | Vertex Game Nexus</title>
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
      font-family: system-ui, -apple-system, sans-serif;
      background:
        radial-gradient(circle at 20% 15%, rgba(56, 189, 248, 0.12), transparent 45%),
        radial-gradient(circle at 80% 85%, rgba(139, 92, 246, 0.12), transparent 45%),
        var(--bg-page);
      color: var(--text-primary);
      display: flex; flex-direction: column;
      justify-content: center; align-items: center;
      padding: 1.25rem;
    }}
    .signup-card {{
      width: 100%; max-width: 420px;
      background: rgba(19, 27, 46, 0.94);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border-subtle);
      border-radius: 16px;
      padding: 2.25rem 1.85rem;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.55);
    }}
    .brand-header {{
      display: flex; flex-direction: column; align-items: center; text-align: center;
      margin-bottom: 1.5rem;
    }}
    .brand-logo {{
      width: 54px; height: 54px; border-radius: 12px; margin-bottom: 0.75rem;
      filter: drop-shadow(0 0 16px rgba(56, 189, 248, 0.35));
    }}
    .brand-header h1 {{
      margin: 0; font-size: 1.55rem; font-weight: 800;
      background: linear-gradient(135deg, #f8fafc 0%, #38bdf8 55%, #a855f7 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .brand-header p {{ margin: 0.35rem 0 0; font-size: 0.85rem; color: var(--text-secondary); }}
    .signup-error {{
      display: flex; align-items: center; gap: 0.55rem;
      background: rgba(239, 68, 68, 0.14); border: 1px solid rgba(239, 68, 68, 0.45);
      color: #fca5a5; padding: 0.75rem 0.9rem; border-radius: 10px;
      font-size: 0.85rem; margin-bottom: 1.25rem;
    }}
    .field-group {{ margin-bottom: 1rem; }}
    .field-group label {{
      display: block; font-size: 0.82rem; font-weight: 600; color: #cbd5e1; margin-bottom: 0.35rem;
    }}
    .field-group input {{
      width: 100%; padding: 0.75rem 0.9rem; border-radius: 10px;
      border: 1px solid #334155; background: var(--bg-input);
      color: var(--text-primary); font-size: 0.98rem;
    }}
    .field-group input:focus {{
      outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2);
    }}
    .hint {{ display: block; font-size: 0.75rem; color: #64748b; margin-top: 0.25rem; }}
    .btn-submit {{
      width: 100%; margin-top: 0.75rem; padding: 0.85rem 1rem;
      border: none; border-radius: 10px;
      background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
      color: #ffffff; font-size: 0.98rem; font-weight: 700; cursor: pointer;
      box-shadow: 0 6px 20px rgba(59, 130, 246, 0.35);
    }}
    .btn-submit:hover {{ filter: brightness(1.08); }}
    .signup-footer {{
      margin-top: 1.25rem; text-align: center; font-size: 0.82rem; color: var(--text-secondary);
    }}
    .signup-footer a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <main class="signup-card">
    <div class="brand-header">
      <img src="/static/vertex_logo.svg" alt="Vertex Logo" class="brand-logo" width="54" height="54">
      <h1>Create Account</h1>
      <p>Set up your unified gaming library on Vertex</p>
    </div>
    {error_html}
    <form method="POST" action="/signup">
      <!-- Bot Honeypot (hidden from human users) -->
      <div style="display:none;" aria-hidden="true">
        <label for="website">Website</label>
        <input type="text" id="website" name="website" tabindex="-1" autocomplete="off">
      </div>

      <div class="field-group">
        <label for="reg-username">Username</label>
        <input
          id="reg-username"
          type="text"
          name="username"
          value="{safe_user}"
          autocomplete="username"
          autocapitalize="none"
          spellcheck="false"
          placeholder="e.g. gamer94"
          required
        >
        <span class="hint">3-32 characters (letters, numbers, underscores, periods).</span>
      </div>

      <div class="field-group">
        <label for="reg-email">Email (Optional)</label>
        <input
          id="reg-email"
          type="email"
          name="email"
          value="{safe_email}"
          autocomplete="email"
          placeholder="Optional: for notifications"
        >
      </div>

      {invite_field_html}

      <div class="field-group">
        <label for="reg-password">Password</label>
        <input
          id="reg-password"
          type="password"
          name="password"
          autocomplete="new-password"
          placeholder="Minimum 8 characters"
          minlength="8"
          required
        >
      </div>

      <div class="field-group">
        <label for="reg-confirm">Confirm Password</label>
        <input
          id="reg-confirm"
          type="password"
          name="confirm_password"
          autocomplete="new-password"
          placeholder="Re-enter password"
          minlength="8"
          required
        >
      </div>

      <button type="submit" class="btn-submit">Register</button>
    </form>
    <div class="signup-footer">
      Already have an account? <a href="/login">Sign in</a>
    </div>
  </main>
</body>
</html>"""
    return HTMLResponse(content, status_code=200 if not error_msg else 400)


def render_approval_notice_page(username: str) -> HTMLResponse:
    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Account Pending Approval | Vertex</title>
  <link rel="icon" type="image/svg+xml" href="/static/vertex_logo.svg">
  <style>
    body {{
      margin: 0; min-height: 100dvh;
      font-family: system-ui, -apple-system, sans-serif;
      background: #0b0f19; color: #f1f5f9;
      display: flex; align-items: center; justify-content: center;
      padding: 1.25rem;
    }}
    .card {{
      max-width: 440px; width: 100%;
      background: #131b2e; border: 1px solid #26334d;
      border-radius: 16px; padding: 2.25rem 2rem; text-align: center;
      box-shadow: 0 20px 50px rgba(0,0,0,0.55);
    }}
    .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
    h1 {{ font-size: 1.45rem; margin: 0 0 0.5rem 0; color: #f8fafc; }}
    p {{ color: #94a3b8; font-size: 0.92rem; line-height: 1.5; margin-bottom: 1.5rem; }}
    a {{ color: #38bdf8; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⏳</div>
    <h1>Registration Received</h1>
    <p>
      Thank you for registering, <strong>{html.escape(username)}</strong>! Your account is currently
      <strong>awaiting administrator approval</strong>. Once an administrator activates your account,
      you will be able to sign in.
    </p>
    <a href="/login">&larr; Return to Sign In</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content, status_code=200)


async def handle_signup_get(request: Request) -> Response:
    if web_auth.is_request_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    policy = system_db.get_registration_policy()
    invite = request.query_params.get("invite", "").strip()
    return render_signup_page(policy=policy, default_invite=invite)


async def handle_signup_post(request: Request) -> Response:
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        return HTMLResponse("Too many registration attempts. Please try again later.", status_code=429)

    form = await request.form()
    # Bot honeypot check
    if form.get("website"):
        logger.warning("Bot honeypot triggered on /signup from IP=%s", client_ip)
        return RedirectResponse(url="/login", status_code=303)

    policy = system_db.get_registration_policy()
    if policy == "disabled":
        return render_signup_page(policy="disabled")

    username = str(form.get("username") or "").strip()
    email = str(form.get("email") or "").strip() or None
    password = str(form.get("password") or "")
    confirm_password = str(form.get("confirm_password") or "")
    invite_code = str(form.get("invite_code") or "").strip()

    if password != confirm_password:
        return render_signup_page(
            policy=policy,
            error_msg="Passwords do not match.",
            default_username=username,
            default_email=email or "",
            default_invite=invite_code,
        )

    try:
        system_db.validate_username(username)
    except ValueError as ve:
        return render_signup_page(
            policy=policy,
            error_msg=str(ve),
            default_username=username,
            default_email=email or "",
            default_invite=invite_code,
        )

    pw_err = passwords.validate_password_policy(password)
    if pw_err:
        return render_signup_page(
            policy=policy,
            error_msg=pw_err,
            default_username=username,
            default_email=email or "",
            default_invite=invite_code,
        )

    # Check if username exists
    existing = system_db.get_user_by_username(username)
    if existing:
        return render_signup_page(
            policy=policy,
            error_msg=f"Username '{username}' is already taken.",
            default_username=username,
            default_email=email or "",
            default_invite=invite_code,
        )

    # Check policy specifics
    if policy == "invite":
        if not invite_code:
            return render_signup_page(
                policy=policy,
                error_msg="An invite code is required.",
                default_username=username,
                default_email=email or "",
            )
        # Verify invite before creating user
        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        invites = system_db.list_invites()
        import hashlib
        code_hash = hashlib.sha256(invite_code.encode("ascii")).hexdigest()
        matched = next((inv for inv in invites if inv["code_hash"] == code_hash), None)
        if not matched or matched["status"] != "active":
            return render_signup_page(
                policy=policy,
                error_msg="Invalid, expired, or already-used invite code.",
                default_username=username,
                default_email=email or "",
                default_invite=invite_code,
            )

    # Determine status based on policy
    status = "pending" if policy == "approval" else "active"
    status_reason = "awaiting_approval" if policy == "approval" else None

    try:
        user_id = system_db.create_user(
            username=username,
            password=password,
            email=email,
            role="user",
            status=status,
            status_reason=status_reason,
        )
    except ValueError as ve:
        return render_signup_page(
            policy=policy,
            error_msg=str(ve),
            default_username=username,
            default_email=email or "",
            default_invite=invite_code,
        )

    if policy == "invite":
        system_db.consume_invite(invite_code, user_id)

    if status == "pending":
        return render_approval_notice_page(username)

    # Active user: create session and log in immediately
    system_db.record_login(user_id)
    session_id = web_auth.create_web_session(username, request.headers.get("user-agent", ""))
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=web_auth.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=web_auth.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=web_auth._is_https_request(request),
        samesite="lax",
        path="/",
    )
    logger.info("New user registered and signed in: %r (policy=%s)", username, policy)
    return response
