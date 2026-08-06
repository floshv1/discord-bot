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
    # balance_after is recorded too, so a wrong balance can be *located* in the ledger.
    assert insert_txn_call[0][1:] == (2, 1, 100, "test", 600)


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
async def test_claim_cooldown_remaining_counts_down_to_midnight():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 1000, "last_claim_at": "2026-01-01T00:00:00Z"}
    pool = _mock_pool_with_conn(conn)
    pool.fetchrow = AsyncMock(return_value={"claimed_today": True, "until_midnight": 3600.0})

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        remaining = await service.claim_cooldown_remaining(guild_id=1, user_id=2)

    assert remaining == 3600.0


@pytest.mark.asyncio
async def test_claim_cooldown_remaining_none_when_last_claim_was_yesterday():
    # Claimed at some point, but not today — the calendar day has rolled over, so it's claimable.
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 1000, "last_claim_at": "2026-01-01T00:00:00Z"}
    pool = _mock_pool_with_conn(conn)
    pool.fetchrow = AsyncMock(return_value={"claimed_today": False, "until_midnight": 3600.0})

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        remaining = await service.claim_cooldown_remaining(guild_id=1, user_id=2)

    assert remaining is None


@pytest.mark.asyncio
async def test_grant_refuses_to_push_balance_negative():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 100}
    pool = _mock_pool_with_conn(conn)

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        result = await service.grant(guild_id=1, user_id=2, amount=-500, reason="admin_grant")

    assert result is None
    # No balance UPDATE / ledger row should have been written.
    assert not any("UPDATE currency_wallets SET balance" in str(c) for c in conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_grant_allows_debit_down_to_zero():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 100}
    pool = _mock_pool_with_conn(conn)

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        result = await service.grant(guild_id=1, user_id=2, amount=-100, reason="admin_grant")

    assert result == 0


@pytest.mark.asyncio
async def test_backfill_wallets_returns_count_created():
    conn = AsyncMock()
    conn.fetch.return_value = [{"user_id": 1, "created": True}, {"user_id": 2, "created": True}]

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        created = await service.backfill_wallets(guild_id=9, user_ids=[1, 2, 3])

    # Only two rows came back (user 3 already had a wallet on this guild), so only two were created.
    assert created == 2
    assert conn.fetch.call_args[0][1] == [1, 2, 3]
    assert conn.fetch.call_args[0][3] == service.STARTING_BALANCE

    # And each created wallet gets its opening balance written into the ledger, or the
    # ledger could never rebuild a balance.
    openings = [c for c in conn.execute.call_args_list if "currency_transactions" in str(c)]
    assert len(openings) == 2
    assert all(c[0][4] == "initial" for c in openings)


@pytest.mark.asyncio
async def test_backfill_wallets_restamps_the_guild_without_minting_coins():
    """A wallet left on the guild the bot moved away from is re-pointed, not re-opened.

    The re-stamp comes back from the same RETURNING as a creation, and paying it an opening
    balance would mint 1000 🪙 per member per /setup currency — the ledger would never
    reconcile again. `xmax = 0` is the whole difference.
    """
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"user_id": 1, "created": True},  # genuinely new member
        {"user_id": 2, "created": False},  # came from the old guild — re-stamped only
    ]

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        created = await service.backfill_wallets(guild_id=9, user_ids=[1, 2])

    assert created == 1
    openings = [c for c in conn.execute.call_args_list if "currency_transactions" in str(c)]
    assert len(openings) == 1
    assert openings[0][0][1] == 1  # user_id — the new one, never the re-stamped one

    # And the write really is conditional, or every /balance would churn a row version.
    sql = conn.fetch.call_args[0][0]
    assert "SET guild_id = EXCLUDED.guild_id" in sql
    assert "IS DISTINCT FROM" in sql


@pytest.mark.asyncio
async def test_backfill_wallets_no_members_is_a_noop():
    pool = MagicMock()
    pool.fetch = AsyncMock()

    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        created = await service.backfill_wallets(guild_id=9, user_ids=[])

    assert created == 0
    pool.fetch.assert_not_called()


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


@pytest.mark.asyncio
async def test_get_or_create_wallet_reports_a_freshly_created_wallet():
    conn = AsyncMock()
    conn.fetchval.return_value = True  # xmax = 0 -> the row was inserted
    conn.fetchrow.return_value = {"balance": 1000}

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        wallet, created = await service.get_or_create_wallet(guild_id=1, user_id=7)

    assert created is True
    assert wallet["balance"] == 1000
    # The opening balance goes in the ledger with it.
    assert any("currency_transactions" in str(c) for c in conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_get_or_create_wallet_reports_an_existing_wallet():
    # The conflict branch's WHERE found nothing to change -> no row returned. The wallet
    # already existed on this guild, so the leaderboard has nothing new and must not be redrawn.
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = {"balance": 250}

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        wallet, created = await service.get_or_create_wallet(guild_id=1, user_id=7)

    assert created is False
    assert wallet["balance"] == 250
    assert not any("currency_transactions" in str(c) for c in conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_get_or_create_wallet_restamped_wallet_is_not_a_creation():
    """A wallet dragged over from the old guild returns False from `xmax = 0`, not None.

    It is an *existing* wallet either way — crediting it an opening balance here would be
    the same phantom 1000 🪙 the backfill guards against.
    """
    conn = AsyncMock()
    conn.fetchval.return_value = False  # conflict branch fired: guild_id re-stamped
    conn.fetchrow.return_value = {"balance": 4200}

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        wallet, created = await service.get_or_create_wallet(guild_id=1, user_id=7)

    assert created is False
    assert wallet["balance"] == 4200  # its balance survived the move
    assert not any("currency_transactions" in str(c) for c in conn.execute.call_args_list)
