-- LoLdle daily posts: the midnight recap and the 09:00 role ping. Each is claimed on the day's row,
-- so a restart cannot post twice. No UPDATE here: migrations re-run on every boot, so stamping
-- existing days would silently cancel a recap after any restart. Seeding happens in claim_recap.

ALTER TABLE loldle_config ADD COLUMN IF NOT EXISTS role_id BIGINT;

ALTER TABLE loldle_days ADD COLUMN IF NOT EXISTS recap_at TIMESTAMPTZ;
ALTER TABLE loldle_days ADD COLUMN IF NOT EXISTS ping_at TIMESTAMPTZ;
ALTER TABLE loldle_days ADD COLUMN IF NOT EXISTS ping_message_id BIGINT;
