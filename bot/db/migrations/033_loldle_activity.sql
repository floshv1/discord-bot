-- LoLdle Activity: an announcement queue on results (the bot drains it, whichever process recorded the win)
-- and the display names the Activity shows. No UPDATE here: migrations re-run on every boot.
ALTER TABLE loldle_results ADD COLUMN IF NOT EXISTS announce_pending BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS loldle_results_pending ON loldle_results (guild_id) WHERE announce_pending;

CREATE TABLE IF NOT EXISTS loldle_players (
    guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
    display_name TEXT NOT NULL, avatar_hash TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);
