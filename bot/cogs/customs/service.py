"""Customs persistence. Every race that matters is settled by the database:

- one open custom per guild → a partial unique index, mapped to `AlreadyOpen`;
- two simultaneous signups → signup order (`created_at`, `id`) decides who is 10th;
- the 10th vote, the winner of a game, the reminder, closing → one conditional
  `UPDATE … WHERE <still in the old state> … RETURNING`, and the loser stands down.
"""

from __future__ import annotations

import datetime as dt
import json

import asyncpg

from bot.db.client import get_pool

OPEN = "('signup', 'teams')"


class AlreadyOpen(Exception):
    def __init__(self, custom_id: int) -> None:
        super().__init__(f"custom {custom_id} already open")
        self.custom_id = custom_id


# --- config ---------------------------------------------------------------


async def get_config(guild_id: int) -> asyncpg.Record | None:
    return await get_pool().fetchrow("SELECT channel_id, role_id FROM customs_config WHERE guild_id = $1", guild_id)


async def set_config(guild_id: int, channel_id: int, role_id: int | None) -> None:
    await get_pool().execute(
        """
        INSERT INTO customs_config (guild_id, channel_id, role_id) VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2, role_id = $3
        """,
        guild_id,
        channel_id,
        role_id,
    )


# --- the custom itself ----------------------------------------------------


