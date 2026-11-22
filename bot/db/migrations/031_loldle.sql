-- LoLdle: one champion a day per guild. The answer's clues and each guess's feedback are stored,
-- so refreshing champions.json can never change a day in progress.

CREATE TABLE IF NOT EXISTS loldle_config (
    guild_id          BIGINT PRIMARY KEY,
    channel_id        BIGINT NOT NULL,
    board_message_id  BIGINT
);

-- up_minutes counts the minutes the bot was running that Paris day: too few excuses the day for
-- streaks (the bot was down), which is how "nobody played" is told apart from an outage.
CREATE TABLE IF NOT EXISTS loldle_days (
    guild_id     BIGINT NOT NULL,
    day          DATE NOT NULL,
    champion_id  TEXT NOT NULL,
    answer       JSONB NOT NULL,
    up_minutes   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, day)
);

CREATE TABLE IF NOT EXISTS loldle_guesses (
    guild_id     BIGINT NOT NULL,
    day          DATE NOT NULL,
    user_id      BIGINT NOT NULL,
    number       INT NOT NULL,
    champion_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    cells        JSONB NOT NULL,
    clues        JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, day, user_id, number),
    UNIQUE (guild_id, day, user_id, champion_id)
);

CREATE TABLE IF NOT EXISTS loldle_results (
    guild_id   BIGINT NOT NULL,
    day        DATE NOT NULL,
    user_id    BIGINT NOT NULL,
    tries      INT NOT NULL,
    hints      INT NOT NULL,
    score      INT NOT NULL,
    grid       TEXT NOT NULL,
    solved_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, day, user_id)
);

CREATE TABLE IF NOT EXISTS loldle_hints (
    guild_id  BIGINT NOT NULL,
    day       DATE NOT NULL,
    user_id   BIGINT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('spell', 'splash', 'letter')),
    PRIMARY KEY (guild_id, day, user_id, kind)
);
