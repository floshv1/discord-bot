from __future__ import annotations

import asyncpg
import discord
from loguru import logger

from bot.cogs.moderation import service as mod_service
from bot.cogs.tribunal import service
from bot.cogs.tribunal.embeds import build_trial_embed


def _embed_for(trial: asyncpg.Record, guilty: int, innocent: int) -> discord.Embed:
    return build_trial_embed(
        target_id=trial["target_id"],
        moderator_id=trial["moderator_id"],
        reason=trial["reason"],
        expires_at=trial["expires_at"],
        plea=trial["plea"],
        verdict=trial["verdict"],
        guilty=guilty,
        innocent=innocent,
    )


class PleaButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tribunal:plea:(?P<tid>\d+)"):
    """The accused's only way to speak — the goulag role need not let them type.

    Dynamic so one registration covers every trial, including the ones opened after boot.
    """

    def __init__(self, trial_id: int) -> None:
        self.trial_id = trial_id
        super().__init__(
            discord.ui.Button(
                label="Plaider ma cause",
                emoji="🗣️",
                style=discord.ButtonStyle.primary,
                custom_id=f"tribunal:plea:{trial_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["tid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        trial = await service.get_trial(self.trial_id)
        if trial is None:
            await interaction.response.send_message("Ce procès n'existe plus.", ephemeral=True)
            return
        if interaction.user.id != trial["target_id"]:
            await interaction.response.send_message("Seul l'accusé peut plaider sa cause.", ephemeral=True)
            return
        if trial["verdict"] is not None:
            await interaction.response.send_message("Le procès est clos.", ephemeral=True)
            return
        if trial["plea"] is not None:
            await interaction.response.send_message("Tu as déjà plaidé — le jury délibère.", ephemeral=True)
            return
        await interaction.response.send_modal(PleaModal(self.trial_id))


class PleaModal(discord.ui.Modal, title="Plaidoyer devant le tribunal"):
    plea_input = discord.ui.TextInput(
        label="Ta défense",
        placeholder="Explique-toi. Le jury lira ceci avant de voter.",
        style=discord.TextStyle.paragraph,
        min_length=10,
        max_length=1000,  # an embed field value caps at 1024
    )

    def __init__(self, trial_id: int) -> None:
        super().__init__()
        self.trial_id = trial_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await service.submit_plea(self.trial_id, str(self.plea_input)):
            await interaction.response.send_message("Trop tard — le procès a avancé sans toi.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Ton plaidoyer est versé au dossier. Le jury peut maintenant voter.", ephemeral=True
        )
        await refresh_trial_message(interaction.client, self.trial_id)


class VerdictButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tribunal:vote:(?P<side>guilty|innocent):(?P<tid>\d+)",
):
    def __init__(self, trial_id: int, vote: int, count: int = 0) -> None:
        self.trial_id = trial_id
        self.vote = vote
        is_guilty = vote == service.GUILTY
        super().__init__(
            discord.ui.Button(
                label=f"Coupable ({count})" if is_guilty else f"Non coupable ({count})",
                emoji="⚖️" if is_guilty else "🕊️",
                style=discord.ButtonStyle.danger if is_guilty else discord.ButtonStyle.success,
                custom_id=f"tribunal:vote:{'guilty' if is_guilty else 'innocent'}:{trial_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["tid"]), service.GUILTY if match["side"] == "guilty" else service.INNOCENT)

    async def callback(self, interaction: discord.Interaction) -> None:
        trial = await service.get_trial(self.trial_id)
        if trial is None:
            await interaction.response.send_message("Ce procès n'existe plus.", ephemeral=True)
            return
        if trial["verdict"] is not None:
            await interaction.response.send_message("Le verdict est déjà tombé.", ephemeral=True)
            return
        if trial["plea"] is None:
            await interaction.response.send_message(
                "L'accusé n'a pas encore plaidé — le jury ne peut pas voter.", ephemeral=True
            )
            return

        judge_role_id = await service.get_judge_role_id(trial["guild_id"])
        if judge_role_id is None or not any(role.id == judge_role_id for role in interaction.user.roles):
            await interaction.response.send_message("Seuls les juges rendent la justice ici.", ephemeral=True)
            return
        if interaction.user.id == trial["target_id"]:
            await interaction.response.send_message("Tu ne juges pas ton propre procès.", ephemeral=True)
            return
        if interaction.user.id == trial["moderator_id"]:
            # Carrying the accusation already was a call. Voting on it too would be two voices.
            await interaction.response.send_message(
                "Tu portes l'accusation — tu ne peux pas aussi la juger.", ephemeral=True
            )
            return

        removed = await service.cast_vote(self.trial_id, interaction.user.id, self.vote)
        guilty, innocent = await service.count_votes(self.trial_id)
        verdict = service.tally(guilty, innocent)

        if verdict is not None:
            if not await service.claim_verdict(self.trial_id, verdict):
                # Another judge's ballot got there a hair earlier. Falling through would
                # redraw the ruled card with live buttons on it — never resurrect a verdict.
                await interaction.response.send_message("Le verdict vient de tomber.", ephemeral=True)
                await refresh_trial_message(interaction.client, self.trial_id)
                return
            ruled = await service.get_trial(self.trial_id)
            await interaction.response.edit_message(embed=_embed_for(ruled, guilty, innocent), view=None)
            await _apply_verdict(interaction.client, ruled, verdict)
            await _announce_verdict(interaction.client, ruled, verdict, guilty, innocent)
            return

        await interaction.response.edit_message(
            embed=_embed_for(trial, guilty, innocent),
            view=VerdictView(self.trial_id, guilty, innocent),
        )
        await interaction.followup.send("Vote retiré." if removed else "Vote enregistré.", ephemeral=True)


class PleaView(discord.ui.View):
    def __init__(self, trial_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(PleaButton(trial_id))


class VerdictView(discord.ui.View):
    def __init__(self, trial_id: int, guilty: int = 0, innocent: int = 0) -> None:
        super().__init__(timeout=None)
        self.add_item(VerdictButton(trial_id, service.GUILTY, guilty))
        self.add_item(VerdictButton(trial_id, service.INNOCENT, innocent))


def build_trial_view(trial: asyncpg.Record, guilty: int, innocent: int) -> discord.ui.View | None:
    """The card's buttons follow the trial's phase — one function, so a redraw can't lie.

    A ruled trial gets no buttons at all: a card that stays clickable after the verdict is
    a card that throws "This interaction failed" at whoever tries.
    """
    if trial["verdict"] is not None:
        return None
    if trial["plea"] is None:
        return PleaView(trial["id"])
    return VerdictView(trial["id"], guilty, innocent)


async def refresh_trial_message(client: discord.Client, trial_id: int) -> None:
    trial = await service.get_trial(trial_id)
    if trial is None or trial["message_id"] is None:
        return
    channel = client.get_channel(trial["channel_id"])
    if channel is None:
        return
    guilty, innocent = await service.count_votes(trial_id)
    try:
        message = await channel.fetch_message(trial["message_id"])
        await message.edit(embed=_embed_for(trial, guilty, innocent), view=build_trial_view(trial, guilty, innocent))
    except discord.NotFound:
        pass


async def open_trial(guild_id: int, reprimand_id: int, channel: discord.abc.Messageable) -> None:
    """Put a fresh reprimand on trial: post the card and summon the accused."""
    trial_id = await service.create_trial(guild_id, reprimand_id, channel.id)
    trial = await service.get_trial(trial_id)
    message = await channel.send(
        content=f"<@{trial['target_id']}>",
        embed=_embed_for(trial, 0, 0),
        view=build_trial_view(trial, 0, 0),
    )
    await service.set_message_id(trial_id, message.id)


async def close_trial(client: discord.Client, reprimand_id: int) -> None:
    """The sentence ended before the bench ruled — the trial is moot. No-op if it already ruled."""
    trial_id = await service.expire_trial(reprimand_id)
    if trial_id is None:
        return
    await refresh_trial_message(client, trial_id)


async def _apply_verdict(client: discord.Client, trial: asyncpg.Record, verdict: str) -> None:
    guild = client.get_guild(trial["guild_id"])
    member = guild.get_member(trial["target_id"]) if guild else None

    if verdict == "acquitted":
        # The whole point of the tribunal: an acquittal frees the accused on the spot,
        # rather than leaving them to serve a sentence the jury just overturned.
        await mod_service.deactivate_reprimand(trial["reprimand_id"])
        if member is None:
            logger.info(f"Trial {trial['id']} acquitted {trial['target_id']}, but they left the guild")
            return
        await mod_service.lift_reprimand(member, trial["original_nick"])

    if member is None:
        return
    await mod_service.log_action(
        client,
        client.config,  # type: ignore[attr-defined]
        trial["guild_id"],
        member,
        client.user,
        f"tribunal_{verdict}",
        trial["reason"],
    )


async def _announce_verdict(
    client: discord.Client,
    trial: asyncpg.Record,
    verdict: str,
    guilty: int,
    innocent: int,
) -> None:
    """The card is edited in place, possibly far up the channel — say it out loud too."""
    channel = client.get_channel(trial["channel_id"])
    if channel is None:
        return
    target = f"<@{trial['target_id']}>"
    if verdict == "guilty":
        text = f"⚖️ **Coupable** — {guilty} voix contre {innocent}. {target}, ta peine est confirmée."
    else:
        text = f"🕊️ **Non coupable** — {innocent} voix contre {guilty}. {target} est libre. Le tribunal a parlé."

    try:
        card = await channel.fetch_message(trial["message_id"])
        await card.reply(text)
    except discord.NotFound:
        await channel.send(text)
