# Vertex Feature Parity Ledger & Legacy Inventory

Generated from direct inspection of `develop` (`db5f83b`), correcting earlier planning summaries.
Legacy baseline: `scripts/check.sh` passes **295 tests across 28 test modules** (`tests/test_*.py`).

## Reconciled Documentation Discrepancies

| Item | Prior inventory/doc claim | Actual repository reality (`develop`) | Rebuild resolution |
| --- | --- | --- | --- |
| 1. Library Export/Import Format | Claimed `/api/user/library/export` and `/api/user/library/import` use JSON | `app.py:2715-2843` uses SQLite online `backup()` -> `.db` file download and validates/moves uploaded `.db`/`.sqlite`/`.sqlite3` files into `user_db_path` | Keep validated legacy SQLite import inspection (`src/vertex/library/portability.py`) without raw file swap into live storage; introduce versioned JSON export/import (`format_version: 1`) |
| 2. MCP Tool, Resource & Transport Count | Claimed 21 MCP tools | `mcp_server.py` registers **23** `@mcp.tool()` tools (including `update_session_notes` and `import_homeassistant_sessions`), **2** `@mcp.resource()` endpoints (`vertex://wishlist/active`, `vertex://wishlist/{id}/history`), and Streamable HTTP + SSE (`/mcp`, `/sse`, `/messages`, root SSE fallback) | Record all 23 tools, 2 resources, and transports below; expose through thin service adapter in Phase 5 after OAuth/SSE decision |
| 3. User DB Directory Naming | `AGENTS.md` / `deploy.sh` refer to `users/<username>/games.db` | `db.py` & `system_db.py` resolve `USERS_DB_DIR / str(user_id) / "games.db"`; `create_user()` sets `id = username.lower()`, `ensure_user()` allows arbitrary IDs | Never infer ownership from directory name alone during migration; resolve strictly through `system.db` `users` records and flag ambiguous directories |

---

## Root Modules Disposition

| Legacy File | Lines | Disposition | Target Location | Phase |
| --- | --- | --- | --- | --- |
| `app.py` | ~3,285 | Split into app factory + `APIRouter` modules + middleware + durable runner | `src/vertex/main.py`, `src/vertex/interfaces/http/` | 1–5 |
| `db.py` | ~5,837 | Split by feature repository + domain service (`UserScope` mandatory) | `src/vertex/{library,catalog,activity,wishlist,analytics}/` | 1–4 |
| `system_db.py` | ~1,315 | Split into SQLite factory, `identity` repo, `catalog` cache, migrations, jobs | `src/vertex/infrastructure/`, `src/vertex/identity/`, `src/vertex/catalog/` | 1–2 |
| `passwords.py` | ~89 | Retain `scrypt$` hashing & password policy verbatim for hash compatibility | `src/vertex/identity/passwords.py` | 1 |
| `taxonomy.py` | ~161 | Retain `GENRES`, `TAGS`, `RETIRED_GENRES`, normalization & merge logic | `src/vertex/catalog/taxonomy.py` | 1 |
| `context.py` | ~23 | Replace implicit global `ContextVar` fallback with explicit `UserScope` injection | `src/vertex/identity/models.py` | 1 |
| `web_auth.py` | ~617 | Replace with `identity` service + explicit Host/Origin/CSRF/throttling deps | `src/vertex/identity/service.py`, `src/vertex/interfaces/http/deps.py` | 1 |
| `signup.py` | ~450 | Remove public registration & approval queue; keep admin invites & onboarding | `src/vertex/identity/service.py`, `src/vertex/migration/` | 1 |
| `billing.py` | ~500 | Remove Stripe/hosted billing runtime; archive legacy rows only during migration | Removed (`src/vertex/migration/legacy_importer.py` archives reasons) | 1 |
| `sync_engine.py` | ~1,950 | Replace with normalized platform adapters (`PlatformAdapter`) + durable jobs | `src/vertex/integrations/platforms/` | 2 |
| `telemetry_engine.py` | ~750 | Move behind observation ingestion + `activity` pipeline + durable runner | `src/vertex/integrations/platforms/`, `src/vertex/activity/` | 3 |
| `homeassistant_sync.py` | ~276 | Move behind scoped ingress adapter + `activity` observation pipeline | `src/vertex/integrations/homeassistant/` | 3 |
| `wishlist_sync.py` | ~1,600 | Move into 6 store price adapters calling `wishlist` service | `src/vertex/integrations/prices/`, `src/vertex/wishlist/` | 4 |
| `web_push.py` | ~211 | Defer as optional disabled-by-default notification adapter | `src/vertex/wishlist/` (Phase 4 optional) | 4 |
| `mcp_server.py` | ~1,187 | Thin adapter calling shared domain services scoped via revocable PAT | `src/vertex/interfaces/mcp/` | 5 |
| `oauth_provider.py` | ~700 | Defer OAuth 2.1/DCR; revisit only if selected LAN/VPN MCP client requires it | Deferred (`docs/adr/0003-private-self-hosted-access-and-auth.md`) | 5 decision |
| `static/` | 3 assets | Replaced incrementally by modular TypeScript + non-API-caching SW under `frontend/` | `frontend/` | 1–4 |

