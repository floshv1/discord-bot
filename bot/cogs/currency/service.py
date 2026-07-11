from __future__ import annotations

import asyncpg

from bot.db.client import get_pool

STARTING_BALANCE = 1000
CLAIM_AMOUNT = 100
CLAIM_COOLDOWN_HOURS = 24


async def get_or_create_wallet(guild_id: int, user_id: int) -> asyncpg.Record:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
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
            return await conn.fetchrow(
                "SELECT * FROM currency_wallets WHERE user_id = $1",
                user_id,
            )


async def get_balance(guild_id: int, user_id: int) -> int:
    wallet = await get_or_create_wallet(guild_id, user_id)
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
        "INSERT INTO currency_transactions (user_id, guild_id, amount, reason) VALUES ($1, $2, $3, $4)",
        user_id,
        guild_id,
        amount,
        reason,
    )
    return new_balance


async def grant(guild_id: int, user_id: int, amount: int, reason: str) -> int:
    """Standalone entry point for adjustments outside an existing transaction."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await adjust(conn, guild_id=guild_id, user_id=user_id, amount=amount, reason=reason)


async def claim(guild_id: int, user_id: int) -> int | None:
    """Grant the daily claim if eligible. Returns the new balance, or None if still on cooldown."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
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
                """
                UPDATE currency_wallets
                SET last_claim_at = NOW()
                WHERE user_id = $1
                  AND (last_claim_at IS NULL OR last_claim_at <= NOW() - INTERVAL '24 hours')
                RETURNING user_id
                """,
                user_id,
            )
            if row is None:
                return None
            return await adjust(conn, guild_id=guild_id, user_id=user_id, amount=CLAIM_AMOUNT, reason="claim")


async def claim_cooldown_remaining(guild_id: int, user_id: int) -> float | None:
    """Seconds remaining until the next claim is available, or None if claimable now."""
    wallet = await get_or_create_wallet(guild_id, user_id)
    if wallet["last_claim_at"] is None:
        return None
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT EXTRACT(EPOCH FROM (last_claim_at + INTERVAL '24 hours' - NOW())) AS remaining
        FROM currency_wallets WHERE user_id = $1
        """,
        user_id,
    )
    remaining = row["remaining"]
    return remaining if remaining and remaining > 0 else None


async def top_balances(guild_id: int, limit: int = 10) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        "SELECT user_id, balance FROM currency_wallets WHERE guild_id = $1 ORDER BY balance DESC LIMIT $2",
        guild_id,
        limit,
    )
