from __future__ import annotations

from collections.abc import Sequence

import asyncpg

from bot.db.client import get_pool

STARTING_BALANCE = 1000
CLAIM_AMOUNT = 100
# The daily claim resets on the calendar day in this zone, not 24h after the last claim.
# Interpolated into SQL rather than passed as a parameter — it's a module constant, never user input.
CLAIM_TZ = "Europe/Paris"


async def get_or_create_wallet(guild_id: int, user_id: int) -> tuple[asyncpg.Record, bool]:
    """The member's wallet, plus whether this call is what created it.

    Callers use the flag to decide whether the leaderboard actually needs redrawing:
    merely *looking* at a balance changes nothing, and redrawing on every /balance meant
    someone repeatedly checking their coins kept re-editing the leaderboard message.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            created = await conn.fetchval(
                """
                INSERT INTO currency_wallets (user_id, guild_id, balance)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO NOTHING
                RETURNING user_id
                """,
                user_id,
                guild_id,
                STARTING_BALANCE,
            )
            if created is not None:
                await _record_opening_balance(conn, guild_id=guild_id, user_id=user_id)
            wallet = await conn.fetchrow(
                "SELECT * FROM currency_wallets WHERE user_id = $1",
                user_id,
            )
            return wallet, created is not None


async def _record_opening_balance(conn, *, guild_id: int, user_id: int) -> None:
    """Put the starting balance in the ledger too.

    It used to be written straight into the wallet with no transaction, so SUM(amount) was
    short by exactly STARTING_BALANCE for everyone and no balance could be rebuilt from the
    ledger — which is the only thing a ledger is for.
    """
    await conn.execute(
        """
        INSERT INTO currency_transactions (user_id, guild_id, amount, reason, balance_after)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id,
        guild_id,
        STARTING_BALANCE,
        "initial",
        STARTING_BALANCE,
    )


async def get_balance(guild_id: int, user_id: int) -> int:
    wallet, _ = await get_or_create_wallet(guild_id, user_id)
    return wallet["balance"]


async def get_balance_locked(conn: asyncpg.Connection, *, guild_id: int, user_id: int) -> int:
    """Ensure a wallet exists and return its balance, holding a row lock for the rest of the transaction.

    Must run inside a transaction. Used to safely check-then-spend a balance (e.g. before placing a bet).
    """
    await conn.execute(
        """
        INSERT INTO currency_wallets (user_id, guild_id, balance)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
        guild_id,
        STARTING_BALANCE,
    )
    row = await conn.fetchrow(
        "SELECT balance FROM currency_wallets WHERE user_id = $1 FOR UPDATE",
        user_id,
    )
    return row["balance"]


async def adjust(
    conn: asyncpg.Connection,
    *,
    guild_id: int,
    user_id: int,
    amount: int,
    reason: str,
) -> int:
    """Apply a signed balance change and record the transaction. Must run inside a transaction.

    Locks the wallet row first to serialize concurrent adjustments (e.g. rapid bet clicks).
    """
    balance = await get_balance_locked(conn, guild_id=guild_id, user_id=user_id)
    new_balance = balance + amount
    await conn.execute(
        "UPDATE currency_wallets SET balance = $1, updated_at = NOW() WHERE user_id = $2",
        new_balance,
        user_id,
    )
    await conn.execute(
        """
        INSERT INTO currency_transactions (user_id, guild_id, amount, reason, balance_after)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id,
        guild_id,
        amount,
        reason,
        new_balance,
    )
    return new_balance


async def grant(guild_id: int, user_id: int, amount: int, reason: str, *, allow_negative: bool = False) -> int | None:
    """Standalone entry point for adjustments outside an existing transaction.

    Returns the new balance, or None if the change would push the wallet below zero
    (which a careless `/currency give @user -99999` would otherwise do).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            balance = await get_balance_locked(conn, guild_id=guild_id, user_id=user_id)
            if not allow_negative and balance + amount < 0:
                return None
            return await adjust(conn, guild_id=guild_id, user_id=user_id, amount=amount, reason=reason)


async def claim(guild_id: int, user_id: int) -> int | None:
    """Grant the daily claim if the wallet hasn't claimed yet *today* (Paris calendar day).

    Returns the new balance, or None if already claimed today. Resets at midnight rather
    than 24h after the last claim, so claiming late doesn't push tomorrow's claim later.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            created = await conn.fetchval(
                """
                INSERT INTO currency_wallets (user_id, guild_id, balance)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO NOTHING
                RETURNING user_id
                """,
                user_id,
                guild_id,
                STARTING_BALANCE,
            )
            if created is not None:
                await _record_opening_balance(conn, guild_id=guild_id, user_id=user_id)
            row = await conn.fetchrow(
                f"""
                UPDATE currency_wallets
                SET last_claim_at = NOW()
                WHERE user_id = $1
                  AND (
                    last_claim_at IS NULL
                    OR (last_claim_at AT TIME ZONE '{CLAIM_TZ}')::date
                       < (NOW() AT TIME ZONE '{CLAIM_TZ}')::date
                  )
                RETURNING user_id
                """,
                user_id,
            )
            if row is None:
                return None
            return await adjust(conn, guild_id=guild_id, user_id=user_id, amount=CLAIM_AMOUNT, reason="claim")