---

## Complete HTTP Route Inventory (`app.py` 85 decorators across 70 paths + `/static` mount; `oauth_provider.py` 10 routes = 81 distinct non-OpenAPI paths, 95 handlers + `/static`)

### Auth, Onboarding, Invites, & Removed Hosted Routes

| Legacy Route | Methods | Target Rebuild Route | Decision | Phase |
| --- | --- | --- | --- | --- |
| `/login` | GET, POST | `POST /api/auth/login`, UI `/login` | Keep (local auth always enabled; add bootstrap flow) | 1 |
| `/logout` | GET, POST | `POST /api/auth/logout` | Keep (revokes DB session + clears client state) | 1 |
| `/signup` | GET, POST | `POST /api/auth/invites/accept` | Remove open signup & approval queue; keep invite redemption | 1 |
| `/billing` | GET | — | Remove (billing retired in self-hosted edition) | 1 |
| `/billing/checkout` | POST | — | Remove | 1 |
| `/billing/portal` | POST | — | Remove | 1 |
| `/billing/webhook` | POST | — | Remove | 1 |
| `/api/user/onboarding/complete` | POST | `POST /api/account/onboarding/complete` | Keep | 1 |

### UI Shell & Health

| Legacy Route | Methods | Target Rebuild Route | Decision | Phase |
| --- | --- | --- | --- | --- |
| `/` | GET | `/` | Keep modular SPA shell | 1 |
| `/manifest.webmanifest` | GET | `/manifest.webmanifest` | Keep | 1 |
| `/sw.js` | GET | `/sw.js` | Keep (static assets only; never cache `/api/*`) | 1 |
| `/healthz` | GET | `/healthz`, `/readyz` | Keep liveness + explicit DB/migration readiness | 1 |

### Account, Connections, Admin & MCP Token Management

| Legacy Route | Methods | Target Rebuild Route | Decision | Phase |
| --- | --- | --- | --- | --- |
| `/api/account` | GET | `GET /api/account` | Keep | 1 |
| `/api/account/timezone` | PATCH | `PATCH /api/account/timezone` | Keep (timezone + timezone_eras) | 1 |
| `/api/account/password` | POST | `POST /api/account/password` | Keep (revokes other sessions/tokens) | 1 |
| `/api/admin/users` | GET, POST | `GET /api/admin/users`, `POST /api/admin/users` | Keep | 1 |
| `/api/admin/users/{target_user_id}` | PATCH, DELETE | `PATCH /api/admin/users/{user_id}`, `DELETE /api/admin/users/{user_id}` | Keep + last-admin / self-delete protection | 1 |
| `/api/admin/users/{target_user_id}/reset-password` | POST | `POST /api/admin/users/{user_id}/reset-password` | Keep + revokes target sessions/PATs | 1 |
| `/api/admin/settings` | GET, PUT | `GET /api/admin/settings`, `PUT /api/admin/settings` | Keep (remove signup_mode/billing keys; keep host/instance config) | 1 |
| `/api/admin/invites` | GET, POST | `GET /api/admin/invites`, `POST /api/admin/invites` | Keep | 1 |
| `/api/admin/invites/{code_hash}` | DELETE | `DELETE /api/admin/invites/{code_hash}` | Keep | 1 |
| `/api/user/connections` | GET, POST | `GET /api/connections`, `PUT /api/connections` | Keep (encrypted at rest; no key leak; never falls back to another user) | 1–2 |
| `/api/user/connections/test` | POST | `POST /api/connections/test` | Keep | 2 |
| `/api/auth/steam/login`, `/api/auth/steam/callback` | GET | `POST /api/connections/steam/validate` (or guarded OpenID on allowed host) | Keep only if host callback verified; manual ID fallback first | 2 |
| `/api/auth/xbox/login`, `/api/auth/xbox/callback` | GET | Guarded Xbox OAuth optional | Validate private callback feasibility before enabling; API key/gamertag fallback preserved | 2 |
| `/api/user/mcp`, `/api/user/mcp/tokens`, `/api/user/mcp/tokens/{token}` | GET, POST, DELETE | `GET /api/account/tokens`, `POST /api/account/tokens`, `DELETE /api/account/tokens/{token_id}` | Keep revocable scoped PATs | 1 / 5 |

