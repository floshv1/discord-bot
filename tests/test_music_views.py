from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.music.views import NowPlayingView


def test_now_playing_view_never_times_out():
    # It sits on a public message. A timeout leaves visibly-clickable dead buttons.
    assert NowPlayingView().timeout is None


def test_every_button_has_a_custom_id_so_it_survives_a_restart():
    view = NowPlayingView()
    ids = [item.custom_id for item in view.children]
    assert all(ids), "a button without a custom_id cannot be re-registered after a restart"
    assert len(set(ids)) == len(ids), "custom_ids must be unique within the view"
    assert all(i.startswith("music:") for i in ids)


def test_can_be_constructed_with_no_player_for_registration_at_startup():
    # bot.add_view() at boot has no player — one may not even be connected.
    assert NowPlayingView().player is None


@pytest.mark.asyncio
async def test_a_stale_card_says_so_instead_of_failing_the_interaction():
    # After a restart the voice client is gone. Pressing Skip on an old card must explain
    # itself, not throw "This interaction failed".
    view = NowPlayingView()
    interaction = MagicMock()
    interaction.guild.voice_client = None
    interaction.response.send_message = AsyncMock()

    await view._skip(interaction)

    message = interaction.response.send_message.call_args[0][0]
    assert "playing" in message.lower()
    assert interaction.response.send_message.call_args[1]["ephemeral"] is True
