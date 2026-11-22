"""Customs persistence. The races that matter are settled by the database, not by checks in Python."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from bot.cogs.customs import service


def _pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    for name in ("fetchrow", "fetchval", "fetch", "execute"):
        setattr(pool, name, getattr(conn, name))
    return pool


@pytest.fixture
def conn():
    c = AsyncMock()
    with patch("bot.cogs.customs.service.get_pool", return_value=_pool(c)):
        yield c


async def test_create_custom_maps_the_one_open_index_to_already_open(conn):
    conn.fetchval.side_effect = [asyncpg.UniqueViolationError("dup"), 7]
    with pytest.raises(service.AlreadyOpen) as e:
        await service.create_custom(1, 2, 3, "fixed", None, [])
    assert e.value.custom_id == 7


async def test_add_signup_twice_is_false_not_an_error(conn):
    conn.execute.side_effect = asyncpg.UniqueViolationError("dup")
    assert await service.add_signup(1, 42, 5) is False


async def test_claim_vote_win_is_a_single_conditional_update(conn):
    conn.fetchval.return_value = None  # someone else converted the card first
    slot = dt.datetime(2026, 11, 22, 21, tzinfo=dt.UTC)
    assert await service.claim_vote_win(1, slot, {}) is False
    sql = conn.fetchval.call_args.args[0]
    assert "mode = 'vote'" in sql and "status = 'signup'" in sql and "RETURNING" in sql
    conn.execute.assert_not_called()  # the loser signs nobody up


async def test_claim_vote_win_signs_up_that_slots_voters_in_vote_order(conn):
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [{"user_id": 10}, {"user_id": 11}]
    slot = dt.datetime(2026, 11, 22, 21, tzinfo=dt.UTC)
    assert await service.claim_vote_win(1, slot, {10: 100, 11: 101}) is True
    inserted = [c.args[2:4] for c in conn.execute.call_args_list if "custom_signups" in c.args[0]]
    assert inserted == [(10, 100), (11, 101)]


async def test_claim_winner_only_when_undecided(conn):
    conn.fetchval.return_value = None
    assert await service.claim_winner(1, 2, "blue") is False
    assert "winner IS NULL" in conn.fetchval.call_args.args[0]


async def test_claim_reminders_stamps_in_the_same_update(conn):
    conn.fetch.return_value = []
    await service.claim_reminders(dt.datetime(2026, 11, 22, tzinfo=dt.UTC))
    sql = conn.fetch.call_args.args[0]
    assert "SET reminded_at" in sql and "reminded_at IS NULL" in sql and "RETURNING" in sql


async def test_end_only_closes_an_open_custom(conn):
    conn.fetchval.return_value = None
    assert await service.end(1, "done") is False
    assert "status IN ('signup', 'teams')" in conn.fetchval.call_args.args[0]
