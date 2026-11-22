"""Customs cards: the signup card (fixed time or slot vote) and the teams card of the evening."""

from __future__ import annotations

import datetime as dt

import discord

from bot.cogs.customs.lolamis import Teams
from bot.cogs.customs.rules import PARIS, PLAYERS

LANES = ("Top", "Jgl", "Mid", "ADC", "Sup")
BLUE, RED = discord.Color.blue(), discord.Color.red()


def hhmm(t: dt.datetime) -> str:
    return t.astimezone(PARIS).strftime("%Hh%M").replace("h00", "h")


def _bar(n: int) -> str:
    filled = min(n, PLAYERS)
    return "▓" * filled + "░" * (PLAYERS - filled)


def build_signup_embed(
    custom, *, players: list[str], waiting: list[str], votes: list[tuple[dt.datetime, int]] | None
) -> discord.Embed:
    if custom["mode"] == "vote":
        title = "⚔️ Custom 5v5 — quand êtes-vous dispo ?"
        desc = "Coche **tous** les créneaux où tu peux jouer. Le premier à 10 l'emporte."
    else:
        title = f"⚔️ Custom 5v5 — {hhmm(custom['start_at'])}"
        desc = "Clique **✋ Je joue** pour t'inscrire. Au-delà de 10, tu passes en liste d'attente."
    e = discord.Embed(title=title, description=f"Organisée par <@{custom['organiser_id']}>\n{desc}", color=BLUE)
    if votes is not None:
        lines = [f"`{hhmm(slot)}` {_bar(n)} **{n}**" for slot, n in votes]
        e.add_field(name="Créneaux", value="\n".join(lines) or "—", inline=False)
    else:
        e.add_field(name=f"Inscrits ({len(players)}/{PLAYERS})", value=" · ".join(players) or "—", inline=False)
        if waiting:
            e.add_field(name=f"Liste d'attente ({len(waiting)})", value=" · ".join(waiting), inline=False)
    return e


def build_teams_embed(
    custom,
    *,
    number: int,
    teams: Teams,
    names: dict[int, str],
    ranks: dict[int, str],
    winner: str | None,
    score: tuple[int, int],
    unrecorded: bool = False,
) -> discord.Embed:
    blue_pct = round(teams.p_blue * 100)
    e = discord.Embed(
        title=f"⚔️ Custom — partie {number}",
        description=f"Équilibre **{blue_pct} / {100 - blue_pct}** · Soirée : 🔵 {score[0]} – {score[1]} 🔴",
        color=BLUE if winner == "blue" else RED if winner == "red" else discord.Color.blurple(),
    )

    def side(slots) -> str:
        rows = sorted(slots, key=lambda s: s.lane)
        return "\n".join(
            f"`{LANES[s.lane]}` {names.get(s.person_id, '?')}"
            + (f" *({ranks[s.person_id]})*" if ranks.get(s.person_id) else "")
            for s in rows
        )

    e.add_field(name="🔵 Bleu" + (" 🏆" if winner == "blue" else ""), value=side(teams.blue), inline=True)
    e.add_field(name="🔴 Rouge" + (" 🏆" if winner == "red" else ""), value=side(teams.red), inline=True)
    if unrecorded:
        e.set_footer(text="⚠️ Résultat non enregistré dans lol-amis — /custom corriger pour réessayer.")
    return e


def build_closed_embed(custom, *, score: tuple[int, int], games: int, reason: str) -> discord.Embed:
    e = discord.Embed(title="⚔️ Custom terminée", description=reason, color=discord.Color.dark_grey())
    if games:
        e.add_field(name="Soirée", value=f"{games} partie(s) · 🔵 {score[0]} – {score[1]} 🔴", inline=False)
    return e
