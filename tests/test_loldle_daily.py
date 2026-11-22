"""The midnight recap and the 09:00 role ping."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

from bot.cogs.loldle import catalog, embeds, rules, service
from bot.cogs.loldle import cog as loldle_cog

PARIS = ZoneInfo("Europe/Paris")
DAY = dt.date(2026, 11, 23)
YESTERDAY = DAY - dt.timedelta(days=1)
MIDNIGHT = dt.datetime(2026, 11, 23, 0, 0, 30, tzinfo=PARIS)
NINE = dt.datetime(2026, 11, 23, 9, 0, 30, tzinfo=PARIS)
RESULTS = [
    {"user_id": 1, "tries": 3, "hints": 0, "score": 3},
    {"user_id": 2, "tries": 4, "hints": 1, "score": 5},
]
CONFIG = {"channel_id": 20, "board_message_id": 30, "role_id": 40}


def http_error(status):
    return discord.HTTPException(MagicMock(status=status, reason="x"), "x")


def forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="x"), "x")


def text(e):
    return " ".join([e.title or "", e.description or "", *(f.name + f.value for f in e.fields)])


@pytest.fixture
def cog():
    bot = MagicMock()
    bot.config.guild_id = 10
    c = loldle_cog.LoldleCog(bot)
    c.channel = MagicMock()
    c.channel.send = AsyncMock(return_value=MagicMock(id=99))
    c.channel.get_partial_message.return_value.delete = AsyncMock()
    bot.get_channel.return_value = c.channel
    c.role = MagicMock(spec=discord.Role)
    c.role.is_default.return_value = False
    c.role.mention = "<@&40>"
    bot.get_guild.return_value.get_role.return_value = c.role
    return c


def svc(**over):
    base = {
        "get_config": CONFIG,
        "claim_recap": "send",
        "release_recap": None,
        "claim_ping": False,
        "release_ping": None,
        "day_row": {"answer": catalog.load()["Annie"].to_json()},
        "day_results": RESULTS,
        "day_dnf": [3],
        "all_days": [rules.DayInfo(YESTERDAY, 1440)],
        "solved_by_user": {1: {YESTERDAY}, 2: {YESTERDAY}},
        "previous_ping": None,
        "set_ping_message": None,
    }
    base.update(over)
    return patch.multiple(
        service, **{k: (v if isinstance(v, AsyncMock) else AsyncMock(return_value=v)) for k, v in base.items()}
    )


def test_ping_window_is_nine_to_twenty_one_paris():
    assert not rules.in_ping_window(dt.datetime(2026, 11, 23, 8, 59, tzinfo=PARIS))
    assert rules.in_ping_window(NINE)
    assert rules.in_ping_window(dt.datetime(2026, 11, 23, 20, 59, tzinfo=PARIS))
    assert not rules.in_ping_window(dt.datetime(2026, 11, 23, 21, 0, tzinfo=PARIS))
    assert rules.in_ping_window(dt.datetime(2026, 11, 23, 8, 0, tzinfo=dt.UTC))  # 09:00 in Paris


def test_recap_lists_yesterday_in_order_with_tries_hints_streak_and_dnf():
    e = embeds.build_recap_embed("Annie", RESULTS, [3], {1: 6, 2: 1})
    t = text(e)
    assert "Annie" in t and t.index("<@1>") < t.index("<@2>") < t.index("<@3>")
    assert "3 essais" in t and "4 essais" in t and "💡" in t and "🔥 6" in t and "DNF" in t


def test_recap_singular_try():
    assert "1 essai" in text(embeds.build_recap_embed("Annie", [{**RESULTS[0], "tries": 1, "score": 1}], [], {}))


def test_recap_without_players_only_announces_the_new_game():
    t = text(embeds.build_recap_embed("Annie", [], [], {}))
    assert "Nouveau LoLdle" in t and "Annie" not in t


def test_huge_recap_stays_under_discord_limits():
    results = [{"user_id": 10**17 + n, "tries": 9, "hints": 3, "score": 12} for n in range(200)]
    e = embeds.build_recap_embed(
        "Annie", results, [10**18 + n for n in range(200)], {r["user_id"]: 99 for r in results}
    )
    assert len(e) <= 6000 and len(e.description) <= 4096 and "+180" in e.description and "+185" in e.description


async def test_recap_is_posted_without_any_ping(cog):
    with svc():
        await cog._daily_posts(10, MIDNIGHT, DAY)
    kwargs = cog.channel.send.await_args.kwargs
    assert "content" not in kwargs
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False and mentions.roles is False and mentions.users is False
    assert "Annie" in text(kwargs["embed"])


async def test_recap_streak_is_counted_up_to_yesterday(cog):
    days = [rules.DayInfo(YESTERDAY - dt.timedelta(days=n), 1440) for n in range(3)]
    solved = {1: {YESTERDAY - dt.timedelta(days=n) for n in range(3)}, 2: {YESTERDAY}}
    with svc(all_days=days, solved_by_user=solved):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    assert "🔥 3" in text(cog.channel.send.await_args.kwargs["embed"])


@pytest.mark.parametrize("state", [None, "seed"])
async def test_recap_not_claimed_or_only_seeded_posts_nothing(cog, state):
    with svc(claim_recap=state):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    cog.channel.send.assert_not_called()


async def test_nothing_happens_without_setup(cog):
    claim = AsyncMock()
    with svc(get_config=None, claim_recap=claim):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    claim.assert_not_called()


async def test_transient_failure_releases_the_recap(cog):
    cog.channel.send.side_effect = http_error(503)
    release = AsyncMock()
    with svc(release_recap=release):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    release.assert_awaited_once_with(10, DAY)


async def test_missing_permission_does_not_retry_every_minute(cog):
    cog.channel.send.side_effect = forbidden()
    release = AsyncMock()
    with svc(release_recap=release):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    release.assert_not_called()


async def test_no_ping_before_nine(cog):
    claim = AsyncMock(return_value=True)
    with svc(claim_recap=None, claim_ping=claim):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    claim.assert_not_called()


async def test_ping_notifies_only_the_role_and_replaces_yesterdays(cog):
    store = AsyncMock()
    with svc(claim_recap=None, claim_ping=True, previous_ping=(YESTERDAY, 77), set_ping_message=store):
        await cog._daily_posts(10, NINE, DAY)
    call = cog.channel.send.await_args
    assert call.args[0].startswith("<@&40>")
    mentions = call.kwargs["allowed_mentions"]
    assert mentions.roles == [cog.role] and mentions.users is False and mentions.everyone is False
    cog.channel.get_partial_message.assert_called_once_with(77)
    cog.channel.get_partial_message.return_value.delete.assert_awaited_once()
    assert [c.args for c in store.await_args_list] == [(10, DAY, 99), (10, YESTERDAY, None)]


async def test_everyone_role_uses_the_literal_mention(cog):
    cog.role.is_default.return_value = True
    with svc(claim_recap=None, claim_ping=True):
        await cog._daily_posts(10, NINE, DAY)
    assert cog.channel.send.await_args.args[0].startswith("@everyone")


async def test_no_role_sends_no_ping(cog):
    claim = AsyncMock(return_value=True)
    with svc(get_config={**CONFIG, "role_id": None}, claim_recap=None, claim_ping=claim):
        await cog._daily_posts(10, NINE, DAY)
    claim.assert_awaited_once()  # stamped, so a role set later today doesn't ping at 3pm
    cog.channel.send.assert_not_called()


async def test_old_ping_already_deleted_is_fine(cog):
    cog.channel.get_partial_message.return_value.delete.side_effect = discord.NotFound(
        MagicMock(status=404, reason="x"), "x"
    )
    store = AsyncMock()
    with svc(claim_recap=None, claim_ping=True, previous_ping=(YESTERDAY, 77), set_ping_message=store):
        await cog._daily_posts(10, NINE, DAY)
    assert (10, YESTERDAY, None) in [c.args for c in store.await_args_list]


async def test_transient_ping_failure_releases_it(cog):
    cog.channel.send.side_effect = http_error(500)
    release = AsyncMock()
    with svc(claim_recap=None, claim_ping=True, release_ping=release):
        await cog._daily_posts(10, NINE, DAY)
    release.assert_awaited_once_with(10, DAY)


async def test_a_failing_daily_post_never_breaks_the_ticker(cog):
    cog.redraw_board = AsyncMock()
    with (
        patch.object(loldle_cog.discord.utils, "utcnow", return_value=NINE),
        patch.multiple(service, tick_day=AsyncMock(), get_config=AsyncMock(side_effect=RuntimeError("db"))),
    ):
        await cog.minute_ticker.coro(cog)
    cog.redraw_board.assert_awaited_once_with(10)


async def test_setup_stores_the_role():
    from bot.cogs.setup import cog as setup_cog

    bot = MagicMock()
    bot.cogs.get.return_value = MagicMock(render_board=AsyncMock(return_value=MagicMock()))
    channel = MagicMock(id=20, mention="#loldle")
    channel.send = AsyncMock(return_value=MagicMock(id=30))
    i = MagicMock(guild_id=10)
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    with (
        patch.object(setup_cog, "_clear_previous", AsyncMock()),
        patch.object(setup_cog, "pin", AsyncMock()),
        patch.object(setup_cog.loldle_service, "set_config", AsyncMock()) as config,
    ):
        await setup_cog.SetupCog.setup_loldle.callback(setup_cog.SetupCog(bot), i, channel, MagicMock(id=40))
    config.assert_awaited_once_with(10, 20, 30, 40)
