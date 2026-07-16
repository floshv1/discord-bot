from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.betting.cog import BettingCog
from bot.cogs.betting.providers import ResultDTO
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
async def test_a_match_still_being_played_is_not_claimed():
    # The window measures from kickoff, and a football match runs ~1h50-2h05 — so at the 2h
    # reminder mark it is routinely still in play. Excluding from the CLAIM (not filtering the
    # result) is the point: the claim stamps resolve_reminded_at, so a claimed-then-discarded
    # market would go quiet for 24h if the result really never arrived.
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.claim_settle_reminders(guild_id=1, exclude_ids=[7, 9])

    sql, args = pool.fetch.call_args[0][0], pool.fetch.call_args[0]
    assert "NOT (id = ANY(" in sql
    assert [7, 9] in args


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
    # A provider market has no creator to ping, so the staff channel has to be told instead —
    # this is the message that was missing when the MSI match froze.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel(channel_id=99)
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    market = _market(provider="pandascore", creator_user_id=None, home_name="BLG", away_name="HLE")
    bets = [{"user_id": 1, "amount": 100, "outcome": "away"}]
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=99)),
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


# --- The reminder must not race the auto-resolution -------------------------
#
# A market locks at kickoff and the reminder is due 2h later, but a football match runs
# ~1h50-2h05. Every case below is a locked market past that mark: only one of them is a match
# that is genuinely still being played, and only that one may mute the reminder.


def _cog() -> BettingCog:
    bot = MagicMock()
    bot.config = MagicMock(football_data_api_key=None, pandascore_api_key=None, guild_id=1)
    return BettingCog(bot)


def _provider(results):
    provider = MagicMock()
    provider.name = "pandascore"
    provider.get_results = AsyncMock(return_value=results)
    return provider


@pytest.mark.asyncio
async def test_a_match_still_on_the_clock_holds_the_reminder_back():
    cog = _cog()
    result = ResultDTO(status="pending", winner=None)

    assert await cog._settle_market(MagicMock(), _market(external_id="x"), result) is True


@pytest.mark.asyncio
async def test_a_finished_match_with_no_readable_winner_still_rings():
    # The MSI case: finished, but the winner is unmappable. This IS the stuck market the
    # reminder exists for — only a mod can call it now, so it must not be muted.
    cog = _cog()
    result = ResultDTO(status="finished", winner=None)

    assert await cog._settle_market(MagicMock(), _market(external_id="x"), result) is False


@pytest.mark.asyncio
async def test_a_result_the_provider_could_not_give_us_still_rings():
    # None means we genuinely don't know: unreachable, rate-limited, or absent from the batch.
    # A provider that stopped answering is precisely what the reminder exists to surface —
    # silence here would be the old MSI bug all over again.
    cog = _cog()

    assert await cog._settle_market(MagicMock(), _market(external_id="x"), None) is False


def _ticker_patches(locked):
    return (
        patch("bot.cogs.betting.cog.service.get_locked_markets", new=AsyncMock(return_value=locked)),
        patch("bot.cogs.betting.cog.service.claim_settle_reminders", new=AsyncMock(return_value=[])),
        patch("bot.cogs.betting.cog.service.get_stuck_markets", new=AsyncMock(return_value=[])),
    )


@pytest.mark.asyncio
async def test_the_ticker_excludes_only_the_matches_still_being_played():
    cog = _cog()
    running = _market(id=5, provider="pandascore", external_id="x")
    stuck = _market(id=6, provider="pandascore", external_id="y")
    # "x" is still on the clock; "y" came back absent from the batch — the provider had nothing
    # for it. Only "x" may be muted.
    cog.providers = [_provider({"x": ResultDTO(status="pending", winner=None)})]

    locked_p, claim_p, stuck_p = _ticker_patches([running, stuck])
    with locked_p, claim_p as claim, stuck_p:
        await cog.resolution_ticker()

    assert claim.await_args.kwargs["exclude_ids"] == [5]  # the stuck one is still chased


