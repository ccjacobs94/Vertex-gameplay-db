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
- `search_game_library`: Filter by title, platform, completion status, genre, tag, or favorites.
- `get_what_to_play_recommendations`: Personalized backlog recommendations tailored to your highest-rated genres.
- `update_game_progress`: Update completion status, rating (`1–10`), notes, or favorite flag.
- `update_game_genres`: Update a game's genres and/or tags. Merge is the default: given genres/tags are added and nothing is removed. Use `mode="replace"` only for an explicit full replacement. Labels outside the controlled taxonomy in `taxonomy.py` are ignored.
- `add_game_to_library`: Add a new game (with automatic cover art lookup).
- `find_duplicate_cross_platform_games` & `reconcile_cross_platform_games`: Identify and merge duplicate titles across platforms.
- `enrich_library_genres`: Backfill missing genres across your catalog. Merge, never replace: discovered genres are added to a game's existing genres, so a Steam-only Action/Adventure list can never drop a manual Platformer, Puzzle, or Stealth tag.

### Genres vs Tags

Genres are broad gameplay categories (1–3 per game, e.g. `Shooter`, `RPG`, `Puzzle`). Tags are mechanics, modes, themes, and settings (e.g. `Stealth`, `Open World`, `Co-op`, `Survival Horror`). The split exists because Steam's official genre list has no Stealth genre, and it lists both Portal 2 and Splinter Cell Blacklist as only Action/Adventure. The controlled lists live in `taxonomy.py`; enrichment only adds labels from those lists, and `Free To Play`, `Casual`, `Utilities`, and `Indie` are tags, not genres.

### Exposing MCP Publicly (Optional OAuth 2.1 + Cloudflare Tunnel)

If you want cloud AI clients (such as Google Gemini Web or Claude Web) to connect to your MCP server over HTTPS:

1. Generate a strong `MCP_CLIENT_SECRET` in `.env`:
   ```bash
   python3 -c "import secrets; print('gh_sec_' + secrets.token_urlsafe(24))"
   ```
2. Set `MCP_PUBLIC_DOMAIN=https://your-mcp-domain.example.com` and `MCP_CLIENT_SECRET=<your-secret>` in `.env`.
3. *(Optional)* To run the bundled `cloudflared` tunnel container, set `COMPOSE_PROFILES=tunnel` and `TUNNEL_TOKEN=<your-cloudflare-tunnel-token>` in `.env`, and point your Cloudflare Tunnel public hostname to `http://game-hub:8096`.

---

## 👥 Multi-User Accounts, Registration & Hosted Edition

Game Hub supports both single-user and multi-user configurations, with independent game databases per user (`data/users/<user_id>/games.db`).

### Registration Policies (Self-Hosted)
Administrators can configure self-registration via the web UI Settings modal (`/api/admin/settings`):
- **Disabled** (`disabled`, default): Only administrators can create accounts in the Admin panel.
- **Invite Only** (`invite`): New users require a single-use invite code generated by an admin.
- **Approval Required** (`approval`): Anyone can register; accounts remain in pending state until approved by an administrator.
- **Open** (`open`): Anyone can register and immediately log in.

### Account Lifecycles & Security
- **Active**: Full access to web UI, library APIs, and MCP tokens.
- **Pending (`awaiting_approval`)**: Awaiting administrator activation; sign-in is blocked.
- **Pending (`awaiting_payment`) / Inactive (`subscription_lapsed`)**: In hosted edition, users are granted restricted billing-only sessions allowing access to `/billing` to start or manage their subscription.
- **Inactive (`admin_suspended`)**: Fully suspended by an administrator; all sessions and OAuth tokens are revoked immediately. Automated billing events or payment webhooks **never** override an admin suspension.
- **Account Deletion & Irreversible Removal**: Administrators can disable/re-enable accounts or permanently delete an account alongside all associated library data, platform credentials, active sessions, and history (`DELETE /api/admin/users/{user_id}`). In the web UI, deletion is guarded by a confirmation dialog with an explicit irreversible task warning that requires typing the username before deletion is executed. Self-deletion and deleting the last active admin are strictly prohibited.

### Hosted Edition (SaaS Subscription Mode)
When running with `VERTEX_EDITION=hosted`, new users register, start a monthly subscription via Stripe Checkout, and manage their invoices in the Stripe Customer Portal. Webhook verification uses SHA-256 HMAC signatures with idempotency checks.

---

## 🔄 CI/CD & Multi-Stage Deployment

Game Hub includes automated CI/CD deployment pipelines for production and QA:

- **Production Container (`main` branch)**:
  - Deploys container `game-hub` on ports `8095` (Web UI) and `8097` (MCP).
  - Uses production data stored in `./data`.
  - Automatically creates a timestamped database backup (`data/games.db.bak-YYYYMMDD-HHMMSS`) before redeploying.
- **QA Validation Container (`develop` branch)**:
  - Spins up container `game-hub-qa` on ports `8098` (Web UI) and `8099` (MCP).
  - Uses an isolated data volume in `./data-qa` to guarantee production data is never touched or mutated during QA.
  - Initial deployment seeds a snapshot from `./data` if empty, allowing realistic staging without risk.
  - Accessible directly over your home LAN (`http://<LAN-IP>:8098`) or via Tailscale (`http://<Tailscale-IP>:8098`).
- **Automation Mechanisms**:
  - **Git post-merge Hook**: `.githooks/post-merge` automatically triggers `scripts/deploy.sh` whenever code is merged into `main` or `develop`.
  - **Deploy Script**: `scripts/deploy.sh [main|develop|auto]` handles automated builds, volume isolation, database backups, and healthcheck verification.
  - **GitHub Actions**: `.github/workflows/cicd.yml` verifies secrets isolation, runs Python/JS syntax checks, and executes container quality tests before triggering self-hosted deployments.

---

## 🔒 Security Notes

- **Web Dashboard (`8095`)**: Designed for trusted local network (LAN) or VPN/Tailscale access. Do **not** expose port `8095` directly to the public internet without an authenticating reverse proxy (such as Cloudflare Access, Authelia, or Authentik).
- **MCP Server (`8096` / `8097`)**: All `/mcp` and `/sse` requests require a per-user `Authorization: Bearer <token>` issued via `/oauth/authorize`, which requires you to be signed in to your Vertex web session (you are prompted to sign in otherwise). Each token is bound to that user's `user_id` in `system.db`, and every MCP tool query runs against that user's own `games.db`. `MCP_CLIENT_SECRET` is no longer accepted as a bearer token, and the `client_credentials` grant is disabled. Your Vertex login must have a matching row in `system.db`'s `users` table.

---

## 📄 License

Released under the [MIT License](LICENSE).
