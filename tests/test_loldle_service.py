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


# --- events and announcements ---------------------------------------------


def _notifies(conn):
    return [c.args for c in conn.execute.await_args_list if "pg_notify" in c.args[0]]


async def test_guess_notifies_inside_the_transaction(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, None, 1]
    await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Annie"])
    calls = _notifies(conn)
    assert len(calls) == 1 and calls[0][1] == "loldle_events" and '"kind": "guess"' in calls[0][2]
    assert conn.execute.await_args_list[-1].args == calls[0]  # on the same conn, last statement of the transaction


async def test_win_notifies_win_and_marks_pending(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, None, 1, 0]
    conn.fetch.return_value = [{"cells": json.dumps(["green"])}]
    out = await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Ahri"])
    assert out.won
    assert "announce_pending" in conn.fetchrow.await_args.args[0]
    assert '"kind": "win"' in _notifies(conn)[0][2]


async def test_a_lost_win_race_does_not_notify(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, None, 1, 0]
    conn.fetch.return_value = [{"cells": json.dumps(["green"])}]
    conn.fetchrow.return_value = None  # ON CONFLICT DO NOTHING: another request already claimed the win
    out = await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Ahri"])
    assert out.result is None
    assert _notifies(conn) == []


async def test_notify_payload_never_names_a_champion(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, None, 1]
    await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Annie"])
    payload = _notifies(conn)[0][2]
    assert set(json.loads(payload)) == {"guild_id", "day", "user_id", "kind"}
    assert "Ahri" not in payload and "Annie" not in payload


async def test_hint_purchase_notifies_hint():
    from bot.cogs.loldle import service as svc

    conn = AsyncMock()
    pool = _pool(conn)
    conn.fetchval.side_effect = [None, 3]
    conn.fetch.return_value = []
    with (
        patch.object(svc, "get_pool", return_value=pool),
        patch.object(svc.currency, "get_or_create_wallet", AsyncMock()),
        patch.object(svc.currency, "ensure_house_wallet", AsyncMock()),
        patch.object(svc.currency, "get_balance_locked", AsyncMock(return_value=500)),
        patch.object(svc.currency, "adjust", AsyncMock(return_value=475)),
    ):
        assert (await svc.buy_hint(1, DAY, 2, "spell")).status == "bought"
        assert '"kind": "hint"' in _notifies(conn)[0][2]
        conn.execute.reset_mock()
        conn.fetchval.side_effect = [None, 1]  # locked: fewer than 3 guesses
        assert (await svc.buy_hint(1, DAY, 2, "spell")).status == "locked"
        assert _notifies(conn) == []


async def test_duplicate_guess_does_not_notify(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [None, 1]
    await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Annie"])
    assert _notifies(conn) == []


async def test_already_solved_guess_does_not_notify(conn):
    champions = catalog.load()
    conn.fetchval.side_effect = [1]
    await service.record_guess(1, DAY, 2, champions["Ahri"], champions["Annie"])
    assert _notifies(conn) == []


async def test_claim_is_one_conditional_update(conn):
    conn.fetch.return_value = []
    await service.claim_announcements(1)
    sql = conn.fetch.await_args.args[0]
    for part in ("SET announce_pending = FALSE", "WHERE", "announce_pending", "RETURNING", "SKIP LOCKED"):
        assert part in sql


async def test_live_rows_types_its_date(conn):
    conn.fetch.return_value = []
    await service.live_rows(1, DAY)
    assert "$2::date" in conn.fetch.await_args.args[0]


async def test_player_names_reads_only_the_players_table(conn):
    conn.fetch.return_value = []
    await service.player_names(1, [5, 6])
    sql = conn.fetch.await_args.args[0]
    assert "loldle_players" in sql and "$2::bigint[]" in sql
    conn.fetch.reset_mock()
    assert await service.player_names(1, []) == []
    conn.fetch.assert_not_called()


def test_resolve_guess_prefers_stored_answer():
    gone = catalog.load()["Ahri"].to_json() | {"id": "Removed", "name": "Removed Hero"}
    answer = catalog.Champion.from_json(gone)
    assert service.resolve_guess("removed hero", answer) is answer
    assert service.resolve_guess("Removed", answer) is answer
    assert service.resolve_guess("Ahri", answer).id == "Ahri"
    assert service.resolve_guess("zzzz", answer) is None
    assert service.resolve_guess("Remov", answer) is None


# --- per-member posts and group data ------------------------------------------


async def test_claim_post_is_unique(conn):
    conn.fetchval.side_effect = [True, None]
    assert await service.claim_post(1, DAY, 2, 30) is True
    assert await service.claim_post(1, DAY, 2, 30) is False
    sql = conn.fetchval.await_args.args[0]
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql and "RETURNING" in sql
    assert "$2::date" in sql


async def test_finish_and_unfinished_posts(conn):
    await service.finish_post(1, DAY, 2)
    assert "finished = TRUE" in conn.execute.await_args.args[0]
    conn.fetch.return_value = [{"user_id": 2, "channel_id": 30, "message_id": 40}]
    rows = await service.unfinished_posts(1, DAY)
    assert rows[0]["user_id"] == 2
    assert "NOT finished" in conn.fetch.await_args.args[0]


async def test_day_grids_groups_cells_by_member_in_order(conn):
    conn.fetch.return_value = [
        {"user_id": 2, "cells": json.dumps(["red"] * 9)},
        {"user_id": 3, "cells": ["green"] * 9},
        {"user_id": 2, "cells": ["green"] * 9},
    ]
    grids = await service.day_grids(1, DAY)
    assert grids == {2: [["red"] * 9, ["green"] * 9], 3: [["green"] * 9]}
    assert "ORDER BY" in conn.fetch.await_args.args[0]


async def test_group_solved_days(conn):
    conn.fetch.return_value = [{"day": DAY}, {"day": DAY - dt.timedelta(days=1)}]
    assert await service.group_solved_days(1) == {DAY, DAY - dt.timedelta(days=1)}
