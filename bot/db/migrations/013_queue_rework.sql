CREATE TABLE IF NOT EXISTS queue_config (
    guild_id         BIGINT PRIMARY KEY,
    channel_id       BIGINT NOT NULL,
    panel_message_id BIGINT
);

CREATE TABLE IF NOT EXISTS game_subscriptions (
    id        BIGSERIAL PRIMARY KEY,
    guild_id  BIGINT NOT NULL,
    user_id   BIGINT NOT NULL,
    preset_id BIGINT NOT NULL REFERENCES game_presets(id) ON DELETE CASCADE,
    UNIQUE (guild_id, user_id, preset_id)
);

-- Per-queue size chosen at creation (Duo/Flex/Custom); NULL falls back to the preset default.
ALTER TABLE game_queues ADD COLUMN IF NOT EXISTS player_count INT;

-- Drives the idle auto-expiry safety net; bumped on every join / can't-attend.
ALTER TABLE game_queues ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
