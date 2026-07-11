CREATE TABLE IF NOT EXISTS currency_wallets (
    user_id BIGINT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    balance BIGINT NOT NULL DEFAULT 1000,
    last_claim_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS currency_transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_currency_transactions_user
    ON currency_transactions (guild_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS currency_leaderboard (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT
);
