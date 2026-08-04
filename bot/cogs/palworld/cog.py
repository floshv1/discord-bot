from __future__ import annotations

import time

import aiohttp
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.palworld import panel, service
from bot.cogs.palworld.service import (
    EMPTY_TIMEOUT,
    EmptyWatch,
    Komodo,
    KomodoError,
    PalworldRest,
    ServerStatus,
    Status,
    derive_status,
)
from bot.cogs.palworld.views import PalworldPanelView
from bot.core.config import Config

# One poll every 30 s. The panel is a status card, not a dashboard: faster would buy a
# fresher player count nobody is watching, and each tick costs one Komodo call, one call
# to the game and one message edit per guild.
POLL_SECONDS = 30

# Sent to the players themselves, in-game. Deliberately plain ASCII: the game's console
# has mangled accented characters before, and a garbled warning is worse than a blunt one.
IN_GAME_WARNING = "Arret du serveur demande par un administrateur - deconnexion imminente."

STOPPED_NOTICE = (
    "🛑 **Serveur arrêté automatiquement** — plus personne en ligne depuis {minutes} minutes.\n"
    "Le monde est sauvegardé et le temps ne passe plus : vos pals ne mourront pas de faim cette nuit. "
    "Cliquez sur **Démarrer** quand vous voulez rejouer."
)


