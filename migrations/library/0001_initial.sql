-- Per-user personal library database initial schema (Phase 1+ modular monolith)
-- Strict tenant isolation: lives exclusively at <data_dir>/users/<user_id>/library.db

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    source TEXT NOT NULL,
    playtime_hours REAL NOT NULL DEFAULT 0.0,
    playtime_minutes INTEGER NOT NULL DEFAULT 0,
    last_synced_playtime_minutes INTEGER,
    last_played TEXT,
    completion_status TEXT NOT NULL DEFAULT 'Not Played',
    status_locked INTEGER NOT NULL DEFAULT 0,
    ownership_status TEXT NOT NULL DEFAULT 'Owned',
    ownership_locked INTEGER NOT NULL DEFAULT 0,
    is_game_pass INTEGER NOT NULL DEFAULT 0,
    is_play_anywhere INTEGER NOT NULL DEFAULT 0,
    achievements_unlocked INTEGER NOT NULL DEFAULT 0,
    achievements_total INTEGER NOT NULL DEFAULT 0,
    achievements_synced_at TEXT,
    genres_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    critic_score INTEGER,
    user_rating INTEGER,
    release_year INTEGER,
    cover_url TEXT,
    notes TEXT NOT NULL DEFAULT '',
    favorite INTEGER NOT NULL DEFAULT 0,
    playtime_prompt_muted INTEGER NOT NULL DEFAULT 0,
    canonical_id INTEGER,
    legacy_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_games_platform ON games(platform);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(completion_status);
CREATE INDEX IF NOT EXISTS idx_games_playtime ON games(playtime_hours DESC);
CREATE INDEX IF NOT EXISTS idx_games_canonical ON games(canonical_id);

CREATE TABLE IF NOT EXISTS platform_copies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    ownership_status TEXT NOT NULL DEFAULT 'Owned',
    ownership_locked INTEGER NOT NULL DEFAULT 0,
    is_game_pass INTEGER NOT NULL DEFAULT 0,
    is_play_anywhere INTEGER NOT NULL DEFAULT 0,
    playtime_minutes INTEGER NOT NULL DEFAULT 0,
    last_played TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_copies_game ON platform_copies(game_id);

CREATE TABLE IF NOT EXISTS reconciliation_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    games_synced INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    external_ach_id TEXT NOT NULL,
    source TEXT NOT NULL,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon_url TEXT,
    icon_gray_url TEXT,
    unlocked INTEGER NOT NULL DEFAULT 0,
    unlock_time TEXT,
    reward_type TEXT,
    reward_value TEXT,
    rarity_pct REAL,
    is_hidden INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(game_id, external_ach_id),
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_game_achievements_game ON game_achievements(game_id);

