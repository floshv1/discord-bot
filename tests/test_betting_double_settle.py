"""A market may only ever be settled once — claim it atomically, never compute-then-write.

Without this, two /bet resolve calls a second apart (double click, two clients, a mod racing
the ticker) both pass a stale status check and both pay out — winners paid twice, minted out
of thin air. The fix mirrors the tribunal's claim_verdict: the first statement of the
transaction is the claim, and the loser of the race gets zero rows back and must stand down
before touching a single coin.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.currency.service import HOUSE_USER_ID


def _mock_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    return pool


def _ledger(conn):
    """Every row adjust() wrote, as (user_id, amount, reason) — the ledger is the source of truth."""
    return [
        (c.args[1], c.args[3], c.args[4])
        for c in conn.execute.call_args_list
        if c.args and "currency_transactions" in str(c.args[0])
    ]


@pytest.mark.asyncio
async def test_an_already_resolved_market_pays_nobody_a_second_time():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None]  # the claim lost the race — zero rows back

    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        resolved = await service.resolve_market(1, "home")

    assert resolved is False
    assert not _ledger(conn)
    conn.fetch.assert_not_awaited()  # never even reads the bets, let alone pays them


@pytest.mark.asyncio
async def test_an_already_resolved_market_cannot_then_be_voided():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None]  # the claim lost the race

    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        result = await service.void_market(1)

    assert result == service.VoidResult(voided=False)
    assert not _ledger(conn)
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_market_claims_with_a_single_atomic_update():
    # Mirrors test_claim_is_a_single_atomic_update in test_betting_resolve_reminder.py: the
    # claim and the write must be one statement, or two racing calls can both read "claimable".
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None]

    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        await service.resolve_market(1, "home")

    sql = conn.fetchrow.call_args_list[0].args[0]
    assert sql.strip().upper().startswith("UPDATE")
    assert "status IN ('open', 'locked')" in sql
    assert "RETURNING" in sql


@pytest.mark.asyncio
async def test_void_market_claims_with_a_single_atomic_update():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None]

    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        await service.void_market(1)

    sql = conn.fetchrow.call_args_list[0].args[0]
    assert sql.strip().upper().startswith("UPDATE")
    assert "status IN ('open', 'locked')" in sql
    assert "RETURNING" in sql


@pytest.mark.asyncio
async def test_a_normal_settle_still_pays_exactly_the_pool():
    """The invariant that makes all this safe: betting cannot mint coins.

    The house wins and loses like a member, so every coin paid to a winner is a coin some
    loser staked. Whatever rounding leaves behind goes to the house rather than vanishing.
    """
    conn = AsyncMock()
    bets = [
        {"id": -1, "user_id": HOUSE_USER_ID, "outcome": "home", "amount": 250},
        {"id": -2, "user_id": HOUSE_USER_ID, "outcome": "away", "amount": 250},
        {"id": 1, "user_id": 7, "outcome": "away", "amount": 100},
        {"id": 2, "user_id": 8, "outcome": "home", "amount": 33},
    ]
    conn.fetchrow.side_effect = [{"guild_id": 1}, *({"balance": 1000} for _ in range(5))]
    conn.fetch.return_value = bets

    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        resolved = await service.resolve_market(1, "away")

    assert resolved is True
    credited = sum(
        c.args[3]
        for c in conn.execute.call_args_list
        if c.args and "currency_transactions" in str(c.args[0]) and c.args[3] > 0
    )
    assert credited == sum(b["amount"] for b in bets)  # every coin accounted for, none created


@pytest.mark.asyncio
async def test_a_normal_void_still_refunds_exactly_the_pool():
    conn = AsyncMock()
    bets = [
        {"id": -1, "user_id": HOUSE_USER_ID, "outcome": "home", "amount": 250},
        {"id": -2, "user_id": HOUSE_USER_ID, "outcome": "away", "amount": 250},
        {"id": 1, "user_id": 7, "outcome": "away", "amount": 100},
    ]
    conn.fetchrow.side_effect = [
        {"guild_id": 1, "provider": "pandascore", "creator_user_id": None},
        *({"balance": 1000} for _ in range(3)),
    ]
    conn.fetch.return_value = bets

    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        result = await service.void_market(1)

    assert result.voided is True
    refunded = sum(
        c.args[3]
        for c in conn.execute.call_args_list
        if c.args and "currency_transactions" in str(c.args[0]) and c.args[4] == "refund"
    )
    assert refunded == sum(b["amount"] for b in bets)
