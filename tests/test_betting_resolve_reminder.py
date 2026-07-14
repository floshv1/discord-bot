from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.currency.service import HOUSE_USER_ID


@pytest.mark.asyncio
async def test_claim_is_a_single_atomic_update():
    # Claiming and sending must not be two steps: a slow tick would double-ping the creator.
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_settle_reminders(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    assert sql.strip().upper().startswith("UPDATE")
    assert "RETURNING" in sql
    assert "resolve_reminded_at" in sql


@pytest.mark.asyncio
async def test_provider_markets_are_chased_too():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_settle_reminders(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    assert "status = 'locked'" in sql
    # This filter is what let a finished MSI match sit locked with a member's coins in it and
    # never say a word: a provider that stops reporting results leaves stakes frozen exactly
    # like a forgetful creator does, and neither may be silent.
    assert "provider = 'custom'" not in sql


@pytest.mark.asyncio
async def test_a_freshly_locked_bet_is_not_nagged_immediately():
    # The creator gets a grace period; the ticker runs every few minutes.
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_settle_reminders(guild_id=1)

    args = pool.fetch.call_args[0]
    assert service.RESOLVE_REMINDER_AFTER in args
    assert service.RESOLVE_REMINDER_REPEAT in args


@pytest.mark.asyncio
async def test_stuck_markets_are_eventually_refunded():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.get_stuck_markets(guild_id=1)

    sql, args = pool.fetch.call_args[0][0], pool.fetch.call_args[0]
    assert "status = 'locked'" in sql
    assert service.STUCK_VOID_AFTER in args


def _market(**kw):
    base = {
        "id": 1,
        "provider": "custom",
        "competition": "Qui gagne le scrim ?",
        "home_name": "Team Blue",
        "away_name": "Team Red",
        "creator_user_id": 42,
        "channel_id": 500,
        "message_id": 600,
        "guild_id": 1,
    }
    return {**base, **kw}


def _channel(channel_id=500):
    # The id matters: remind_to_settle only replies to the card when it is posting into the
    # card's own channel. A bare MagicMock's .id is a Mock, which equals nothing — every test
    # would then take the "not the card's channel" branch and the reply path would be dead
    # code that no test covers.
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock())
    return channel


@pytest.mark.asyncio
async def test_no_reminder_when_nobody_staked():
    # Nobody's coins are stuck, so there is nothing to chase. Nagging would just be noise.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    with patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=[])):
        await remind_to_settle(client, _market())

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_house_seed_alone_is_not_stuck_money():
    # The house is staked on every market ever opened. If its seed counted as money at risk,
    # every unplayed fixture on the board would nag the channel forever.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    seed = [
        {"user_id": HOUSE_USER_ID, "amount": 250, "outcome": "home"},
        {"user_id": HOUSE_USER_ID, "amount": 250, "outcome": "away"},
    ]
    with patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=seed)):
        await remind_to_settle(client, _market())

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_reminder_names_the_creator_the_stake_and_the_way_out():
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    bets = [
        {"user_id": 1, "amount": 100, "outcome": "home"},
        {"user_id": 2, "amount": 250, "outcome": "away"},
        {"user_id": HOUSE_USER_ID, "amount": 250, "outcome": "home"},
    ]
    with patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)):
        await remind_to_settle(client, _market())

    text = channel.send.call_args[0][0]
    assert "<@42>" in text  # pings the creator, not the channel
    assert "350" in text  # the members' money, not the house's seed
    assert "/bet resolve" in text  # and how to unfreeze it
    assert "/bet cancel" in text


@pytest.mark.asyncio
async def test_a_stuck_match_names_the_teams_and_the_deadline():
    # A provider market has no creator to ping, so the channel has to be told instead —
    # this is the message that was missing when the MSI match froze.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    market = _market(provider="pandascore", creator_user_id=None, home_name="BLG", away_name="HLE")
    bets = [{"user_id": 1, "amount": 100, "outcome": "away"}]
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=None)),
    ):
        await remind_to_settle(client, market)

    text = channel.send.call_args[0][0]
    assert "BLG" in text and "HLE" in text
    assert "<@None>" not in text  # there is no creator to ping
    assert "/bet resolve" in text
    assert str(service.STUCK_VOID_AFTER.days) in text  # promises the automatic refund


@pytest.mark.asyncio
async def test_a_creator_who_bet_is_chased_in_the_staff_channel_instead():
    # They can't settle their own bet any more, so pinging them under the card would be telling
    # them to do something the bot will refuse. It's a mod's job now — chase the mods.
    from bot.cogs.betting.views import remind_to_settle

    staff = _channel(channel_id=99)
    client = MagicMock()
    client.get_channel = MagicMock(return_value=staff)

    bets = [{"user_id": 42, "amount": 100, "outcome": "home"}]  # 42 is the creator
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=99)),
    ):
        await remind_to_settle(client, _market())

    client.get_channel.assert_called_with(99)
    staff.send.assert_awaited_once()
    text = staff.send.call_args[0][0]
    assert "modérateur" in text


@pytest.mark.asyncio
async def test_a_staff_channel_reminder_never_calls_fetch_message():
    # The card lives in the betting channel, not the staff channel — attempting a reference
    # there is a guaranteed NotFound (a wasted round-trip), and a Forbidden there (no Read
    # Message History) would be swallowed and leave the reminder unsent entirely, which is
    # the exact silence this function exists to prevent. Skip the reference off the card's
    # own channel and send plainly instead.
    from bot.cogs.betting.views import remind_to_settle

    staff = _channel(channel_id=99)
    client = MagicMock()
    client.get_channel = MagicMock(return_value=staff)

    bets = [{"user_id": 42, "amount": 100, "outcome": "home"}]  # creator bet, gavel is gone
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=99)),
    ):
        await remind_to_settle(client, _market())

    staff.fetch_message.assert_not_awaited()
    staff.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_reminder_still_replies_under_the_card_in_its_own_channel():
    # The other half of the rule above, and the half a regression would eat silently: in the
    # betting channel the reminder *must* still hang off the card, or a member reading it has
    # no idea which bet is being chased.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()  # id 500 — the card's own channel
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    bets = [{"user_id": 1, "amount": 100, "outcome": "home"}]  # the creator (42) stayed out
    with patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)):
        await remind_to_settle(client, _market())

    channel.fetch_message.assert_awaited_once_with(600)
    assert channel.send.call_args.kwargs["reference"] is channel.fetch_message.return_value


@pytest.mark.asyncio
async def test_with_no_staff_channel_the_reminder_still_lands_in_the_betting_channel():
    # A frozen market must never be silent. Falling back is worse than the staff channel, and
    # far better than nothing.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    bets = [{"user_id": 42, "amount": 100, "outcome": "home"}]  # the creator bet, gavel is gone
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=None)),
    ):
        await remind_to_settle(client, _market())

    channel.send.assert_awaited_once()
