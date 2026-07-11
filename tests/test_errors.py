from types import SimpleNamespace

import discord
from discord import app_commands

from bot.core.errors import user_facing_message


class _FakeResponse:
    status = 403
    reason = "Forbidden"


def test_missing_permissions_explains_the_gate():
    msg = user_facing_message(app_commands.MissingPermissions(["kick_members"]))
    assert "permission" in msg.lower()


def test_forbidden_blames_the_bots_own_permissions():
    error = discord.Forbidden(_FakeResponse(), "Missing Permissions")
    msg = user_facing_message(error)
    assert "my" in msg.lower() or "role" in msg.lower()


def test_unwraps_command_invoke_error():
    inner = discord.Forbidden(_FakeResponse(), "Missing Permissions")
    command = SimpleNamespace(name="kick")
    wrapped = app_commands.CommandInvokeError(command=command, e=inner)  # type: ignore[arg-type]
    assert user_facing_message(wrapped) == user_facing_message(inner)


def test_unknown_error_gets_a_generic_apology_not_a_traceback():
    msg = user_facing_message(RuntimeError("connection to postgres refused: password=hunter2"))
    assert "hunter2" not in msg
    assert "postgres" not in msg
    assert msg.startswith("❌")
