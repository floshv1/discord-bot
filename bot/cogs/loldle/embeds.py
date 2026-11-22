"""LoLdle cards. The public ones never contain the champion's name: only colours."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import discord

from bot.cogs.loldle import rules, service
from bot.cogs.loldle.catalog import Champion

COLOR = discord.Color.gold()
MAX_PRIVATE_ROWS = 15
MAX_PUBLIC_ROWS = 8
LEGEND = "🟩 exact · 🟨 partiel · 🟥 faux · ⬆️⬇️ sorti plus tard / plus tôt"
COLUMNS = "Position · Classe · Portée · Ressource · Région · Année"


def _row(guess: Mapping) -> str:
    cells = service.cells_of(guess)
    clues = service.clues_of(guess)
    detail = " · ".join(f"{rules.EMOJI[c]} {v}" for c, v in zip(cells, clues, strict=True))
    return f"**{guess['number']}. {guess['name']}**\n{detail}"


def build_game_embed(day: dt.date, rows: Sequence[Mapping], result: Mapping | None, answer: Champion) -> discord.Embed:
    """The member's private card. It names the answer only once they have found it."""
    shown = rows[-MAX_PRIVATE_ROWS:]
    lines = [_row(g) for g in shown]
    if len(rows) > len(shown):
        lines.insert(0, f"*+{len(rows) - len(shown)} essais précédents*")
    body = "\n".join(lines) if lines else "Devine le champion du jour avec `/loldle champion:…`."
    if result:
        hints = f" + {result['hints']} 💡" if result["hints"] else ""
        title = f"🎯 C'était {answer.name} !"
        body = f"Trouvé en **{result['tries']}** essais{hints} · score **{result['score']}**\n\n{body}"
    else:
        title = f"🎯 LoLdle du {day:%d/%m}"
    embed = discord.Embed(title=title, description=body, color=COLOR)
    embed.set_footer(text=f"{COLUMNS}\n{LEGEND}")
    return embed


def build_public_embed(mention: str, result: Mapping, streak: int) -> discord.Embed:
    grid = result["grid"].split("\n")
    shown = grid[-MAX_PUBLIC_ROWS:]
    lines = list(shown)
    if len(grid) > len(shown):
        lines.insert(0, f"*… +{len(grid) - len(shown)} essais*")
    tries = result["tries"]
    bulbs = f" · {'💡' * result['hints']}" if result["hints"] else ""
    flame = f" · série : **{streak}** 🔥" if streak > 1 else ""
    header = f"🎯 {mention} a trouvé le LoLdle du jour en **{tries}** essai{'s' if tries > 1 else ''}{bulbs}{flame}"
    return discord.Embed(description=header + "\n" + "\n".join(lines), color=COLOR)


def build_board_embed(
    yesterday: str | None, solved_today: int, rows: Sequence[Mapping], streaks: Mapping[int, int]
) -> discord.Embed:
    """Yesterday's answer only: today's must never appear on a public message."""
    head = [f"Hier : **{yesterday}**"] if yesterday else []
    head.append(f"Aujourd'hui : **{solved_today}** {'ont trouvé' if solved_today > 1 else 'a trouvé'}")
    head.append("Joue avec `/loldle` : un champion par jour, changé à minuit.")
    embed = discord.Embed(title="🏆 LoLdle — 30 derniers jours", description="\n".join(head), color=COLOR)
    lines = []
    for rank, r in enumerate(rows, 1):
        flame = f" · 🔥 {streaks[r['user_id']]}" if streaks.get(r["user_id"], 0) > 1 else ""
        lines.append(
            f"**{rank}.** <@{r['user_id']}> moy. {r['average']:.1f} · {r['solved']} trouvés · {r['dnf']} DNF{flame}"
        )
    value = "\n".join(lines) or f"Il faut {rules.BOARD_MIN_SOLVED} parties trouvées pour apparaître ici."
    embed.add_field(name="Classement", value=value, inline=False)
    best = sorted(((n, u) for u, n in streaks.items() if n > 1), key=lambda x: (-x[0], x[1]))[:3]
    if best:
        embed.add_field(name="Meilleures séries", value=" · ".join(f"<@{u}> 🔥 {n}" for n, u in best), inline=False)
    return embed
