-- Track when completion_status was last transitioned and distinguish congrats notifications from playtime nudges
ALTER TABLE games ADD COLUMN status_updated_at TEXT;
UPDATE games SET status_updated_at = updated_at WHERE status_locked = 1 AND status_updated_at IS NULL;

ALTER TABLE playtime_prompts ADD COLUMN prompt_type TEXT NOT NULL DEFAULT 'playtime';
