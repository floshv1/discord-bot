from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from bot.db.client import get_pool

PARIS_TZ = ZoneInfo("Europe/Paris")


def parse_start_time(time_str: str) -> datetime.datetime | None:
    """Parse a Paris-local HH:MM (or HH:MM:SS); roll to tomorrow if already past. Returns UTC."""
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.datetime.strptime(time_str, fmt).time()
        except ValueError:
            continue
        now_paris = datetime.datetime.now(tz=PARIS_TZ)
        dt = datetime.datetime.combine(now_paris.date(), t, tzinfo=PARIS_TZ)
        if dt <= now_paris:
            dt += datetime.timedelta(days=1)
        return dt.astimezone(datetime.UTC)
    return None


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


async def list_presets(guild_id: int):
    """The presets that get a button on the shared panel. Ad-hoc ones are excluded."""
    pool = get_pool()
    return await pool.fetch(
        "SELECT id, name, player_count FROM game_presets WHERE guild_id = $1 AND on_panel ORDER BY name",
        guild_id,
    )


async def get_preset(preset_id: int):
    pool = get_pool()
    return await pool.fetchrow(
        "SELECT id, name, player_count FROM game_presets WHERE id = $1",
        preset_id,
    )


async def upsert_preset(guild_id: int, name: str, player_count: int, on_panel: bool = True):
    """Insert the preset if missing, otherwise return the existing row unchanged.

    ``on_panel=False`` makes it ad-hoc: usable for a one-off queue, but no button on the
    shared panel. Promotion is one-way — a mod running /queue add on an existing ad-hoc
    preset gives it a button, but a member's ad-hoc queue never takes one away.
    """
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO game_presets (guild_id, name, player_count, on_panel)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id, name) DO UPDATE
          SET on_panel = game_presets.on_panel OR EXCLUDED.on_panel
        """,
        guild_id,
        name,
        player_count,
        on_panel,
    )
    return await pool.fetchrow(
        "SELECT id, name, player_count FROM game_presets WHERE guild_id = $1 AND name = $2",
        guild_id,
        name,
    )


def can_close_queue(queue, user_id: int, is_mod: bool) -> bool:
    """Only the host (or a mod) may close a queue.

    This used to allow anyone who had *joined*, which let a drive-by joiner kill someone
    else's lobby. Matches the host-only gate already on the start-time button.
    """
    return is_mod or queue["creator_user_id"] == user_id


# ---------------------------------------------------------------------------
# Queue lifecycle
# ---------------------------------------------------------------------------


async def fetch_queue_state(queue_id: int):
    """Return (queue, members). player_count is the per-queue size or the preset default."""
    pool = get_pool()
    queue = await pool.fetchrow(
        """
        SELECT gq.id, gq.status, gq.start_time, gq.creator_user_id, gq.channel_id, gq.message_id,
               gp.name, COALESCE(gq.player_count, gp.player_count) AS player_count
        FROM game_queues gq
        JOIN game_presets gp ON gp.id = gq.preset_id
        WHERE gq.id = $1
        """,
        queue_id,
    )
    members = (
        await pool.fetch(
            """
            SELECT user_id, in_lane, cant_attend
            FROM queue_members
            WHERE queue_id = $1
            ORDER BY cant_attend, in_lane, joined_at
            """,
            queue_id,
        )
        if queue
        else []
    )
    return queue, members


async def create_queue(
    guild_id: int,
    channel_id: int,
    preset_id: int,
    creator_user_id: int,
    player_count: int | None,
    start_time: datetime.datetime | None,
) -> int:
    """Create a queue and add the creator as the first main member. Returns the queue id."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO game_queues (guild_id, channel_id, preset_id, player_count, start_time, creator_user_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        guild_id,
        channel_id,
        preset_id,
        player_count,
        start_time,
        creator_user_id,
    )
    queue_id = row["id"]
    await pool.execute(
        "INSERT INTO queue_members (queue_id, user_id, in_lane) VALUES ($1, $2, FALSE)",
        queue_id,
        creator_user_id,
    )
    return queue_id


async def set_queue_message(queue_id: int, message_id: int) -> None:
    pool = get_pool()
    await pool.execute("UPDATE game_queues SET message_id = $1 WHERE id = $2", message_id, queue_id)


async def join_queue(queue_id: int, user_id: int) -> str:
    """Add a user to the queue. Returns one of: 'ok', 'closed', 'already_in'."""
    pool = get_pool()
    queue, members = await fetch_queue_state(queue_id)
    if not queue or queue["status"] not in ("open", "filled"):
        return "closed"

    existing = next((m for m in members if m["user_id"] == user_id), None)
    if existing and not existing["cant_attend"]:
        return "already_in"

    active_main = [m for m in members if not m["in_lane"] and not m["cant_attend"]]
    in_lane = len(active_main) >= queue["player_count"]

    if existing and existing["cant_attend"]:
        await pool.execute(
            "UPDATE queue_members SET cant_attend = FALSE, in_lane = $1 WHERE queue_id = $2 AND user_id = $3",
            in_lane,
            queue_id,
            user_id,
        )
    else:
        await pool.execute(
            "INSERT INTO queue_members (queue_id, user_id, in_lane) VALUES ($1, $2, $3)",
            queue_id,
            user_id,
            in_lane,
        )

    await _refresh_fill_status(queue_id)
    await touch_activity(queue_id)
    return "ok"


