"""The pinned board and the minute ticker that tells "nobody played" from "the bot was down"."""

import datetime as dt
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.loldle import catalog, rules, service
from bot.cogs.loldle import cog as loldle_cog

CHAMPS = catalog.load()
TODAY = dt.date(2026, 11, 22)
NOW = dt.datetime(2026, 11, 22, 12, 0, 30, tzinfo=dt.UTC)
ROWS = [
    {"user_id": 1, "average": 3.8, "solved": 21, "dnf": 1},
    {"user_id": 2, "average": 4.25, "solved": 18, "dnf": 3},
]


@pytest.fixture
def cog():
    bot = MagicMock()
    bot.config.guild_id = 10
    c = loldle_cog.LoldleCog(bot)
    with patch.object(loldle_cog.discord.utils, "utcnow", return_value=NOW):
        yield c


def board_svc(yesterday=True):
    async def day_row(_guild, day):
        if day == TODAY:
            return {"answer": CHAMPS["Ahri"].to_json()}
        return {"answer": CHAMPS["Annie"].to_json()} if yesterday else None

    days = [rules.DayInfo(TODAY - dt.timedelta(days=n), 1440) for n in range(3)]
    solved = {1: {TODAY, TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=2)}, 2: {TODAY - dt.timedelta(days=2)}}
    return patch.multiple(
        service,
        day_row=AsyncMock(side_effect=day_row),
        solved_count=AsyncMock(return_value=6),
        board_rows=AsyncMock(return_value=ROWS),
        all_days=AsyncMock(return_value=days),
        solved_by_user=AsyncMock(return_value=solved),
    )


def text(e):
    return " ".join([e.title or "", e.description or "", *(f.name + f.value for f in e.fields)])


async def test_board_names_yesterdays_answer_never_todays(cog):
    with board_svc():
        e = await cog.render_board(10)
    assert "Annie" in text(e) and "Ahri" not in text(e)


async def test_board_shows_average_solved_dnf_and_streak(cog):
    with board_svc():
        e = await cog.render_board(10)
    t = text(e)
    assert t.index("<@1>") < t.index("<@2>")
    assert "3.8" in t and "21" in t and "1 DNF" in t and "3 DNF" in t
    assert "🔥 3" in t and "6" in t


async def test_board_handles_a_first_day(cog):
    with board_svc(yesterday=False), patch.object(service, "board_rows", AsyncMock(return_value=[])):
        e = await cog.render_board(10)
    assert e.description


async def test_ticker_counts_a_minute_even_without_setup_or_players(cog):
    tick = AsyncMock()
    with patch.multiple(service, tick_day=tick, get_config=AsyncMock(return_value=None)):
        await cog.minute_ticker.coro(cog)
    tick.assert_awaited_once()
    assert tick.await_args.args[:2] == (10, TODAY)


async def test_a_catch_up_burst_counts_one_minute(cog):
    tick = AsyncMock()
    with patch.multiple(service, tick_day=tick, get_config=AsyncMock(return_value=None)):
        await cog.minute_ticker.coro(cog)
        await cog.minute_ticker.coro(cog)
    tick.assert_awaited_once()


async def test_a_failing_tick_never_raises_and_still_redraws(cog):
    redraw = AsyncMock()
    cog.redraw_board = redraw
    with patch.multiple(service, tick_day=AsyncMock(side_effect=RuntimeError("db"))):
        await cog.minute_ticker.coro(cog)
    redraw.assert_awaited_once_with(10)


async def test_board_is_redrawn_every_tick_so_a_failed_edit_recovers(cog):
    # edit_if_changed swallows HTTP errors and skips identical renders: a one-shot flag would
    # lose the midnight redraw on a single Discord 503.
    cog.redraw_board = AsyncMock(side_effect=[RuntimeError("x"), None])
    with patch.multiple(service, tick_day=AsyncMock()):
        await cog.minute_ticker.coro(cog)
        await cog.minute_ticker.coro(cog)
    assert cog.redraw_board.await_count == 2


async def test_redraw_goes_through_edit_if_changed(cog):
    channel = MagicMock()
    cog.bot.get_channel.return_value = channel
    edit = AsyncMock()
    with (
        board_svc(),
        patch.object(service, "get_config", AsyncMock(return_value={"channel_id": 20, "board_message_id": 30})),
        patch.object(loldle_cog, "edit_if_changed", edit),
    ):
        await cog.redraw_board(10)
    channel.get_partial_message.assert_called_once_with(30)
    edit.assert_awaited_once()


async def test_setup_loldle_clears_previous_posts_and_pins():
    from bot.cogs.setup import cog as setup_cog

    bot = MagicMock()
    board = MagicMock()
    bot.cogs.get.return_value = MagicMock(render_board=AsyncMock(return_value=board))
    channel = MagicMock(id=20, mention="#loldle")
    message = MagicMock(id=30)
    channel.send = AsyncMock(return_value=message)
    i = MagicMock(guild_id=10)
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    with (
        patch.object(setup_cog, "_clear_previous", AsyncMock()) as clear,
        patch.object(setup_cog, "pin", AsyncMock()) as pin,
        patch.object(setup_cog.loldle_service, "set_config", AsyncMock()) as config,
    ):
        cog = setup_cog.SetupCog(bot)
        await setup_cog.SetupCog.setup_loldle.callback(cog, i, channel)
    clear.assert_awaited_once_with(bot, 10, "loldle_config", ["board_message_id"])
    pin.assert_awaited_once_with(message)
    config.assert_awaited_once_with(10, 20, 30, None)


def test_feature_is_wired_everywhere():
    from bot.cogs.help import embeds as help_embeds
    from bot.cogs.help.cog import FEATURE_TABLES
    from bot.cogs.setup.cog import FEATURES
    from bot.core.bot import COGS

    assert "bot.cogs.loldle.cog" in COGS
    importlib.import_module("bot.cogs.loldle.cog")
    assert ("LoLdle", "loldle_config", ["board_message_id"], "/setup loldle") in FEATURES
    assert FEATURE_TABLES["loldle"] == ("loldle_config", None)
    assert "loldle" in [k for k, *_ in help_embeds.CONFIGURABLE]


async def test_audit_log_never_records_a_loldle_guess():
    from bot.cogs.logs import cog as logs_cog

    bot = MagicMock()
    bot.config.guild_id = 10
    bot.config.log_ignored_channel_ids = set()
    bot.config.log_muted_events = set()
    logs = logs_cog.LogsCog(bot)
    logs._send = AsyncMock()
    logs._log_to_db = AsyncMock()
    logs._cache_user = AsyncMock()
    i = MagicMock(guild_id=10, channel_id=20, type=logs_cog.discord.InteractionType.application_command)
    i.command.qualified_name = "loldle"
    i.namespace = MagicMock()
    i.namespace.__dict__.update(champion="Ahri")
    with patch.object(logs_cog, "vars", return_value={"champion": "Ahri"}, create=True):
        await logs.on_interaction(i)
    assert "Ahri" not in str(logs._send.await_args)
    assert "Ahri" not in str(logs._log_to_db.await_args)
    assert logs._log_to_db.await_args.kwargs["details"]["command"] == "loldle"
