"""LoLdle persistence. Races are settled in SQL:

- the day's champion: first `INSERT … ON CONFLICT DO NOTHING` wins, everyone re-reads the row;
- one member's guesses (and hint purchases): serialised by a per-member advisory lock;
- the win: a single `INSERT … ON CONFLICT DO NOTHING RETURNING`, so it is announced once.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from dataclasses import dataclass

import asyncpg

from bot.cogs.currency import service as currency
from bot.cogs.loldle import rules
from bot.cogs.loldle.catalog import Champion
from bot.db.client import get_pool


@dataclass(frozen=True)
class GuessOutcome:
    status: str  # ok | duplicate | solved_already
    cells: list[str] | None = None
    number: int = 0
    won: bool = False
    result: asyncpg.Record | None = None  # set only for the request that claimed the win


@dataclass(frozen=True)
class BuyOutcome:
    status: str  # bought | owned | locked | poor
    balance: int | None = None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def answer_of(day_row) -> Champion:
    """The stored answer, never the catalog: a refreshed file cannot change a day in progress."""
    return Champion.from_json(_json(day_row["answer"]))


def cells_of(guess_row) -> list[str]:
    return list(_json(guess_row["cells"]))


def clues_of(guess_row) -> list[str]:
    return list(_json(guess_row["clues"]))


async def lock_member(conn, guild_id: int, day: dt.date, user_id: int) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
        f"loldle:{guild_id}:{day}:{user_id}",
    )


# --- config ---------------------------------------------------------------


async def get_config(guild_id: int) -> asyncpg.Record | None:
    return await get_pool().fetchrow(
        "SELECT channel_id, board_message_id FROM loldle_config WHERE guild_id = $1", guild_id
    )


async def set_config(guild_id: int, channel_id: int, board_message_id: int | None) -> None:
    await get_pool().execute(
        """
        INSERT INTO loldle_config (guild_id, channel_id, board_message_id) VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2, board_message_id = $3
        """,
        guild_id,
        channel_id,
        board_message_id,
    )


async def configured_guilds() -> list[asyncpg.Record]:
    return await get_pool().fetch("SELECT guild_id, channel_id, board_message_id FROM loldle_config")


# --- the day --------------------------------------------------------------


async def get_or_pick_day(guild_id: int, day: dt.date, champions: dict[str, Champion]) -> asyncpg.Record:
    """Today's row, picking the champion on first use. No midnight job can skip a day."""
    pool = get_pool()
    select = "SELECT * FROM loldle_days WHERE guild_id = $1 AND day = $2::date"
    row = await pool.fetchrow(select, guild_id, day)
    if row is not None:
        return row
    recent = await pool.fetch(
        "SELECT champion_id FROM loldle_days WHERE guild_id = $1 AND day >= $2::date - $3::int",
        guild_id,
        day,
        rules.NO_REPEAT_DAYS,
    )
    used = {r["champion_id"] for r in recent}
    candidates = sorted(set(champions) - used) or sorted(champions)
    champion = champions[secrets.choice(candidates)]
    await pool.execute(
        """
        INSERT INTO loldle_days (guild_id, day, champion_id, answer) VALUES ($1, $2::date, $3, $4::jsonb)
        ON CONFLICT (guild_id, day) DO NOTHING
        """,
        guild_id,
        day,
        champion.id,
        json.dumps(champion.to_json()),
    )
    return await pool.fetchrow(select, guild_id, day)


async def tick_day(guild_id: int, day: dt.date, champions: dict[str, Champion]) -> None:
    """One minute of uptime. Also creates the day, so "nobody played" differs from "bot down"."""
    await get_or_pick_day(guild_id, day, champions)
    await get_pool().execute(
        "UPDATE loldle_days SET up_minutes = up_minutes + 1 WHERE guild_id = $1 AND day = $2::date", guild_id, day
    )


async def all_days(guild_id: int) -> list[rules.DayInfo]:
    rows = await get_pool().fetch("SELECT day, up_minutes FROM loldle_days WHERE guild_id = $1", guild_id)
    return [rules.DayInfo(r["day"], r["up_minutes"]) for r in rows]


