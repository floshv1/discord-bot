-- The ledger could not reconstruct a balance, which is the one thing a ledger is for.
--
-- 1. The opening 1000 was written straight into currency_wallets with no matching row in
--    currency_transactions, so SUM(amount) was short by exactly STARTING_BALANCE for every
--    member. Those rows are backfilled below, and the code now records them going forward.
--
-- 2. balance_after lets a bad balance be *located*: if the running total stops matching the
--    wallet, the transaction where they diverged is the one that went wrong. Without it you
--    can only see that something is off, not where.
ALTER TABLE currency_transactions
    ADD COLUMN IF NOT EXISTS balance_after BIGINT;

-- The opening balance every existing wallet was given but never recorded. Dated from the
-- wallet's creation so the ledger reads in the right order.
--
-- The house (user_id 0) is excluded, and must be: this statement re-runs on every boot, and
-- the house's opening row is a 'house_endowment', not an 'initial'. Without the guard it would
-- be handed a phantom 1000 🪙 transaction on the first restart after the house shipped, and
-- its ledger would never reconcile again.
INSERT INTO currency_transactions (user_id, guild_id, amount, reason, created_at)
SELECT w.user_id, w.guild_id, 1000, 'initial', COALESCE(w.updated_at, NOW())
FROM currency_wallets w
WHERE w.user_id <> 0
  AND NOT EXISTS (
    SELECT 1 FROM currency_transactions t
    WHERE t.user_id = w.user_id AND t.reason = 'initial'
);

-- Where the log-channel mirror got to, so a restart doesn't re-post the whole ledger.
CREATE TABLE IF NOT EXISTS currency_log_cursor (
    guild_id            BIGINT PRIMARY KEY,
    last_transaction_id BIGINT NOT NULL
);