### Library, Metadata, Reconciliation, Portability & Stats

| Legacy Route | Methods | Target Rebuild Route | Decision | Phase |
| --- | --- | --- | --- | --- |
| `/api/games` | GET, POST | `GET /api/games`, `POST /api/games` | Keep (manual CRUD + filters + sorting) | 1 |
| `/api/games/{game_id}` | PATCH, DELETE | `GET /api/games/{id}`, `PATCH /api/games/{id}`, `DELETE /api/games/{id}` | Keep + strict edit/lock preservation | 1 |
| `/api/stats` | GET | `GET /api/stats` | Keep summary library stats | 1 |
| `/api/reconcile/candidates` | GET | `GET /api/reconcile/candidates` | Keep | 2 |
| `/api/reconcile/confirm` | POST | `POST /api/reconcile/confirm` | Keep (preserve independent copies & ownership) | 2 |
| `/api/reconcile/dismiss` | POST | `POST /api/reconcile/dismiss` | Keep | 2 |
| `/api/reconcile/unlink/{game_id}` | POST | `POST /api/reconcile/unlink/{game_id}` | Keep | 2 |
| `/api/reconcile/reset-dismissals` | POST | `POST /api/reconcile/reset-dismissals` | Keep | 2 |
| `/api/sync` | POST | `POST /api/sync`, `GET /api/jobs` | Keep via durable `jobs` queue + job status UI | 2 |
| `/api/enrich-metadata` | POST | `POST /api/catalog/enrich` | Keep (optional network; merge-never-replace taxonomy) | 2 |
| `/api/search-external` | GET | `GET /api/catalog/search` | Keep | 2 |
| `/api/games/{game_id}/achievements` | GET | `GET /api/games/{id}/achievements` | Keep | 2 |
| `/api/games/{game_id}/achievements/sync` | POST | `POST /api/games/{id}/achievements/sync` | Keep | 2 |
| `/api/user/library/export` | GET | `GET /api/portability/export.json` (+ legacy support noted) | Replace SQLite blob download with versioned JSON export (no secrets) | 1 |
| `/api/user/library/import` | POST | `POST /api/portability/import/json`, `POST /api/portability/import/legacy-sqlite` | Validate size/header/integrity; dry-run & merge/replace; never swap raw file | 1 |

### Sessions, Analytics, Prompts, & Home Assistant

| Legacy Route | Methods | Target Rebuild Route | Decision | Phase |
| --- | --- | --- | --- | --- |
| `/api/sessions` | GET | `GET /api/sessions` | Keep (via unified activity pipeline) | 3 |
| `/api/sessions/stats` | GET | `GET /api/sessions/stats` | Keep | 3 |
| `/api/sessions/{session_id}` | PATCH, DELETE | `PATCH /api/sessions/{id}`, `DELETE /api/sessions/{id}` | Keep (preserves provider baselines on deletion!) | 3 |
| `/api/analytics` | GET | `GET /api/analytics` | Keep (derived projection) | 3 |
| `/api/analytics/sessions` | GET | `GET /api/analytics/sessions` | Keep | 3 |
| `/api/playtime-prompts` | GET | `GET /api/playtime-prompts` | Keep | 3 |
| `/api/playtime-prompts/{prompt_id}/resolve` | POST | `POST /api/playtime-prompts/{id}/resolve` | Keep | 3 |
| `/api/webhooks/session-transition` | POST | `POST /api/integrations/homeassistant/events` | Keep with scoped auth + replay idempotency | 3 |
| `/api/integrations/homeassistant/import-csv` | POST | `POST /api/integrations/homeassistant/import-csv` | Keep | 3 |
| `/api/integrations/homeassistant/sync` | POST | `POST /api/integrations/homeassistant/sync` | Keep | 3 |

