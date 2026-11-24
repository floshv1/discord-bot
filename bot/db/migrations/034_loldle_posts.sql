-- One public "X joue au LoLdle" message per member and day, created at the first guess and then
-- edited. The claim is the INSERT: the row exists before the message is sent, so two guesses
-- arriving together cannot post twice. message_id stays NULL until the send succeeds.

CREATE TABLE IF NOT EXISTS loldle_posts (
    guild_id    BIGINT NOT NULL,
    day         DATE NOT NULL,
    user_id     BIGINT NOT NULL,
    channel_id  BIGINT NOT NULL,
    message_id  BIGINT,
    finished    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, day, user_id)
);
