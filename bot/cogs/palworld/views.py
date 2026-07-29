from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.cogs.palworld.service import Status

if TYPE_CHECKING:
    from bot.cogs.palworld.cog import PalworldCog

NOT_WIRED = (
    "⚙️ Le panneau n'est pas branché à Komodo — les boutons ne peuvent rien piloter. "
    "Préviens un admin, il manque des variables d'environnement."
)
NOT_ALLOWED = "❌ Seuls les admins peuvent arrêter le serveur. Il s'arrête tout seul quand il reste vide."


def _cog(interaction: discord.Interaction) -> PalworldCog | None:
    """Resolve the cog at click time rather than capturing it.

    Same reason as the music card: this view is registered once at boot and dispatches
    clicks on a message that outlives any number of restarts, so it must own no state.
    """
    return interaction.client.get_cog("PalworldCog")  # type: ignore[return-value]


class ConfirmStopView(discord.ui.View):
    """Asked for only when someone is actually connected.

    Ephemeral and short-lived, so it may keep a timeout — the persistence rule applies to
    the public panel, not to a private confirmation.
    """

    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.confirmed = False

    @discord.ui.button(label="Arrêter quand même", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(content="⏳ Arrêt demandé…", view=None)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="👍 Rien n'a été touché.", view=None)


class StartButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="Démarrer",
            style=discord.ButtonStyle.success,
            emoji="▶️",
            custom_id="palworldpanel:start",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = _cog(interaction)
        if cog is None or not cog.is_wired:
            await interaction.response.send_message(NOT_WIRED, ephemeral=True)
            return

        status = cog.status.status
        if status in (Status.ONLINE, Status.BOOTING):
            await interaction.response.send_message(
                "✅ Le serveur est déjà en route — la carte se met à jour toute seule.", ephemeral=True
            )
            return

        # Komodo runs `docker compose start` synchronously, and the panel redraw that
        # follows costs another round trip: well past the three seconds Discord gives us.
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await cog.start_server(interaction.user.display_name)
        await interaction.followup.send(message, ephemeral=True)


class StopButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="Arrêter",
            style=discord.ButtonStyle.danger,
            emoji="⏹️",
            custom_id="palworldpanel:stop",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = _cog(interaction)
        if cog is None or not cog.is_wired:
            await interaction.response.send_message(NOT_WIRED, ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(NOT_ALLOWED, ephemeral=True)
            return

        status = cog.status
        if status.status in (Status.OFFLINE, Status.STOPPING):
            await interaction.response.send_message(
                "✅ Il est déjà éteint (ou en train de s'éteindre).", ephemeral=True
            )
            return

        # Cutting a session short is the one irreversible thing this panel does, so it
        # asks — but only when there is actually someone to cut off.
        if status.player_count:
            names = ", ".join(p.name for p in status.players)
            confirm = ConfirmStopView()
            await interaction.response.send_message(
                f"⚠️ **{status.player_count} connecté(s)** : {names}.\nLes prévenir avant ?",
                view=confirm,
                ephemeral=True,
            )
            if not await confirm.wait() and confirm.confirmed:
                message = await cog.stop_server(interaction.user.display_name, warn_in_game=True)
                await interaction.followup.send(message, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await cog.stop_server(interaction.user.display_name, warn_in_game=False)
        await interaction.followup.send(message, ephemeral=True)


class PalworldPanelView(discord.ui.View):
    """The pinned panel's buttons.

    Persistent by construction: no timeout, a custom_id per button, registered once in
    ``cog_load``. ``status`` only greys the buttons out for rendering — the view the bot
    registered at boot still dispatches, so a click racing a redraw is answered rather
    than dropped.
    """

    def __init__(self, status: Status | None = None) -> None:
        super().__init__(timeout=None)
        in_transition = status in (Status.BOOTING, Status.STOPPING)
        unreachable = status in (Status.DOWN, Status.UNKNOWN)
        self.add_item(StartButton(disabled=in_transition or unreachable or status is Status.ONLINE))
        self.add_item(StopButton(disabled=in_transition or unreachable or status is Status.OFFLINE))
