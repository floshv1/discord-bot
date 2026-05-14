CREATE TABLE IF NOT EXISTS music_state (
    guild_id         BIGINT PRIMARY KEY,
    track_title      TEXT,
    track_author     TEXT,
    track_uri        TEXT,
    track_length_ms  BIGINT,
    track_artwork    TEXT,
    requester_id     BIGINT,
    is_paused        BOOLEAN NOT NULL DEFAULT FALSE,
    volume           INTEGER NOT NULL DEFAULT 100,
    autoplay_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    position_ms      INTEGER NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS music_queue (
    guild_id        BIGINT NOT NULL,
    position        INTEGER NOT NULL,
    track_title     TEXT NOT NULL,
    track_author    TEXT,
    track_uri       TEXT,
    track_length_ms BIGINT,
    track_artwork   TEXT,
    requester_id    BIGINT,
    PRIMARY KEY (guild_id, position)
);

CREATE TABLE IF NOT EXISTS music_commands (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    command     TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_music_commands_pending
    ON music_commands (guild_id, created_at)
    WHERE executed_at IS NULL;
