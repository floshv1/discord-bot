from __future__ import annotations

import discord
from discord import app_commands
from loguru import logger

GENERIC = "❌ Something went wrong on my end. It's been logged — try again in a moment."


def user_facing_message(error: BaseException) -> str:
    """A short, safe explanation for the member who ran the command.

    Never leaks an exception string: those carry DSNs, tokens and stack detail. Anything
    we don't recognise gets the generic apology, and the real error goes to the log.
    """
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original

    if isinstance(error, app_commands.MissingPermissions):
        return "❌ You don't have permission to use that command."
    if isinstance(error, app_commands.CheckFailure):
        return "❌ You can't use that command here."
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"⏳ Slow down — try again in {error.retry_after:.0f}s."
    if isinstance(error, app_commands.TransformerError):
        return "❌ That input wasn't valid. Check the command's options and try again."
    if isinstance(error, discord.Forbidden):
        # The bot, not the user, is missing a permission — almost always role hierarchy.
        return "❌ I'm missing the permissions (or the role position) to do that. Ask an admin to check my role."
    if isinstance(error, discord.NotFound):
        return "❌ That no longer exists — it may have been deleted."
    if isinstance(error, TimeoutError):
        # asyncpg raises this on `command_timeout` (asyncio.TimeoutError *is* the builtin on
        # 3.11+), which in practice means a query stuck behind someone else's row lock. Saying
        # so beats the generic apology: "try again" is genuinely the right advice here, and a
        # database that stopped answering is exactly what an admin needs to hear about.
        return "⏳ The database isn't responding right now. Try again in a moment."
    return GENERIC


async def handle_app_command_error(interaction: discord.Interaction, error: BaseException) -> None:
    """Reply to the member instead of leaving them on 'The application did not respond'."""
    command = interaction.command.qualified_name if interaction.command else "unknown"
    logger.opt(exception=error).error(f"Unhandled error in /{command}")

    message = user_facing_message(error)
    try:
        # A command that already deferred (or replied) can only be answered via followup.
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (discord.HTTPException, discord.ClientException, TimeoutError) as exc:
        # `discord.py` calls this from a bare `finally` and only catches AppCommandError above
        # it, so anything escaping here reaches nobody and the member is left on the spinner.
        # HTTPException alone wasn't enough: InteractionResponded (lost race against another
        # coroutine answering first) is a ClientException, and a timeout is neither.
        logger.warning(f"Could not deliver error message for /{command}: {exc}")
