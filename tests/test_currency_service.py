from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.currency import service


def _mock_pool_with_conn(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    return pool


@pytest.mark.asyncio
async def test_adjust_credits_balance_and_records_transaction():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 500}

    new_balance = await service.adjust(conn, guild_id=1, user_id=2, amount=100, reason="test")

    assert new_balance == 600
    update_call = conn.execute.call_args_list[1]
    assert update_call[0][1] == 600
    insert_txn_call = conn.execute.call_args_list[2]
    assert insert_txn_call[0][1:] == (2, 1, 100, "test")


@pytest.mark.asyncio
async def test_adjust_debits_balance():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 500}

    new_balance = await service.adjust(conn, guild_id=1, user_id=2, amount=-200, reason="bet")

    assert new_balance == 300


@pytest.mark.asyncio
async def test_claim_within_cooldown_returns_none():
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # UPDATE ... RETURNING found no eligible row
    pool = _mock_pool_with_conn(conn)

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        result = await service.claim(guild_id=1, user_id=2)

    assert result is None


@pytest.mark.asyncio
async def test_claim_success_grants_amount():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"user_id": 2},  # UPDATE ... RETURNING succeeded
        {"balance": 1000},  # adjust()'s FOR UPDATE select
    ]
    pool = _mock_pool_with_conn(conn)

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        result = await service.claim(guild_id=1, user_id=2)

    assert result == 1000 + service.CLAIM_AMOUNT


@pytest.mark.asyncio
async def test_claim_cooldown_remaining_none_when_never_claimed():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 1000, "last_claim_at": None}
    pool = _mock_pool_with_conn(conn)

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        remaining = await service.claim_cooldown_remaining(guild_id=1, user_id=2)

    assert remaining is None


@pytest.mark.asyncio
async def test_claim_cooldown_remaining_returns_seconds():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 1000, "last_claim_at": "2026-01-01T00:00:00Z"}
    pool = _mock_pool_with_conn(conn)
    pool.fetchrow = AsyncMock(return_value={"remaining": 3600.0})

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        remaining = await service.claim_cooldown_remaining(guild_id=1, user_id=2)

    assert remaining == 3600.0


@pytest.mark.asyncio
async def test_top_balances_queries_by_guild():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"user_id": 1, "balance": 500}])

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        rows = await service.top_balances(guild_id=1, limit=5)

    assert rows == [{"user_id": 1, "balance": 500}]
    pool.fetch.assert_called_once()
    assert pool.fetch.call_args[0][1] == 1
    assert pool.fetch.call_args[0][2] == 5
