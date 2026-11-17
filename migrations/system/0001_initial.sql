-- System database initial schema (Phase 1+ modular monolith)
-- Shared authentication, household invitations, encrypted credentials, public catalog cache, durable jobs.

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'invited', 'suspended', 'pending_provisioning', 'deleting')
    ),
    status_reason TEXT,
    password_hash TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'auto',
    timezone_eras_json TEXT NOT NULL DEFAULT '[]',
    onboarding_completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS user_connections (
    user_id TEXT PRIMARY KEY,
    steam_id TEXT,
    steam_api_key_encrypted TEXT,
    xbl_api_key_encrypted TEXT,
    xbl_watcher_api_key_encrypted TEXT,
    xbox_gamertag TEXT,
    xbox_xuid TEXT,
    psn_npsso_encrypted TEXT,
    ha_url TEXT,
    ha_token_encrypted TEXT,
    ha_entity_id TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS web_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_user_id ON web_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expires_at ON web_sessions(expires_at);

CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    token_prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    scopes_json TEXT NOT NULL DEFAULT '["read:library"]',
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at TEXT,
    revoked_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pat_user_id ON personal_access_tokens(user_id);

CREATE TABLE IF NOT EXISTS invites (
    code_hash TEXT PRIMARY KEY,
    created_by TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    used_by TEXT,
    used_at TEXT,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_invites_used_by ON invites(used_by);

CREATE TABLE IF NOT EXISTS instance_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    actor_id TEXT,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_account_events_user_id ON account_events(user_id);

CREATE TABLE IF NOT EXISTS catalog_cache (
    normalized_title TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    steam_appid TEXT,
    genres_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    release_year INTEGER,
    critic_score INTEGER,
    cover_url TEXT,
    source TEXT NOT NULL DEFAULT 'unknown',
    retrieved_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'internal',
    payload_json TEXT NOT NULL DEFAULT '{}',
    dedup_key TEXT,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (
        state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    scheduled_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    last_error_code TEXT,
    last_error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_dedup
    ON jobs(dedup_key)
    WHERE dedup_key IS NOT NULL AND state IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_jobs_state_avail ON jobs(state, available_at);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS provider_rate_limits (
    provider TEXT PRIMARY KEY,
    next_allowed_at TEXT NOT NULL,
    last_error_at TEXT,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS migration_imports (
    import_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
