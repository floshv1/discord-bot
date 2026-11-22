"""LoLdle persistence. Races are checked against a real Postgres when the build runs; these lock the shape."""

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.loldle import catalog, service

DAY = dt.date(2026, 11, 22)


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
    with patch("bot.cogs.loldle.service.get_pool", return_value=_pool(c)):
        yield c


async def test_pick_avoids_recent_champions_and_lets_the_first_insert_win(conn):
    champions = catalog.load()
    recent = [{"champion_id": k} for k in champions if k != "Ahri"]
    row = {"champion_id": "Ahri"}
    conn.fetchrow.side_effect = [None, row]
    conn.fetch.return_value = recent
    assert await service.get_or_pick_day(1, DAY, champions) == row
    sql, _guild, _day, picked, answer = conn.execute.await_args.args
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
    assert picked == "Ahri" and json.loads(answer)["id"] == "Ahri"


async def test_pick_falls_back_when_every_champion_is_recent(conn):
    champions = catalog.load()
    conn.fetchrow.side_effect = [None, {"champion_id": "x"}]
    conn.fetch.return_value = [{"champion_id": k} for k in champions]
    await service.get_or_pick_day(1, DAY, champions)
    assert conn.execute.await_args.args[3] in champions


async def test_existing_day_is_never_picked_again(conn):
    conn.fetchrow.return_value = {"champion_id": "Ahri"}
    await service.get_or_pick_day(1, DAY, catalog.load())
    conn.execute.assert_not_called()


def test_answer_comes_from_the_stored_row_not_the_catalog():
    gone = catalog.load()["Ahri"].to_json() | {"id": "Removed", "name": "Removed"}
    assert service.answer_of({"answer": json.dumps(gone)}).id == "Removed"
    assert service.answer_of({"answer": gone}).id == "Removed"


async def test_guess_is_serialised_per_member(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, None, 1]  # no result, not a duplicate, number 1
    out = await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Annie"])
    first_sql = conn.execute.await_args_list[0].args[0]
    assert "pg_advisory_xact_lock" in first_sql
    assert out.status == "ok" and out.number == 1 and not out.won


async def test_duplicate_guess_costs_nothing(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, 1]
    out = await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Annie"])
    assert out.status == "duplicate"
    assert not any("INSERT INTO loldle_guesses" in c.args[0] for c in conn.execute.await_args_list)


async def test_guess_after_solving_is_refused(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [1]
    out = await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Ahri"])
    assert out.status == "solved_already"


async def test_every_date_parameter_is_typed(conn):
    conn.fetch.return_value = []
    await service.board_rows(1, DAY)
    assert "$2::date" in conn.fetch.await_args.args[0]


def test_a_day_stored_before_the_new_clues_is_completed_from_the_catalog():
    stored = catalog.load()["Ahri"].to_json()
    for key in ("gender", "damage", "species"):
        del stored[key]
    answer = service.answer_of({"answer": stored})
    assert answer == catalog.load()["Ahri"]


def test_old_answer_no_longer_in_the_catalog_still_loads():
    stored = catalog.load()["Ahri"].to_json() | {"id": "Removed", "name": "Removed"}
    for key in ("gender", "damage", "species"):
        del stored[key]
    answer = service.answer_of({"answer": stored})
    assert answer.id == "Removed" and answer.gender == "" and answer.species == ()
