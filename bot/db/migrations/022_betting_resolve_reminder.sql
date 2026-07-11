-- A community bet has no provider to settle it, so it sits at 'locked' until its creator
-- runs /bet resolve. If they forget, everyone's stakes are trapped there forever and the
-- bot never says a word about it.
--
-- This records when we last nudged the creator, so the reminder can repeat until the bet
-- is settled without spamming the channel on every tick.
ALTER TABLE betting_markets
    ADD COLUMN IF NOT EXISTS resolve_reminded_at TIMESTAMPTZ;
