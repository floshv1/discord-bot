"""LoLdle persistence. Races are settled in SQL:

- the day's champion: first `INSERT … ON CONFLICT DO NOTHING` wins, everyone re-reads the row;
- one member's guesses (and hint purchases): serialised by a per-member advisory lock;
- the win: a single `INSERT … ON CONFLICT DO NOTHING RETURNING`, so it is announced once.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from dataclasses import dataclass, replace

import asyncpg

from bot.cogs.currency import service as currency
from bot.cogs.loldle import catalog, rules
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


CHANNEL = "loldle_events"


async def notify(conn, guild_id: int, day: dt.date, user_id: int, kind: str) -> None:
    """Tell listeners something changed. Runs on the transaction's `conn`: delivered at commit only.

    The payload never names a champion (it travels to the Activity).
    """
    payload = json.dumps({"guild_id": guild_id, "day": day.isoformat(), "user_id": user_id, "kind": kind})
    await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)


def resolve_guess(text: str, answer: Champion) -> Champion | None:
    """The stored answer first: a refreshed catalog may no longer contain it."""
    if catalog.normalise(text) in (catalog.normalise(answer.id), catalog.normalise(answer.name)):
        return answer
    return catalog.find(text)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def answer_of(day_row) -> Champion:
    """The stored answer, never the catalog: a refreshed file cannot change a day in progress."""
    answer = Champion.from_json(_json(day_row["answer"]))
    if not answer.gender:  # stored before gender/damage/species existed: only those are completed
        current = catalog.load().get(answer.id)
        if current is not None:
            answer = replace(answer, gender=current.gender, damage=current.damage, species=current.species)
    return answer


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
            await notify(conn, guild_id, day, user_id, "guess")
            return GuessOutcome("ok", cells, number)
        rows = await conn.fetch(
            f"SELECT cells FROM loldle_guesses WHERE {where} ORDER BY number", guild_id, day, user_id
        )
        grid = "\n".join(rules.grid_row(cells_of(r)) for r in rows)
        used = await conn.fetchval(f"SELECT COUNT(*) FROM loldle_hints WHERE {where}", guild_id, day, user_id)
        won = await conn.fetchrow(
            """
            INSERT INTO loldle_results (guild_id, day, user_id, tries, hints, score, grid, announce_pending)
            VALUES ($1, $2::date, $3, $4, $5, $6, $7, TRUE)
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
        if won is not None:  # a lost race has no pending row: nothing to announce
            await notify(conn, guild_id, day, user_id, "win")
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
        await notify(conn, guild_id, day, user_id, "hint")
        return BuyOutcome("bought", new_balance)


# --- announcements and live view ------------------------------------------


