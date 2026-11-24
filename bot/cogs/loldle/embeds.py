"""LoLdle cards. The public ones never contain the champion's name: only colours."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import discord

from bot.cogs.loldle import rules, service
from bot.cogs.loldle.catalog import Champion

COLOR = discord.Color.gold()
MAX_PRIVATE_ROWS = 15
MAX_RECAP_MENTIONS = 40  # 2000 characters per message, a mention is ~22
LEGEND = "🟩 exact · 🟨 partiel · 🟥 faux · ⬆️⬇️ sorti plus tard / plus tôt"
COLUMNS = "Position · Classe · Portée · Ressource · Région · Genre · Dégâts · Espèce · Année"


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


def build_game_post(mention: str, tries: int, hints: int, solved: bool, finished: bool, streak: int) -> str:
    """The text of a member's public game message. Counts only: the picture carries the colours."""
    word = "essai" if tries == 1 else "essais"
    bulbs = f" · {'💡' * hints}" if hints else ""
    if solved:
        flame = f" · série : **{streak}** 🔥" if streak > 1 else ""
        return f"{mention} a trouvé le LoLdle en **{tries}** {word} 🎯{bulbs}{flame}"
    if finished:
        return f"{mention} n'a pas trouvé le LoLdle · {tries} {word}"
    return f"{mention} joue au LoLdle · **{tries}** {word}…{bulbs}"


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


def build_recap_text(yesterday: str | None, streak: int, results: Sequence[Mapping], dnf: Sequence[int]) -> str:
    """The midnight message: the group's run, then yesterday grouped by tries, best crowned.

    Names yesterday's champion only, and only if someone played it. Mentions render as names; the
    caller sends them with `AllowedMentions.none()`, so nobody is notified.
    """
    lines: list[str] = []
    if streak >= 2:
        lines.append(f"🔥 Le serveur est sur une série de {streak} jours !")
    if not (yesterday and (results or dnf)):
        lines.append("🎯 **Nouveau LoLdle disponible !**")
        return "\n".join(lines)
    lines.append(f"Voici les résultats d'hier (c'était **{yesterday}**) :")
    budget = MAX_RECAP_MENTIONS
    groups: dict[int, list[Mapping]] = {}
    for r in sorted(results, key=lambda r: (r["tries"], r.get("score", 0), r["user_id"])):
        groups.setdefault(r["tries"], []).append(r)
    best = min(groups) if groups else None
    for tries, members in groups.items():
        shown, budget = members[: max(budget, 0)], budget - len(members)
        names = " ".join(f"<@{m['user_id']}>" + (" 💡" if m["hints"] else "") for m in shown)
        more = f" +{len(members) - len(shown)}" if len(shown) < len(members) else ""
        crown = "👑 " if tries == best else ""
        lines.append(f"{crown}{tries} {'essai' if tries == 1 else 'essais'} : {names}{more}".rstrip())
    if dnf:
        shown_dnf = list(dnf[: max(budget, 0)])
        more = f" +{len(dnf) - len(shown_dnf)}" if len(shown_dnf) < len(dnf) else ""
        lines.append(f"Pas trouvé : {' '.join(f'<@{u}>' for u in shown_dnf)}{more}".rstrip())
    return "\n".join(lines)
