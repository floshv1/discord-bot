"""Customs ticker (reminder, expiry) and the wiring that makes the feature visible — or not."""

import datetime as dt
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

from bot.cogs.customs import cog as customs_cog


def _cog():
    bot = MagicMock()
    return customs_cog.CustomsCog(bot), bot


async def test_reminder_mentions_the_ten_players_not_guests_nor_waitlist():
    cog, bot = _cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    custom = {"id": 5, "channel_id": 20, "start_at": dt.datetime(2026, 11, 22, 20, tzinfo=dt.UTC)}
    rows = [{"id": i, "user_id": (None if i == 0 else 100 + i), "person_id": i} for i in range(11)]
    with (
        patch.object(customs_cog.service, "claim_reminders", AsyncMock(return_value=[custom])),
        patch.object(customs_cog.service, "expire_due", AsyncMock(return_value=[])),
        patch.object(customs_cog.service, "signups", AsyncMock(return_value=rows)),
    ):
        await cog.minute_ticker.coro(cog)
    text = channel.send.await_args.args[0]
    assert "<@101>" in text and "<@109>" in text
    assert "<@110>" not in text and "None" not in text  # waitlist and guest left out


async def test_no_reminder_when_nothing_is_claimed():
    cog, bot = _cog()
    with (
        patch.object(customs_cog.service, "claim_reminders", AsyncMock(return_value=[])),
        patch.object(customs_cog.service, "expire_due", AsyncMock(return_value=[])),
    ):
        await cog.minute_ticker.coro(cog)
    bot.get_channel.assert_not_called()


async def test_expired_customs_get_their_card_redrawn():
    cog, _ = _cog()
    with (
        patch.object(customs_cog.service, "claim_reminders", AsyncMock(return_value=[])),
        patch.object(customs_cog.service, "expire_due", AsyncMock(return_value=[{"id": 9}])),
    ):
        await cog.minute_ticker.coro(cog)
    assert 9 in cog._dirty


async def _help_channels(lolamis_url):
    from bot.cogs.help import cog as help_cog

    bot = MagicMock()
    bot.config.lolamis_url = lolamis_url
    bot.get_channel.return_value = MagicMock(mention="#customs")
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"channel_id": 1, "judge_role_id": 1})
    i = MagicMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    captured = {}
    with (
        patch.object(help_cog, "get_pool", return_value=pool),
        patch.object(help_cog, "build_help_embed", side_effect=lambda ch, mod: captured.update(ch) or MagicMock()),
    ):
        await help_cog.HelpCog(bot).help_command.callback(help_cog.HelpCog(bot), i)
    return captured


async def test_help_lists_customs_only_when_lolamis_is_configured():
    assert (await _help_channels("http://lol-amis:8000"))["customs"] == "#customs"
    assert (await _help_channels(None))["customs"] is None


def test_customs_cog_is_loaded_and_importable():
    from bot.core.bot import COGS

    assert "bot.cogs.customs.cog" in COGS
    importlib.import_module("bot.cogs.customs.cog")


def test_setup_status_knows_customs():
    from bot.cogs.setup.cog import FEATURES

    assert ("Customs", "customs_config", [], "/setup customs") in FEATURES
