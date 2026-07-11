from __future__ import annotations

import datetime

import discord

# Discord's own limits. The modal is capped to match, so a valid modal can never produce
# an embed Discord will reject.
TITLE_LIMIT = 256
BODY_LIMIT = 4000  # the embed description allows 4096; the modal caps at 4000


def build_announcement_embed(title: str, body: str, author_name: str) -> discord.Embed:
    """The announcement itself.

    The body is the description verbatim — newlines and markdown intact, which is the whole
    point of collecting it through a modal rather than a slash-command argument.
    """
    embed = discord.Embed(
        title=title,
        description=body,
        color=discord.Color.blurple(),
        timestamp=datetime.datetime.now(datetime.UTC),
    )
    embed.set_footer(text=f"Annonce de {author_name}")
    return embed
