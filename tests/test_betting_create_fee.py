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


async def _charge(conn):
    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        return await service.charge_creation_fee(guild_id=1, user_id=2)


@pytest.mark.asyncio
async def test_opening_a_bet_costs_coins():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"balance": 500},  # balance check
        {"balance": 500},  # adjust() re-reads the locked row
    ]

    charged, balance = await _charge(conn)

    assert charged is True
    assert balance == 500 - service.CREATE_FEE


@pytest.mark.asyncio
async def test_cannot_open_a_bet_you_cannot_afford():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"balance": service.CREATE_FEE - 1}]

    charged, balance = await _charge(conn)

    assert charged is False
    # Report what they have, so the error can say why.
    assert balance == service.CREATE_FEE - 1
    # And nothing was debited.
    assert not [c for c in conn.execute.call_args_list if "currency_transactions" in str(c)]


@pytest.mark.asyncio
async def test_exactly_affording_the_fee_is_allowed():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"balance": service.CREATE_FEE},
        {"balance": service.CREATE_FEE},
    ]

    charged, balance = await _charge(conn)

    assert charged is True
    assert balance == 0