### Wishlist & Web Push

| Legacy Route | Methods | Target Rebuild Route | Decision | Phase |
| --- | --- | --- | --- | --- |
| `/api/wishlist/candidates` | GET | `GET /api/wishlist/candidates` | Keep | 4 |
| `/api/wishlist` | GET, POST | `GET /api/wishlist`, `POST /api/wishlist` | Keep (manual first, parity Phase 4) | 4 |
| `/api/wishlist/deals` | GET | `GET /api/wishlist/deals` | Keep | 4 |
| `/api/wishlist/{item_id}` | GET, PATCH, DELETE | `GET/PATCH/DELETE /api/wishlist/{item_id}` | Keep | 4 |
| `/api/wishlist/{item_id}/platforms` | PATCH | `PATCH /api/wishlist/{item_id}/platforms` | Keep | 4 |
| `/api/wishlist/{item_id}/price` | POST | `POST /api/wishlist/{item_id}/price` | Keep | 4 |
| `/api/wishlist/{item_id}/purchase` | POST | `POST /api/wishlist/{item_id}/purchase` | Keep (idempotent conversion) | 4 |
| `/api/wishlist/{item_id}/history` | GET | `GET /api/wishlist/{item_id}/history` | Keep | 4 |
| `/api/wishlist/sync` | POST | `POST /api/wishlist/sync` | Keep (all 6 adapters in Phase 4) | 4 |
| `/api/push/vapid-public-key`, `/api/push/subscribe`, `/api/push/test` | GET, POST, DELETE | Optional plugin routes | Optional, disabled by default | 4 |

### MCP Transports & OAuth Provider (10 routes in `oauth_provider.py` + 3 MCP mount prefixes in `app.py`)

| Endpoint | Decision | Phase |
| --- | --- | --- |
| `/mcp{path:path}` (Streamable HTTP) | Keep as optional scoped MCP interface on private network | 5 |
| `/sse{path:path}`, `/messages{path:path}`, root SSE ASGI fallback | Defer; retain only if selected LAN/VPN MCP client requires SSE | 5 decision |
| `/.well-known/*`, `/oauth/authorize`, `/oauth/token`, `/oauth/userinfo`, `/oauth/register` (10 OAuth routes) | Defer; prefer per-user revocable PATs; add OAuth only if selected client requires it | 5 decision |

---

## Complete MCP Inventory (`mcp_server.py`: 23 Tools + 2 Resources)

| # | MCP Tool Name | Legacy Summary Status | Decision | Target Service | Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | `get_gaming_profile_and_metrics` | Present in 21 | Keep | `analytics` / `library` | 5 |
| 2 | `search_game_library` | Present in 21 | Keep | `library` | 5 |
| 3 | `get_what_to_play_recommendations` | Present in 21 | Keep | `analytics` / `library` | 5 |
| 4 | `update_game_genres` | Present in 21 | Keep (default merge=True) | `library` + `catalog` | 5 |
| 5 | `update_game_progress` | Present in 21 | Keep | `library` | 5 |
| 6 | `update_game_ownership` | Present in 21 | Keep | `library` | 5 |
| 7 | `add_game_to_library` | Present in 21 | Keep | `library` | 5 |
| 8 | `find_duplicate_cross_platform_games` | Present in 21 | Keep | `library` | 5 |
| 9 | `reconcile_cross_platform_games` | Present in 21 | Keep | `library` | 5 |
| 10 | `enrich_library_genres` | Present in 21 | Keep | `catalog` + `library` | 5 |
| 11 | `get_game_achievements_detail` | Present in 21 | Keep | `library` | 5 |
| 12 | `respond_to_playtime_prompt` | Present in 21 | Keep | `activity` | 5 |
| 13 | `search_game_candidates` | Present in 21 | Keep | `wishlist` / `catalog` | 5 |
| 14 | `add_to_wishlist` | Present in 21 | Keep | `wishlist` | 5 |
| 15 | `update_wishlist_platforms` | Present in 21 | Keep | `wishlist` | 5 |
| 16 | `update_item_price` | Present in 21 | Keep | `wishlist` | 5 |
| 17 | `get_wishlist_deals` | Present in 21 | Keep | `wishlist` | 5 |
| 18 | `mark_as_purchased` | Present in 21 | Keep (idempotent) | `wishlist` + `library` | 5 |
| 19 | `get_session_history` | Present in 21 | Keep | `activity` | 5 |
| 20 | `get_play_sessions` | Present in 21 | Keep | `activity` | 5 |
| 21 | `get_session_analytics` | Present in 21 | Keep | `analytics` | 5 |
| 22 | `update_session_notes` | **Omitted from 21** | Keep | `activity` | 5 |
| 23 | `import_homeassistant_sessions` | **Omitted from 21** | Keep | `integrations.homeassistant` + `activity` | 5 |

