from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

from bot.cogs.betting.cog import BettingCog


def _cog(*, football: str | None, pandascore: str | None) -> BettingCog:
    bot = MagicMock()
    bot.config = MagicMock(football_data_api_key=football, pandascore_api_key=pandascore, guild_id=1)
    return BettingCog(bot)


async def _boot(cog) -> list[str]:
    """Run cog_load and return everything it logged."""
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    try:
        with (
            patch.object(cog, "_purge_retired_leagues", new=AsyncMock()),
            patch.object(cog, "_reregister_open_views", new=AsyncMock()),
            patch.object(cog, "_seed_unseeded_markets", new=AsyncMock()),
        ):
            await cog.cog_load()
    finally:
        logger.remove(sink_id)
        for ticker in (cog.fixture_poll_ticker, cog.lock_ticker, cog.resolution_ticker, cog.card_refresh_ticker):
            if ticker.is_running():
                ticker.cancel()
    return lines


@pytest.mark.asyncio
async def test_the_loaded_providers_are_named_at_boot():
    """A provider with no key is skipped in __init__ without a word — and a provider that is
    working says nothing either. Silence meant both "football is fine" and "there is no football
    at all", and the only way to tell them apart was to go hunting for cards in the channel.
    """
    cog = _cog(football="k1", pandascore="k2")

    logged = " ".join(await _boot(cog))

    assert "football_data" in logged
    assert "pandascore" in logged


@pytest.mark.asyncio
async def test_a_provider_without_a_key_is_not_claimed_as_loaded():
    cog = _cog(football=None, pandascore="k2")

    logged = " ".join(await _boot(cog))

    assert "pandascore" in logged
    assert "football_data" not in logged


@pytest.mark.asyncio
async def test_no_provider_at_all_is_a_warning_not_silence():
    # Perfectly legal — /bet create needs no key — but it must not look like a working board.
    cog = _cog(football=None, pandascore=None)

    logged = " ".join(await _boot(cog))

    assert "No betting provider configured" in logged
    assert not cog.fixture_poll_ticker.is_running()  # nothing to poll
