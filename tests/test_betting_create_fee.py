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


async def _void(conn):
    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        await service.void_market(1)


def _fee_refunds(conn):
    return [c for c in conn.execute.call_args_list if "bet_create_fee_refund" in str(c)]


CREATOR = 10
OTHER = 20


@pytest.mark.asyncio
async def test_cancelling_a_bet_others_joined_refunds_the_creation_fee():
    # A real bet that had to be voided — the creator shouldn't be out of pocket.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"guild_id": 1, "provider": "custom", "creator_user_id": CREATOR},
        {"balance": 500},  # adjust() for the refunded stake
        {"balance": 600},  # adjust() for the refunded fee
    ]
    conn.fetch.return_value = [{"id": 1, "user_id": OTHER, "amount": 50, "outcome": "home"}]

    await _void(conn)

    assert _fee_refunds(conn), "the creation fee should come back when the bet was genuine"


@pytest.mark.asyncio
async def test_cancelling_a_bet_nobody_joined_keeps_the_fee():
    # Nobody else staked. This is exactly the spam case the fee exists to deter — refunding
    # it would make create-and-cancel free.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"guild_id": 1, "provider": "custom", "creator_user_id": CREATOR}]
    conn.fetch.return_value = []

    await _void(conn)

    assert not _fee_refunds(conn)


@pytest.mark.asyncio
async def test_the_creator_betting_on_their_own_bet_does_not_earn_the_refund():
    # Otherwise: create, stake 1 coin yourself, cancel -> fee back, free spam loop.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"guild_id": 1, "provider": "custom", "creator_user_id": CREATOR},
        {"balance": 500},
    ]
    conn.fetch.return_value = [{"id": 1, "user_id": CREATOR, "amount": 1, "outcome": "home"}]

    await _void(conn)

    assert not _fee_refunds(conn)


@pytest.mark.asyncio
async def test_voiding_a_provider_match_refunds_no_fee():
    # Football/LoL markets are bot-created — nobody paid a fee for them.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"guild_id": 1, "provider": "football-data", "creator_user_id": None},
        {"balance": 500},
    ]
    conn.fetch.return_value = [{"id": 1, "user_id": OTHER, "amount": 50, "outcome": "home"}]

    await _void(conn)

    assert not _fee_refunds(conn)
