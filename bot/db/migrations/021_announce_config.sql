-- Where /announce publishes. Like every other feature, the channel is chosen with a
-- /setup command argument rather than an env var, so it can be moved without a restart.
CREATE TABLE IF NOT EXISTS announce_config (
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL
);
