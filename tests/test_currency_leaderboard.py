from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.currency import service
from bot.cogs.currency.embeds import build_leaderboard_embed


def _row(user_id, balance, staked=0):
    return {"user_id": user_id, "balance": balance, "staked": staked, "total": balance + staked}


def test_ranking_counts_coins_staked_in_open_bets():
    # The whole point: a member who staked 500 is not poorer than one who never played.
    # Their coins are in the pool, not gone.
    prudent = _row(1, balance=1000, staked=0)
    joueur = _row(2, balance=500, staked=500)

    embed = build_leaderboard_embed([joueur, prudent], names={1: "Prudent", 2: "Joueur"}, updated="now")
    lines = embed.description.split("\n")

    assert "Joueur" in lines[0]  # ranked first: 500 + 500 staked = 1000, and he actually played
    assert "Prudent" in lines[1]


def test_staked_coins_are_shown_not_hidden():
    embed = build_leaderboard_embed([_row(2, balance=500, staked=500)], names={2: "Joueur"}, updated="now")
    text = embed.description
    assert "1,000" in text  # the total it's ranked on
    assert "500" in text  # and how much of it is actually at risk


def test_a_member_with_nothing_staked_shows_no_clutter():
    embed = build_leaderboard_embed([_row(1, balance=1000)], names={1: "Prudent"}, updated="now")
    assert "en jeu" not in embed.description


def test_empty_leaderboard_still_renders():
    assert build_leaderboard_embed([], names={}, updated="now").description


@pytest.mark.asyncio
async def test_only_unresolved_bets_count_as_staked():
    # A settled bet already paid out or refunded — counting it again would double it.
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    with patch("bot.cogs.currency.service.get_pool", return_value=pool):
        await service.top_balances(guild_id=1)

    sql = pool.fetch.call_args[0][0]
    assert "'open'" in sql
    assert "'locked'" in sql
    assert "resolved" not in sql
