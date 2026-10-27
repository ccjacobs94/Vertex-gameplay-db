# 🎮 Game Hub — Unified Game Library & MCP Server

**Game Hub** is a self-hosted, multi-platform video game library tracker, metadata enricher, and [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server. It aggregates your games, playtime, achievements, and backlog statuses across **PC (Steam)**, **Xbox / PC Game Pass**, **PlayStation 4 / 5**, and **Nintendo Switch** into a fast, responsive web dashboard and exposes your library to AI assistants.

---

## ✨ Features

- **Automated Multi-Platform Sync**:
  - **Steam (PC)**: Syncs owned games, playtime, last-played timestamps, and achievements via the Steam Web API.
  - **Xbox & PC Game Pass**: Syncs title history, gamerscore/achievements, and playtime via [OpenXBL](https://xbl.io/).
  - **PlayStation 4 & 5**: Syncs PSN titles, playtime, and trophy progress via `psnawp`.
  - **Nintendo Switch & Manual Titles**: Add physical or digital console titles with keyless high-res cover art & release year lookup (Steam Store Search + Wikipedia API, with optional RAWG enrichment).
- **Smart Cross-Platform Reconciliation**:
  - Detects duplicate games owned across multiple platforms (e.g., Steam + Xbox) and lets you merge them into a unified library card while preserving per-platform stats.
- **User Edits Protected**:
  - Manually locking a completion status (`Completed`, `Beaten`, `Playing`, `Plan to Play`, `Dropped`, `Not Played`), rating a game, favoriting, or adding personal notes will never be overwritten by background syncs.
- **Built-in MCP Server (Streamable HTTP + SSE + OAuth 2.1)**:
  - Connect Claude, Google Gemini, Cursor, or Antigravity directly to your game library to ask *"What should I play next?"*, analyze your taste profile, or update your backlog by voice/chat.

---

## 🚀 Quick Start (Docker Compose)

### 1. Clone & Configure Environment

```bash
git clone https://github.com/<your-username>/game-hub.git
cd game-hub
cp .env.example .env
```

Edit `.env` and fill in whichever platform credentials you use (all platforms are optional; unconfigured platforms are skipped automatically):

| Variable | Description |
| :--- | :--- |
| `STEAM_API_KEY` | Get from [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| `STEAM_ID` | Your 17-digit SteamID64 or custom profile vanity name |
| `XBL_API_KEY` | Free API key from [xbl.io](https://xbl.io/) |
| `PSN_NPSSO` | 64-character `npsso` token from [ca.account.sony.com/api/v1/ssocookie](https://ca.account.sony.com/api/v1/ssocookie) |
| `RAWG_API_KEY` | *(Optional)* Extra genre/critic metadata from [rawg.io/apidocs](https://rawg.io/apidocs) |
| `SYNC_INTERVAL_HOURS` | Background auto-sync interval in hours (default: `6`) |

### 2. Build & Start

```bash
docker compose up -d --build
```

- **Web UI**: Open `http://localhost:8095`
- **MCP Endpoint (Streamable HTTP)**: `http://localhost:8097/mcp`

---

## 🤖 Connecting AI Assistants (MCP)

Game Hub runs an MCP server on container port `8096` (mapped to `localhost:8097` on the host by default) with the following tools:
- `get_gaming_profile_and_metrics`: Aggregate playtime, platform breakdown, and top-rated taste profile.
- `search_game_library`: Filter by title, platform, completion status, genre, or favorites.
- `get_what_to_play_recommendations`: Personalized backlog recommendations tailored to your highest-rated genres.
- `update_game_progress`: Update completion status, rating (`1–10`), notes, or favorite flag.
- `add_game_to_library`: Add a new game (with automatic cover art lookup).
- `find_duplicate_cross_platform_games` & `reconcile_cross_platform_games`: Identify and merge duplicate titles across platforms.
- `enrich_library_genres`: Backfill missing genres across your catalog.

### Exposing MCP Publicly (Optional OAuth 2.1 + Cloudflare Tunnel)

If you want cloud AI clients (such as Google Gemini Web or Claude Web) to connect to your MCP server over HTTPS:

1. Generate a strong `MCP_CLIENT_SECRET` in `.env`:
   ```bash
   python3 -c "import secrets; print('gh_sec_' + secrets.token_urlsafe(24))"
   ```
2. Set `MCP_PUBLIC_DOMAIN=https://your-mcp-domain.example.com` and `MCP_CLIENT_SECRET=<your-secret>` in `.env`.
3. *(Optional)* To run the bundled `cloudflared` tunnel container, set `COMPOSE_PROFILES=tunnel` and `TUNNEL_TOKEN=<your-cloudflare-tunnel-token>` in `.env`, and point your Cloudflare Tunnel public hostname to `http://game-hub:8096`.

---

## 🔒 Security Notes

- **Web Dashboard (`8095`)**: Designed for trusted local network (LAN) or VPN/Tailscale access. Do **not** expose port `8095` directly to the public internet without an authenticating reverse proxy (such as Cloudflare Access, Authelia, or Authentik).
- **MCP Server (`8096` / `8097`)**: When `MCP_CLIENT_SECRET` is set in `.env`, all `/mcp` and `/sse` requests require a valid `Authorization: Bearer <token>` issued via the `/oauth/authorize` consent screen (which prompts for `MCP_CLIENT_SECRET`) or direct Bearer authentication with `MCP_CLIENT_SECRET`.

---

## 📄 License

Released under the [MIT License](LICENSE).
