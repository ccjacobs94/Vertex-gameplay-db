# ADR 0003: Private Self-Hosted Access, Authentication, and Encryption

- **Status**: Accepted
- **Date**: 2026-11-15

## Context

Legacy Vertex included hosted SaaS constructs (Stripe subscriptions, billing-only sessions, public registration/approval queue), bundled public Cloudflare Tunnel assumptions, optional auth disabling (`VERTEX_REQUIRE_AUTH=false`), a fallback envelope key for credential decryption, and a service worker (`static/sw.js`) caching `GET /api/*` responses.

## Decision

1. **Always-on Local Authentication & Closed Bootstrap**: Every personal/admin endpoint requires authentication, even for 1 account. First run exposes a one-time bootstrap flow creating the initial administrator and closing permanently once an admin exists.
2. **Household Accounts & Invites Only**: Remove Stripe, billing sessions, subscription states, open registration, and the approval queue from the replacement runtime. Support `admin` and `member` roles with `active`, `invited`, and `suspended` states. Invitations expire and consume atomically inside a transaction. Last-active-admin and self-deletion/self-demotion protections are enforced server-side.
3. **Fail-Closed Secrets at Rest**: Connector credentials use Fernet (`MultiFernet` supported for controlled rotation) keyed by explicit `VERTEX_SECRET_KEY` / `GAMEHUB_SECRET_KEY`. Never synthesize ephemeral keys or fall back silently to weak hardcoded keys when writing or decrypting new secrets; fail loudly if the key is missing when credential encryption/decryption is attempted. Legacy migration uses explicit source-key verification and reports any credentials needing re-entry.
4. **Private Network Binding & Security Middleware**: Bind default Compose examples to `127.0.0.1` behind a reverse proxy terminating private HTTPS (Caddy/LAN/Tailscale). Remove bundled public tunnels. Retain Host/Allowed-Origin validation, CSRF verification (`Sec-Fetch-Site` / `Origin`), strict forwarded-header trust (`trusted_proxies` only), and per-IP/username login throttling.
5. **Static-Only PWA Cache & Logout Hygiene**: Replacement `frontend/src/sw.js` caches static shell assets ONLY, refuses caching `/api/*`, and triggers client cache + memory wipe on logout.
6. **Personal Access Tokens & Deferred OAuth**: Provide DB-backed SHA-256-hashed revocable PATs with explicit capability scopes (`read:library`, `write:library`, `sync:platforms`) for CLI/HA/Phase 5 MCP use. Defer legacy OAuth 2.1 / dynamic client registration and SSE transport pending a Phase 5 client compatibility decision.

## Consequences

- Password resets for non-email home deployments occur via authenticated administrator action or the host CLI (`python -m vertex reset-password`).
- Public cloud AI assistants cannot assume reachability to a private LAN server without explicit VPN/private ingress.

## Alternatives Considered

- **Optional anonymous single-user mode (`VERTEX_REQUIRE_AUTH=false`)**: Rejected because single-account self-hosted servers frequently expose LAN/VPN URLs to household devices and scripts; requiring a session or scoped PAT uniformly removes fallback-scope bugs.
- **Keep Stripe/subscription tables active with free-tier bypass**: Rejected because carrying billing session restrictions and signup approval queues increases attack surface and complexity without purpose in a private household deployment.
- **Implicit ephemeral secret generation on first boot**: Rejected because restarting without persisting `.env` silently renders encrypted connector credentials unreadable; explicit key configuration fails fast and loudly.

## Revisit Triggers

- Revisit OAuth 2.1 / dynamic client registration deferral if a chosen private-network MCP client cannot send static `Authorization: Bearer <PAT>` headers in Phase 5.
- Revisit email/OIDC password recovery if household deployments grow beyond local administrator resets.
