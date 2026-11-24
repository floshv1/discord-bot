"""The midnight recap and the 09:00 ping of yesterday's players."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

from bot.cogs.loldle import catalog, embeds, rules, service
from bot.cogs.loldle import cog as loldle_cog
from bot.cogs.loldle.views import PlayView

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
        "ping_targets": [1, 2],
        "previous_ping": None,
        "set_ping_message": None,
        "group_solved_days": {YESTERDAY},
        "day_grids": {1: [["green"] * 9], 2: [["red"] * 9, ["green"] * 9], 3: [["red"] * 9]},
        "unfinished_posts": [],
        "finish_post": None,
        "player_names": [],
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


async def test_ping_mentions_only_yesterdays_players_and_replaces_the_old_one(cog):
    store = AsyncMock()
    with svc(claim_recap=None, claim_ping=True, previous_ping=(YESTERDAY, 77), set_ping_message=store):
        await cog._daily_posts(10, NINE, DAY)
    call = cog.channel.send.await_args
    assert call.args[0].startswith("<@1> <@2>") and "/loldle" not in call.args[0]
    assert isinstance(call.kwargs["view"], PlayView)
    mentions = call.kwargs["allowed_mentions"]
    assert [u.id for u in mentions.users] == [1, 2]
    assert mentions.roles is False and mentions.everyone is False
    cog.channel.get_partial_message.assert_called_once_with(77)
    cog.channel.get_partial_message.return_value.delete.assert_awaited_once()
    assert [c.args for c in store.await_args_list] == [(10, DAY, 99), (10, YESTERDAY, None)]


async def test_a_role_left_in_the_config_is_never_pinged(cog):
    with svc(claim_recap=None, claim_ping=True):
        await cog._daily_posts(10, NINE, DAY)
    assert "<@&40>" not in cog.channel.send.await_args.args[0]


async def test_nobody_to_ping_sends_nothing(cog):
    claim = AsyncMock(return_value=True)
    with svc(claim_recap=None, claim_ping=claim, ping_targets=[]):
        await cog._daily_posts(10, NINE, DAY)
    claim.assert_awaited_once()
    cog.channel.send.assert_not_called()


async def test_ping_stays_within_discord_limits(cog):
    targets = [10**18 + n for n in range(300)]
    with svc(claim_recap=None, claim_ping=True, ping_targets=targets):
        await cog._daily_posts(10, NINE, DAY)
    call = cog.channel.send.await_args
    assert len(call.args[0]) <= 2000 and len(call.kwargs["allowed_mentions"].users) <= 100


async def test_failed_lookup_releases_the_ping(cog):
    release = AsyncMock()
    with svc(
        claim_recap=None,
        claim_ping=True,
        ping_targets=AsyncMock(side_effect=TimeoutError()),
        release_ping=release,
    ):
        await cog._daily_posts(10, NINE, DAY)
    release.assert_awaited_once_with(10, DAY)
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


async def test_setup_takes_no_role():
    import inspect

    from bot.cogs.setup import cog as setup_cog

    assert "role" not in inspect.signature(setup_cog.SetupCog.setup_loldle.callback).parameters


async def test_ping_targets_are_yesterdays_players_who_have_not_played_today():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"user_id": 1}, {"user_id": 2}])
    with patch.object(service, "get_pool", return_value=conn):
        assert await service.ping_targets(10, DAY) == [1, 2]
    sql = conn.fetch.await_args.args[0]
    assert "$2::date - 1" in sql and "NOT EXISTS" in sql


# --- the group recap -----------------------------------------------------------

GROUPED = [
    {"user_id": 1, "tries": 4, "hints": 0, "score": 4},
    {"user_id": 2, "tries": 5, "hints": 0, "score": 5},
    {"user_id": 3, "tries": 5, "hints": 2, "score": 7},
]


def test_recap_text_groups_by_tries_with_crown():
    t = embeds.build_recap_text("Annie", 0, GROUPED, [4])
    assert "👑 4 essais : <@1>" in t
    assert "5 essais : <@2> <@3> 💡" in t
    assert "Pas trouvé : <@4>" in t
    assert t.index("👑 4 essais") < t.index("5 essais") < t.index("Pas trouvé")
    assert "Annie" in t


def test_recap_text_crowns_every_tie():
    t = embeds.build_recap_text("Annie", 0, [{**GROUPED[0]}, {**GROUPED[1], "tries": 4}], [])
    assert "👑 4 essais : <@1> <@2>" in t


def test_recap_text_marks_hints():
    t = embeds.build_recap_text("Annie", 0, GROUPED, [])
    assert "<@3> 💡" in t and "<@2> 💡" not in t


def test_recap_text_singular_try():
    assert "👑 1 essai : <@1>" in embeds.build_recap_text("Annie", 0, [{**GROUPED[0], "tries": 1}], [])


def test_recap_text_shows_group_streak_from_two():
    assert "série" not in embeds.build_recap_text("Annie", 1, GROUPED, [])
    assert "🔥 Le serveur est sur une série de 6 jours !" in embeds.build_recap_text("Annie", 6, GROUPED, [])


def test_recap_text_without_players_stays_short():
    t = embeds.build_recap_text("Annie", 0, [], [])
    assert "Nouveau LoLdle" in t and "Annie" not in t and "essai" not in t


def test_huge_recap_text_stays_under_discord_limits():
    results = [{"user_id": 10**17 + n, "tries": 3 + n % 9, "hints": 3, "score": 9} for n in range(200)]
    t = embeds.build_recap_text("Annie", 99, results, [10**18 + n for n in range(200)])
    assert len(t) <= 2000 and "+" in t


async def test_recap_posts_image_and_both_buttons(cog):
    with svc():
        await cog._daily_posts(10, MIDNIGHT, DAY)
    kwargs = cog.channel.send.await_args.kwargs
    assert kwargs["file"].filename == "recap.png"
    assert isinstance(kwargs["view"], PlayView)
    assert {item.custom_id for item in kwargs["view"].children} == {"loldle:play", "loldle:text"}
    assert "Annie" in kwargs["content"]


async def test_recap_mentions_never_ping(cog):
    with svc():
        await cog._daily_posts(10, MIDNIGHT, DAY)
    mentions = cog.channel.send.await_args.kwargs["allowed_mentions"]
    assert mentions.everyone is False and mentions.roles is False and mentions.users is False


async def test_recap_group_streak_is_counted_up_to_yesterday(cog):
    days = [rules.DayInfo(YESTERDAY - dt.timedelta(days=n), 1440) for n in range(3)]
    won = {YESTERDAY - dt.timedelta(days=n) for n in range(3)}
    with svc(all_days=days, group_solved_days=won):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    assert "série de 3 jours" in cog.channel.send.await_args.kwargs["content"]


async def test_recap_finalizes_yesterday_once(cog):
    finish = AsyncMock()
    unfinished = [{"user_id": 3, "channel_id": 20, "message_id": 55}]
    with svc(unfinished_posts=unfinished, finish_post=finish):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    finish.assert_awaited_once_with(10, YESTERDAY, 3)
    assert (YESTERDAY, 3) in cog._dirty_posts
    with svc(claim_recap=None, unfinished_posts=unfinished, finish_post=finish):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    finish.assert_awaited_once()  # the recap claim is what makes it once


async def test_recap_without_players_still_posts(cog):
    with svc(day_results=[], day_dnf=[], day_grids={}):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    kwargs = cog.channel.send.await_args.kwargs
    assert "Nouveau LoLdle" in kwargs["content"]


async def test_recap_text_survives_a_picture_failure(cog):
    release = AsyncMock()
    with svc(release_recap=release), patch.object(loldle_cog.images, "render_recap", side_effect=OSError("font")):
        await cog._daily_posts(10, MIDNIGHT, DAY)
    kwargs = cog.channel.send.await_args.kwargs
    assert "file" not in kwargs and "Annie" in kwargs["content"]
    release.assert_not_called()
