from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.currency.service import HOUSE_USER_ID


def _ledger(conn):
    """Every row adjust() wrote, as (user_id, amount, reason) — the ledger is the source of truth."""
    return [
        (c.args[1], c.args[3], c.args[4])
        for c in conn.execute.call_args_list
        if c.args and "currency_transactions" in str(c.args[0])
    ]


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
        {"balance": 9000},  # adjust() on the house, which the fee is paid to
    ]

    charged, balance = await _charge(conn)

    assert charged is True
    assert balance == 500 - service.CREATE_FEE


@pytest.mark.asyncio
async def test_the_fee_is_paid_to_the_house_not_burned():
    # It is what funds the seed on the next market. Burning it would make the house a pure
    # faucet that drains until it is broke.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"balance": 500}, {"balance": 500}, {"balance": 9000}]

    await _charge(conn)

    assert (HOUSE_USER_ID, service.CREATE_FEE, "bet_create_fee") in _ledger(conn)
    # And the member paid exactly it — the fee moved, it was not conjured.
    assert (2, -service.CREATE_FEE, "bet_create_fee") in _ledger(conn)


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
        {"balance": 9000},  # the house being paid the fee
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
        {"balance": 9000},  # adjust() taking it back off the house
    ]
    conn.fetch.return_value = [{"id": 1, "user_id": OTHER, "amount": 50, "outcome": "home"}]

    await _void(conn)

    assert _fee_refunds(conn), "the creation fee should come back when the bet was genuine"
    # Out of the house's pocket, since that is where it went — not minted out of nowhere.
    assert (HOUSE_USER_ID, -service.CREATE_FEE, "bet_create_fee_refund") in _ledger(conn)


@pytest.mark.asyncio
async def test_the_house_seed_does_not_earn_the_creator_their_fee_back():
    # The house is staked on every bet ever opened. If its seed counted as "somebody else
    # joined", the refund would be unconditional and create-and-cancel would be free again —
    # which is the exact loop the fee exists to close.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"guild_id": 1, "provider": "custom", "creator_user_id": CREATOR},
        {"balance": 500},
        {"balance": 500},
    ]
    conn.fetch.return_value = [
        {"id": 1, "user_id": HOUSE_USER_ID, "amount": 250, "outcome": "home"},
        {"id": 2, "user_id": HOUSE_USER_ID, "amount": 250, "outcome": "away"},
    ]

    await _void(conn)

    assert not _fee_refunds(conn)


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
