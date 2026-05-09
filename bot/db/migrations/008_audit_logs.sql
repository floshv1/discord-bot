CREATE TABLE IF NOT EXISTS audit_logs (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id   BIGINT,
    target_id  BIGINT,
    channel_id BIGINT,
    details    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_guild_time
    ON audit_logs (guild_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type
    ON audit_logs (guild_id, event_type);
