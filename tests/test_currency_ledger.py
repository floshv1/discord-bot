from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.currency import service
from bot.cogs.currency.embeds import build_history_embed


def _mock_pool_with_conn(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    return pool


def _txn_inserts(conn):
    return [c for c in conn.execute.call_args_list if "INSERT INTO currency_transactions" in str(c)]


# --- the opening balance must appear in the ledger --------------------------


@pytest.mark.asyncio
async def test_a_new_wallet_records_its_opening_balance():
    # Without this the ledger is short by 1000 per member and cannot rebuild a balance.
    conn = AsyncMock()
    conn.fetchval.return_value = True  # xmax = 0 -> the INSERT created a wallet
    conn.fetchrow.return_value = {"balance": service.STARTING_BALANCE}

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        _, created = await service.get_or_create_wallet(guild_id=1, user_id=7)

    assert created is True
    inserts = _txn_inserts(conn)
    assert len(inserts) == 1
    assert inserts[0][0][4] == "initial"


@pytest.mark.asyncio
async def test_an_existing_wallet_records_nothing():
    conn = AsyncMock()
    conn.fetchval.return_value = None  # the conflict branch had nothing to change -> already existed
    conn.fetchrow.return_value = {"balance": 250}

    with patch("bot.cogs.currency.service.get_pool", return_value=_mock_pool_with_conn(conn)):
        _, created = await service.get_or_create_wallet(guild_id=1, user_id=7)

    assert created is False
    assert _txn_inserts(conn) == []


# --- every transaction records the balance it left behind -------------------


@pytest.mark.asyncio
async def test_adjust_records_the_resulting_balance():
    # Lets a bad balance be *located* — the transaction where the running total diverges.
    conn = AsyncMock()
    conn.fetchrow.return_value = {"balance": 500}

    await service.adjust(conn, guild_id=1, user_id=2, amount=-200, reason="bet")

    insert = _txn_inserts(conn)[0]
    assert insert[0][3] == -200  # amount
    assert insert[0][5] == 300  # balance_after


# --- the history embed ------------------------------------------------------


def _row(amount, reason, balance_after=None):
    created = MagicMock()
    created.strftime.return_value = "11/07 15:30"
    return {"amount": amount, "reason": reason, "balance_after": balance_after, "created_at": created}


def test_history_shows_sign_reason_and_resulting_balance():
    embed = build_history_embed("flosh", [_row(-250, "bet", 750)], balance=750)
    text = embed.description
    assert "-250" in text
    assert "Mise" in text  # a readable label, not the raw `bet` key
    assert "750" in text


def test_history_falls_back_to_the_raw_reason_if_unlabelled():
    # A new reason string must still show up rather than render blank.
    embed = build_history_embed("flosh", [_row(5, "some_new_reason", 5)], balance=5)
    assert "some_new_reason" in embed.description


def test_history_marks_a_credit_with_a_plus():
    embed = build_history_embed("flosh", [_row(100, "claim", 1100)], balance=1100)
    assert "+100" in embed.description


def test_history_handles_an_empty_ledger():
    embed = build_history_embed("flosh", [], balance=1000)
    assert embed.description  # must say something, not render blank
