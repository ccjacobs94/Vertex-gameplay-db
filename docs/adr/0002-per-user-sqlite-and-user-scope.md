# ADR 0002: Per-User SQLite Storage and Explicit UserScope

- **Status**: Accepted
- **Date**: 2026-11-15

## Context

In the legacy system, `db.get_connection()` inspects a `ContextVar`, falling back to `DEFAULT_DB_PATH` (`data/games.db`) when unset or when multi-user mode appears inactive. Furthermore, startup lifecycle hooks copy files and run library cleanup automatically. This invites accidental cross-user leakage or data loss if a code path omits user scoping.

## Decision

1. **Retain `system.db` + one SQLite database per stable `user_id` (`users/<user_id>/library.db`)** initially. Active databases live on local disk (not network-mounted NFS/SMB shares), configured with `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=15000`, `synchronous=NORMAL`, and short transactions.
2. **Require an explicit `UserScope`** (`user_id`, `username`, `role`, `status`) as an explicit parameter to every personal service and repository method. Eliminate default single-user database fallback: even a 1-user deployment operates exclusively through authenticated, resolved `UserScope`.
3. **Separate shared public data from personal data**: `system.db` holds accounts, invites, encrypted connection secrets, PATs, durable jobs, and public `catalog_cache`. Personal databases hold games, platform copies, reconciliation groups/dismissals, observations, provider baselines, play sessions, prompts, daily ledgers, and wishlist/history.
4. **No cross-file atomic transaction illusion**: Cross-file lifecycle flows (provisioning, suspension/deletion) execute idempotent, resumable steps (`pending_provisioning`, `suspended` -> session/token revocation -> library/credential removal).
5. **Re-verify account state before background execution**: Queued jobs re-load the owner's account status and abort/cancel if suspended.

## Alternatives Considered

- **Single shared SQLite DB with `user_id` column on all tables**: One backup file, but every query bears cross-tenant filtering risk and requires merging per-user schemas before Phase 1.
- **PostgreSQL**: Unjustified operational burden for 2–20 users on a single home server.

## Revisit Triggers

- Concurrent write contention across 20+ active writers that cannot be alleviated by bounded WAL transactions and job pacing.
- Multi-host high-availability requirements.
