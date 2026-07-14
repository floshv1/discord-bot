"""The house: the wallet that stakes both sides so a lone bettor has someone to win from."""

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


def _market(**kw):
    base = {
        "id": 1,
        "guild_id": 1,
        "status": "open",
        "provider": "pandascore",
        "sport": "lol",
        "home_name": "BLG",
        "away_name": "HLE",
    }
    return {**base, **kw}


async def _seed(conn):
    with patch("bot.cogs.betting.service.get_pool", return_value=_mock_pool(conn)):
        return await service.seed_market(1)


def _seed_rows(conn):
    """The betting_bets rows written for the house."""
    return [
        c.args
        for c in conn.execute.call_args_list
        if c.args and "INSERT INTO betting_bets" in c.args[0] and c.args[2] == HOUSE_USER_ID
    ]


@pytest.mark.asyncio
async def test_the_house_backs_every_outcome():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market(), {"balance": 9000}]
    conn.fetchval.side_effect = [False, 9000]  # not seeded yet; house balance

    assert await _seed(conn) is True

    outcomes = [row[3] for row in _seed_rows(conn)]
    assert outcomes == ["home", "away"]
    assert {row[4] for row in _seed_rows(conn)} == {service.HOUSE_SEED_PER_OUTCOME}


@pytest.mark.asyncio
async def test_football_gets_a_line_on_the_draw_too():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market(sport="football"), {"balance": 9000}]
    conn.fetchval.side_effect = [False, 9000]

    await _seed(conn)

    assert [row[3] for row in _seed_rows(conn)] == ["home", "draw", "away"]


@pytest.mark.asyncio
async def test_a_custom_market_is_seeded_lighter_than_a_real_match():
    # Its winner is declared by a member, so the seed must stay under the fee charged to open
    # it — otherwise create/stake/resolve prints coins. See test_betting_house_invariant.py.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market(provider="custom", sport="custom"), {"balance": 9000}]
    conn.fetchval.side_effect = [False, 9000]

    await _seed(conn)

    assert {row[4] for row in _seed_rows(conn)} == {service.CUSTOM_SEED_PER_OUTCOME}
    assert service.CUSTOM_SEED_PER_OUTCOME <= service.CREATE_FEE


@pytest.mark.asyncio
async def test_a_broke_house_leaves_the_market_unseeded_rather_than_breaking_it():
    # Degraded, not broken: the market still prices itself off the crowd, exactly as before.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market()]
    conn.fetchval.side_effect = [False, service.HOUSE_SEED_PER_OUTCOME]  # can't cover both sides

    assert await _seed(conn) is False
    assert not _seed_rows(conn)


@pytest.mark.asyncio
async def test_a_missing_house_wallet_is_not_quietly_invented():
    # get_balance_locked would happily create one with a *member's* opening balance, handing
    # the bank 1000 🪙 out of thin air. Better to open unseeded and say so.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market()]
    conn.fetchval.side_effect = [False, None]

    assert await _seed(conn) is False
    assert not _seed_rows(conn)


@pytest.mark.asyncio
async def test_a_locked_market_is_never_reseeded():
    # Its bettors were promised the pool they could see — moving the odds under them after
    # betting closed would change what they are paid.
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market(status="locked")]

    assert await _seed(conn) is False
    assert not _seed_rows(conn)


@pytest.mark.asyncio
async def test_seeding_twice_does_not_double_the_line():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_market()]
    conn.fetchval.side_effect = [True]  # already has house bets

    assert await _seed(conn) is False
    assert not _seed_rows(conn)


@pytest.mark.asyncio
async def test_the_house_is_kept_off_the_leaderboards():
    from bot.cogs.currency import service as currency_service

    for fn in (service.betting_pnl, currency_service.top_balances):
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        with patch(f"{fn.__module__}.get_pool", return_value=pool):
            await fn(guild_id=1)
        args = pool.fetch.call_args[0]
        assert HOUSE_USER_ID in args, f"{fn.__name__} does not exclude the house"


@pytest.mark.asyncio
async def test_the_pool_never_pays_out_more_than_was_staked():
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
        await service.resolve_market(1, "away")

    credited = sum(
        c.args[3]
        for c in conn.execute.call_args_list
        if c.args and "currency_transactions" in str(c.args[0]) and c.args[3] > 0
    )
    assert credited == sum(b["amount"] for b in bets)  # every coin accounted for, none created


def test_the_backfill_migration_never_touches_the_house():
    """023 re-runs on every boot and backfills an 'initial' row for any wallet lacking one.

    The house's opening row is a 'house_endowment', so without an explicit guard the very next
    restart would hand it a phantom 1000 🪙 transaction and its ledger would never reconcile
    again — silently, a boot *after* the change that caused it.
    """
    from bot.db.models import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "023_currency_ledger.sql").read_text(encoding="utf-8")
    backfill = sql[sql.index("INSERT INTO currency_transactions") :]
    assert f"w.user_id <> {HOUSE_USER_ID}" in backfill