async def create_custom(
    guild_id: int, organiser_id: int, channel_id: int, mode: str, start_at: dt.datetime | None, slots: list[dt.datetime]
) -> int:
    pool = get_pool()
    try:
        async with pool.acquire() as conn, conn.transaction():
            cid = await conn.fetchval(
                """
                INSERT INTO customs (guild_id, organiser_id, channel_id, mode, start_at)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                guild_id,
                organiser_id,
                channel_id,
                mode,
                start_at,
            )
            for slot in slots:
                await conn.execute("INSERT INTO custom_slots (custom_id, slot_at) VALUES ($1, $2)", cid, slot)
            return cid
    except asyncpg.UniqueViolationError:
        existing = await pool.fetchval(f"SELECT id FROM customs WHERE guild_id = $1 AND status IN {OPEN}", guild_id)
        raise AlreadyOpen(existing) from None


async def set_message(custom_id: int, message_id: int) -> None:
    await get_pool().execute("UPDATE customs SET message_id = $2 WHERE id = $1", custom_id, message_id)


async def get(custom_id: int) -> asyncpg.Record | None:
    return await get_pool().fetchrow("SELECT * FROM customs WHERE id = $1", custom_id)


async def get_open(guild_id: int) -> asyncpg.Record | None:
    return await get_pool().fetchrow(f"SELECT * FROM customs WHERE guild_id = $1 AND status IN {OPEN}", guild_id)


async def latest_id(guild_id: int) -> int | None:
    return await get_pool().fetchval("SELECT id FROM customs WHERE guild_id = $1 ORDER BY id DESC LIMIT 1", guild_id)


async def end(custom_id: int, status: str) -> bool:
    """`done` or `cancelled`. False when it was already closed (a second click, the ticker)."""
    row = await get_pool().fetchval(
        f"UPDATE customs SET status = $2 WHERE id = $1 AND status IN {OPEN} RETURNING id", custom_id, status
    )
    return row is not None


# --- signups --------------------------------------------------------------


async def signups(custom_id: int) -> list[asyncpg.Record]:
    return await get_pool().fetch(
        "SELECT id, user_id, person_id FROM custom_signups WHERE custom_id = $1 ORDER BY created_at, id", custom_id
    )


async def add_signup(custom_id: int, user_id: int | None, person_id: int) -> bool:
    """False when that member (or that player) is already signed up."""
    try:
        await get_pool().execute(
            "INSERT INTO custom_signups (custom_id, user_id, person_id) VALUES ($1, $2, $3)",
            custom_id,
            user_id,
            person_id,
        )
    except asyncpg.UniqueViolationError:
        return False
    return True


async def remove_signup(custom_id: int, user_id: int) -> bool:
    row = await get_pool().fetchval(
        "DELETE FROM custom_signups WHERE custom_id = $1 AND user_id = $2 RETURNING id", custom_id, user_id
    )
    return row is not None


# --- slot vote ------------------------------------------------------------


async def slots(custom_id: int) -> list[dt.datetime]:
    rows = await get_pool().fetch("SELECT slot_at FROM custom_slots WHERE custom_id = $1 ORDER BY slot_at", custom_id)
    return [r["slot_at"] for r in rows]


async def toggle_vote(custom_id: int, slot_at: dt.datetime, user_id: int) -> bool:
    """True = the vote is now cast, False = it was removed."""
    pool = get_pool()
    removed = await pool.fetchval(
        "DELETE FROM custom_votes WHERE custom_id = $1 AND slot_at = $2 AND user_id = $3 RETURNING user_id",
        custom_id,
        slot_at,
        user_id,
    )
    if removed is not None:
        return False
    await pool.execute(
        "INSERT INTO custom_votes (custom_id, slot_at, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        custom_id,
        slot_at,
        user_id,
    )
    return True


async def remove_votes(custom_id: int, user_id: int) -> None:
    await get_pool().execute("DELETE FROM custom_votes WHERE custom_id = $1 AND user_id = $2", custom_id, user_id)


async def votes(custom_id: int) -> dict[dt.datetime, list[int]]:
    rows = await get_pool().fetch(
        "SELECT slot_at, user_id FROM custom_votes WHERE custom_id = $1 ORDER BY voted_at", custom_id
    )
    out: dict[dt.datetime, list[int]] = {}
    for r in rows:
        out.setdefault(r["slot_at"], []).append(r["user_id"])
    return out


async def claim_vote_win(custom_id: int, slot_at: dt.datetime, persons_by_user: dict[int, int]) -> bool:
    """Turn a vote card into a fixed-time one for `slot_at`, signing up that slot's voters.

    The claim is the first statement: two 10th votes landing together both see ten, only one
    gets the row back, and the loser signs nobody up.
    """
    async with get_pool().acquire() as conn, conn.transaction():
        won = await conn.fetchval(
            """
            UPDATE customs SET mode = 'fixed', start_at = $2
            WHERE id = $1 AND mode = 'vote' AND status = 'signup'
            RETURNING id
            """,
            custom_id,
            slot_at,
        )
        if won is None:
            return False
        voters = await conn.fetch(
            "SELECT user_id FROM custom_votes WHERE custom_id = $1 AND slot_at = $2 ORDER BY voted_at",
            custom_id,
            slot_at,
        )
        for v in voters:
            person = persons_by_user.get(v["user_id"])
            if person is not None:
                await conn.execute(
                    """
                    INSERT INTO custom_signups (custom_id, user_id, person_id) VALUES ($1, $2, $3)
                    ON CONFLICT DO NOTHING
                    """,
                    custom_id,
                    v["user_id"],
                    person,
                )
        return True


# --- games of the evening -------------------------------------------------


async def games(custom_id: int) -> list[asyncpg.Record]:
    return await get_pool().fetch("SELECT * FROM custom_games WHERE custom_id = $1 ORDER BY number", custom_id)


async def add_game(custom_id: int, teams: dict) -> int | None:
    """Start the next game of the evening (status → teams). Returns its number, or None when a
    game is still undecided — a double click on Générer / Partie suivante must not open two."""
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            number = await conn.fetchval(
                """
                INSERT INTO custom_games (custom_id, number, teams)
                SELECT $1, COALESCE(MAX(number), 0) + 1, $2::jsonb FROM custom_games WHERE custom_id = $1
                HAVING NOT EXISTS (SELECT 1 FROM custom_games WHERE custom_id = $1 AND winner IS NULL)
                RETURNING number
                """,
                custom_id,
                json.dumps(teams),
            )
            if number is not None:
                await conn.execute(
                    "UPDATE customs SET status = 'teams' WHERE id = $1 AND status IN ('signup', 'teams')", custom_id
                )
            return number
    except asyncpg.UniqueViolationError:  # two first games inserted at once: the PK settles it
        return None


async def set_game_teams(custom_id: int, number: int, teams: dict) -> bool:
    """Regenerate/swap: only while that game has no winner yet."""
    row = await get_pool().fetchval(
        """
        UPDATE custom_games SET teams = $3::jsonb
        WHERE custom_id = $1 AND number = $2 AND winner IS NULL RETURNING number
        """,
        custom_id,
        number,
        json.dumps(teams),
    )
    return row is not None


async def claim_winner(custom_id: int, number: int, winner: str) -> bool:
    row = await get_pool().fetchval(
        """
        UPDATE custom_games SET winner = $3
        WHERE custom_id = $1 AND number = $2 AND winner IS NULL RETURNING number
        """,
        custom_id,
        number,
        winner,
    )
    return row is not None


async def correct_winner(custom_id: int, number: int, winner: str) -> asyncpg.Record | None:
    return await get_pool().fetchrow(
        "UPDATE custom_games SET winner = $3 WHERE custom_id = $1 AND number = $2 RETURNING *",
        custom_id,
        number,
        winner,
    )


async def set_lolamis_game_id(custom_id: int, number: int, game_id: int) -> None:
    await get_pool().execute(
        "UPDATE custom_games SET lolamis_game_id = $3 WHERE custom_id = $1 AND number = $2", custom_id, number, game_id
    )


# --- ticker ---------------------------------------------------------------


async def claim_reminders(now: dt.datetime) -> list[asyncpg.Record]:
    """Customs starting within 15 min, stamped in the same UPDATE so a restart can't re-ping."""
    return await get_pool().fetch(
        f"""
        UPDATE customs SET reminded_at = $1
        WHERE status IN {OPEN} AND mode = 'fixed' AND reminded_at IS NULL
          AND start_at IS NOT NULL AND start_at <= $1 + INTERVAL '15 minutes' AND start_at > $1
        RETURNING *
        """,
        now,
    )


async def expire_due(now: dt.datetime) -> list[asyncpg.Record]:
    return await get_pool().fetch(
        f"""
        UPDATE customs SET status = 'cancelled'
        WHERE status IN {OPEN}
          AND COALESCE(start_at + INTERVAL '6 hours', created_at + INTERVAL '24 hours') <= $1
        RETURNING *
        """,
        now,
    )
