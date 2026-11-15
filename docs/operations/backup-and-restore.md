# Backup, Restore, and Legacy Migration Operations

## Whole-Instance Backup Sets

Vertex stores `system.db` plus one SQLite database per user (`users/<user_id>/library.db`). Active databases MUST live on a local filesystem so SQLite WAL locking functions reliably; use network storage (NAS) only for storing completed backup sets.

Creating a backup uses SQLite's online backup API (`sqlite3.Connection.backup()`) across `system.db` and every user library database, writing:
- `backup/system.db`
- `backup/users/<user_id>/library.db`
- `backup/manifest.json` (format version, app version, creation timestamp, per-file SHA-256 checksums, applied schema migration versions, user inventory, and key-recovery instructions)

> **Security**: `manifest.json` records only the SHA-256 key fingerprint of the active encryption key, **never** the plaintext `VERTEX_SECRET_KEY` / `GAMEHUB_SECRET_KEY`. Store your encryption key separately (password manager + encrypted offline copy). Without the key, backed-up platform connection credentials (`xbl_api_key`, `psn_npsso`, `ha_token`, etc.) cannot be decrypted after restore and will fail closed.

### Recommended Household Retention Defaults

- Pre-upgrade backup set before applying any release or schema migration.
- Nightly automated backup sets: keep **7 daily** and **4 weekly** complete backup sets.
- Maintain at least one off-device copy of both the backup set and `VERTEX_SECRET_KEY`.

### CLI Commands

```bash
# Create a consistent whole-instance backup set
PYTHONPATH=src python3 -m vertex backup --data-dir /path/to/data --dest /path/to/backups/set-20261115

# Verify and restore into a clean destination directory (never overwrite live active files in place)
PYTHONPATH=src python3 -m vertex restore --backup-dir /path/to/backups/set-20261115 --dest /path/to/restored-data

# Check sanitized runtime diagnostics (never prints secrets)
PYTHONPATH=src python3 -m vertex diagnostics --data-dir /path/to/data
```

## Restoring & Drilling

1. Stop any running Vertex server before swapping active data directories.
2. Restore into a clean directory (`python -m vertex restore ...`), which verifies every SHA-256 checksum in `manifest.json` and runs `PRAGMA integrity_check` on every SQLite file before finalizing.
3. Configure `VERTEX_SECRET_KEY` (or legacy `GAMEHUB_SECRET_KEY`) in the environment.
4. Start Vertex in offline test mode (`VERTEX_OUTBOUND_SYNC_ENABLED=false`) and confirm login, library counts, locked edits, and two-user isolation before opening network traffic.

## Migrating From Legacy Game Hub

Never point the replacement runtime directly at legacy `data/` files. Instead:

```bash
# Dry-run or clean import into a new destination directory
PYTHONPATH=src python3 -m vertex import-legacy \
  --source-dir /path/to/legacy-data \
  --dest-dir /path/to/new-data \
  --report /tmp/migration-report.json
```

Key migration guarantees:
- **Both layouts supported**: Single-user `games.db` and multi-user `system.db` + `users/<id>/games.db`.
- **No blind directory matching**: User IDs are resolved via `system.db`'s `users` table; orphaned directories are reported for explicit review.
- **Credentials & Hashes**: Compatible `scrypt$` password hashes are preserved; encrypted platform secrets are migrated only if the configured decryption key successfully decrypts them, otherwise flagged fail-closed for user re-entry.
- **Sessions & Billing Retired**: All legacy `web_sessions` and `oauth_tokens` are revoked (users re-login). Accounts in `awaiting_approval`, `awaiting_payment`, or `subscription_lapsed` translate to `suspended` with the original lifecycle reason logged in the report so billing retirement never silently activates unauthorized or lapsed accounts.
- **Explicit Rollback**: Keep the frozen legacy data directory and legacy container image. If rollback is necessary, stop the replacement container and check whether users created post-cutover edits before restoring the legacy image/data.
