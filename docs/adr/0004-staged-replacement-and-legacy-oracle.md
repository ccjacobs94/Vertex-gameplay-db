# ADR 0004: Staged Replacement, Dual-Track Validation, and Legacy Oracle

- **Status**: Accepted
- **Date**: 2026-11-15

## Context

The legacy application runs on the user's home server and includes 28 test files (`tests/test_invariants.py`, `tests/test_multiuser_isolation.py`, etc.) encoding hard-won product behavior and security fixes. Attempting a big-bang rewrite or modifying legacy files in place before replacement parity passes risks breaking live data or subtly eroding invariants.

## Decision

1. **Preserve legacy modules untouched during Stages 0–5**: Root modules (`app.py`, `db.py`, `system_db.py`, `static/`, etc.) and all 28 existing test modules remain completely intact and runnable under `scripts/check.sh` throughout the rebuild.
2. **Isolate new implementation under `src/vertex/`, `migrations/`, `frontend/`, `deploy/`**: New unit, integration, contract, and migration tests live in `tests/unit/`, `tests/integration/`, `tests/contracts/`, and `tests/migration/` and execute offline alongside the legacy oracle inside `scripts/check.sh`.
3. **Import into a clean destination data directory**: Never point `src/vertex` directly at a live legacy `data/` directory for implicit startup upgrades. Use `python -m vertex import-legacy` to inventory, verify checksums/keys, translate statuses (`awaiting_approval` / `awaiting_payment` / `subscription_lapsed` -> `suspended` with explicit audit reason, revoking old sessions/OAuth tokens), and produce canonical verification counts + reproducible reports.
4. **Reversible Cutover**: Phase 6 swaps the running container only after parity tests, offline restore drills, and migration verification succeed, retaining the frozen legacy backup and old container image for safe rollback.

## Alternatives Considered

- **In-place refactoring of `app.py`, `db.py`, and `static/app.js`**: Rejected because high-density interdependencies (single-user fallbacks, inline startup migrations, mixed SQLite tables) risk breaking the running home-server deployment and muddying legacy invariant tests before parity is proven.
- **Big-bang parallel rewrite without legacy oracle checks**: Rejected because dropping parity with `test_invariants.py`, `test_multiuser_isolation.py`, and edge-case fixes verified over 28 legacy test modules would cause subtle data loss regressions.

## Revisit Triggers

- Legacy oracle freeze lifts at Phase 6 cutover once MCP parity (Phase 5), Xbox/PSN sync adapters, and live backup/restore verification pass acceptance checks and user approval is granted.
