from __future__ import annotations

import asyncpg

from bot.db.client import get_pool

# What the pinned history card shows, and how much we keep behind it. The card is capped
# because an embed description has 4096 characters; the table is capped because a server
# that plays music all day would otherwise grow a row per track forever, and nobody has
# ever wanted to read the 3000th-most-recent song.
HISTORY_DISPLAY = 15
HISTORY_KEEP = 200


async def get_config(guild_id: int) -> asyncpg.Record | None:
    """The music channel and its two pinned message ids, or ``None`` if never set up.

    ``None`` is a supported state, not an error: without a config the cog falls back to
    posting a transient card in whatever channel `/play` was typed in, exactly as it did
    before this table existed.
    """
    pool = get_pool()
    return await pool.fetchrow(
        """
        SELECT guild_id, channel_id, now_playing_message_id, history_message_id
        FROM music_config
        WHERE guild_id = $1
        """,
        guild_id,
    )


async def set_config(
    guild_id: int,
    channel_id: int,
    now_playing_message_id: int,
    history_message_id: int,
) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO music_config (guild_id, channel_id, now_playing_message_id, history_message_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id) DO UPDATE
          SET channel_id             = EXCLUDED.channel_id,
              now_playing_message_id = EXCLUDED.now_playing_message_id,
              history_message_id     = EXCLUDED.history_message_id
        """,
        guild_id,
        channel_id,
        now_playing_message_id,
        history_message_id,
    )


async def get_configured_guild_ids() -> list[int]:
    pool = get_pool()
    rows = await pool.fetch("SELECT guild_id FROM music_config")
    return [row["guild_id"] for row in rows]


async def record_play(
    guild_id: int,
    *,
    title: str,
    author: str | None,
    uri: str | None,
    artwork: str | None,
    requester_id: int | None,
) -> None:
    """Append one track to the history, then trim the tail.

    ``requester_id`` is NULL for an autoplay pick — that distinction is the whole reason the
    history card can say who asked for what. The prune runs in the same transaction as the
    insert so the table can't drift above the cap if a later call fails.
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO music_history
                (guild_id, track_title, track_author, track_uri, track_artwork, requester_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            guild_id,
            title,
            author,
            uri,
            artwork,
            requester_id,
        )
        await conn.execute(
            """
            DELETE FROM music_history
            WHERE guild_id = $1
              AND id NOT IN (
                  SELECT id FROM music_history
                  WHERE guild_id = $1
                  ORDER BY played_at DESC, id DESC
                  LIMIT $2
              )
            """,
            guild_id,
            HISTORY_KEEP,
        )


async def get_history(guild_id: int, limit: int = HISTORY_DISPLAY) -> list[asyncpg.Record]:
    """The most recently played tracks, newest first."""
    pool = get_pool()
    return await pool.fetch(
        """
        SELECT track_title, track_author, track_uri, requester_id, played_at
        FROM music_history
        WHERE guild_id = $1
        ORDER BY played_at DESC, id DESC
        LIMIT $2
        """,
        guild_id,
        limit,
    )


async def clear_history(guild_id: int) -> None:
    pool = get_pool()
    await pool.execute("DELETE FROM music_history WHERE guild_id = $1", guild_id)