async def mark_cant_attend(queue_id: int, user_id: int) -> tuple[str, int | None]:
    """Mark a user as can't-attend. Returns (result, promoted_user_id).

    result is 'ok' or 'not_in'. promoted_user_id is set when a waiting player was moved up.
    """
    pool = get_pool()
    _, members = await fetch_queue_state(queue_id)
    member = next((m for m in members if m["user_id"] == user_id), None)
    if not member or member["cant_attend"]:
        return "not_in", None

    was_in_main = not member["in_lane"]
    await pool.execute(
        "UPDATE queue_members SET cant_attend = TRUE WHERE queue_id = $1 AND user_id = $2",
        queue_id,
        user_id,
    )

    promoted_user_id: int | None = None
    if was_in_main:
        await pool.execute(
            "UPDATE game_queues SET status = 'open', filled_at = NULL WHERE id = $1 AND status = 'filled'",
            queue_id,
        )
        promoted = await pool.fetchrow(
            """
            UPDATE queue_members SET in_lane = FALSE
            WHERE queue_id = $1 AND user_id = (
                SELECT user_id FROM queue_members
                WHERE queue_id = $1 AND in_lane = TRUE AND cant_attend = FALSE
                ORDER BY joined_at LIMIT 1
            )
            RETURNING user_id
            """,
            queue_id,
        )
        if promoted:
            promoted_user_id = promoted["user_id"]

    await _refresh_fill_status(queue_id)
    await touch_activity(queue_id)
    return "ok", promoted_user_id


async def _refresh_fill_status(queue_id: int) -> None:
    """Flip a queue between 'open' and 'filled' based on the current main-member count."""
    pool = get_pool()
    queue, members = await fetch_queue_state(queue_id)
    if not queue or queue["status"] not in ("open", "filled"):
        return
    main_count = len([m for m in members if not m["in_lane"] and not m["cant_attend"]])
    if main_count >= queue["player_count"] and queue["status"] == "open":
        await pool.execute(
            "UPDATE game_queues SET status = 'filled', filled_at = NOW() WHERE id = $1 AND status = 'open'",
            queue_id,
        )


async def touch_activity(queue_id: int) -> None:
    pool = get_pool()
    await pool.execute("UPDATE game_queues SET last_activity_at = NOW() WHERE id = $1", queue_id)


async def is_member(queue_id: int, user_id: int) -> bool:
    pool = get_pool()
    found = await pool.fetchval(
        "SELECT 1 FROM queue_members WHERE queue_id = $1 AND user_id = $2",
        queue_id,
        user_id,
    )
    return found is not None


async def close_queue(queue_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE game_queues SET status = 'done' WHERE id = $1 AND status IN ('open', 'filled')",
        queue_id,
    )


async def set_start_time(queue_id: int, start_time: datetime.datetime) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE game_queues SET start_time = $1, reminder_sent = FALSE WHERE id = $2",
        start_time,
        queue_id,
    )


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


async def get_subscriptions(guild_id: int, user_id: int) -> set[int]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT preset_id FROM game_subscriptions WHERE guild_id = $1 AND user_id = $2",
        guild_id,
        user_id,
    )
    return {r["preset_id"] for r in rows}


async def set_subscriptions(guild_id: int, user_id: int, preset_ids: set[int]) -> None:
    """Replace a user's subscriptions with exactly the given preset ids."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM game_subscriptions WHERE guild_id = $1 AND user_id = $2",
                guild_id,
                user_id,
            )
            if preset_ids:
                await conn.executemany(
                    "INSERT INTO game_subscriptions (guild_id, user_id, preset_id) VALUES ($1, $2, $3)",
                    [(guild_id, user_id, pid) for pid in preset_ids],
                )


async def get_subscribers(guild_id: int, preset_id: int, exclude_user_id: int) -> list[int]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT user_id FROM game_subscriptions
        WHERE guild_id = $1 AND preset_id = $2 AND user_id <> $3
        """,
        guild_id,
        preset_id,
        exclude_user_id,
    )
    return [r["user_id"] for r in rows]


# ---------------------------------------------------------------------------
# Panel config
# ---------------------------------------------------------------------------


async def get_queue_config(guild_id: int):
    pool = get_pool()
    return await pool.fetchrow(
        "SELECT guild_id, channel_id, panel_message_id FROM queue_config WHERE guild_id = $1",
        guild_id,
    )


async def set_queue_config(guild_id: int, channel_id: int, panel_message_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO queue_config (guild_id, channel_id, panel_message_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id) DO UPDATE
          SET channel_id = EXCLUDED.channel_id,
              panel_message_id = EXCLUDED.panel_message_id
        """,
        guild_id,
        channel_id,
        panel_message_id,
    )
