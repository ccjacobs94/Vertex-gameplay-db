# ADR 0006: Private-Network Streamable HTTP MCP Transport and PAT Authentication

## Status

Accepted

## Context

Legacy Vertex exposed Model Context Protocol (MCP) tools via `mcp_server.py` using both `/mcp` and a legacy `/sse` transport, with manual environment configuration and without per-client revocable scope enforcement at the tool boundary. Because self-hosted Vertex instances run in single-household or small-group LAN / Tailscale environments (frequently accessed through local stdio bridges or remote AI assistants like Claude and ChatGPT), exposing unauthenticated SSE endpoints or full multi-tenant OAuth dynamic client registration introduces unnecessary attack surface and operational complexity.

## Decision

1. **Streamable HTTP Only (`/mcp`)**: Standardized exclusively on MCP Streamable HTTP (`mcp>=2.0`, `MCPServer.streamable_http_app`) mounted both on the main ASGI application at `/mcp` (`VERTEX_MCP_ENABLED=true`) and as an isolated dedicated private listener via `vertex serve-mcp` (`VERTEX_MCP_BIND_HOST`, `VERTEX_MCP_PORT=8096`). Legacy `/sse` and `/messages` return `410 Gone` on the standalone listener with explicit upgrade guidance.
2. **Scoped, Revocable PAT Authentication**: Every HTTP request and every individual tool/resource invocation validates a Bearer Personal Access Token via `IdentityService.resolve_pat(...)`, immediately reflecting token revocation, expiry, or admin user suspension, and enforcing `read:library` vs `write:library` scopes at each handler.
3. **Host, Origin, and Session-Owner Isolation**: `McpSecurityMiddleware` enforces strict `Host` validation (`400`), `Origin` validation against `get_allowed_mcp_origins()` (`403`, allowing absent `Origin` for non-browser CLI/desktop clients), trailing-slash normalization without `307` redirects, and `mcp-session-id` binding to the originating authenticated `user_id` (`403` on cross-user session hijack).
4. **Prompt-Injection Output Hardening**: All 23 tools and 2 resources sanitize output strings recursively (`_sanitize_output`) before returning JSON payloads.

## Consequences

- Clients on private networks (localhost, LAN, Tailscale, reverse-proxied personal domains) authenticate cleanly using scoped Bearer PATs without OAuth dynamic registration state.
- Compromised or stale AI client credentials can be scoped (`read:library` only) or revoked immediately in Account Settings.
- All MCP mutations (`add_to_wishlist`, `update_game_progress`, `mark_as_purchased`, etc.) delegate directly to domain services under `UserScope`, upholding every invariant and audit/ownership guarantee.

## Alternatives Considered

- **Full OAuth 2.1 Authorization Server + Dynamic Client Registration (DCR)**: Rejected for the self-hosted target deployment because it adds certificate/callback setup friction, dynamic client table maintenance, and broader unauthenticated registration surface on homelab reverse proxies.
- **Retain Legacy Unauthenticated `/sse` Transport**: Rejected because `/sse` bypasses modern Streamable HTTP session negotiation and makes per-request scope and origin verification brittle across proxies.

## Revisit Triggers

- Adopting multi-tenant public SaaS hosting or integrating third-party hosted AI connectors that strictly mandate OAuth 2.1 Dynamic Client Registration instead of static/revocable Bearer PATs.
