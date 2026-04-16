CREATE TABLE IF NOT EXISTS suggestion_config (
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT
);

CREATE TABLE IF NOT EXISTS suggestions (
    id         BIGSERIAL PRIMARY KEY,
    number     INT NOT NULL,
    guild_id   BIGINT NOT NULL,
    author_id  BIGINT NOT NULL,
    type       TEXT NOT NULL,
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (guild_id, number)
);

CREATE TABLE IF NOT EXISTS suggestion_votes (
    suggestion_id BIGINT NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL,
    vote          SMALLINT NOT NULL,
    PRIMARY KEY (suggestion_id, user_id)
);
