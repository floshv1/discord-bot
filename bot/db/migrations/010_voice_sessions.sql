DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'voice_sessions' AND column_name = 'joined_at'
    ) THEN
        DROP TABLE voice_sessions CASCADE;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS voice_sessions (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_voice_sessions_lookup
    ON voice_sessions (guild_id, user_id, started_at);

CREATE TABLE IF NOT EXISTS voice_leaderboard (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    weekly_message_id BIGINT,
    alltime_message_id BIGINT
);
