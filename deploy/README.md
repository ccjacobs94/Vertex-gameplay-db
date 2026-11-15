# Vertex v2 Deployment Examples (Private Self-Hosted)

- `docker-compose.yml`: Binds loopback (`127.0.0.1:8095`) by default without any public tunnel sidecars.
- `Caddyfile.example`: Reverse-proxy private LAN/Tailscale HTTPS configuration (required for Service Worker & Secure cookies on non-localhost hostnames).
- `Dockerfile.v2`: Compiles modular TypeScript UI assets into `frontend/dist/` at image build time so Node.js is never required on the runtime server, and runs explicit `python -m vertex migrate` before starting `serve`.

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