async def claim_announcements(guild_id: int) -> list[asyncpg.Record]:
    """Wins not yet announced, claimed atomically: two claimers never get the same row."""
    return await get_pool().fetch(
        """
        UPDATE loldle_results SET announce_pending = FALSE
        WHERE (guild_id, day, user_id) IN (
            SELECT guild_id, day, user_id FROM loldle_results
            WHERE guild_id = $1 AND announce_pending
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        guild_id,
    )


async def upsert_player(guild_id: int, user_id: int, display_name: str, avatar_hash: str | None) -> None:
    await get_pool().execute(
        """
        INSERT INTO loldle_players (guild_id, user_id, display_name, avatar_hash)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, user_id) DO UPDATE
        SET display_name = EXCLUDED.display_name, avatar_hash = EXCLUDED.avatar_hash, updated_at = NOW()
        """,
        guild_id,
        user_id,
        display_name,
        avatar_hash,
    )


async def live_rows(guild_id: int, day: dt.date) -> list[asyncpg.Record]:
    """Colours only, never names: who is playing today, how far, and their latest row of cells."""
    return await get_pool().fetch(
        """
        SELECT g.user_id,
               COALESCE(p.display_name, g.user_id::text) AS display_name,
               p.avatar_hash,
               g.tries,
               EXISTS (
                   SELECT 1 FROM loldle_results r
                   WHERE r.guild_id = $1 AND r.day = $2::date AND r.user_id = g.user_id
               ) AS solved,
               (SELECT l.cells FROM loldle_guesses l
                WHERE l.guild_id = $1 AND l.day = $2::date AND l.user_id = g.user_id
                ORDER BY l.number DESC LIMIT 1) AS last_cells
        FROM (
            SELECT user_id, COUNT(*)::int AS tries FROM loldle_guesses
            WHERE guild_id = $1 AND day = $2::date GROUP BY user_id
        ) g
        LEFT JOIN loldle_players p ON p.guild_id = $1 AND p.user_id = g.user_id
        ORDER BY g.user_id
        """,
        guild_id,
        day,
    )


async def player_names(guild_id: int, user_ids: list[int]) -> list[asyncpg.Record]:
    """Name and avatar of known players, for members who have the Activity open but have not guessed."""
    if not user_ids:
        return []
    return await get_pool().fetch(
        """
        SELECT user_id, display_name, avatar_hash FROM loldle_players
        WHERE guild_id = $1 AND user_id = ANY($2::bigint[])
        """,
        guild_id,
        user_ids,
    )


# --- daily posts ----------------------------------------------------------


async def claim_recap(guild_id: int, day: dt.date) -> str | None:
    """Claim the day's recap: `send`, `seed`, or None when already claimed.

    The guild's very first claim only seeds (and stamps the ping too): deploying or running
    /setup at 3pm must not announce a reset that did not just happen.
    """
    row = await get_pool().fetchrow(
        """
        UPDATE loldle_days d
        SET recap_at = NOW(), ping_at = CASE WHEN s.seeding THEN NOW() ELSE d.ping_at END
        FROM (
            SELECT NOT EXISTS (SELECT 1 FROM loldle_days WHERE guild_id = $1 AND recap_at IS NOT NULL) AS seeding
        ) s
        WHERE d.guild_id = $1 AND d.day = $2::date AND d.recap_at IS NULL
        RETURNING s.seeding
        """,
        guild_id,
        day,
    )
    if row is None:
        return None
    return "seed" if row["seeding"] else "send"


async def release_recap(guild_id: int, day: dt.date) -> None:
    await get_pool().execute(
        "UPDATE loldle_days SET recap_at = NULL WHERE guild_id = $1 AND day = $2::date", guild_id, day
    )


async def claim_ping(guild_id: int, day: dt.date) -> bool:
    row = await get_pool().fetchval(
        """
        UPDATE loldle_days SET ping_at = NOW()
        WHERE guild_id = $1 AND day = $2::date AND ping_at IS NULL AND recap_at IS NOT NULL
        RETURNING day
        """,
        guild_id,
        day,
    )
    return row is not None


async def release_ping(guild_id: int, day: dt.date) -> None:
    await get_pool().execute(
        "UPDATE loldle_days SET ping_at = NULL WHERE guild_id = $1 AND day = $2::date", guild_id, day
    )


async def set_ping_message(guild_id: int, day: dt.date, message_id: int | None) -> None:
    await get_pool().execute(
        "UPDATE loldle_days SET ping_message_id = $3 WHERE guild_id = $1 AND day = $2::date",
        guild_id,
        day,
        message_id,
    )


async def previous_ping(guild_id: int, day: dt.date) -> tuple[dt.date, int] | None:
    row = await get_pool().fetchrow(
        """
        SELECT day, ping_message_id FROM loldle_days
        WHERE guild_id = $1 AND day < $2::date AND ping_message_id IS NOT NULL
        ORDER BY day DESC LIMIT 1
        """,
        guild_id,
        day,
    )
    return (row["day"], row["ping_message_id"]) if row else None


async def ping_targets(guild_id: int, day: dt.date) -> list[int]:
    """Who the 09:00 ping is for: played yesterday (won or DNF), and hasn't already played today."""
    rows = await get_pool().fetch(
        """
        SELECT DISTINCT y.user_id FROM loldle_guesses y
        WHERE y.guild_id = $1 AND y.day = $2::date - 1
          AND NOT EXISTS (
              SELECT 1 FROM loldle_guesses t
              WHERE t.guild_id = y.guild_id AND t.day = $2::date AND t.user_id = y.user_id
          )
        ORDER BY y.user_id
        """,
        guild_id,
        day,
    )
    return [r["user_id"] for r in rows]


