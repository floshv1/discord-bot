CREATE TABLE IF NOT EXISTS palworld_config (
    guild_id          BIGINT PRIMARY KEY,
    channel_id        BIGINT NOT NULL,
    panel_message_id  BIGINT
);
