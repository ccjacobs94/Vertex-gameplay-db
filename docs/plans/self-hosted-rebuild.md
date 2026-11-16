# Vertex self hosted rebuild plan

Prepared November 15, 2026. Repository contract document: `docs/plans/self-hosted-rebuild.md`.

## Direction

Rebuild Vertex as a private, multi-account game library for one household and its invited friends. Keep Python, FastAPI, SQLite, Docker Compose, and the existing product knowledge. Replace the large, intertwined modules with a modular application whose features can be understood, tested, and contributed to independently.

Deliver the replacement in working slices, preserving the existing application until migration and feature acceptance pass. A clean architecture does not require rewriting every proven algorithm. Retain the taxonomy, regression cases, connector normalization rules, and useful UI behavior where they remain correct.

Assumptions for this plan: approximately 2–20 accounts, one home server, a browser interface accessible over LAN or VPN, and optional outbound internet requests for platform sync and prices. These are design targets, not measured capacity. “Self hosted” means no public service, subscription system, required vendor account for Vertex, or public inbound endpoint. If the intention is a native desktop client or fully disconnected operation, the UI and connector scope need a separate decision. Manual library management must work without internet access.

## What the repository tells us

| Evidence in the repository | Consequence for the rebuild |
| --- | --- |
| `app.py`: HTTP routes, authentication wiring, scheduled loops, MCP startup, and startup data operations | Reduce the entry point to composition and lifecycle management; move behavior into feature modules. |
| `db.py`: Storage, reconciliation, sessions, taxonomy updates, wishlist, and analytics combined | Separate application rules from SQL repositories and read models. |
| `static/app.js` and `static/styles.css`: Single monolithic UI script and stylesheet | Split views, dialogs, API access, state, and styling by feature. |
| `db.get_connection()` uses user context but can fall back to a default database in single-user mode | Make every personal operation require a resolved `UserScope`, including installations with only one account. |
| `app.lifespan()` performs legacy copying, cleanup, backfill, and scheduler startup | Move data transformations to explicit, versioned maintenance commands with reports. Startup must not infer which libraries to purge. |
| `play_sessions`, `game_sessions`, and `daily_playtime_ledger` coexist | Establish one authoritative session model (`play_observations` -> `provider_baselines` / `play_sessions`) and documented derived projections (`daily_playtime_ledger`); avoid double counting. |
| `static/sw.js` caches successful GET `/api/*` responses in one cache | Cache only static assets in the replacement SW; purge legacy caches during upgrade and clear in-memory user state on logout. |
| Production deployment uses SQLite backup APIs and includes per-user databases | Preserve SQLite online backup API (`sqlite3.Connection.backup`); add consistent whole-instance backup sets, SHA-256 manifest, key recovery instructions, and restore checks. |
| `scripts/deploy.sh` ignores failed health checks with `|| true`; Git hooks and CI deploy on integration | Make release installation explicit and health failures fatal. Ordinary contribution workflows must not operate the home server. |
| 28 existing test modules (295 baseline tests on `develop`) | Preserve existing invariant tests untouched as the legacy oracle and add equivalent regression tests against the replacement services. |

Two inventory corrections precede implementation (documented in [feature-parity-ledger.md](feature-parity-ledger.md)):

1. `app.py:2715-2843` exports a raw SQLite `.db` snapshot via SQLite's online backup API and imports `.db`/`.sqlite`/`.sqlite3` files (`shutil.move`); earlier `features.md` documentation inaccurately called this JSON export/import. The rebuild preserves a validated legacy SQLite importer and adds versioned JSON portability cleanly.
2. `mcp_server.py` declares **23** tools (including `update_session_notes` and `import_homeassistant_sessions` omitted from older 21-tool summaries), plus **2** resources (`vertex://wishlist/active`, `vertex://wishlist/{id}/history`) and **Streamable HTTP / SSE / legacy root SSE** transports.
3. Username vs `user_id` DB directory resolution: `db.py` / `system_db.py` resolve `USERS_DB_DIR / str(user_id) / "games.db"`, whereas `create_user()` sets `id = username.lower()` and `ensure_user()` allows arbitrary IDs. Migration reconciles against `system.db` `users` records explicitly and never guesses ownership from folder names alone.

