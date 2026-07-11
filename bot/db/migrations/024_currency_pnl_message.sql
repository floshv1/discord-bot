-- The coin leaderboard says who is rich; it doesn't say who is actually good at betting.
-- Someone can sit on their starting balance and outrank a bettor who is up 900 coins.
-- This is the message id of the profit-and-loss board that answers that.
ALTER TABLE currency_leaderboard
    ADD COLUMN IF NOT EXISTS pnl_message_id BIGINT;
