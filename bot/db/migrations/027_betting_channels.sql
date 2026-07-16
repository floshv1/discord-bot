-- One betting channel per category, so a football matchday doesn't bury the LEC cards.
--
-- betting_config.guild_id is a PRIMARY KEY, hence a child table rather than five more columns
-- on it. betting_config.channel_id stays the fallback: a category with no row here lands there,
-- so routing degrades to the old single-channel behaviour instead of dropping cards on the floor.
--
-- board_message_id is the pinned "what's followed here + what's coming" message for that
-- channel. NULL means it hasn't been posted yet, which is not an error.
CREATE TABLE IF NOT EXISTS betting_channels (
    guild_id BIGINT NOT NULL,
    category TEXT NOT NULL,
    channel_id BIGINT NOT NULL,
    board_message_id BIGINT,
    PRIMARY KEY (guild_id, category)
);
