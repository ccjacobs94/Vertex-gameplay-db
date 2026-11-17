# Security Policy

## Reporting a Vulnerability

Vertex is a private self-hosted application designed to run on trusted household LANs or private VPNs behind HTTPS reverse proxies.

If you discover a security vulnerability (for example: authentication bypass, user-scope isolation leak, CSRF/Host header bypass, credential encryption flaw, or path traversal):

1. **Do not open a public GitHub issue.**
2. Contact the repository maintainer privately via GitHub Security Advisories ("Report a vulnerability") or direct private message.
3. Include steps to reproduce cleanly using synthetic test fixtures (never share personal `.db` exports or real API keys).

## Supported Threat Model & Safeguards

- Always-on local authentication with per-IP + per-username login rate limiting.
- Explicit per-user `UserScope` required on every library operation (`user_id` validated against path traversal).
- Fail-closed Fernet (`MultiFernet`) encryption at rest using explicit `VERTEX_SECRET_KEY` / `GAMEHUB_SECRET_KEY`.
- Strict `Host` validation, CSRF checks (`Sec-Fetch-Site` / `Origin`), and `trusted_proxies`-scoped forwarded headers.
- Static-only service worker caching (`sw.js` never caches `/api/*`).
