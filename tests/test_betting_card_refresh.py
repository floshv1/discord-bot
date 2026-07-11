from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.betting.cog import BettingCog


def _cog() -> BettingCog:
    bot = MagicMock()
    bot.config = MagicMock(football_data_api_key=None, pandascore_api_key=None)
    return BettingCog(bot)


@pytest.mark.asyncio
async def test_a_flurry_of_bets_collapses_into_one_edit_per_card():
    # The whole point of the debounce: 20 people betting on one card must not produce
    # 20 message edits and trip Discord's rate limit.
    cog = _cog()
    for _ in range(20):
        cog.mark_card_dirty(7)

    with patch("bot.cogs.betting.cog.refresh_market_message", new=AsyncMock()) as refresh:
        await cog.card_refresh_ticker()

    refresh.assert_awaited_once()
    assert refresh.await_args[0][1] == 7


@pytest.mark.asyncio
async def test_each_dirty_card_is_refreshed():
    cog = _cog()
    cog.mark_card_dirty(1)
    cog.mark_card_dirty(2)

    with patch("bot.cogs.betting.cog.refresh_market_message", new=AsyncMock()) as refresh:
        await cog.card_refresh_ticker()

    assert {call[0][1] for call in refresh.await_args_list} == {1, 2}


@pytest.mark.asyncio
async def test_ticker_does_nothing_when_no_card_is_dirty():
    cog = _cog()
    with patch("bot.cogs.betting.cog.refresh_market_message", new=AsyncMock()) as refresh:
        await cog.card_refresh_ticker()
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_card_is_not_refreshed_twice_for_the_same_bets():
    cog = _cog()
    cog.mark_card_dirty(7)

    with patch("bot.cogs.betting.cog.refresh_market_message", new=AsyncMock()) as refresh:
        await cog.card_refresh_ticker()
        await cog.card_refresh_ticker()

    refresh.assert_awaited_once()