## Target architecture

### Modular monolith with explicit boundaries

```
src/vertex/
  main.py                  # App factory, dependency wiring, lifecycle; zero domain logic
  config.py                # Validated settings with explicit private-host defaults
  identity/                # Accounts, local auth, invites, admin guardrails, PATs, credentials
  catalog/                 # Public metadata cache, controlled genres/tags taxonomy
  library/                 # Personal library, ownership vs Game Pass, reconciliation, portability
  activity/                # Observations, provider baselines, play_sessions, locks, ledger projection
  wishlist/                # Editions/SKUs, platform targets, prices, deals, push subscriptions
  analytics/               # Derived read models & calculations
  integrations/
    platforms/             # Normalized Steam/Xbox/PSN/GOG adapters, scheduled sync, telemetry
    prices/                # Six store price adapters + automated wishlist sync orchestrator
    push/                  # Optional, disabled-by-default Web Push / VAPID alert delivery
    homeassistant/         # History, CSV, scoped event ingestion
  interfaces/
    http/                  # FastAPI APIRouters, CSRF/Host/Origin middleware, Pydantic schemas
    mcp/                   # Authenticated Streamable HTTP MCP server (23 tools + 2 resources)
    cli.py                 # Maintenance CLI (`migrate`, `backup`, `restore`, `import-legacy`, `serve-mcp`, etc.)
  migration/               # Versioned legacy SQLite single-user & multi-user importer
  infrastructure/          # SQLite connection factory (`UserScope`), Fernet secrets, migrations, jobs, backup
migrations/
  system/                  # Ordered SHA-256 checksummed system.db SQL migrations
  library/                 # Ordered SHA-256 checksummed user games.db SQL migrations
frontend/
  src/                     # Modular TypeScript browser UI + OpenAPI-generated types + build pipeline
deploy/                    # Private-by-default Compose (+ dedicated-mcp profile), Caddy TLS example, release Dockerfile
```

Domain and application services never import FastAPI, HTTP clients, or frontend code. API routers, durable jobs, CLI commands, and MCP tool handlers call the same application services with an explicit `UserScope`.

## Delivery roadmap

| Phase | Concrete output | Acceptance gate | Status in this branch |
| --- | --- | --- | --- |
| 0 — Establish the contract | Corrected feature ledger, route/tool/table manifest, synthetic migration fixtures, ADRs, reproducible baseline | Every feature mapped to keep/remove/defer; legacy invariant tests untouched; baseline green | Complete |
| 1 — Private usable core | Package skeleton, local accounts, invites, admin guardrails, `UserScope`, migrations, manual library API + UI, backup/restore CLI, no billing runtime | Two users maintain isolated libraries; fresh install and clean restore work offline | Complete |
| 2 — Library sync and catalog | Durable SQLite job queue, Steam/Xbox/PSN/GOG adapters, metadata/taxonomy merge, reconciliation, achievements | Retries do not duplicate records; preservation invariants pass; provider failures isolated | Complete |
| 3 — Activity and insights | Unified observation/baseline/session model, locked sessions, deletion baseline protection, daily ledger, HA + live telemetry | No double counting; locked edits survive replay; session deletion does not cause recreation | Complete |
| 4 — Wishlist and alerts | Editions/SKUs, 6 price adapters, `/api/wishlist/sync` + `/api/wishlist/candidates`, deals, idempotent purchase conversion, optional Web Push | Currency/freshness preserved; purchases idempotent; zero cross-account caching; push disabled by default | Complete |
| 5 — MCP and compatibility | All 23 tools and 2 resources calling shared domain services; scoped revocable PATs; Streamable HTTP `/mcp` + `serve-mcp` + ADR 0006 | Tool contract suite, owner isolation, session-hijack guard, PAT scope/revocation, Host/Origin validation pass | Complete |
| 6 — Migration and release | Legacy importer parity (wishlist, deals, push, library), backup/restore rehearsal, ops/deploy docs, frontend/OpenAPI parity | Retained features accepted; migration parity verified; `./scripts/check.sh` green | Ready for cutover validation |
