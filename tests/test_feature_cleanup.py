"""The queue, tribunal and suggestions features were removed (2026-11-22, ~3 uses in 90 days).

These lock that nothing still loads, advertises or depends on them — and that `/reprimand`
survives as a plain sanction even for a guild whose config still carries a jury role from
the tribunal era.
"""

import importlib.util
from unittest.mock import AsyncMock, MagicMock

REMOVED = ("queue", "suggestions", "tribunal")


def test_removed_cogs_are_not_loaded():
    from bot.core.bot import COGS

    assert not [c for c in COGS if any(f"bot.cogs.{r}." in c for r in REMOVED)]


def test_help_does_not_advertise_removed_features():
    from bot.cogs.help import embeds
    from bot.cogs.help.cog import FEATURE_TABLES

    assert set(FEATURE_TABLES) == {"currency", "betting", "music", "palworld"}
    assert not [k for k, *_ in embeds.CONFIGURABLE if k in REMOVED]


def test_moderator_help_no_longer_mentions_trials():
    from bot.cogs.help.embeds import MODERATION

    text = " ".join(MODERATION).lower()
    assert "trial" not in text and "jury" not in text


def test_setup_status_has_no_removed_rows():
    from bot.cogs.setup.cog import FEATURES

    assert {name for name, *_ in FEATURES}.isdisjoint({"Queue", "Suggestions"})


def test_removed_packages_are_gone():
    for r in REMOVED:
        assert importlib.util.find_spec(f"bot.cogs.{r}") is None


async def test_reprimand_posts_plain_message_even_with_a_leftover_jury_role(monkeypatch):
    from bot.cogs.moderation import cog as mod

    pool = MagicMock()
    # reprimand_config still holds a judge_role_id written while the tribunal existed.
    pool.fetchrow = AsyncMock(side_effect=[{"role_id": 1, "channel_id": 2, "judge_role_id": 123}, None])
    pool.execute = AsyncMock()
    monkeypatch.setattr(mod, "get_pool", lambda: pool)
    monkeypatch.setattr(mod, "_log_action", AsyncMock())

    channel = MagicMock()
    channel.send = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.guild.get_role.return_value = MagicMock()
    interaction.guild.get_channel.return_value = channel
    interaction.response.send_message = AsyncMock()
    user = MagicMock()
    user.add_roles = AsyncMock()
    user.edit = AsyncMock()
    user.nick = None

    cog = mod.ModerationCog(MagicMock())
    await mod.ModerationCog.reprimand.callback(cog, interaction, user, "spam")

    channel.send.assert_awaited_once()
    assert "spam" in channel.send.await_args.args[0]
