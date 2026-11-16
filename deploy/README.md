# Vertex v2 Deployment Examples (Private Self-Hosted)

- `docker-compose.yml`: Binds loopback (`127.0.0.1:8095`) by default without any public tunnel sidecars. Includes an optional `dedicated-mcp` profile (`vertex-mcp` running `vertex serve-mcp` on loopback `127.0.0.1:8097` -> container `8096`) when separating Web UI and MCP listeners, alongside the built-in `/mcp` endpoint (`VERTEX_MCP_ENABLED=true`).
- `Caddyfile.example`: Reverse-proxy private LAN/Tailscale HTTPS configuration (required for Service Worker, Secure cookies, and optional Web Push on non-localhost hostnames). Includes `flush_interval -1` for Streamable HTTP MCP.
- `Dockerfile.v2`: Compiles modular TypeScript UI assets into `frontend/dist/` at image build time so Node.js is never required on the runtime server, and runs explicit `python -m vertex migrate` before starting `serve`.

## Phase 4–6 Configuration Notes

- **Wishlist Sync**: Controlled via `VERTEX_WISHLIST_SYNC_ENABLED` (default `true`), `VERTEX_WISHLIST_SYNC_INTERVAL_SECONDS` (default `21600`), and optional `VERTEX_ITAD_API_KEY`.
- **Optional Web Push / VAPID**: Disabled by default (`VERTEX_WEB_PUSH_ENABLED=false`). Enable explicitly only behind trusted HTTPS; private VAPID keys are generated automatically and encrypted at rest via `SecretBox` in `system.db`.
- **Private MCP Transport**: Exposed at `/mcp` via Streamable HTTP requiring scoped Bearer Personal Access Tokens (`read:library` / `write:library`). Set `VERTEX_MCP_ALLOWED_ORIGINS` if accessing from custom browser-based MCP clients beyond localhost / `VERTEX_ALLOWED_HOSTS` / Claude / ChatGPT.

## Manual Release Checklist

1. Create a verified whole-instance backup first:
   ```bash
   PYTHONPATH=src python3 -m vertex backup --data-dir /path/to/data-v2 --dest /path/to/backups/$(date +%Y%m%d-%H%M%S)
   ```
2. Build and start with explicit health checks:
   ```bash
   docker compose -f deploy/docker-compose.yml up -d --build --wait
   ```
   A failing `/readyz` healthcheck aborts cleanly rather than masking failures with `|| true`.
