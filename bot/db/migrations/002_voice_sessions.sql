CREATE TABLE IF NOT EXISTS voice_sessions (
    id               BIGSERIAL PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    user_id          BIGINT NOT NULL,
    channel_id       BIGINT NOT NULL,
    joined_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at          TIMESTAMPTZ,
    duration_seconds INT
);

CREATE INDEX IF NOT EXISTS idx_voice_sessions_guild_user ON voice_sessions (guild_id, user_id);

CREATE TABLE IF NOT EXISTS voice_leaderboard_config (
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT
);
