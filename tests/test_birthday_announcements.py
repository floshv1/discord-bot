from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.birthday.cog import claim_wishes_day


async def _claim(returned_row):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=returned_row)
    with patch("bot.cogs.birthday.cog.get_pool", return_value=pool):
        result = await claim_wishes_day(guild_id=1, today=date(2026, 7, 11))
    return result, pool


@pytest.mark.asyncio
async def test_a_new_day_is_claimed_and_wishes_go_out():
    # An UPDATE (xmax != 0) — we had announced before, and today is newer.
    claimed, _ = await _claim({"inserted": False})
    assert claimed is True


@pytest.mark.asyncio
async def test_a_day_already_announced_cannot_be_claimed_again():
    # The guard against double-wishing when a restart lands inside the midnight minute.
    claimed, _ = await _claim(None)
    assert claimed is False


@pytest.mark.asyncio
async def test_the_very_first_boot_seeds_the_day_instead_of_blasting_wishes():
    # A fresh INSERT means this guild has never announced. Installing the bot at 3pm must
    # not immediately fire birthday wishes at the channel — start from the next midnight.
    claimed, _ = await _claim({"inserted": True})
    assert claimed is False


@pytest.mark.asyncio
async def test_claim_is_a_single_atomic_statement():
    # Two ticks racing must not both win, so the check and the write cannot be separate.
    _, pool = await _claim({"inserted": False})
    sql = pool.fetchrow.call_args[0][0]
    assert "ON CONFLICT" in sql
    assert "RETURNING" in sql
    assert pool.fetchrow.await_count == 1
