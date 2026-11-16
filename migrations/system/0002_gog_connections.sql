-- Add GOG Galaxy OAuth and profile fields to user_connections.

ALTER TABLE user_connections ADD COLUMN gog_user_id TEXT;
ALTER TABLE user_connections ADD COLUMN gog_username TEXT;
ALTER TABLE user_connections ADD COLUMN gog_access_token_encrypted TEXT;
ALTER TABLE user_connections ADD COLUMN gog_refresh_token_encrypted TEXT;
