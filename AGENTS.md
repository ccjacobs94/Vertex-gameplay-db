# Game Hub — Project Guidelines

Game Hub is a self-hosted, multi-platform game library tracker and MCP server running in Docker on this Linux home server. It aggregates games, playtime, achievements, and backlog statuses across **PC (Steam)**, **Xbox / PC Game Pass**, **PlayStation 4 / 5**, and **Nintendo Switch**.

## Architecture & Key Files

- `app.py`: FastAPI web server (port `8090` inside container, mapped to `8095` on the host) and background auto-sync scheduler.
- `db.py`: SQLite database layer (`data/games.db` on the host, `/app/data/games.db` inside the container).
- `sync_engine.py`: Automated sync connectors for Steam Web API, Xbox (`xbl.io`), PSN (`psnawp`), plus keyless game catalog & cover art lookup (Steam Store Search + Wikipedia API).
- `mcp_server.py`: Model Context Protocol (MCP) server exposing library metrics, backlog search, recommendations, and status updates to AI agents.
- `static/`: Responsive web frontend (`index.html`, `styles.css`, `app.js`).

## Endpoints & Operations

- **Local Web UI URL**: `http://localhost:8095` (or `http://<your-lan-or-tailscale-ip>:8095`)
- **Streamable HTTP MCP URL**: `http://localhost:8097/mcp`
- **Rebuild & Restart Container**:
  ```bash
  docker compose up -d --build
  ```
- **Trigger Manual Sync via CLI**:
  ```bash
  curl -X POST http://localhost:8095/api/sync
  ```
- **Preserve User Edits**: When modifying `db.py` or `sync_engine.py`, never overwrite a game's `completion_status` during automated syncs if `status_locked = 1`, and never overwrite `user_rating`, `notes`, or `favorite`.
- **Genres & Tags**: Genres are broad categories, tags are mechanics/modes/themes (see `taxonomy.py`). Enrichment and syncs merge, never replace: discovered genres/tags are added to the existing lists, existing entries are never dropped. The `update_game_genres` MCP tool defaults to merge mode for the same reason. Labels outside the controlled taxonomy are ignored, never written silently.