**MCP Resources (2)**:
1. `vertex://wishlist/active` -> `wishlist` service (Phase 5)
2. `vertex://wishlist/{id}/history` -> `wishlist` service (Phase 5)

---

## Price Adapters (`wishlist_sync.py`: 6 Adapters)

1. `ITADAdapter` -> Keep in `integrations/prices/` (Phase 4)
2. `CheapSharkAdapter` -> Keep in `integrations/prices/` (Phase 4)
3. `SteamStoreAdapter` -> Keep in `integrations/prices/` (Phase 4)
4. `GOGDirectAdapter` -> Keep in `integrations/prices/` (Phase 4)
5. `XboxCatalogAdapter` -> Keep in `integrations/prices/` (Phase 4)
6. `PSNStoreGraphQLAdapter` -> Keep in `integrations/prices/` (Phase 4)

---

## Database Schema Inventory (10 `system.db` + 15 `games.db` Legacy Tables)

### `system.db` (10 tables)
- `users`, `user_connections`, `web_sessions`, `account_events`, `instance_settings`, `invites`, `catalog_cache`: Keep / evolve in `migrations/system/0001_initial.sql` (+ new `personal_access_tokens`, `jobs`, `migration_imports`, `schema_migrations`).
- `oauth_tokens`: Not imported as live credentials (all legacy tokens revoked on migration); table retired unless OAuth deferred decision activates in Phase 5.
- `subscriptions`, `billing_events_seen`: Retired from runtime; migration inspects & records legacy lifecycle context in the migration report/backup archive without activating lapsed/unapproved accounts.

### `games.db` (15 tables)
- `games`, `sync_logs`, `reconcile_dismissals`, `game_achievements`, `playtime_prompts`, `play_sessions`, `daily_playtime_ledger`, `canonical_games`, `game_editions`, `edition_platform_skus`, `wishlist_items`, `wishlist_platform_targets`, `price_history`, `push_subscriptions`: Preserved and normalized in `migrations/library/0001_initial.sql` (adding `platform_copies`, `play_observations`, `provider_baselines`, `user_preferences`, `schema_migrations`).
- `game_sessions`: Migrated cleanly into unified `play_observations` / `play_sessions` with provenance (`detection_method`, `active_minutes`, `wall_clock_minutes`) to eliminate double counting alongside `play_sessions`.

---

## Existing Legacy Test Suites (28 Modules / 295 Tests) Mapping

All 28 modules run untouched under `scripts/check.sh`:
- Core invariants & security oracle: `test_invariants.py`, `test_multiuser_isolation.py`, `test_oauth_user_scope.py`, `test_security_crit_high.py`, `test_security_h3_h4.py`, `test_taxonomy.py`, `test_play_anywhere.py`, `test_user_settings_portability.py`.
- Accounts & hosted removal regression refs: `test_accounts.py`, `test_registration.py`, `test_billing.py`.
- Sync, activity, analytics: `test_sync_engine.py`, `test_steam_auth.py`, `test_xbox_auth.py`, `test_xbox_gamertag_sync.py`, `test_connections.py`, `test_scheduled_sync.py`, `test_play_sessions.py`, `test_analytics.py`, `test_telemetry.py`, `test_homeassistant_sync.py`.
- Wishlist, MCP, UI, CI: `test_wishlist.py`, `test_wishlist_mcp.py`, `test_wishlist_sync.py`, `test_cover_art_mcp.py`, `test_ui_assets.py`, `test_system_db.py`, `test_cicd.py`.
