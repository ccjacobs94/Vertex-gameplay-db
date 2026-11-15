# Vertex Modular Architecture Overview

This document describes the replacement architecture under `src/vertex/` alongside the existing legacy application.

## Module Boundaries

- **`src/vertex/config.py`**: Loads validated configuration (`Settings.from_env()`), binding loopback (`127.0.0.1`) by default, failing closed when explicit secrets are required for credential encryption, and separating `trusted_proxies`, `allowed_hosts`, and outbound sync settings.
- **`src/vertex/infrastructure/`**:
  - `sqlite.py`: `SystemDatabase` and `UserLibraryDatabase` enforcing `WAL`, `foreign_keys=ON`, `busy_timeout=15000`, and requiring an explicit `UserScope` (`user_id` validated against path traversal and suspended state).
  - `secrets.py`: `SecretBox` wrapping Fernet (`MultiFernet` key list for rotation) with strict key presence verification.
  - `migrations.py`: Ordered `.sql` migrations in `migrations/system/` and `migrations/library/` tracked in `schema_migrations` with SHA-256 checksum validation.
  - `jobs.py`: Durable SQLite job queue (`jobs` table) providing atomic lease acquisition, expired lease recovery, bounded exponential backoff, per-provider throttling, fair per-user pick order, and Settings status reporting.
  - `backup.py`: Whole-instance backup & restore using `sqlite3.Connection.backup()`, creating SHA-256 checksum manifests, schema metadata, and key-recovery guidance (without exporting plaintext keys).
- **`src/vertex/identity/`**:
  - `passwords.py`: `scrypt$` password hashing (compatible with legacy hashes) and policy validation.
  - `models.py` / `repository.py` / `service.py`: One-time local bootstrap admin creation, local authentication with per-IP + per-username throttling, single-use expiring invites, administrator user lifecycle with last-admin & self-delete protections, revocable sessions & scoped PATs, encrypted connection credentials, and timezone/era settings.
- **`src/vertex/catalog/`**:
  - `taxonomy.py`: Exact controlled `GENRES`, `TAGS`, and `RETIRED_GENRES` plus case-insensitive merge/validation helpers.
  - `repository.py` / `service.py`: Shared public `catalog_cache` storing provenance and retrieval timestamps (no personal data).
- **`src/vertex/library/`**:
  - `models.py` / `repository.py` / `service.py`: Personal library entries, independent platform copies, cross-platform reconciliation & reversible unlink, strict preservation of `status_locked`, `ownership_locked`, `user_rating`, `notes`, `favorite`, and Game Pass vs `ownership_status` separation.
  - `portability.py`: Schema-validated versioned JSON export/import (`format_version: 1`, dry-run preview, `merge` vs `replace` policy, zero secrets/passwords) and size/header/integrity-validated legacy SQLite snapshot import.
- **`src/vertex/activity/`**:
  - `models.py` / `repository.py` / `service.py`: Unified `play_observations` ledger, separate `provider_baselines`, `play_sessions` with `is_locked`, 30-minute delta coalescing excluding locked sessions, non-recreation on session deletion, negative-delta guard, unlocked backlog-to-`Playing` auto-transition, and timezone-aware `daily_playtime_ledger` projection.
- **`src/vertex/integrations/platforms/`**:
  - `base.py`: `PlatformAdapter` protocol declaring capabilities, auth modes, normalized snapshots, and structured errors (`timeout`, `rate_limited`, `auth_failed`, `upstream_error`).
  - `steam.py`: Async `httpx` Steam library/vanity client (sanitized errors, timeout/retry safe) + durable job runner execution (`sync_steam_for_user`).
- **`src/vertex/migration/legacy_importer.py`**:
  - Non-destructive versioned importer reading legacy single-user or multi-user SQLite layouts into a fresh destination directory, producing canonical parity verification reports and translating legacy billing/approval states cleanly.
- **`src/vertex/interfaces/`**:
  - `http/`: Modular FastAPI `APIRouter`s (`health`, `auth`, `account`, `admin`, `library`, `connections`, `jobs`, `portability`), middleware enforcing Host/CSRF/TrustedProxy rules, and Pydantic OpenAPI schemas.
  - `cli.py`: Explicit maintenance CLI (`migrate`, `bootstrap-admin`, `reset-password`, `backup`, `restore`, `import-legacy`, `diagnostics`, `serve`).

## Data Flow Rules

1. External provider adapters NEVER mutate personal tables directly; they return `PlatformSyncResult` snapshots applied transactionally by `library` + `activity` services under an active `UserScope`.
2. Background jobs execute through `JobRunner` -> verify user is still `active` -> resolve decrypted credentials per user (never falling back to another user's credentials) -> execute service -> persist structured status (`last_success_at`, `last_failure_at`, `next_attempt_at`, `credential_expired`).
3. Legacy `app.py` / `db.py` / `static/` remain untouched during staged replacement; the rebuilt app can run side-by-side or via `python -m vertex`.
