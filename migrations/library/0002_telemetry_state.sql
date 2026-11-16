-- Persist active live telemetry sessions per provider cleanly outside play_sessions.

CREATE TABLE IF NOT EXISTS telemetry_active_sessions (
    provider TEXT PRIMARY KEY,
    external_title_id TEXT NOT NULL,
    game_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    baseline_minutes INTEGER NOT NULL DEFAULT 0,
    baseline_achievements INTEGER NOT NULL DEFAULT 0,
    rich_presence TEXT,
    detection_method TEXT NOT NULL DEFAULT 'presence_poll',
    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
);
