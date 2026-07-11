CREATE TABLE IF NOT EXISTS betting_config (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS betting_markets (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    competition TEXT NOT NULL,
    home_name TEXT NOT NULL,
    away_name TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    winner TEXT,
    channel_id BIGINT,
    message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_betting_markets_status ON betting_markets (status, start_time);

CREATE TABLE IF NOT EXISTS betting_bets (
    id BIGSERIAL PRIMARY KEY,
    market_id BIGINT NOT NULL REFERENCES betting_markets(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    outcome TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),
    payout BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_betting_bets_market ON betting_bets (market_id);
