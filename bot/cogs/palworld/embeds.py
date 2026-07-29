from __future__ import annotations

import datetime

import discord

from bot.cogs.palworld.service import EMPTY_TIMEOUT, Player, ServerStatus, Status

TITLE = "🎮 Serveur Palworld"

# Boot measured on the machine: ~80 s from `docker start` to "Running Palworld dedicated
# server on :8211", of which ~40 s is SteamCMD checking for an update, plus the world
# load. Announcing "une minute trente" is honest; announcing "un instant" is not.
_BOOT_HINT = "environ 1 min 30"

_COLORS = {
    Status.ONLINE: discord.Color.green(),
    Status.BOOTING: discord.Color.gold(),
    Status.STOPPING: discord.Color.gold(),
    Status.OFFLINE: discord.Color.dark_grey(),
    Status.DOWN: discord.Color.red(),
    Status.UNKNOWN: discord.Color.dark_grey(),
}


def _players_block(players: list[Player]) -> str:
    return "\n".join(f"• **{p.name}** — niveau {p.level}" for p in players)


def _describe(status: ServerStatus, address: str | None) -> str:
    match status.status:
        case Status.ONLINE if status.players:
            count = status.player_count
            plural = "joueurs" if count > 1 else "joueur"
            lines = [f"🟢 **En ligne** — {count} {plural}", "", _players_block(status.players)]
        case Status.ONLINE:
            minutes = int(EMPTY_TIMEOUT // 60)
            lines = [
                "🟢 **En ligne** — personne connecté",
                "",
                f"S'il reste vide {minutes} minutes, il s'arrêtera tout seul.",
            ]
        case Status.BOOTING:
            lines = [
                "🟡 **Démarrage en cours…**",
                "",
                f"Compte {_BOOT_HINT} : le serveur vérifie les mises à jour, puis charge le monde. "
                "Cette carte se met à jour toute seule.",
            ]
        case Status.STOPPING:
            lines = ["🟡 **Arrêt en cours…**", "", "La partie est sauvegardée avant l'extinction."]
        case Status.OFFLINE:
            lines = ["🔴 **Hors ligne**", "", "Clique sur **Démarrer** — tout le monde peut le faire."]
        case Status.DOWN:
            lines = [
                "⛔ **La stack n'est pas simplement arrêtée**",
                "",
                "Elle a été détruite ou mise en pause : un bouton n'y peut rien. Il faut la redéployer depuis Komodo.",
            ]
        case _:
            lines = [
                "❔ **État inconnu**",
                "",
                "Komodo ne répond pas. Le serveur tourne peut-être très bien — c'est la télécommande qui est muette.",
            ]

    if address and status.status in (Status.ONLINE, Status.BOOTING):
        lines += ["", f"Adresse : `{address}`"]
    return "\n".join(lines)


def build_panel_embed(status: ServerStatus, address: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=TITLE,
        description=_describe(status, address),
        color=_COLORS[status.status],
        timestamp=datetime.datetime.now(datetime.UTC),
    )
    embed.set_footer(text="Dernière vérification")
    return embed


def build_unconfigured_embed() -> discord.Embed:
    """What the panel shows when the bot has no way to reach Komodo.

    A feature that silently does nothing is worse than one that says why: without the
    Komodo variables the buttons would fail one click at a time, and nobody would know
    whether the server or the bot was at fault.
    """
    return discord.Embed(
        title=TITLE,
        description=(
            "⚙️ **Pas encore branché.**\n\n"
            "Il manque les identifiants Komodo (`KOMODO_URL`, `KOMODO_API_KEY`, `KOMODO_API_SECRET`). "
            "Tant qu'ils ne sont pas dans l'environnement du bot, les boutons ne peuvent rien piloter."
        ),
        color=discord.Color.dark_grey(),
    )