CREATE TABLE IF NOT EXISTS play_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    game_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    platform TEXT NOT NULL,
    observation_kind TEXT NOT NULL CHECK (
        observation_kind IN ('cumulative_total', 'interval', 'baseline_init', 'baseline_reset')
    ),
    observed_at TEXT NOT NULL,
    cumulative_minutes INTEGER,
    interval_started_at TEXT,
    interval_ended_at TEXT,
    interval_minutes INTEGER,
    precision_level TEXT NOT NULL DEFAULT 'estimated' CHECK (
        precision_level IN ('exact', 'estimated', 'coarse')
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_play_observations_game ON play_observations(game_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS provider_baselines (
    game_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    baseline_minutes INTEGER NOT NULL DEFAULT 0,
    last_observed_at TEXT NOT NULL,
    reset_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(game_id, source),
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS play_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    source TEXT NOT NULL DEFAULT 'sync',
    precision_level TEXT NOT NULL DEFAULT 'estimated' CHECK (
        precision_level IN ('exact', 'estimated', 'coarse')
    ),
    notes TEXT,
    achievements_unlocked INTEGER NOT NULL DEFAULT 0,
    is_locked INTEGER NOT NULL DEFAULT 0,
    active_minutes REAL,
    idle_minutes REAL,
    external_session_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_play_sessions_game ON play_sessions(game_id, ended_at DESC);

CREATE TABLE IF NOT EXISTS playtime_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL UNIQUE,
    previous_hours REAL NOT NULL,
    new_hours REAL NOT NULL,
    delta_hours REAL NOT NULL,
    current_status TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'dismissed')),
    resolved_at TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_playtime_ledger (
    date TEXT NOT NULL,
    game_id INTEGER NOT NULL,
    platform_family TEXT NOT NULL,
    minutes_played REAL NOT NULL DEFAULT 0.0,
    sessions_count INTEGER NOT NULL DEFAULT 0,
    achievements_unlocked INTEGER NOT NULL DEFAULT 0,
    first_session_hour INTEGER,
    PRIMARY KEY (date, game_id),
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_ledger_date ON daily_playtime_ledger(date DESC);

-- Wishlist & edition tables preserved cleanly for Phase 4 parity & legacy migration
CREATE TABLE IF NOT EXISTS canonical_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_title TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    release_year INTEGER,
    developer TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    cover_url TEXT,
    genres_json TEXT NOT NULL DEFAULT '[]',
    critic_score INTEGER,
    library_game_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(library_game_id) REFERENCES games(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS game_editions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_game_id INTEGER NOT NULL,
    edition_name TEXT NOT NULL,
    edition_tier TEXT NOT NULL DEFAULT 'base',
    tier_rank INTEGER NOT NULL DEFAULT 1,
    is_base_edition INTEGER NOT NULL DEFAULT 0,
    included_dlc_notes TEXT NOT NULL DEFAULT '',
    cover_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(canonical_game_id, edition_name),
    FOREIGN KEY(canonical_game_id) REFERENCES canonical_games(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edition_platform_skus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edition_id INTEGER NOT NULL,
    platform_family TEXT NOT NULL,
    platform_label TEXT NOT NULL,
    storefront TEXT NOT NULL,
    store_sku_id TEXT NOT NULL,
    itad_plain TEXT,
    store_url TEXT NOT NULL DEFAULT '',
    msrp_usd REAL,
    current_price_usd REAL,
    regular_price_usd REAL,
    discount_pct INTEGER NOT NULL DEFAULT 0,
    historical_low_usd REAL,
    is_authorized_seller INTEGER NOT NULL DEFAULT 1,
    is_drm_free INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(edition_id, storefront, store_sku_id),
    FOREIGN KEY(edition_id) REFERENCES game_editions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wishlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_game_id INTEGER NOT NULL UNIQUE,
    preferred_edition_id INTEGER,
    allow_smart_upgrade INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    added_via TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(canonical_game_id) REFERENCES canonical_games(id) ON DELETE CASCADE,
    FOREIGN KEY(preferred_edition_id) REFERENCES game_editions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS wishlist_platform_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wishlist_item_id INTEGER NOT NULL,
    platform_family TEXT NOT NULL,
    allowed_storefronts TEXT NOT NULL DEFAULT '[]',
    target_price_usd REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    muted_owned_conflict INTEGER NOT NULL DEFAULT 0,
    last_alerted_at TEXT,
    last_alerted_price_usd REAL,
    last_alerted_edition_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(wishlist_item_id, platform_family),
    FOREIGN KEY(wishlist_item_id) REFERENCES wishlist_items(id) ON DELETE CASCADE,
    FOREIGN KEY(last_alerted_edition_id) REFERENCES game_editions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id INTEGER NOT NULL,
    storefront TEXT NOT NULL,
    price_usd REAL NOT NULL,
    regular_price_usd REAL,
    discount_pct INTEGER NOT NULL DEFAULT 0,
    is_all_time_low INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'daily_sync',
    recorded_at TEXT NOT NULL,
    FOREIGN KEY(sku_id) REFERENCES edition_platform_skus(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh_key TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    device_label TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS user_preferences (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
