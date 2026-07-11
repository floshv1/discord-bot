from __future__ import annotations

import discord

from bot.cogs.betting.service import CREATE_FEE

# Features that only exist once an admin has run the matching /setup. Advertising a
# command whose channel doesn't exist yet just sends members into a dead end, so each
# of these is listed only when it's actually configured.
CONFIGURABLE = [
    (
        "currency",
        "🪙 FloshCoins",
        [
            "`/balance` — check your wallet (or someone else's)",
            "`/claim` — collect your daily coins (resets at midnight, Paris time)",
        ],
    ),
    (
        "betting",
        "🎲 Betting",
        [
            f"`/bet create` — open a bet on anything, with two outcomes (costs {CREATE_FEE} 🪙)",
            "`/bet mine` — see the bets you currently have riding",
            "`/bet resolve` / `/bet cancel` — settle a bet you opened",
            "Press the buttons on a match card to stake. Payouts are pooled: "
            "winners split the losers' stakes, so the odds move until it locks.",
        ],
    ),
    ("queue", "🎮 Game queues", ["Use the panel to start or join a lobby."]),
    ("suggestions", "💡 Suggestions", ["Use the panel to suggest a feature or an improvement."]),
]

ALWAYS = [
    (
        "🎂 Birthdays",
        [
            "`/birthday set` — register yours so the server can wish you",
            "`/birthday list` — see who's coming up",
            "`/birthday delete` — remove yours",
        ],
    ),
    (
        "🎵 Music",
        [
            "`/play` — queue a track (join a voice channel first)",
            "`/skip`, `/pause`, `/list`, `/nowplaying`, `/lyrics`",
        ],
    ),
]

MODERATION = [
    "`/setup status` — see which features are configured, and which need attention",
    "`/kick` `/ban` `/unban` `/timeout` `/warn` `/history` — moderation, all logged",
    "`/clear` — bulk-delete messages",
    "`/bet resolve` / `/bet cancel` — settle *any* stuck market, not just your own",
]


def build_help_embed(channels: dict[str, str | None], is_mod: bool) -> discord.Embed:
    """The bot's only front door. ``channels`` maps a feature key to its channel mention.

    A feature whose key is missing or ``None`` is not set up, and is left out entirely.
    """
    embed = discord.Embed(
        title="🤖 What I can do",
        description="Everything below is a slash command — type `/` and Discord will autocomplete.",
        color=discord.Color.blurple(),
    )

    for key, title, lines in CONFIGURABLE:
        mention = channels.get(key)
        if not mention:
            continue
        embed.add_field(name=title, value="\n".join([*lines, f"→ {mention}"]), inline=False)

    for title, lines in ALWAYS:
        embed.add_field(name=title, value="\n".join(lines), inline=False)

    if is_mod:
        embed.add_field(name="🛠️ Moderator", value="\n".join(MODERATION), inline=False)

    return embed
