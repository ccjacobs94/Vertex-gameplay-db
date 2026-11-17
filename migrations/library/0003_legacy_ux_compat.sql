-- Bridge v2 library.db schema with legacy UX consolidation & habits analytics queries
ALTER TABLE games ADD COLUMN genres TEXT NOT NULL DEFAULT '[]';
ALTER TABLE games ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
UPDATE games SET genres = genres_json, tags = tags_json;

CREATE TRIGGER IF NOT EXISTS trg_games_sync_json_ai AFTER INSERT ON games
BEGIN
    UPDATE games
    SET genres = CASE WHEN NEW.genres != '[]' AND NEW.genres_json = '[]' THEN NEW.genres ELSE NEW.genres_json END,
        tags = CASE WHEN NEW.tags != '[]' AND NEW.tags_json = '[]' THEN NEW.tags ELSE NEW.tags_json END,
        genres_json = CASE WHEN NEW.genres != '[]' AND NEW.genres_json = '[]' THEN NEW.genres ELSE NEW.genres_json END,
        tags_json = CASE WHEN NEW.tags != '[]' AND NEW.tags_json = '[]' THEN NEW.tags ELSE NEW.tags_json END
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_games_sync_from_json_au AFTER UPDATE OF genres_json, tags_json ON games
WHEN NEW.genres != NEW.genres_json OR NEW.tags != NEW.tags_json
BEGIN
    UPDATE games SET genres = NEW.genres_json, tags = NEW.tags_json WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_games_sync_from_legacy_au AFTER UPDATE OF genres, tags ON games
WHEN NEW.genres != NEW.genres_json OR NEW.tags != NEW.tags_json
BEGIN
    UPDATE games SET genres_json = NEW.genres, tags_json = NEW.tags WHERE id = NEW.id;
END;

ALTER TABLE canonical_games ADD COLUMN genres TEXT NOT NULL DEFAULT '[]';
UPDATE canonical_games SET genres = genres_json;

CREATE TRIGGER IF NOT EXISTS trg_canonical_games_sync_ai AFTER INSERT ON canonical_games
BEGIN
    UPDATE canonical_games
    SET genres = CASE WHEN NEW.genres != '[]' AND NEW.genres_json = '[]' THEN NEW.genres ELSE NEW.genres_json END,
        genres_json = CASE WHEN NEW.genres != '[]' AND NEW.genres_json = '[]' THEN NEW.genres ELSE NEW.genres_json END
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_canonical_games_sync_json_au AFTER UPDATE OF genres_json ON canonical_games
WHEN NEW.genres != NEW.genres_json
BEGIN
    UPDATE canonical_games SET genres = NEW.genres_json WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_canonical_games_sync_legacy_au AFTER UPDATE OF genres ON canonical_games
WHEN NEW.genres != NEW.genres_json
BEGIN
    UPDATE canonical_games SET genres_json = NEW.genres WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS reconcile_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO reconcile_dismissals (pair_key, created_at)
SELECT pair_key, created_at FROM reconciliation_dismissals;

CREATE TRIGGER IF NOT EXISTS trg_reconcile_dismissals_to_v2 AFTER INSERT ON reconcile_dismissals
BEGIN
    INSERT OR IGNORE INTO reconciliation_dismissals (pair_key, created_at) VALUES (NEW.pair_key, NEW.created_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_reconciliation_dismissals_to_v1 AFTER INSERT ON reconciliation_dismissals
BEGIN
    INSERT OR IGNORE INTO reconcile_dismissals (pair_key, created_at) VALUES (NEW.pair_key, NEW.created_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_reconcile_dismissals_del_v2 AFTER DELETE ON reconcile_dismissals
BEGIN
    DELETE FROM reconciliation_dismissals WHERE pair_key = OLD.pair_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_reconcile_dismissals_del_v1 AFTER DELETE ON reconciliation_dismissals
BEGIN
    DELETE FROM reconcile_dismissals WHERE pair_key = OLD.pair_key;
END;

CREATE VIEW IF NOT EXISTS game_sessions AS
SELECT
    ps.id AS id,
    ps.game_id AS game_id,
    g.canonical_id AS canonical_id,
    ps.platform AS platform,
    ps.source AS source,
    COALESCE(ps.external_session_ref, '') AS external_title_id,
    ps.started_at AS started_at,
    ps.ended_at AS last_seen_at,
    ps.ended_at AS ended_at,
    CAST(ps.duration_minutes AS REAL) AS wall_clock_minutes,
    COALESCE(ps.active_minutes, CAST(ps.duration_minutes AS REAL)) AS active_minutes,
    COALESCE(ps.idle_minutes, 0.0) AS idle_minutes,
    ps.achievements_unlocked AS achievements_earned,
    ps.source AS detection_method,
    'completed' AS status
FROM play_sessions ps
LEFT JOIN games g ON ps.game_id = g.id;