async def day_row(guild_id: int, day: dt.date) -> asyncpg.Record | None:
    return await get_pool().fetchrow("SELECT * FROM loldle_days WHERE guild_id = $1 AND day = $2::date", guild_id, day)


# --- one member's game ----------------------------------------------------


async def guesses(guild_id: int, day: dt.date, user_id: int) -> list[asyncpg.Record]:
    return await get_pool().fetch(
        """
        SELECT number, champion_id, name, cells, clues FROM loldle_guesses
        WHERE guild_id = $1 AND day = $2::date AND user_id = $3 ORDER BY number
        """,
        guild_id,
        day,
        user_id,
    )


async def result(guild_id: int, day: dt.date, user_id: int) -> asyncpg.Record | None:
    return await get_pool().fetchrow(
        "SELECT * FROM loldle_results WHERE guild_id = $1 AND day = $2::date AND user_id = $3", guild_id, day, user_id
    )


async def hints(guild_id: int, day: dt.date, user_id: int) -> set[str]:
    rows = await get_pool().fetch(
        "SELECT kind FROM loldle_hints WHERE guild_id = $1 AND day = $2::date AND user_id = $3", guild_id, day, user_id
    )
    return {r["kind"] for r in rows}


async def record_guess(guild_id: int, day: dt.date, user_id: int, guess: Champion, answer: Champion) -> GuessOutcome:
    if guess.id == answer.id:  # a refreshed catalog must not turn the winning row red
        guess = answer
    async with get_pool().acquire() as conn, conn.transaction():
        await lock_member(conn, guild_id, day, user_id)
        where = "guild_id = $1 AND day = $2::date AND user_id = $3"
        if await conn.fetchval(f"SELECT 1 FROM loldle_results WHERE {where}", guild_id, day, user_id):
            return GuessOutcome("solved_already")
        if await conn.fetchval(
            f"SELECT 1 FROM loldle_guesses WHERE {where} AND champion_id = $4", guild_id, day, user_id, guess.id
        ):
            return GuessOutcome("duplicate")
        number = await conn.fetchval(
            f"SELECT COALESCE(MAX(number), 0) + 1 FROM loldle_guesses WHERE {where}", guild_id, day, user_id
        )
        cells = rules.compare(guess, answer)
        await conn.execute(
            """
            INSERT INTO loldle_guesses (guild_id, day, user_id, number, champion_id, name, cells, clues)
            VALUES ($1, $2::date, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            """,
            guild_id,
            day,
            user_id,
            number,
            guess.id,
            guess.name,
            json.dumps(cells),
            json.dumps(rules.clues(guess), ensure_ascii=False),
        )
        if guess.id != answer.id:
            return GuessOutcome("ok", cells, number)
        rows = await conn.fetch(
            f"SELECT cells FROM loldle_guesses WHERE {where} ORDER BY number", guild_id, day, user_id
        )
        grid = "\n".join(rules.grid_row(cells_of(r)) for r in rows)
        used = await conn.fetchval(f"SELECT COUNT(*) FROM loldle_hints WHERE {where}", guild_id, day, user_id)
        won = await conn.fetchrow(
            """
            INSERT INTO loldle_results (guild_id, day, user_id, tries, hints, score, grid)
            VALUES ($1, $2::date, $3, $4, $5, $6, $7)
            ON CONFLICT (guild_id, day, user_id) DO NOTHING
            RETURNING *
            """,
            guild_id,
            day,
            user_id,
            number,
            used,
            rules.score(number, used),
            grid,
        )
        return GuessOutcome("ok", cells, number, True, won)


# --- paid hints -----------------------------------------------------------


