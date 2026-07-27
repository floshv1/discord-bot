CREATE TABLE IF NOT EXISTS music_config (
    guild_id               BIGINT PRIMARY KEY,
    channel_id             BIGINT NOT NULL,
    now_playing_message_id BIGINT,
    history_message_id     BIGINT
);

CREATE TABLE IF NOT EXISTS music_history (
    id            SERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    track_title   TEXT NOT NULL,
    track_author  TEXT,
    track_uri     TEXT,
    track_artwork TEXT,
    requester_id  BIGINT,
    played_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_music_history_recent
    ON music_history (guild_id, played_at DESC);