class PalworldCog(commands.Cog):
    """Opens and closes the Palworld server from a pinned panel.

    Two halves that answer different questions. Komodo starts and stops the stack — with
    an API key scoped to that stack alone, so a compromised bot cannot touch Vaultwarden
    or the other 28 containers. The game's own REST API says who is connected, which is
    what makes the automatic shutdown possible at all.

    Neither is required for the cog to load. Missing Komodo credentials leave it inert but
    honest (`is_wired` is False, and both the buttons and `/setup palworld` say so);
    missing REST credentials keep the buttons working and only cost the player count and
    the automatic shutdown.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]
        self._session: aiohttp.ClientSession | None = None
        self._komodo: Komodo | None = None
        self._rest: PalworldRest | None = None
        # The optimistic state a click installs, so the card reacts immediately instead of
        # waiting out a tick. Lives in memory on purpose: a restart is not a transition.
        self._transition: tuple[str, float] | None = None
        self._watch = EmptyWatch()
        self.status = ServerStatus(Status.UNKNOWN)

    @property
    def is_wired(self) -> bool:
        return self._komodo is not None

    async def cog_load(self) -> None:
        # Registered whatever the configuration: an unwired panel must answer clicks with
        # an explanation, not with "This interaction failed".
        self.bot.add_view(PalworldPanelView())

        config = self.config
        if not (config.komodo_url and config.komodo_api_key and config.komodo_api_secret):
            logger.warning("KOMODO_URL/KOMODO_API_KEY/KOMODO_API_SECRET unset — the Palworld panel stays inert.")
            return

        self._session = aiohttp.ClientSession()
        self._komodo = Komodo(
            config.komodo_url,
            config.komodo_api_key,
            config.komodo_api_secret,
            config.palworld_stack,
            self._session,
        )

        if config.palworld_rest_url and config.palworld_admin_password:
            self._rest = PalworldRest(config.palworld_rest_url, config.palworld_admin_password, self._session)
        else:
            logger.warning(
                "PALWORLD_REST_URL/PALWORLD_ADMIN_PASSWORD unset — no player count, and no automatic shutdown."
            )

        self.status_ticker.start()
        logger.info("Palworld panel wired to Komodo stack {!r}.", config.palworld_stack)

    async def cog_unload(self) -> None:
        self.status_ticker.cancel()
        if self._session is not None:
            await self._session.close()

    # ------------------------------------------------------------------ polling

    async def _poll(self) -> ServerStatus:
        assert self._komodo is not None
        stack_state = await self._komodo.stack_state()

        # Without the REST API there is no way to tell an empty server from a booting one.
        # Claiming "nobody answered" would pin the card on "démarrage" forever, so the
        # degraded mode assumes an empty running server — and the ticker never stops one.
        players = await self._rest.players() if self._rest is not None else []

        now = time.monotonic()
        status = derive_status(stack_state, players, self._transition, now)
        if status not in (Status.BOOTING, Status.STOPPING):
            self._transition = None
        return ServerStatus(status, players or [])

    async def _refresh(self) -> ServerStatus:
        """Poll and repaint every configured panel. Also the tick's body."""
        self.status = await self._poll()
        for guild_id in await service.configured_guild_ids():
            await panel.redraw(self.bot, guild_id, self.status, self.config.palworld_address)
        return self.status

    @tasks.loop(seconds=POLL_SECONDS)
    async def status_ticker(self) -> None:
        # `_auto_stop` has to be inside the guard too. It talks to Komodo over the network,
        # so it raises `aiohttp.ClientError`/`TimeoutError` like anything else — and those
        # are exactly the exceptions `tasks.loop(reconnect=True)` treats as "connection
        # blip", restarting the loop on an ExponentialBackoff of a second or two instead of
        # the 30s interval. A Komodo outage would have turned this panel into an edit storm.
        try:
            status = await self._refresh()

            if self._rest is None:
                return  # no player count, so "empty" is not something we actually know
            if self._watch.observe(status.status, status.player_count, time.monotonic()):
                await self._auto_stop()
        except Exception:
            # A ticker that dies takes the whole panel with it, silently, until the next
            # restart — and the auto-shutdown with it.
            logger.exception("Palworld status tick failed.")

    @status_ticker.before_loop
    async def _before_ticker(self) -> None:
        # The channel cache is empty inside setup_hook, so a redraw attempted from
        # cog_load would silently find no channel and do nothing.
        await self.bot.wait_until_ready()

    @status_ticker.error
    async def _status_ticker_error(self, error: BaseException) -> None:
        # The body swallows its own exceptions, so reaching here means something escaped the
        # loop machinery itself. Without a handler discord.py logs it and the ticker is over:
        # the panel freezes and, worse, the automatic shutdown stops running — the server
        # would then stay up and populated-looking until someone noticed by hand.
        logger.warning(f"palworld status_ticker error — restarting the ticker: {error}")
        self.status_ticker.restart()

    async def refresh_cards(self, guild_id: int) -> None:
        """Repaint one guild's panel — what `/setup palworld` calls after posting it."""
        await panel.redraw(self.bot, guild_id, self.status, self.config.palworld_address)

    # ------------------------------------------------------------------ actions

    async def start_server(self, actor: str) -> str:
        assert self._komodo is not None
        now = time.monotonic()
        self._transition = ("starting", now)
        self._watch.note_start(now)
        try:
            await self._komodo.start()
        except KomodoError as exc:
            self._transition = None
            logger.warning("Komodo refused to start the Palworld stack: {}", exc)
            return "❌ Komodo a refusé de démarrer la stack. Réessaie dans un instant, sinon regarde du côté de Komodo."

        logger.info("Palworld server started by {}.", actor)
        await self._refresh()
        return (
            "▶️ **C'est parti.** Compte environ **1 min 30** : le serveur vérifie les mises à jour, "
            "puis charge le monde. La carte passera au vert toute seule."
        )

    async def stop_server(self, actor: str, *, warn_in_game: bool) -> str:
        assert self._komodo is not None
        if self._rest is not None:
            if warn_in_game:
                await self._rest.announce(IN_GAME_WARNING)
            await self._rest.save()

        self._transition = ("stopping", time.monotonic())
        try:
            await self._komodo.stop()
        except KomodoError as exc:
            self._transition = None
            logger.warning("Komodo refused to stop the Palworld stack: {}", exc)
            return "❌ Komodo a refusé d'arrêter la stack. Le serveur tourne toujours."

        logger.info("Palworld server stopped by {}.", actor)
        self._watch.empty_since = None
        await self._refresh()
        return "⏹️ **Serveur arrêté**, partie sauvegardée."

    async def _auto_stop(self) -> None:
        logger.info("Palworld server empty for {} minutes — shutting it down.", int(EMPTY_TIMEOUT // 60))
        message = await self.stop_server("auto", warn_in_game=False)
        if message.startswith("❌"):
            return
        notice = STOPPED_NOTICE.format(minutes=int(EMPTY_TIMEOUT // 60))
        for guild_id in await service.configured_guild_ids():
            await panel.announce(self.bot, guild_id, notice)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PalworldCog(bot))
