from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bot.core.command_sync import purge_stale_global_commands

ENTRY_POINT = {"id": "1", "type": 4, "name": "launch"}
STALE = {"id": "2", "type": 1, "name": "old"}


async def test_keeps_entry_point_and_deletes_the_rest():
    http = MagicMock(
        get_global_commands=AsyncMock(return_value=[ENTRY_POINT, STALE]),
        delete_global_command=AsyncMock(),
    )
    assert await purge_stale_global_commands(http, 99) == 1
    http.delete_global_command.assert_awaited_once_with(99, "2")


async def test_nothing_to_delete_costs_no_delete():
    http = MagicMock(
        get_global_commands=AsyncMock(return_value=[ENTRY_POINT]),
        delete_global_command=AsyncMock(),
    )
    assert await purge_stale_global_commands(http, 99) == 0
    http.delete_global_command.assert_not_awaited()


def test_boot_never_bulk_overwrites_global_commands():
    source = Path("bot/core/bot.py").read_text(encoding="utf-8")
    assert "await self.tree.sync()" not in source
