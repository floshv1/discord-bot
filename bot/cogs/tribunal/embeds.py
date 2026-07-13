from __future__ import annotations

import datetime

import discord

from bot.cogs.tribunal.service import QUORUM

PHASE_COLORS: dict[str | None, discord.Color] = {
    None: discord.Color.dark_orange(),
    "guilty": discord.Color.dark_red(),
    "acquitted": discord.Color.green(),
    "expired": discord.Color.greyple(),
}


def _phase_text(target_id: int, plea: str | None, verdict: str | None, guilty: int, innocent: int) -> str:
    if verdict == "guilty":
        return f"⚖️ **Coupable** — {guilty} voix contre {innocent}. La peine est confirmée et suit son cours."
    if verdict == "acquitted":
        return f"🕊️ **Non coupable** — {innocent} voix contre {guilty}. <@{target_id}> est libéré sur-le-champ."
    if verdict == "expired":
        return "⌛ La peine a pris fin avant que le jury ne se prononce. Le procès est sans objet."
    if plea is None:
        return (
            f"<@{target_id}>, tu es accusé. **Clique sur « Plaider ma cause »** pour te défendre.\n"
            "Tant que tu n'as pas plaidé, le jury ne peut pas voter — et ta peine suit son cours."
        )
    return f"Le jury délibère. Il faut **{QUORUM} voix** et une majorité stricte pour prononcer le verdict."


def build_trial_embed(
    target_id: int,
    moderator_id: int,
    reason: str,
    expires_at: datetime.datetime | None,
    plea: str | None,
    verdict: str | None,
    guilty: int,
    innocent: int,
) -> discord.Embed:
    embed = discord.Embed(
        title="⚖️ Tribunal",
        description=_phase_text(target_id, plea, verdict, guilty, innocent),
        color=PHASE_COLORS.get(verdict, discord.Color.dark_orange()),
    )
    embed.add_field(name="Accusé", value=f"<@{target_id}>", inline=True)
    embed.add_field(name="Plaignant", value=f"<@{moderator_id}>", inline=True)
    embed.add_field(
        name="Fin de peine",
        value=discord.utils.format_dt(expires_at, "R") if expires_at else "Illimitée",
        inline=True,
    )
    embed.add_field(name="Chef d'accusation", value=reason, inline=False)
    if plea:
        embed.add_field(name="🗣️ Plaidoyer de l'accusé", value=plea, inline=False)
    elif verdict is not None:
        embed.add_field(name="🗣️ Plaidoyer de l'accusé", value="_L'accusé n'a jamais plaidé._", inline=False)
    return embed
