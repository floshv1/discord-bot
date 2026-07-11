from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence

import discord

CURRENCY_NAME = "FloshCoins"
CURRENCY_EMOJI = "🪙"


REASON_LABELS = {
    "initial": "Solde de départ",
    "claim": "Claim quotidien",
    "bet": "Mise",
    "payout": "Gain",
    "refund": "Remboursement",
    "bet_create_fee": "Ouverture d'un pari",
    "bet_create_fee_refund": "Frais d'ouverture rendus",
    "admin_grant": "Ajustement admin",
}


def build_history_embed(display_name: str, rows: Sequence[Mapping], balance: int) -> discord.Embed:
    """A member's ledger — what moved, why, and what it left behind.

    `balance_after` is shown per line so a wrong balance can be *located*: the row where the
    running total stops making sense is the one that went wrong.
    """
    if not rows:
        description = "*Aucune transaction enregistrée.*"
    else:
        lines = []
        for r in rows:
            when = r["created_at"].strftime("%d/%m %H:%M")
            label = REASON_LABELS.get(r["reason"], r["reason"])
            after = f" → **{r['balance_after']:,}**" if r["balance_after"] is not None else ""
            lines.append(f"`{when}`  **{r['amount']:+,}** {CURRENCY_EMOJI} — {label}{after}")
        description = "\n".join(lines)

    embed = discord.Embed(
        title=f"{CURRENCY_EMOJI} Historique — {display_name}",
        description=description,
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Solde actuel : {balance:,} {CURRENCY_NAME}")
    return embed


def build_transactions_log_embed(rows: Sequence[Mapping]) -> discord.Embed:
    """A batch of ledger movements, for the audit-log channel.

    Batched deliberately: settling one match credits every winner at once, which would be a
    dozen separate embeds — and a rate-limit breach — if posted one by one.
    """
    lines = []
    for r in rows:
        label = REASON_LABELS.get(r["reason"], r["reason"])
        after = f" → {r['balance_after']:,}" if r["balance_after"] is not None else ""
        lines.append(f"<@{r['user_id']}> **{r['amount']:+,}**{after} — {label}")

    return discord.Embed(
        description=f"**{CURRENCY_EMOJI} Transactions**\n" + "\n".join(lines),
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(datetime.UTC),
    )


def build_panel_embed() -> discord.Embed:
    """The persistent control-panel message posted in the currency channel."""
    return discord.Embed(
        title=f"{CURRENCY_EMOJI} {CURRENCY_NAME}",
        description=(
            f"Everyone starts with **1,000** {CURRENCY_NAME}.\n\n"
            "**•** Tap 🎁 **Claim daily** for **100** more — once a day, resets at midnight.\n"
            "**•** Tap 💰 **My balance** to check your wallet.\n"
            "**•** Spend them betting on matches — winners split the losers' pool."
        ),
        color=discord.Color.gold(),
    )


def build_leaderboard_embed(rows: Sequence[Mapping], names: Mapping[int, str], updated: str) -> discord.Embed:
    embed = discord.Embed(title=f"{CURRENCY_EMOJI} {CURRENCY_NAME} Leaderboard", color=discord.Color.gold())
    embed.set_footer(text=f"Updated {updated}")
    if not rows:
        embed.description = "No wallets yet."
        return embed
    lines = [
        f"**#{i}** {names.get(r['user_id'], f'<@{r["user_id"]}>')} — {r['balance']:,} {CURRENCY_EMOJI}"
        for i, r in enumerate(rows, 1)
    ]
    embed.description = "\n".join(lines)
    return embed
