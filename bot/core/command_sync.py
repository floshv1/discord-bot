from loguru import logger

# Discord's "Entry Point" command (the Activity launcher) is a global command of this type.
# discord.py's AppCommandType doesn't know it, and a bulk overwrite of global commands would
# delete it (or be rejected), so we work on the raw HTTP routes and skip it.
ENTRY_POINT_TYPE = 4


async def purge_stale_global_commands(http, application_id: int) -> int:
    """Delete every global command except the Entry Point; return how many were deleted."""
    commands = await http.get_global_commands(application_id)
    deleted = 0
    for command in commands:
        if command.get("type") == ENTRY_POINT_TYPE:
            continue
        await http.delete_global_command(application_id, command["id"])
        deleted += 1
    if deleted:
        logger.info(f"Deleted {deleted} stale global command(s).")
    return deleted
