CREATE TABLE IF NOT EXISTS birthdays (
    user_id BIGINT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    day INT NOT NULL CHECK (day BETWEEN 1 AND 31),
    month INT NOT NULL CHECK (month BETWEEN 1 AND 12),
    year INT NOT NULL CHECK (year BETWEEN 1900 AND 2100),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_birthdays_guild ON birthdays (guild_id, month, day);

CREATE TABLE IF NOT EXISTS birthday_config (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    upcoming_message_id BIGINT,
    month_message_id BIGINT
);
