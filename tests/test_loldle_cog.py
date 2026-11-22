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


async def test_win_posts_once_publicly_without_the_champion_name(cog):
    i = interaction()
    channel = MagicMock()
    channel.send = AsyncMock()
    cog.bot.get_channel.return_value = channel
    with svc(
        record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, RESULT),
        get_config={"channel_id": 20, "board_message_id": None},
        result=RESULT,
    ):
        await cog.play(i, "Ahri")
    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert "Ahri" not in embed_text(kwargs["embed"])
    assert kwargs["allowed_mentions"].users is False


async def test_a_lost_win_race_does_not_post(cog):
    i = interaction()
    channel = MagicMock()
    channel.send = AsyncMock()
    cog.bot.get_channel.return_value = channel
    with svc(
        record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, None),
        get_config={"channel_id": 20, "board_message_id": None},
        result=RESULT,
    ):
        await cog.play(i, "Ahri")
    channel.send.assert_not_called()


async def test_win_without_a_channel_still_works(cog):
    i = interaction()
    with svc(record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, RESULT), result=RESULT):
        await cog.play(i, "Ahri")
    assert "Ahri" in embed_text(sent(i).kwargs["embed"])  # private card may name it


async def test_public_post_failure_does_not_lose_the_reply(cog):
    i = interaction()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403, reason="x"), "no"))
    cog.bot.get_channel.return_value = channel
    with svc(
        record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, RESULT),
        get_config={"channel_id": 20, "board_message_id": None},
        result=RESULT,
    ):
        await cog.play(i, "Ahri")
    i.followup.send.assert_awaited()


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


async def test_announce_db_failure_does_not_lose_the_reply(cog):
    i = interaction()
    with svc(
        record_guess=GuessOutcome("ok", ["green"] * 6, 4, True, RESULT),
        get_config=AsyncMock(side_effect=TimeoutError()),
        result=RESULT,
    ):
        await cog.play(i, "Ahri")
    assert "Ahri" in embed_text(sent(i).kwargs["embed"])


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
    assert out.won and out.cells == ["green"] * 6
