-- Restore historical legacy_gs play_observations in game_sessions view & achievements metadata
UPDATE play_observations
SET payload_json = json_set(
    CASE WHEN json_valid(payload_json) THEN payload_json ELSE '{}' END,
    '$.achievements_earned',
    (
        SELECT COUNT(*)
        FROM game_achievements ga
        WHERE ga.game_id = play_observations.game_id
          AND ga.unlocked = 1
          AND ga.unlock_time IS NOT NULL
          AND substr(ga.unlock_time, 1, 19) >= substr(play_observations.interval_started_at, 1, 19)
          AND substr(ga.unlock_time, 1, 19) <= substr(COALESCE(play_observations.interval_ended_at, play_observations.observed_at), 1, 19)
    )
)
WHERE observation_kind = 'interval'
  AND idempotency_key LIKE 'legacy_gs:%'
  AND interval_started_at IS NOT NULL
  AND json_extract(payload_json, '$.achievements_earned') IS NULL;

DROP VIEW IF EXISTS game_sessions;

CREATE VIEW game_sessions AS
SELECT
    CAST(substr(po.idempotency_key, 11) AS INTEGER) AS id,
    po.game_id AS game_id,
    g.canonical_id AS canonical_id,
    po.platform AS platform,
    po.source AS source,
    '' AS external_title_id,
    po.interval_started_at AS started_at,
    COALESCE(po.interval_ended_at, po.observed_at) AS last_seen_at,
    COALESCE(po.interval_ended_at, po.observed_at) AS ended_at,
    CAST(COALESCE(po.interval_minutes, 0) AS REAL) AS wall_clock_minutes,
    CAST(COALESCE(po.interval_minutes, 0) AS REAL) AS active_minutes,
    0.0 AS idle_minutes,
    COALESCE(CAST(json_extract(po.payload_json, '$.achievements_earned') AS INTEGER), 0) AS achievements_earned,
    COALESCE(json_extract(po.payload_json, '$.detection_method'), po.source) AS detection_method,
    COALESCE(json_extract(po.payload_json, '$.status'), 'reconciled') AS status
FROM play_observations po
LEFT JOIN games g ON po.game_id = g.id
WHERE po.observation_kind = 'interval'
  AND po.idempotency_key LIKE 'legacy_gs:%'
  AND NOT EXISTS (
      SELECT 1 FROM play_sessions ps2
      WHERE ps2.game_id = po.game_id
        AND substr(ps2.started_at, 1, 19) = substr(po.interval_started_at, 1, 19)
  )
UNION ALL
SELECT
    1000000 + ps.id AS id,
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
