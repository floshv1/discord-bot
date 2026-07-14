"""A market's result goes to the bettors in DMs, not to the channel."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.betting.views import build_dm_text, dm_bettors

MARKET = {
    "id": 1,
    "guild_id": 1,
    "provider": "custom",
    "sport": "custom",
    "status": "resolved",
    "competition": "Qui gagne le scrim ?",
    "home_name": "Team Bleue",
    "away_name": "Team Rouge",
    "winner": "home",
    "channel_id": 5,
    "message_id": 6,
}


def _bet(outcome, amount, payout, user_id=7):
    return {"id": 1, "user_id": user_id, "outcome": outcome, "amount": amount, "payout": payout}


def test_a_winner_is_told_what_they_made():
    text = build_dm_text(MARKET, [_bet("home", 300, 720)], balance=2150)
    assert "Team Bleue" in text
    assert "300" in text and "720" in text
    assert "+420" in text
    assert "2 150" in text or "2,150" in text


def test_a_loser_is_told_too():
    # They staked coins and they are gone; learning that from a silently edited card far up the
    # channel is not learning it.
    text = build_dm_text(MARKET, [_bet("away", 200, 0)], balance=800)
    assert "Team Rouge" in text
    assert "200" in text
    assert "+" not in text


def test_a_topped_up_position_is_one_dm_not_three():
    # Three rows in the table, one person, one message — summed.
    bets = [_bet("home", 100, 240), _bet("home", 200, 480)]
    text = build_dm_text(MARKET, bets, balance=1000)
    assert "300" in text  # 100 + 200 staked
    assert "720" in text  # 240 + 480 paid


def test_a_void_market_says_refunded_not_lost():
    text = build_dm_text({**MARKET, "status": "void", "winner": None}, [_bet("home", 200, 200)], balance=1000)
    assert "annulé" in text
    assert "rendus" in text


@pytest.mark.asyncio
async def test_every_bettor_gets_one_dm_and_the_house_gets_none():
    user = MagicMock()
    user.send = AsyncMock()
    client = MagicMock()
    client.get_user = MagicMock(return_value=user)

    with (
        patch("bot.cogs.betting.views.service.get_market", AsyncMock(return_value=MARKET)),
        patch(
            "bot.cogs.betting.views.service.get_bets",
            AsyncMock(return_value=[_bet("home", 300, 720, user_id=7), _bet("away", 100, 0, user_id=0)]),
        ),
        patch("bot.cogs.betting.views.currency_service.get_balance", AsyncMock(return_value=1000)),
    ):
        await dm_bettors(client, 1)

    assert user.send.await_count == 1  # user 0 is the house — it does not read its mail


@pytest.mark.asyncio
async def test_closed_dms_do_not_stop_the_other_bettors():
    # A member who blocked the bot must not cost the other nine their result.
    blocked = MagicMock()
    blocked.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "cannot send"))
    ok = MagicMock()
    ok.send = AsyncMock()

    client = MagicMock()
    client.get_user = MagicMock(side_effect=[blocked, ok])

    with (
        patch("bot.cogs.betting.views.service.get_market", AsyncMock(return_value=MARKET)),
        patch(
            "bot.cogs.betting.views.service.get_bets",
            AsyncMock(return_value=[_bet("home", 300, 720, user_id=7), _bet("home", 100, 240, user_id=8)]),
        ),
        patch("bot.cogs.betting.views.currency_service.get_balance", AsyncMock(return_value=1000)),
    ):
        await dm_bettors(client, 1)  # must not raise

    ok.send.assert_awaited_once()