@pytest.mark.asyncio
async def test_the_ticker_asks_each_provider_once_for_every_match():
    # The rate-limit fix at the call site: one request per provider per tick, not per market.
    # football-data's free tier allows 10 a minute and a Champions League matchday locks 18.
    cog = _cog()
    markets = [_market(id=i, provider="pandascore", external_id=f"x{i}") for i in range(18)]
    provider = _provider({})
    cog.providers = [provider]

    locked_p, claim_p, stuck_p = _ticker_patches(markets)
    with locked_p, claim_p, stuck_p:
        await cog.resolution_ticker()

    provider.get_results.assert_awaited_once()
    assert list(provider.get_results.await_args[0][0]) == [f"x{i}" for i in range(18)]


@pytest.mark.asyncio
async def test_the_ticker_never_asks_a_provider_about_another_providers_markets():
    # Custom bets have no provider and are settled by hand; filtering by name skips them free.
    cog = _cog()
    locked = [
        _market(id=1, provider="pandascore", external_id="lol"),
        _market(id=2, provider="football_data", external_id="foot"),
        _market(id=3, provider="custom", external_id="nope"),
    ]
    provider = _provider({})
    cog.providers = [provider]

    locked_p, claim_p, stuck_p = _ticker_patches(locked)
    with locked_p, claim_p, stuck_p:
        await cog.resolution_ticker()

    assert list(provider.get_results.await_args[0][0]) == ["lol"]


def _mod(user_id, *, is_mod=True, is_bot=False):
    member = MagicMock()
    member.id = user_id
    member.bot = is_bot
    member.guild_permissions.manage_messages = is_mod
    member.send = AsyncMock()
    return member


def _guild_with(members):
    guild = MagicMock()
    guild.members = members
    return guild


@pytest.mark.asyncio
async def test_with_no_staff_channel_the_reminder_is_dmed_to_the_mods_not_the_channel():
    # The old behaviour dumped this in the public betting channel and polluted it. With no staff
    # channel, chase the mods (anyone with Manage Messages) in private instead — the public
    # channel is the last resort, only when not one mod is reachable.
    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    mod, not_a_mod = _mod(7), _mod(8, is_mod=False)
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)
    client.get_guild = MagicMock(return_value=_guild_with([mod, not_a_mod]))

    bets = [{"user_id": 42, "amount": 100, "outcome": "home"}]  # the creator bet, gavel is gone
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=None)),
    ):
        await remind_to_settle(client, _market())

    mod.send.assert_awaited_once()
    not_a_mod.send.assert_not_awaited()  # Manage Messages is the gate
    channel.send.assert_not_awaited()  # the point: the public channel is left clean


@pytest.mark.asyncio
async def test_the_reminder_falls_back_to_the_public_channel_when_no_mod_is_reachable():
    # A frozen market must never be silent. If the only mod has DMs closed, the public channel
    # is worse than a DM and far better than nothing.
    import discord

    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    mod = _mod(7)
    mod.send.side_effect = discord.Forbidden(MagicMock(), "closed")
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)
    client.get_guild = MagicMock(return_value=_guild_with([mod]))

    bets = [{"user_id": 42, "amount": 100, "outcome": "home"}]  # the creator bet, gavel is gone
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=None)),
    ):
        await remind_to_settle(client, _market())

    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_mod_with_closed_dms_does_not_cost_the_others_theirs():
    # Isolated per send like dm_bettors — a blocked mod is a choice, not a failure that swallows
    # the rest. And since a mod was reached, nothing spills into the public channel.
    import discord

    from bot.cogs.betting.views import remind_to_settle

    channel = _channel()
    blocked, reachable = _mod(7), _mod(8)
    blocked.send.side_effect = discord.Forbidden(MagicMock(), "closed")
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)
    client.get_guild = MagicMock(return_value=_guild_with([blocked, reachable]))

    bets = [{"user_id": 42, "amount": 100, "outcome": "home"}]  # the creator bet, gavel is gone
    with (
        patch("bot.cogs.betting.views.service.get_bets", new=AsyncMock(return_value=bets)),
        patch("bot.cogs.betting.views.service.get_staff_channel", new=AsyncMock(return_value=None)),
    ):
        await remind_to_settle(client, _market())

    reachable.send.assert_awaited_once()
    channel.send.assert_not_awaited()
