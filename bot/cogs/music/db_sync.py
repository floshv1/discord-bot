from __future__ import annotations

import asyncpg
from loguru import logger

from bot.cogs.music.player import MusicPlayer


async def sync_state(pool: asyncpg.Pool, player: MusicPlayer) -> None:
    guild_id = player.guild.id
    if player.current is None:
        await clear_state(pool, guild_id)
        return
    track = player.current
    await pool.execute(
        """
        INSERT INTO music_state
            (guild_id, track_title, track_author, track_uri, track_length_ms,
             track_artwork, requester_id, is_paused, volume, autoplay_enabled,
             position_ms, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
        ON CONFLICT (guild_id) DO UPDATE SET
            track_title      = EXCLUDED.track_title,
            track_author     = EXCLUDED.track_author,
            track_uri        = EXCLUDED.track_uri,
            track_length_ms  = EXCLUDED.track_length_ms,
            track_artwork    = EXCLUDED.track_artwork,
            requester_id     = EXCLUDED.requester_id,
            is_paused        = EXCLUDED.is_paused,
            volume           = EXCLUDED.volume,
            autoplay_enabled = EXCLUDED.autoplay_enabled,
            position_ms      = EXCLUDED.position_ms,
            updated_at       = EXCLUDED.updated_at
        """,
        guild_id,
        track.title,
        track.author,
        track.uri,
        track.length,
        track.artwork,
        getattr(track.extras, "requester", None),
        player.paused,
        player.volume,
        player.autoplay_enabled,
        player.position,
    )


async def sync_queue(pool: asyncpg.Pool, player: MusicPlayer) -> None:
    guild_id = player.guild.id
    queue_tracks = list(player.queue)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM music_queue WHERE guild_id = $1", guild_id)
            if not queue_tracks:
                return
            await conn.executemany(
                """
                INSERT INTO music_queue
                    (guild_id, position, track_title, track_author, track_uri,
                     track_length_ms, track_artwork, requester_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                [
                    (
                        guild_id,
                        i + 1,
                        t.title,
                        t.author,
                        t.uri,
                        t.length,
                        t.artwork,
                        getattr(t.extras, "requester", None),
                    )
                    for i, t in enumerate(queue_tracks)
                ],
            )


async def clear_state(pool: asyncpg.Pool, guild_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM music_state WHERE guild_id = $1", guild_id)
            await conn.execute("DELETE FROM music_queue WHERE guild_id = $1", guild_id)


async def poll_and_execute_commands(pool: asyncpg.Pool, player: MusicPlayer) -> None:
    guild_id = player.guild.id
    rows = await pool.fetch(
        """
        SELECT id, command, payload FROM music_commands
        WHERE guild_id = $1 AND executed_at IS NULL
        ORDER BY created_at
        """,
        guild_id,
    )
    for row in rows:
        cmd = row["command"]
        payload: dict = row["payload"] or {}
        try:
            if cmd == "pause":
                await player.pause(True)
                await sync_state(pool, player)
            elif cmd == "resume":
                await player.pause(False)
                await sync_state(pool, player)
            elif cmd == "skip":
                await player.stop(force=True)
            elif cmd == "stop":
                player.queue.clear()
                try:
                    await player.disconnect()
                finally:
                    await clear_state(pool, guild_id)
            elif cmd == "volume":
                await player.set_volume(int(payload.get("volume", 100)))
                await sync_state(pool, player)
            else:
                logger.warning("Unknown music command: {!r} (id={})", cmd, row["id"])
        finally:
            await pool.execute(
                "UPDATE music_commands SET executed_at = NOW() WHERE id = $1",
                row["id"],
            )
