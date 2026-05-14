from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.music.db_sync import clear_state, poll_and_execute_commands, sync_queue, sync_state


def _make_track(title="Song", author="Artist", uri="https://y.be/x", length=180_000, artwork=None, requester=None):
    t = MagicMock()
    t.title = title
    t.author = author
    t.uri = uri
    t.length = length
    t.artwork = artwork
    t.extras = MagicMock()
    t.extras.requester = requester
    return t


def _make_player(
    guild_id=123, paused=False, volume=100, autoplay_enabled=False, position=5000, current=None, queue_tracks=None
):
    player = MagicMock()
    player.guild.id = guild_id
    player.paused = paused
    player.volume = volume
    player.autoplay_enabled = autoplay_enabled
    player.position = position
    player.current = current
    player.queue = MagicMock()
    player.queue.__iter__ = lambda self: iter(queue_tracks or [])
    return player


@pytest.mark.asyncio
async def test_sync_state_upserts_row():
    pool = AsyncMock()
    track = _make_track(requester=42)
    player = _make_player(guild_id=999, current=track, position=12000)
    await sync_state(pool, player)
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
    assert call_args[1] == 999  # guild_id
    assert call_args[2] == "Song"
    assert call_args[8] is False  # is_paused
    assert call_args[10] is False  # autoplay_enabled
    assert call_args[11] == 12000  # position_ms


@pytest.mark.asyncio
async def test_sync_state_clears_when_no_current():
    pool = AsyncMock()
    player = _make_player(guild_id=999, current=None)
    with patch("bot.cogs.music.db_sync.clear_state", new_callable=AsyncMock) as mock_clear:
        await sync_state(pool, player)
    mock_clear.assert_called_once_with(pool, 999)
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_sync_queue_writes_tracks():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    tracks = [_make_track(f"Song {i}") for i in range(3)]
    player = _make_player(guild_id=777, queue_tracks=tracks)
    await sync_queue(pool, player)
    conn.execute.assert_called_once()  # DELETE
    conn.executemany.assert_called_once()
    rows = conn.executemany.call_args[0][1]
    assert len(rows) == 3
    assert rows[0][1] == 1  # position starts at 1
    assert rows[0][2] == "Song 0"


@pytest.mark.asyncio
async def test_clear_state_deletes_both_tables():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    await clear_state(pool, 123)
    assert conn.execute.call_count == 2


@pytest.mark.asyncio
async def test_poll_executes_pause_command():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": 1, "command": "pause", "payload": None}]
    player = _make_player()
    player.pause = AsyncMock()
    with patch("bot.cogs.music.db_sync.sync_state", new_callable=AsyncMock):
        await poll_and_execute_commands(pool, player)
    player.pause.assert_called_once_with(True)
    pool.execute.assert_called_once()  # mark executed_at


@pytest.mark.asyncio
async def test_poll_executes_volume_command():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": 5, "command": "volume", "payload": {"volume": 60}}]
    player = _make_player()
    player.set_volume = AsyncMock()
    with patch("bot.cogs.music.db_sync.sync_state", new_callable=AsyncMock):
        await poll_and_execute_commands(pool, player)
    player.set_volume.assert_called_once_with(60)


@pytest.mark.asyncio
async def test_sync_queue_empty_does_not_insert():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False))
    )
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    player = _make_player(guild_id=555, queue_tracks=[])
    await sync_queue(pool, player)
    conn.execute.assert_called_once()  # DELETE only
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_poll_marks_executed_even_on_error():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": 9, "command": "pause", "payload": None}]
    player = _make_player()
    player.pause = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await poll_and_execute_commands(pool, player)
    pool.execute.assert_called_once()  # executed_at was still marked


@pytest.mark.asyncio
async def test_poll_unknown_command_marked_executed():
    pool = AsyncMock()
    pool.fetch.return_value = [{"id": 7, "command": "seek", "payload": None}]
    player = _make_player()
    await poll_and_execute_commands(pool, player)
    pool.execute.assert_called_once()  # still marked executed
