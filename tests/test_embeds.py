import discord

from bot.cogs.logs.cog import make_embed


def test_embed_has_description():
    embed = make_embed(discord.Color.orange(), "Message Deleted", '#general — @user — "hello"')
    assert embed.description == '**Message Deleted** — #general — @user — "hello"'


def test_embed_has_correct_color():
    embed = make_embed(discord.Color.green(), "Member Joined", "@user")
    assert embed.color == discord.Color.green()


def test_embed_has_timestamp():
    embed = make_embed(discord.Color.red(), "Member Left", "@user")
    assert embed.timestamp is not None
