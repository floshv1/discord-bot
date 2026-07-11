from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

import asyncpg

from bot.cogs.currency import service as currency_service
from bot.db.client import get_pool

PlaceBetResult = Literal["ok", "closed", "insufficient_funds", "invalid_amount"]


# ---------------------------------------------------------------------------
# Pure payout math
# ---------------------------------------------------------------------------


def settle_parimutuel(bets: Sequence[Mapping], winner: str) -> dict[int, int]:
    """Compute payouts for a parimutuel market. Returns {bet_id: payout} for winning bets only.

    Winners split the entire losing pool proportional to their stake, on top of getting
    their own stake back. A market where nobody bet the winning outcome pays out nothing.
    """
    total_pool = sum(b["amount"] for b in bets)
    winning_bets = [b for b in bets if b["outcome"] == winner]
    winning_pool = sum(b["amount"] for b in winning_bets)
    if winning_pool == 0:
        return {}
    losing_pool = total_pool - winning_pool
    return {b["id"]: b["amount"] + (b["amount"] * losing_pool) // winning_pool for b in winning_bets}


def pool_totals(bets: Sequence[Mapping]) -> dict[str, dict[str, int]]:
    """Aggregate bets by outcome: {outcome: {"total": int, "count": int}}."""
    totals: dict[str, dict[str, int]] = {}
    for b in bets:
        entry = totals.setdefault(b["outcome"], {"total": 0, "count": 0})
        entry["total"] += b["amount"]
        entry["count"] += 1
    return totals


def outcomes_for_market(market: Mapping) -> list[tuple[str, str]]:
    """The bettable (outcome_key, label) pairs for a market — omits Draw for sports with no draws."""
    outcomes = [("home", market["home_name"])]
    if market["sport"] == "football":
        outcomes.append(("draw", "Draw"))
    outcomes.append(("away", market["away_name"]))
    return outcomes


# ---------------------------------------------------------------------------
# Market CRUD
# ---------------------------------------------------------------------------


async def create_market(
    *,
    guild_id: int,
    provider: str,
    external_id: str,
    sport: str,
    competition: str,
    home_name: str,
    away_name: str,
    start_time,
) -> int | None:
    """Insert a new market. Returns its id, or None if it already exists for this provider/external_id."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO betting_markets
            (guild_id, provider, external_id, sport, competition, home_name, away_name, start_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (provider, external_id) DO NOTHING
        RETURNING id
        """,
        guild_id,
        provider,
        external_id,
        sport,
        competition,
        home_name,
        away_name,
        start_time,
    )
    return row["id"] if row else None


async def set_market_message(market_id: int, channel_id: int, message_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE betting_markets SET channel_id = $1, message_id = $2 WHERE id = $3",
        channel_id,
        message_id,
        market_id,
    )


async def get_market(market_id: int) -> asyncpg.Record | None:
    pool = get_pool()
    return await pool.fetchrow("SELECT * FROM betting_markets WHERE id = $1", market_id)


async def get_bets(market_id: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch("SELECT * FROM betting_bets WHERE market_id = $1 ORDER BY created_at", market_id)


async def get_open_markets(guild_id: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch("SELECT * FROM betting_markets WHERE guild_id = $1 AND status = 'open'", guild_id)


async def lock_due_markets(guild_id: int) -> list[asyncpg.Record]:
    """Flip any open market past its start time to locked, returning the ones just locked."""
    pool = get_pool()
    return await pool.fetch(
        """
        UPDATE betting_markets
        SET status = 'locked'
        WHERE guild_id = $1 AND status = 'open' AND start_time <= NOW()
        RETURNING *
        """,
        guild_id,
    )


async def get_locked_markets(guild_id: int) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch("SELECT * FROM betting_markets WHERE guild_id = $1 AND status = 'locked'", guild_id)


# ---------------------------------------------------------------------------
# Betting
# ---------------------------------------------------------------------------


async def place_bet(
    *, market_id: int, guild_id: int, user_id: int, outcome: str, amount: int
) -> tuple[PlaceBetResult, int | None]:
    """Debit the bettor's wallet and record a bet, atomically. Returns (result, new_balance)."""
    if amount <= 0:
        return "invalid_amount", None

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow("SELECT status FROM betting_markets WHERE id = $1 FOR UPDATE", market_id)
            if not market or market["status"] != "open":
                return "closed", None

            balance = await currency_service.get_balance_locked(conn, guild_id=guild_id, user_id=user_id)
            if balance < amount:
                return "insufficient_funds", None

            new_balance = await currency_service.adjust(
                conn, guild_id=guild_id, user_id=user_id, amount=-amount, reason="bet"
            )
            await conn.execute(
                "INSERT INTO betting_bets (market_id, user_id, outcome, amount) VALUES ($1, $2, $3, $4)",
                market_id,
                user_id,
                outcome,
                amount,
            )
            return "ok", new_balance


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


async def resolve_market(market_id: int, winner: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow("SELECT guild_id FROM betting_markets WHERE id = $1 FOR UPDATE", market_id)
            bets = await conn.fetch("SELECT * FROM betting_bets WHERE market_id = $1", market_id)
            payouts = settle_parimutuel(bets, winner)
            for bet in bets:
                payout = payouts.get(bet["id"], 0)
                await conn.execute("UPDATE betting_bets SET payout = $1 WHERE id = $2", payout, bet["id"])
                if payout > 0:
                    await currency_service.adjust(
                        conn, guild_id=market["guild_id"], user_id=bet["user_id"], amount=payout, reason="payout"
                    )
            await conn.execute(
                "UPDATE betting_markets SET status = 'resolved', winner = $1 WHERE id = $2", winner, market_id
            )


async def void_market(market_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            market = await conn.fetchrow("SELECT guild_id FROM betting_markets WHERE id = $1 FOR UPDATE", market_id)
            bets = await conn.fetch("SELECT * FROM betting_bets WHERE market_id = $1", market_id)
            for bet in bets:
                await conn.execute("UPDATE betting_bets SET payout = $1 WHERE id = $2", bet["amount"], bet["id"])
                await currency_service.adjust(
                    conn, guild_id=market["guild_id"], user_id=bet["user_id"], amount=bet["amount"], reason="refund"
                )
            await conn.execute("UPDATE betting_markets SET status = 'void' WHERE id = $1", market_id)
