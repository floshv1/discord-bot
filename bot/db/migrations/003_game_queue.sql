CREATE TABLE IF NOT EXISTS game_presets (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    name         TEXT NOT NULL,
    player_count INT NOT NULL,
    UNIQUE (guild_id, name)
);

CREATE TABLE IF NOT EXISTS game_queues (
    id            BIGSERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    channel_id    BIGINT NOT NULL,
    preset_id     BIGINT NOT NULL REFERENCES game_presets(id),
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at     TIMESTAMPTZ,
    start_time    TIMESTAMPTZ,
    reminder_sent BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_game_queues_one_open
    ON game_queues (guild_id, preset_id)
    WHERE status = 'open';

CREATE TABLE IF NOT EXISTS queue_members (
    id        BIGSERIAL PRIMARY KEY,
    queue_id  BIGINT NOT NULL REFERENCES game_queues(id),
    user_id   BIGINT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (queue_id, user_id)
);
