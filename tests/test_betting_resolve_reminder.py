from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service


@pytest.mark.asyncio
async def test_claim_is_a_single_atomic_update():
    # Claiming and sending must not be two steps: a slow tick would double-ping the creator.
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_resolve_reminders(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    assert sql.strip().upper().startswith("UPDATE")
    assert "RETURNING" in sql
    assert "resolve_reminded_at" in sql


@pytest.mark.asyncio
async def test_only_unsettled_community_bets_are_nagged():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_resolve_reminders(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    # A real match settles itself from its provider — nagging its creator makes no sense,
    # and it has no creator anyway.
    assert "status = 'locked'" in sql
    assert "provider = 'custom'" in sql


@pytest.mark.asyncio
async def test_a_freshly_locked_bet_is_not_nagged_immediately():
    # The creator gets a grace period; the ticker runs every few minutes.
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_resolve_reminders(guild_id=1)

    args = pool.fetch.call_args[0]
    assert service.RESOLVE_REMINDER_AFTER in args
    assert service.RESOLVE_REMINDER_REPEAT in args


def _market(**kw):
    base = {
        "id": 1,
        "competition": "Qui gagne le scrim ?",
        "creator_user_id": 42,
        "channel_id": 500,
        "message_id": 600,
    }
    return {**base, **kw}


@pytest.mark.asyncio
async def test_no_reminder_when_nobody_staked():
    # Nobody's coins are stuck, so there is nothing to chase. Nagging would just be noise.
    from bot.cogs.betting.views import remind_creator_to_settle

    channel = MagicMock()
    channel.send = AsyncMock()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    with patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=[])):
        await remind_creator_to_settle(client, _market())

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_reminder_names_the_creator_the_stake_and_the_way_out():
    from bot.cogs.betting.views import remind_creator_to_settle

    channel = MagicMock()
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock())
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    bets = [
        {"user_id": 1, "amount": 100},
        {"user_id": 2, "amount": 250},
    ]
    with patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)):
        await remind_creator_to_settle(client, _market())

    text = channel.send.call_args[0][0]
    assert "<@42>" in text  # pings the creator, not the channel
    assert "350" in text  # says how much is actually frozen
    assert "/bet resolve" in text  # and how to unfreeze it
    assert "/bet cancel" in text
