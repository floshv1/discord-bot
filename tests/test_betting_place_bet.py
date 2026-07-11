from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service


def _mock_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    return pool


async def _place(conn, outcome="home", amount=100):
    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        return await service.place_bet(market_id=1, guild_id=2, user_id=3, outcome=outcome, amount=amount)


@pytest.mark.asyncio
async def test_first_bet_on_a_market_is_accepted():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"status": "open"},  # market lock
        None,  # no existing bet
        {"balance": 500},  # balance check
        {"balance": 500},  # adjust() re-reads the locked row
    ]
    result = await _place(conn)

    assert result.status == "ok"
    assert result.new_balance == 400
    assert result.topped_up is False


@pytest.mark.asyncio
async def test_cannot_back_a_second_option_on_the_same_market():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"status": "open"},
        {"outcome": "home"},  # already on home
    ]
    result = await _place(conn, outcome="away")

    assert result.status == "other_outcome"
    assert result.existing_outcome == "home"
    # Nothing was debited and no bet row was written.
    assert not any("INSERT INTO betting_bets" in str(c) for c in conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_topping_up_the_same_option_is_allowed():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"status": "open"},
        {"outcome": "home"},  # already on home, betting home again
        {"balance": 500},
        {"balance": 500},  # adjust() re-reads the locked row
    ]
    result = await _place(conn, outcome="home", amount=200)

    assert result.status == "ok"
    assert result.topped_up is True
    assert result.new_balance == 300


@pytest.mark.asyncio
async def test_cannot_bet_on_a_locked_market():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"status": "locked"}]
    result = await _place(conn)

    assert result.status == "closed"


@pytest.mark.asyncio
async def test_cannot_bet_more_than_your_balance():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"status": "open"},
        None,
        {"balance": 50},
    ]
    result = await _place(conn, amount=100)

    assert result.status == "insufficient_funds"
    # Report what they actually have — "not enough coins" alone leaves them guessing.
    assert result.new_balance == 50


@pytest.mark.asyncio
async def test_stake_must_be_positive():
    conn = AsyncMock()
    result = await _place(conn, amount=0)
    assert result.status == "invalid_amount"
