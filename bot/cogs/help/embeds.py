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
    (
        "palworld",
        "🌴 Serveur Palworld",
        [
            "Le panneau épinglé ouvre le serveur : **n'importe qui** peut cliquer sur **Démarrer**, "
            "il est prêt en une minute trente.",
            "Il s'éteint tout seul quand plus personne n'est connecté — le temps ne passe plus dans "
            "le monde, donc vos pals ne meurent pas de faim pendant la nuit.",
        ],
    ),
    ("suggestions", "💡 Suggestions", ["Use the panel to suggest a feature or an improvement."]),
    (
        "tribunal",
        "⚖️ Tribunal",
        [
            "Réprimandé ? Clique sur **« Plaider ma cause »** sous ta carte pour te défendre. "
            "Tant que tu n'as pas plaidé, le jury ne peut pas voter.",
            "Les juges tranchent ensuite coupable ou non coupable — **un acquittement te libère "
            "sur-le-champ**, sans attendre la fin de ta peine.",
        ],
    ),
]

# Features that work whether or not an admin has run a /setup. The first item is an
# optional feature key: music commands work everywhere, but once `/setup music` has run
# all the output lands in one channel, and the member needs to be told which.
ALWAYS: list[tuple[str | None, str, list[str]]] = [
    (
        None,
        "🎂 Birthdays",
        [
            "`/birthday set` — register yours so the server can wish you",
            "`/birthday list` — see who's coming up",
            "`/birthday delete` — remove yours",
        ],
    ),
    (
        "music",
        "🎵 Music",
        [
            "`/play` — queue a track (join a voice channel first)",
            "`/skip`, `/pause`, `/list`, `/nowplaying`, `/lyrics`",
        ],
    ),
]

MODERATION = [
    "`/setup status` — see which features are configured, and which need attention",
    "`/announce` — publish a formatted announcement (opens a multi-line editor)",
    "`/kick` `/ban` `/unban` `/timeout` `/warn` `/history` — moderation, all logged",
    "`/reprimand` — send someone to the goulag; opens a trial if a jury role is configured",
    "`/pardon` — lift a reprimand early (and drop the trial)",
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

    for key, title, lines in ALWAYS:
        mention = channels.get(key) if key else None
        value = "\n".join([*lines, f"→ {mention}"]) if mention else "\n".join(lines)
        embed.add_field(name=title, value=value, inline=False)

    if is_mod:
        embed.add_field(name="🛠️ Moderator", value="\n".join(MODERATION), inline=False)

    return embed
