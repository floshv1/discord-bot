-- The goulag channel *is* the tribunal channel, so no second config table and no second
-- /setup: reprimand_config only gains the jury's role. NULL means no jury is configured,
-- and /reprimand behaves exactly as it did before — degraded, not broken.
ALTER TABLE reprimand_config ADD COLUMN IF NOT EXISTS judge_role_id BIGINT;

-- One trial per reprimand (UNIQUE), so a double /reprimand can never open two cards over
-- the same sentence. channel_id is stored on the trial rather than read back from the
-- config: moving the goulag channel later must not orphan the trials already in progress.
CREATE TABLE IF NOT EXISTS tribunal_trials (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    reprimand_id BIGINT NOT NULL UNIQUE REFERENCES reprimands (id) ON DELETE CASCADE,
    channel_id   BIGINT NOT NULL,
    message_id   BIGINT,
    plea         TEXT,
    plea_at      TIMESTAMPTZ,
    -- NULL = still running. 'expired' = the sentence was served or pardoned before the
    -- bench ruled, which makes the trial moot.
    verdict      TEXT CHECK (verdict IN ('guilty', 'acquitted', 'expired')),
    verdict_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The composite primary key is the whole "one judge, one voice" rule: changing your mind
-- is an ON CONFLICT DO UPDATE, not a second ballot.
CREATE TABLE IF NOT EXISTS tribunal_votes (
    trial_id BIGINT NOT NULL REFERENCES tribunal_trials (id) ON DELETE CASCADE,
    judge_id BIGINT NOT NULL,
    vote     SMALLINT NOT NULL CHECK (vote IN (1, -1)),
    voted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trial_id, judge_id)
);