async def day_results(guild_id: int, day: dt.date) -> list[asyncpg.Record]:
    return await get_pool().fetch(
        """
        SELECT user_id, tries, hints, score FROM loldle_results
        WHERE guild_id = $1 AND day = $2::date
        ORDER BY score, solved_at, user_id
        """,
        guild_id,
        day,
    )


async def day_dnf(guild_id: int, day: dt.date) -> list[int]:
    rows = await get_pool().fetch(
        """
        SELECT DISTINCT g.user_id FROM loldle_guesses g
        WHERE g.guild_id = $1 AND g.day = $2::date
          AND NOT EXISTS (
              SELECT 1 FROM loldle_results r
              WHERE r.guild_id = g.guild_id AND r.day = g.day AND r.user_id = g.user_id
          )
        ORDER BY g.user_id
        """,
        guild_id,
        day,
    )
    return [r["user_id"] for r in rows]


# --- per-member public posts ---------------------------------------------


async def claim_post(guild_id: int, day: dt.date, user_id: int, channel_id: int) -> bool:
    """True only for the call that created the row: it alone sends the message."""
    created = await get_pool().fetchval(
        """
        INSERT INTO loldle_posts (guild_id, day, user_id, channel_id) VALUES ($1, $2::date, $3, $4)
        ON CONFLICT (guild_id, day, user_id) DO NOTHING
        RETURNING TRUE
        """,
        guild_id,
        day,
        user_id,
        channel_id,
    )
    return bool(created)


async def set_post_message(guild_id: int, day: dt.date, user_id: int, message_id: int | None) -> None:
    await get_pool().execute(
        """
        UPDATE loldle_posts SET message_id = $4, updated_at = NOW()
        WHERE guild_id = $1 AND day = $2::date AND user_id = $3
        """,
        guild_id,
        day,
        user_id,
        message_id,
    )


async def set_post_channel(guild_id: int, day: dt.date, user_id: int, channel_id: int) -> None:
    await get_pool().execute(
        """
        UPDATE loldle_posts SET channel_id = $4, message_id = NULL, updated_at = NOW()
        WHERE guild_id = $1 AND day = $2::date AND user_id = $3
        """,
        guild_id,
        day,
        user_id,
        channel_id,
    )


async def get_post(guild_id: int, day: dt.date, user_id: int) -> asyncpg.Record | None:
    return await get_pool().fetchrow(
        """
        SELECT channel_id, message_id, finished FROM loldle_posts
        WHERE guild_id = $1 AND day = $2::date AND user_id = $3
        """,
        guild_id,
        day,
        user_id,
    )


async def finish_post(guild_id: int, day: dt.date, user_id: int) -> None:
    await get_pool().execute(
        """
        UPDATE loldle_posts SET finished = TRUE, updated_at = NOW()
        WHERE guild_id = $1 AND day = $2::date AND user_id = $3
        """,
        guild_id,
        day,
        user_id,
    )


async def unfinished_posts(guild_id: int, day: dt.date) -> list[asyncpg.Record]:
    return await get_pool().fetch(
        """
        SELECT user_id, channel_id, message_id FROM loldle_posts
        WHERE guild_id = $1 AND day = $2::date AND NOT finished
        ORDER BY user_id
        """,
        guild_id,
        day,
    )


async def day_grids(guild_id: int, day: dt.date) -> dict[int, list[list[str]]]:
    """Every member's colours for the day, guess by guess. Colours only: no champion leaves here."""
    rows = await get_pool().fetch(
        """
        SELECT user_id, cells FROM loldle_guesses
        WHERE guild_id = $1 AND day = $2::date
        ORDER BY user_id, number
        """,
        guild_id,
        day,
    )
    grids: dict[int, list[list[str]]] = {}
    for r in rows:
        grids.setdefault(r["user_id"], []).append(cells_of(r))
    return grids


async def group_solved_days(guild_id: int) -> set[dt.date]:
    rows = await get_pool().fetch("SELECT DISTINCT day FROM loldle_results WHERE guild_id = $1", guild_id)
    return {r["day"] for r in rows}


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
