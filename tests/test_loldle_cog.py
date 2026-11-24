"""/loldle: private play, a public grid that never names the champion, hints that charge honestly."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.loldle import catalog, embeds, hints, rules, service
from bot.cogs.loldle import cog as loldle_cog
from bot.cogs.loldle.service import BuyOutcome, GuessOutcome
from bot.cogs.loldle.views import GameView

CHAMPS = catalog.load()
AHRI, ANNIE = CHAMPS["Ahri"], CHAMPS["Annie"]
DAY = dt.date(2026, 11, 22)
NOW = dt.datetime(2026, 11, 22, 12, tzinfo=dt.UTC)
DAY_ROW = {"answer": AHRI.to_json(), "champion_id": "Ahri", "up_minutes": 10}


def guess_row(n, c=ANNIE, answer=AHRI):
    return {
        "number": n,
        "champion_id": c.id,
        "name": c.name,
        "cells": rules.compare(c, answer),
        "clues": rules.clues(c),
    }


RESULT = {"tries": 4, "hints": 1, "score": 5, "grid": "🟥🟥🟥🟥🟥⬆️\n🟩🟩🟩🟩🟩🟩", "user_id": 2}


def interaction(user_id=2):
    i = MagicMock()
    i.user.id = user_id
    i.user.mention = f"<@{user_id}>"
    i.user.display_name = "Flo"
    i.user.avatar = None
    i.guild_id = 10
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.response.is_done = MagicMock(return_value=True)
    i.followup.send = AsyncMock()
    return i


@pytest.fixture
def cog():
    bot = MagicMock()
    c = loldle_cog.LoldleCog(bot)
    c._session = MagicMock()
    with patch.object(loldle_cog.discord.utils, "utcnow", return_value=NOW):
        yield c


def svc(**over):
    base = {
        "upsert_player": None,
        "claim_announcements": [],
        "get_or_pick_day": DAY_ROW,
        "day_row": DAY_ROW,
        "guesses": [],
        "result": None,
        "hints": set(),
        "get_config": None,
        "all_days": [],
        "solved_days": {DAY},
    }
    base.update(over)
    fns = {k: (v if isinstance(v, AsyncMock) else AsyncMock(return_value=v)) for k, v in base.items()}
    return patch.multiple(service, **fns)


def sent(i):
    return i.followup.send.await_args


def embed_text(e):
    return " ".join([e.title or "", e.description or "", *(f.name + f.value for f in e.fields), e.footer.text or ""])


async def test_unknown_champion_is_refused_and_costs_nothing(cog):
    i = interaction()
    record = AsyncMock()
    with svc(record_guess=record):
        await cog.play(i, "pas un champion")
    record.assert_not_called()
    assert "inconnu" in sent(i).args[0].lower()


async def test_replies_are_ephemeral(cog):
    i = interaction()
    with svc(record_guess=GuessOutcome("ok", rules.compare(ANNIE, AHRI), 1)):
        await cog.play(i, "Annie")
    i.response.defer.assert_awaited_once_with(ephemeral=True)
    assert sent(i).kwargs["ephemeral"] is True


async def test_duplicate_is_told_and_not_counted(cog):
    i = interaction()
    with svc(record_guess=GuessOutcome("duplicate"), guesses=[guess_row(1)]):
        await cog.play(i, "Annie")
    assert "déjà" in sent(i).kwargs["content"]


async def test_win_without_a_channel_still_works(cog):
    i = interaction()
    with svc(record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, RESULT), result=RESULT):
        await cog.play(i, "Ahri")
    assert "Ahri" in embed_text(sent(i).kwargs["embed"])  # private card may name it


async def test_no_argument_shows_the_grid_without_guessing(cog):
    i = interaction()
    record = AsyncMock()
    with svc(record_guess=record, guesses=[guess_row(1)]):
        await cog.play(i, None)
    record.assert_not_called()
    assert "Annie" in embed_text(sent(i).kwargs["embed"])


async def test_unsolved_card_never_names_the_answer(cog):
    i = interaction()
    with svc(guesses=[guess_row(1)]):
        await cog.play(i, None)
    assert "Ahri" not in embed_text(sent(i).kwargs["embed"])


async def test_stale_day_button_sells_nothing(cog):
    i = interaction()
    buy = AsyncMock()
    with svc(buy_hint=buy):
        await cog.buy(i, "spell", DAY - dt.timedelta(days=1))
    buy.assert_not_called()
    assert "nouveau" in sent(i).args[0].lower()


async def test_cdn_failure_charges_nothing(cog):
    i = interaction()
    buy = AsyncMock()
    with svc(buy_hint=buy), patch.object(hints, "render_hint", AsyncMock(side_effect=hints.HintUnavailable("x"))):
        await cog.buy(i, "spell", DAY)
    buy.assert_not_called()
    assert "rien" in sent(i).args[0].lower()


async def test_bought_hint_is_sent_as_a_neutral_attachment(cog):
    i = interaction()
    content = hints.HintContent("✨ Un de ses sorts :", b"png")
    with svc(buy_hint=BuyOutcome("bought", 975)), patch.object(hints, "render_hint", AsyncMock(return_value=content)):
        await cog.buy(i, "spell", DAY)
    call = sent(i)
    assert call.kwargs["file"].filename == "indice.png" and call.kwargs["ephemeral"] is True
    assert "975" in call.args[0]


@pytest.mark.parametrize(("status", "word"), [("poor", "100"), ("locked", "pas")])
async def test_refused_purchase_reveals_nothing(cog, status, word):
    i = interaction()
    content = hints.HintContent("🔤 Son nom commence par **A**.")
    with (
        svc(buy_hint=BuyOutcome(status, 3)),
        patch.object(hints, "render_hint", AsyncMock(return_value=content)),
    ):
        await cog.buy(i, "letter", DAY)
    assert word in sent(i).args[0]
    assert "**A**" not in sent(i).args[0] and "file" not in sent(i).kwargs


async def test_autocomplete_offers_champions(cog):
    choices = await cog.champion_autocomplete(interaction(), "kai")
    assert "Kaisa" in [c.value for c in choices] and len(choices) <= 25


def _buttons(view):
    return {c.kind: c.disabled for c in view.children}


def test_view_keeps_hints_locked_before_three_guesses():
    assert _buttons(GameView(DAY, set(), 2, False)) == {"spell": True, "splash": True, "letter": True}


def test_view_opens_only_the_next_hint_and_owned_ones():
    assert _buttons(GameView(DAY, {"spell"}, 3, False)) == {"spell": False, "splash": False, "letter": True}


def test_view_after_solving_only_reopens_owned_hints():
    assert _buttons(GameView(DAY, {"spell"}, 5, True)) == {"spell": False, "splash": True, "letter": True}


def test_long_games_stay_under_discord_limits():
    rows = [guess_row(n, list(CHAMPS.values())[n]) for n in range(1, 120)]
    e = embeds.build_game_embed(DAY, rows, None, AHRI)
    assert len(e) <= 6000 and len(e.description) <= 4096 and "+" in e.description
    grid = "\n".join(["🟥🟥🟥🟥🟥⬆️"] * 120)
    public = embeds.build_public_embed("<@2>", {**RESULT, "tries": 120, "grid": grid}, 3)
    assert len(public) <= 6000 and len(public.description) <= 4096


# --- final review regressions ---


async def test_answer_dropped_from_the_catalog_can_still_be_found(cog):
    gone = {"id": "Removed", "name": "Removed", **{k: v for k, v in AHRI.to_json().items() if k not in ("id", "name")}}
    row = {**DAY_ROW, "answer": gone, "champion_id": "Removed"}
    record = AsyncMock(return_value=GuessOutcome("ok", ["green"] * 6, 1, True, None))
    with svc(get_or_pick_day=row, record_guess=record, result=RESULT):
        await cog.play(interaction(), "removed")
    assert record.await_args.args[3].id == "Removed"


async def test_hint_purchase_refreshes_the_coin_leaderboard(cog):
    content = hints.HintContent("x")
    with (
        svc(buy_hint=BuyOutcome("bought", 975)),
        patch.object(hints, "render_hint", AsyncMock(return_value=content)),
        patch.object(loldle_cog, "mark_coins_dirty") as dirty,
    ):
        await cog.buy(interaction(), "letter", DAY)
    dirty.assert_called_once_with(cog.bot)


async def test_midnight_during_the_download_sells_nothing(cog):
    buy = AsyncMock()
    later = dt.datetime(2026, 11, 22, 23, 0, 1, tzinfo=dt.UTC)  # 00:00:01 in Paris

    async def slow(*_args):
        loldle_cog.discord.utils.utcnow.return_value = later
        return hints.HintContent("x")

    i = interaction()
    with svc(buy_hint=buy), patch.object(hints, "render_hint", AsyncMock(side_effect=slow)):
        await cog.buy(i, "letter", DAY)
    buy.assert_not_called()
    assert "nouveau" in sent(i).args[0].lower()


async def test_correct_guess_is_all_green_even_if_the_catalog_changed():
    from dataclasses import replace

    conn = AsyncMock()
    conn.fetchval.side_effect = [None, None, 1, 0]
    conn.fetch.return_value = [{"cells": ["green"] * 6}]
    conn.fetchrow.return_value = RESULT
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    refreshed = replace(AHRI, positions=("Top",), region="Noxus")
    with patch.object(service, "get_pool", return_value=pool):
        out = await service.record_guess(1, DAY, 2, refreshed, AHRI)
    assert out.won and out.cells == ["green"] * 9


# --- announcements come from the queue ---

NOW_UTC = NOW


def pending(user_id=2, solved_at=NOW - dt.timedelta(minutes=5), day=DAY):
    return {**RESULT, "user_id": user_id, "solved_at": solved_at, "day": day}


def channel_mock():
    channel = MagicMock()
    channel.send = AsyncMock()
    cog_channel = channel
    return cog_channel


async def test_win_is_announced_once_from_the_queue(cog):
    channel = channel_mock()
    cog.bot.get_channel.return_value = channel
    with svc(
        claim_announcements=[pending()],
        get_config={"channel_id": 20, "board_message_id": None},
    ):
        await cog._drain_announcements(10)
    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert "Ahri" not in embed_text(kwargs["embed"])
    assert kwargs["allowed_mentions"].users is False
    assert "<@2>" in embed_text(kwargs["embed"])


async def test_wins_are_posted_in_solving_order(cog):
    channel = channel_mock()
    cog.bot.get_channel.return_value = channel
    late, early = pending(3, NOW - dt.timedelta(minutes=1)), pending(4, NOW - dt.timedelta(minutes=9))
    with svc(claim_announcements=[late, early], get_config={"channel_id": 20, "board_message_id": None}):
        await cog._drain_announcements(10)
    texts = [embed_text(c.kwargs["embed"]) for c in channel.send.await_args_list]
    assert "<@4>" in texts[0] and "<@3>" in texts[1]


async def test_stale_wins_are_claimed_but_not_posted(cog):
    channel = channel_mock()
    cog.bot.get_channel.return_value = channel
    claim = AsyncMock(return_value=[pending(solved_at=NOW - dt.timedelta(hours=25))])
    with svc(claim_announcements=claim, get_config={"channel_id": 20, "board_message_id": None}):
        await cog._drain_announcements(10)
    claim.assert_awaited_once()
    channel.send.assert_not_called()


async def test_no_channel_discards_pending_wins(cog):
    claim = AsyncMock(return_value=[pending()])
    with svc(claim_announcements=claim, get_config=None):
        await cog._drain_announcements(10)
    claim.assert_awaited_once()
    cog.bot.get_channel.assert_not_called()


async def test_channel_failure_keeps_the_ticker_alive(cog):
    channel = channel_mock()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403, reason="x"), "no"))
    cog.bot.get_channel.return_value = channel
    with svc(
        claim_announcements=[pending(3), pending(4)],
        get_config={"channel_id": 20, "board_message_id": None},
    ):
        await cog._drain_announcements(10)  # must not raise
    assert channel.send.await_count == 2  # one failure does not cost the others


async def test_claim_failure_does_not_raise(cog):
    with svc(claim_announcements=AsyncMock(side_effect=TimeoutError())):
        await cog._drain_announcements(10)


async def test_win_event_drains_and_hint_event_marks_coins_dirty(cog):
    from bot.cogs.loldle.events import Event

    cog.bot.config.guild_id = 10
    drain = AsyncMock()
    with patch.object(cog, "_drain_announcements", drain), patch.object(loldle_cog, "mark_coins_dirty") as dirty:
        await cog._on_event(Event(10, DAY, 2, "hint"))
        dirty.assert_called_once_with(cog.bot)
        drain.assert_not_called()
        await cog._on_event(Event(10, DAY, 2, "win"))
    drain.assert_awaited_once_with(10)


async def test_event_for_another_guild_is_ignored(cog):
    from bot.cogs.loldle.events import Event

    cog.bot.config.guild_id = 10
    drain = AsyncMock()
    with patch.object(cog, "_drain_announcements", drain):
        await cog._on_event(Event(99, DAY, 2, "win"))
    drain.assert_not_called()


async def test_reconnect_resyncs_both(cog):
    cog.bot.config.guild_id = 10
    drain = AsyncMock()
    with patch.object(cog, "_drain_announcements", drain), patch.object(loldle_cog, "mark_coins_dirty") as dirty:
        await cog._on_connect()
    drain.assert_awaited_once_with(10)
    dirty.assert_called_once_with(cog.bot)


async def test_guess_no_longer_announces_directly(cog):
    i = interaction()
    claim = AsyncMock(return_value=[])
    channel = channel_mock()
    cog.bot.get_channel.return_value = channel
    with svc(
        record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, RESULT),
        get_config={"channel_id": 20, "board_message_id": None},
        claim_announcements=claim,
        result=RESULT,
    ):
        await cog.play(i, "Ahri")
    channel.send.assert_not_called()
    assert "Ahri" in embed_text(sent(i).kwargs["embed"])


async def test_play_registers_the_player(cog):
    i = interaction()
    upsert = AsyncMock()
    with svc(upsert_player=upsert):
        await cog.play(i, None)
    upsert.assert_awaited_once_with(10, 2, "Flo", None)


async def test_player_upsert_failure_does_not_cost_the_game(cog):
    i = interaction()
    with svc(upsert_player=AsyncMock(side_effect=TimeoutError()), guesses=[guess_row(1)]):
        await cog.play(i, None)
    assert "Annie" in embed_text(sent(i).kwargs["embed"])


# --- launching the Activity ---------------------------------------------------


def launchable(cog, url="http://activity:8080", up=True):
    cog.bot.config.loldle_activity_internal_url = url
    cog._activity_up = up
    i = interaction()
    i.response.is_done = MagicMock(return_value=False)
    i.response.launch_activity = AsyncMock()
    return i


def http_error(status=403):
    return discord.HTTPException(MagicMock(status=status, reason="x"), "x")


async def test_loldle_without_argument_launches_first(cog):
    i = launchable(cog)
    with svc():
        await cog.loldle.callback(cog, i, None)
    i.response.launch_activity.assert_awaited_once()
    i.response.defer.assert_not_called()
    i.followup.send.assert_not_called()


async def test_probe_down_shows_the_private_card(cog):
    i = launchable(cog, up=False)
    with svc():
        await cog.loldle.callback(cog, i, None)
    i.response.launch_activity.assert_not_called()
    assert sent(i).kwargs["embed"] is not None


async def test_no_url_configured_shows_the_private_card(cog):
    i = launchable(cog, url=None, up=True)
    with svc():
        await cog.loldle.callback(cog, i, None)
    i.response.launch_activity.assert_not_called()
    assert sent(i).kwargs["embed"] is not None


async def test_launch_http_error_falls_back_to_card(cog):
    i = launchable(cog)
    i.response.launch_activity.side_effect = http_error()
    with svc():
        await cog.loldle.callback(cog, i, None)
    i.response.defer.assert_awaited_once()
    assert sent(i).kwargs["embed"] is not None


async def test_launch_error_after_responding_sends_a_short_followup(cog):
    i = launchable(cog)
    i.response.launch_activity.side_effect = http_error()
    i.response.is_done = MagicMock(return_value=True)
    with svc():
        await cog.launch_or_card(i)
    i.response.defer.assert_not_called()
    assert sent(i).kwargs["ephemeral"] is True
    assert "embed" not in sent(i).kwargs


async def test_guess_argument_still_plays_in_chat(cog):
    i = launchable(cog)
    with svc(record_guess=AsyncMock(return_value=GuessOutcome(status="ok"))):
        await cog.loldle.callback(cog, i, "Annie")
    i.response.launch_activity.assert_not_called()
    assert sent(i).kwargs["embed"] is not None


async def test_text_button_shows_the_private_card(cog):
    from bot.cogs.loldle.views import PlayView

    i = launchable(cog)
    i.client.get_cog.return_value = cog
    view = PlayView()
    text_button = next(c for c in view.children if c.custom_id == "loldle:text")
    with svc():
        await text_button.callback(i)
    i.response.launch_activity.assert_not_called()
    assert sent(i).kwargs["embed"] is not None


async def test_play_button_launches(cog):
    from bot.cogs.loldle.views import PlayView

    i = launchable(cog)
    i.client.get_cog.return_value = cog
    play_button = next(c for c in PlayView().children if c.custom_id == "loldle:play")
    await play_button.callback(i)
    i.response.launch_activity.assert_awaited_once()


def test_play_view_is_persistent():
    from bot.cogs.loldle.views import PlayView

    view = PlayView()
    assert view.timeout is None
    assert {c.custom_id for c in view.children} == {"loldle:play", "loldle:text"}


async def test_board_recap_and_ping_carry_the_view(cog):
    from bot.cogs.loldle.views import PlayView

    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(id=5))
    cog.bot.get_channel.return_value = channel
    edit = AsyncMock()
    config = {"channel_id": 20, "board_message_id": 30}
    with (
        svc(
            get_config=config,
            claim_recap="send",
            claim_ping=True,
            day_results=[],
            day_dnf=[],
            solved_by_user={},
            solved_count=0,
            board_rows=[],
            ping_targets=[1],
            set_ping_message=None,
            previous_ping=None,
        ),
        patch.object(loldle_cog, "edit_if_changed", edit),
    ):
        await cog.redraw_board(10)
        await cog._post_recap(channel, 10, DAY)
        await cog._post_ping(channel, 10, DAY)
    assert isinstance(edit.await_args.kwargs["view"], PlayView)
    assert all(isinstance(c.kwargs["view"], PlayView) for c in channel.send.await_args_list)
    assert channel.send.await_count == 2


def probe_session(status=200, error=None):
    response = MagicMock(status=status)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=error, return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


@pytest.mark.parametrize(("status", "expected"), [(200, True), (503, False)])
async def test_probe_reads_the_health_status(cog, status, expected):
    cog.bot.config.loldle_activity_internal_url = "http://activity:8080"
    cog._session = probe_session(status)
    await cog.activity_probe.coro(cog)
    assert cog._activity_up is expected
    assert cog._session.get.call_args.args[0] == "http://activity:8080/health"


async def test_probe_failure_never_raises(cog):
    cog.bot.config.loldle_activity_internal_url = "http://activity:8080"
    cog._activity_up = True
    cog._session = probe_session(error=TimeoutError("secret-dsn"))
    await cog.activity_probe.coro(cog)
    assert cog._activity_up is False


def test_fresh_boot_assumes_the_activity_is_down(cog):
    assert cog._activity_up is False


async def test_probe_logs_only_on_transitions(cog):
    from loguru import logger

    cog.bot.config.loldle_activity_internal_url = "http://activity:8080"
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(f"{m.record['level'].name}:{m.record['message']}"), level="INFO")
    try:
        cog._activity_up = False
        for status in (503, 503, 503):  # still down, as at boot: silence
            cog._session = probe_session(status)
            await cog.activity_probe.coro(cog)
        assert lines == []
        cog._session = probe_session(200)
        await cog.activity_probe.coro(cog)  # comes up: one info
        await cog.activity_probe.coro(cog)  # stays up: silence
        assert [line.split(":")[0] for line in lines] == ["INFO"]
        cog._session = probe_session(error=TimeoutError("secret-dsn"))
        await cog.activity_probe.coro(cog)  # goes down: one warning
        await cog.activity_probe.coro(cog)  # stays down: silence
        assert [line.split(":")[0] for line in lines] == ["INFO", "WARNING"]
        assert all("secret-dsn" not in line for line in lines)
    finally:
        logger.remove(sink)


async def test_drain_before_ready_claims_nothing(cog):
    cog.bot.is_ready.return_value = False
    claim = AsyncMock(return_value=[pending()])
    with svc(claim_announcements=claim):
        await cog._drain_announcements(10)
    claim.assert_not_called()


async def test_drain_after_ready_claims(cog):
    cog.bot.is_ready.return_value = True
    claim = AsyncMock(return_value=[])
    with svc(claim_announcements=claim):
        await cog._drain_announcements(10)
    claim.assert_awaited_once_with(10)


async def test_cog_load_starts_the_listener_and_unload_stops_it(cog):
    cog.bot.config.database_url = "postgresql://x"
    cog.bot.config.loldle_activity_internal_url = None
    with (
        patch.object(loldle_cog, "EventListener") as listener_cls,
        patch.object(loldle_cog.aiohttp, "ClientSession") as session_cls,
        patch.object(type(cog.minute_ticker), "start") as start,
        patch.object(type(cog.minute_ticker), "cancel"),
        patch.object(type(cog.activity_probe), "cancel"),
    ):
        listener = listener_cls.return_value
        listener.stop = AsyncMock()
        session_cls.return_value.close = AsyncMock()
        await cog.cog_load()
        listener.start.assert_called_once()
        start.assert_called_once()
        await cog.cog_unload()
        listener.stop.assert_awaited_once()