async def claim_cooldown_remaining(guild_id: int, user_id: int) -> float | None:
    """Seconds until midnight (Paris), when the next claim unlocks. None if claimable now."""
    wallet, _ = await get_or_create_wallet(guild_id, user_id)
    if wallet["last_claim_at"] is None:
        return None
    pool = get_pool()
    row = await pool.fetchrow(
        f"""
        SELECT
            (last_claim_at AT TIME ZONE '{CLAIM_TZ}')::date
                >= (NOW() AT TIME ZONE '{CLAIM_TZ}')::date AS claimed_today,
            EXTRACT(EPOCH FROM (
                (date_trunc('day', NOW() AT TIME ZONE '{CLAIM_TZ}') + INTERVAL '1 day')
                    AT TIME ZONE '{CLAIM_TZ}' - NOW()
            )) AS until_midnight
        FROM currency_wallets WHERE user_id = $1
        """,
        user_id,
    )
    if not row or not row["claimed_today"]:
        return None
    return float(row["until_midnight"])


async def backfill_wallets(guild_id: int, user_ids: Sequence[int]) -> int:
    """Give every listed member a starting wallet. Members who already have one are left untouched.

    Returns the number of wallets actually created.
    """
    if not user_ids:
        return 0
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                INSERT INTO currency_wallets (user_id, guild_id, balance)
                SELECT unnest($1::BIGINT[]), $2, $3
                ON CONFLICT (user_id) DO NOTHING
                RETURNING user_id
                """,
                list(user_ids),
                guild_id,
                STARTING_BALANCE,
            )
            # The same opening balance, in the ledger, for exactly the wallets just created.
            for row in rows:
                await _record_opening_balance(conn, guild_id=guild_id, user_id=row["user_id"])
    return len(rows)


async def get_history(guild_id: int, user_id: int, limit: int = 15) -> list[asyncpg.Record]:
    """A member's most recent transactions, newest first."""
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT amount, reason, balance_after, created_at
        FROM currency_transactions
        WHERE guild_id = $1 AND user_id = $2
        ORDER BY id DESC
        LIMIT $3
        """,
        guild_id,
        user_id,
        limit,
    )


async def ledger_total(guild_id: int, user_id: int) -> int:
    """What the wallet *should* hold, according to the ledger alone.

    If this disagrees with the stored balance, something wrote a balance without recording
    it — that gap is the incident, and it's what makes the ledger worth keeping.
    """
    pool = get_pool()
    return (
        await pool.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM currency_transactions WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        or 0
    )


async def drain_new_transactions(guild_id: int, limit: int = 20) -> list[asyncpg.Record]:
    """Transactions not yet mirrored to the log channel, advancing the cursor as it reads.

    Reading the ledger itself — rather than posting from each of the ten-odd call sites that
    move coins — means a transaction cannot be missed by someone forgetting a call. There is
    one source of truth, and it is the table `adjust()` writes to.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cursor = (
                await conn.fetchval(
                    "SELECT last_transaction_id FROM currency_log_cursor WHERE guild_id = $1 FOR UPDATE",
                    guild_id,
                )
                or 0
            )
            rows = await conn.fetch(
                """
                SELECT id, user_id, amount, reason, balance_after, created_at
                FROM currency_transactions
                WHERE guild_id = $1 AND id > $2
                ORDER BY id
                LIMIT $3
                """,
                guild_id,
                cursor,
                limit,
            )
            if rows:
                await conn.execute(
                    """
                    INSERT INTO currency_log_cursor (guild_id, last_transaction_id)
                    VALUES ($1, $2)
                    ON CONFLICT (guild_id) DO UPDATE SET last_transaction_id = EXCLUDED.last_transaction_id
                    """,
                    guild_id,
                    rows[-1]["id"],
                )
            return rows


async def start_log_cursor_at_latest(guild_id: int) -> None:
    """Skip whatever is already in the ledger, so first boot doesn't dump its whole history."""
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO currency_log_cursor (guild_id, last_transaction_id)
        VALUES ($1, COALESCE((SELECT MAX(id) FROM currency_transactions WHERE guild_id = $1), 0))
        ON CONFLICT (guild_id) DO NOTHING
        """,
        guild_id,
    )


async def top_balances(guild_id: int, limit: int = 10) -> list[asyncpg.Record]:
    """The leaderboard: ranked on wallet + coins currently staked in unsettled bets.

    A stake is debited from the wallet the moment it's placed, so ranking on `balance` alone
    made anyone who actually bet look poorer than someone who never played — the leaderboard
    rewarded sitting on your hands, on a server built around betting. Those coins aren't
    gone; they're in the pool, and they come back on a win or a void.

    Only `open`/`locked` markets count. A resolved or voided one has already paid out or
    refunded into the wallet, so counting it again would double it.
    """
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT w.user_id,
               w.balance,
               COALESCE(s.staked, 0) AS staked,
               w.balance + COALESCE(s.staked, 0) AS total
        FROM currency_wallets w
        LEFT JOIN (
            SELECT b.user_id, SUM(b.amount) AS staked
            FROM betting_bets b
            JOIN betting_markets m ON m.id = b.market_id
            WHERE m.guild_id = $1 AND m.status IN ('open', 'locked')
            GROUP BY b.user_id
        ) s ON s.user_id = w.user_id
        WHERE w.guild_id = $1
        ORDER BY total DESC, w.balance DESC
        LIMIT $2
        """,
        guild_id,
        limit,
    )
