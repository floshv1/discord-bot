CREATE TABLE IF NOT EXISTS reprimand_config (
    guild_id   BIGINT PRIMARY KEY,
    role_id    BIGINT NOT NULL,
    channel_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS reprimands (
    id            BIGSERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    target_id     BIGINT NOT NULL,
    moderator_id  BIGINT NOT NULL,
    reason        TEXT NOT NULL,
    original_nick TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ,
    active        BOOLEAN NOT NULL DEFAULT TRUE
);

-- expires_at IS NULL means the reprimand is unbounded (lifted only via /pardon).
CREATE INDEX IF NOT EXISTS idx_reprimands_active_expiry
    ON reprimands (active, expires_at) WHERE active;
