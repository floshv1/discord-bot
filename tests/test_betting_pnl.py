from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting import service
from bot.cogs.betting.embeds import build_pnl_embed


def _row(user_id, profit, bets, wins):
    return {"user_id": user_id, "profit": profit, "bets": bets, "wins": wins}


def _text(embed) -> str:
    """Everything the reader actually sees — description and fields alike."""
    parts = [embed.description or ""]
    parts += [f"{f.name}\n{f.value}" for f in embed.fields]
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_only_settled_bets_count():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.betting_pnl(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    # A stake still in play is neither a win nor a loss, and a voided bet was refunded —
    # counting either would make the stats lie.
    assert "'resolved'" in sql
    assert "'open'" not in sql
    assert "'locked'" not in sql
    assert "'void'" not in sql


@pytest.mark.asyncio
async def test_profit_is_payout_minus_stake():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.betting_pnl(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    assert "payout" in sql
    assert "amount" in sql


def test_winners_and_losers_are_both_shown():
    rows = [
        _row(1, 900, 10, 7),
        _row(2, 50, 3, 2),
        _row(3, -400, 6, 1),
    ]
    text = _text(build_pnl_embed(rows, names={1: "Chanceux", 2: "Moyen", 3: "Malchanceux"}, updated="now"))

    assert "Chanceux" in text
    assert "Malchanceux" in text
    assert "+900" in text
    assert "-400" in text


def test_a_loss_is_not_disguised_as_a_gain():
    text = _text(build_pnl_embed([_row(3, -400, 6, 1)], names={3: "Malchanceux"}, updated="now"))
    assert "+400" not in text


def test_shows_how_many_bets_back_the_number():
    # +900 over 2 bets and +900 over 50 bets are not the same story.
    text = _text(build_pnl_embed([_row(1, 900, 10, 7)], names={1: "Chanceux"}, updated="now"))
    assert "10" in text
    assert "7" in text


def test_empty_pnl_still_renders():
    assert _text(build_pnl_embed([], names={}, updated="now")).strip()
