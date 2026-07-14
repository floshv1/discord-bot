-- Settling a market used to dump a pinged list of every winner into the betting channel. A
-- football matchday settles five matches in a row, so that was five walls of pings in the one
-- channel that should hold nothing but cards.
--
-- The bettors now get a DM. The full breakdown — pool, per-bettor payouts, house P&L, and
-- crucially *who* pressed /bet resolve — goes here instead. That last column is the audit
-- surface for the arbiter rule: a dishonest settlement has to be visible to moderators
-- without anyone querying the database.
--
-- NULL means no staff channel, and nothing breaks: the DMs still go out and the card is still
-- rewritten in place. Degraded, not broken.
ALTER TABLE betting_config
    ADD COLUMN IF NOT EXISTS staff_channel_id BIGINT;
