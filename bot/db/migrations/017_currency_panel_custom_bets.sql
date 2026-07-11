-- Currency panel (Claim / Balance buttons) lives alongside the leaderboard message.
ALTER TABLE currency_leaderboard ADD COLUMN IF NOT EXISTS panel_message_id BIGINT;

-- Custom, user-created betting markets: provider = 'custom', sport = 'custom',
-- home_name/away_name hold the two user-defined outcome labels, and the market is
-- resolved manually by its creator (or a mod) rather than by the resolution ticker.
ALTER TABLE betting_markets ADD COLUMN IF NOT EXISTS creator_user_id BIGINT;
