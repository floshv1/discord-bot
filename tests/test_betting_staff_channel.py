"""The staff channel: where a settlement recap goes, and nothing else."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.betting import service
from bot.cogs.betting.embeds import build_staff_result_embed
from bot.cogs.betting.views import announce_result_to_staff
from bot.cogs.currency.service import HOUSE_USER_ID


def _pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.mark.asyncio
async def test_setting_the_betting_channel_stores_the_staff_channel():
    pool = _pool()
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.set_betting_channel(1, 111, 222)

    sql, *args = pool.execute.call_args[0]
    assert "staff_channel_id" in sql
    assert args == [1, 111, 222]


@pytest.mark.asyncio
async def test_rerunning_setup_without_a_staff_channel_clears_it():
    # /setup betting is re-runnable, and a re-run replaces what the last one posted. Leaving a
    # stale staff channel behind would keep mirroring settlements into a channel the admin
    # just decided not to use.
    pool = _pool()
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        await service.set_betting_channel(1, 111)

    sql, *args = pool.execute.call_args[0]
    assert "EXCLUDED.staff_channel_id" in sql
    assert args == [1, 111, None]


@pytest.mark.asyncio
async def test_no_staff_channel_configured_reads_back_as_none():
    pool = _pool()
    with patch("bot.cogs.betting.service.get_pool", return_value=pool):
        assert await service.get_staff_channel(1) is None


def test_the_migration_is_additive_and_rerunnable():
    from bot.db.models import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "026_betting_staff_channel.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS staff_channel_id" in sql


RESOLVED = {
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
    "creator_user_id": 10,
}

# A real settlement, not made-up numbers: pool 600, home wins with 400 on it, losing pool 200.
# settle_parimutuel pays the house 100 + 100*200//400 = 150, and user 7 300 + 300*200//400 = 450.
# Nothing is left over, so the house simply loses 50 of its 200 seed on this one.
BETS = [
    {"id": -1, "user_id": HOUSE_USER_ID, "outcome": "home", "amount": 100, "payout": 150},
    {"id": -2, "user_id": HOUSE_USER_ID, "outcome": "away", "amount": 100, "payout": 0},
    {"id": 1, "user_id": 7, "outcome": "home", "amount": 300, "payout": 450},
    {"id": 2, "user_id": 8, "outcome": "away", "amount": 100, "payout": 0},
]


def test_the_staff_embed_names_who_settled_it():
    # The whole point of the staff channel: a dishonest settlement must be visible without
    # anyone querying the database.
    embed = build_staff_result_embed(RESOLVED, BETS, settled_by="<@10>")
    assert any("<@10>" in (f.value or "") for f in embed.fields)


def test_the_staff_embed_reports_the_house_pnl():
    # Whether a market fed the treasury or drained it is the one number a mod can't get from
    # the card. House staked 200, was paid 150 back, no residual -> -50.
    embed = build_staff_result_embed(RESOLVED, BETS, settled_by="<@10>")
    assert any("-50" in (f.value or "") for f in embed.fields)


def test_the_staff_embed_lists_the_players_and_not_the_house():
    embed = build_staff_result_embed(RESOLVED, BETS, settled_by="auto")
    body = "\n".join(f.value or "" for f in embed.fields)
    assert "<@7>" in body and "<@8>" in body
    assert f"<@{HOUSE_USER_ID}>" not in body


@pytest.mark.asyncio
async def test_no_staff_channel_means_no_recap_and_no_crash():
    client = MagicMock()
    with (
        patch("bot.cogs.betting.views.service.get_market", AsyncMock(return_value=RESOLVED)),
        patch("bot.cogs.betting.views.service.get_bets", AsyncMock(return_value=BETS)),
        patch("bot.cogs.betting.views.service.get_staff_channel", AsyncMock(return_value=None)),
    ):
        await announce_result_to_staff(client, 1, settled_by="<@10>")  # must not raise

    client.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_a_market_nobody_played_is_not_worth_a_recap():
    # Every unbet football fixture would otherwise post one. The house is on all of them.
    channel = MagicMock()
    channel.send = AsyncMock()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)
    house_only = [b for b in BETS if b["user_id"] == HOUSE_USER_ID]

    with (
        patch("bot.cogs.betting.views.service.get_market", AsyncMock(return_value=RESOLVED)),
        patch("bot.cogs.betting.views.service.get_bets", AsyncMock(return_value=house_only)),
        patch("bot.cogs.betting.views.service.get_staff_channel", AsyncMock(return_value=99)),
    ):
        await announce_result_to_staff(client, 1, settled_by="auto")

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_the_recap_is_sent_to_the_staff_channel():
    channel = MagicMock()
    channel.send = AsyncMock()
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)

    with (
        patch("bot.cogs.betting.views.service.get_market", AsyncMock(return_value=RESOLVED)),
        patch("bot.cogs.betting.views.service.get_bets", AsyncMock(return_value=BETS)),
        patch("bot.cogs.betting.views.service.get_staff_channel", AsyncMock(return_value=99)),
    ):
        await announce_result_to_staff(client, 1, settled_by="<@10>")

    client.get_channel.assert_called_once_with(99)
    assert isinstance(channel.send.await_args.kwargs["embed"], discord.Embed)


def test_the_public_channel_is_never_told_a_result_again():
    # The betting channel holds cards. Nothing else.
    from bot.cogs.betting import views

    assert not hasattr(views, "announce_result")