async def buy_hint(guild_id: int, day: dt.date, user_id: int, kind: str) -> BuyOutcome:
    """Charge a hint once. Coins go to the house, so nothing is minted and the ledger reconciles.

    Both wallets are created first, through the paths that record their opening transaction:
    `adjust()` alone would create a missing wallet with 1000 coins and no ledger row.
    """
    await currency.get_or_create_wallet(guild_id, user_id)
    await currency.ensure_house_wallet(guild_id)
    price = rules.HINTS[kind]
    where = "guild_id = $1 AND day = $2::date AND user_id = $3"
    async with get_pool().acquire() as conn, conn.transaction():
        await lock_member(conn, guild_id, day, user_id)
        solved = await conn.fetchval(f"SELECT 1 FROM loldle_results WHERE {where}", guild_id, day, user_id)
        rows = await conn.fetch(f"SELECT kind FROM loldle_hints WHERE {where}", guild_id, day, user_id)
        owned = {r["kind"] for r in rows}
        if kind in owned:
            return BuyOutcome("owned")
        count = await conn.fetchval(f"SELECT COUNT(*) FROM loldle_guesses WHERE {where}", guild_id, day, user_id)
        if solved or not rules.can_buy(kind, owned, count):
            return BuyOutcome("locked")
        balance = await currency.get_balance_locked(conn, guild_id=guild_id, user_id=user_id)
        if balance < price:
            return BuyOutcome("poor", balance)
        await conn.execute(
            "INSERT INTO loldle_hints (guild_id, day, user_id, kind) VALUES ($1, $2::date, $3, $4)",
            guild_id,
            day,
            user_id,
            kind,
        )
        new_balance = await currency.adjust(
            conn, guild_id=guild_id, user_id=user_id, amount=-price, reason="loldle_hint"
        )
        await currency.adjust(
            conn, guild_id=guild_id, user_id=currency.HOUSE_USER_ID, amount=price, reason="loldle_hint"
        )
        return BuyOutcome("bought", new_balance)


# --- streaks and board ----------------------------------------------------


async def solved_days(guild_id: int, user_id: int) -> set[dt.date]:
    rows = await get_pool().fetch(
        "SELECT day FROM loldle_results WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
    )
    return {r["day"] for r in rows}


async def solved_by_user(guild_id: int) -> dict[int, set[dt.date]]:
    out: dict[int, set[dt.date]] = {}
    for r in await get_pool().fetch("SELECT user_id, day FROM loldle_results WHERE guild_id = $1", guild_id):
        out.setdefault(r["user_id"], set()).add(r["day"])
    return out


async def solved_count(guild_id: int, day: dt.date) -> int:
    return await get_pool().fetchval(
        "SELECT COUNT(*) FROM loldle_results WHERE guild_id = $1 AND day = $2::date", guild_id, day
    )


async def board_rows(guild_id: int, today: dt.date) -> list[asyncpg.Record]:
    """Top 10 by average score over the window. A DNF is a past day with a guess and no result:
    outside the average, but counted. Today is never a DNF yet."""
    return await get_pool().fetch(
        """
        WITH won AS (
            SELECT user_id, AVG(score)::float AS average, COUNT(*) AS solved
            FROM loldle_results
            WHERE guild_id = $1 AND day > $2::date - $3::int AND day <= $2::date
            GROUP BY user_id
        ), dnf AS (
            SELECT g.user_id, COUNT(DISTINCT g.day) AS dnf
            FROM loldle_guesses g
            WHERE g.guild_id = $1 AND g.day > $2::date - $3::int AND g.day < $2::date
              AND NOT EXISTS (
                  SELECT 1 FROM loldle_results r
                  WHERE r.guild_id = g.guild_id AND r.day = g.day AND r.user_id = g.user_id
              )
            GROUP BY g.user_id
        )
        SELECT won.user_id, won.average, won.solved, COALESCE(dnf.dnf, 0) AS dnf
        FROM won LEFT JOIN dnf USING (user_id)
        WHERE won.solved >= $4
        ORDER BY won.average, won.solved DESC, won.user_id
        LIMIT 10
        """,
        guild_id,
        today,
        rules.BOARD_DAYS,
        rules.BOARD_MIN_SOLVED,
    )
