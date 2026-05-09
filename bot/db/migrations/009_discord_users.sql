CREATE TABLE IF NOT EXISTS discord_users (
    user_id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    display_name TEXT,
    avatar TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
