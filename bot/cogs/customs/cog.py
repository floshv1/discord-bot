"""LoL 5v5 customs: a signup card replaces the group's poll, lol-amis builds the teams.

lol-amis is the source of truth for players, links (person.discord_id), ratings and games; this
cog only keeps the evening's own state (service.py) and talks to members. Cards are redrawn off
a dirty set on a short ticker through `edit_if_changed` — never edited from a button callback.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from loguru import logger

from bot.cogs.customs import embeds, rules, service
from bot.cogs.customs.lolamis import LinkTaken, LolAmisClient, LolAmisError, Person, Teams
from bot.cogs.customs.views import NOT_LISTED, CustomButton, PersonPicker, SwapPicker, card_view
from bot.core.discord_utils import edit_if_changed

DOWN = "Le générateur d'équipes (lol-amis) ne répond pas — réessaie dans un instant."
NOT_SET_UP = "Les customs ne sont pas configurées sur ce bot."
ORGANISER_ONLY = "Seul l'organisateur peut faire ça."
ORGANISER_OR_MOD = "Seul l'organisateur ou un modo peut faire ça."
PERSONS_TTL = 60.0


def _jump(guild_id: int, channel_id: int, message_id: int | None) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id or ''}"


class CustomsCog(commands.Cog):
    custom = app_commands.Group(name="custom", description="Customs LoL 5v5", guild_only=True)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = bot.config  # type: ignore[attr-defined]
        self.client: LolAmisClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._dirty: set[int] = set()
        self._persons: dict[int, Person] = {}
        self._persons_at: float | None = None  # None = never loaded: stale whatever the clock says
        self._ticking = False
        self._create_locks: dict[int, asyncio.Lock] = {}

    # --- lifecycle --------------------------------------------------------

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(CustomButton)
        if not self.config.lolamis_url:
            logger.info("Customs: LOLAMIS_URL unset — the feature is off.")
            return
        self._session = aiohttp.ClientSession()
        self.client = LolAmisClient(self.config.lolamis_url, self._session)
        logger.info(f"Customs: using lol-amis at {self.config.lolamis_url}.")
        self.card_ticker.start()
        self.minute_ticker.start()

    async def cog_unload(self) -> None:
        self.card_ticker.cancel()
        self.minute_ticker.cancel()
        if self._session:
            await self._session.close()

    # --- helpers ----------------------------------------------------------

    def mark_dirty(self, custom_id: int) -> None:
        self._dirty.add(custom_id)

    async def persons(self, *, fresh: bool = False) -> dict[int, Person]:
        if fresh or self._persons_at is None or time.monotonic() - self._persons_at > PERSONS_TTL:
            assert self.client is not None
            self._persons = {p.id: p for p in await self.client.list_persons()}
            self._persons_at = time.monotonic()
        return self._persons

    async def person_of(self, discord_id: int, *, fresh: bool = True) -> Person | None:
        did = str(discord_id)
        return next((p for p in (await self.persons(fresh=fresh)).values() if p.discord_id == did), None)

    @staticmethod
    async def reply(interaction: discord.Interaction, text: str, **kw) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, **kw)
        else:
            await interaction.response.send_message(text, ephemeral=True, **kw)

    @staticmethod
    def is_mod(interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        return bool(perms and perms.manage_messages)

    def guarded(self, callback):
        """Picker callbacks run outside `handle`'s try and `tree.on_error` never sees views:
        without this, a lol-amis failure there leaves the member on « is thinking… » forever."""

        async def run(interaction: discord.Interaction, *args) -> None:
            try:
                await callback(interaction, *args)
            except LolAmisError as exc:
                logger.warning(f"Customs picker: lol-amis failed: {exc}")
                await self.reply(interaction, DOWN)
            except (ValueError, KeyError):
                await self.reply(interaction, "Choix invalide — réessaie.")

        return run

    # --- commands ---------------------------------------------------------

    @custom.command(name="lancer", description="Ouvre l'inscription à une custom 5v5.")
    @app_commands.describe(
        heure="Heure fixe, ex. 21h ou 21h30", creneaux="Ou plusieurs créneaux à voter, ex. « 20h 21h 22h »"
    )
    async def launch_command(
        self, interaction: discord.Interaction, heure: str | None = None, creneaux: str | None = None
    ) -> None:
        await self.launch(interaction, heure=heure, creneaux=creneaux)

    async def launch(self, interaction: discord.Interaction, *, heure: str | None, creneaux: str | None) -> None:
        if self.client is None:
            await self.reply(interaction, NOT_SET_UP)
            return
        cfg = await service.get_config(interaction.guild_id)
        if cfg is None:
            await self.reply(
                interaction, "Les customs ne sont pas configurées — un admin doit lancer `/setup customs`."
            )
            return
        if (heure is None) == (creneaux is None):
            await self.reply(interaction, "Donne **soit** une `heure`, **soit** des `creneaux` à voter.")
            return
        now = discord.utils.utcnow()
        try:
            start_at = rules.parse_time(heure, now) if heure else None
            slots = rules.parse_slots(creneaux, now) if creneaux else []
        except ValueError as exc:
            await self.reply(interaction, f"{exc}. Exemples : `21h`, `21h30`, `20h 21h 22h`.")
            return
        channel = interaction.guild.get_channel(cfg["channel_id"])
        if channel is None:
            await self.reply(interaction, "Le salon des customs n'existe plus — relance `/setup customs`.")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            cid = await service.create_custom(
                interaction.guild_id, interaction.user.id, channel.id, "vote" if slots else "fixed", start_at, slots
            )
        except service.AlreadyOpen as exc:
            row = await service.get(exc.custom_id)
            link = _jump(interaction.guild_id, row["channel_id"], row["message_id"]) if row else ""
            await self.reply(interaction, f"Une custom est déjà ouverte : {link}")
            return
        embed, view = await self.render(cid)
        ping = f"<@&{cfg['role_id']}>" if cfg["role_id"] else None
        msg = await channel.send(
            content=ping, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(roles=True)
        )
        await service.set_message(cid, msg.id)
        await self.reply(interaction, f"Custom ouverte : {msg.jump_url}")

    @custom.command(name="lien", description="Relie ton compte Discord à ton joueur lol-amis (ou corrige-le).")
    @app_commands.describe(membre="(modo) relier quelqu'un d'autre")
    async def link_command(self, interaction: discord.Interaction, membre: discord.Member | None = None) -> None:
        if self.client is None:
            await self.reply(interaction, NOT_SET_UP)
            return
        target = membre or interaction.user
        if target.id != interaction.user.id and not self.is_mod(interaction):
            await self.reply(interaction, "Seul un modo peut relier quelqu'un d'autre.")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            persons = sorted((await self.persons(fresh=True)).values(), key=lambda p: p.name.lower())
        except LolAmisError:
            await self.reply(interaction, DOWN)
            return

        async def picked(i: discord.Interaction, value: int | str) -> None:
            await i.response.defer(ephemeral=True)
            pid = (
                await self.not_listed(i, target.id)
                if value == NOT_LISTED
                else await self.link_member(i, target.id, value)
            )
            if pid is not None:
                name = self._persons[pid].name if pid in self._persons else f"#{pid}"
                await self.reply(i, f"✅ {target.mention} est relié à **{name}**.")

        await self.reply(
            interaction,
            f"Choisis le joueur lol-amis de {target.mention} :",
            view=PersonPicker(persons, self.guarded(picked), placeholder="Joueur…", allow_new=True),
        )

    @custom.command(name="corriger", description="(modo) Corrige le gagnant d'une partie de la dernière custom.")
    @app_commands.describe(partie="Numéro de la partie", gagnant="Équipe gagnante")
    @app_commands.choices(
        gagnant=[app_commands.Choice(name="Bleu", value="blue"), app_commands.Choice(name="Rouge", value="red")]
    )
    @app_commands.default_permissions(manage_messages=True)
    async def correct_command(self, interaction: discord.Interaction, partie: int, gagnant: str) -> None:
        if self.client is None:
            await self.reply(interaction, NOT_SET_UP)
            return
        await interaction.response.defer(ephemeral=True)
        cid = await service.latest_id(interaction.guild_id)
        game = await service.correct_winner(cid, partie, gagnant) if cid else None
        if game is None:
            await self.reply(interaction, "Partie introuvable.")
            return
        ok = await self._push_result(cid, partie, Teams.from_json(_teams_json(game)), gagnant, game["lolamis_game_id"])
        self.mark_dirty(cid)
        await self.reply(interaction, "✅ Corrigé." if ok else DOWN)

    # --- the one entry point for card buttons -----------------------------

    async def handle(self, interaction: discord.Interaction, action: str, cid: int, arg: int | None) -> None:
        custom = await service.get(cid)
        if custom is None or custom["status"] not in ("signup", "teams"):
            await self.reply(interaction, "Cette custom est terminée.")
            return
        if self.client is None:
            await self.reply(interaction, NOT_SET_UP)
            return
        handler = {
            "join": self._join,
            "leave": self._leave,
            "vote": self._vote,
            "validate": self._validate,
            "guest": self._guest,
            "cancel": self._cancel,
            "gen": self._generate,
            "regen": self._regenerate,
            "swap": self._swap,
            "win": self._win,
            "next": self._next,
            "end": self._end,
        }.get(action)
        if handler is None:
            await self.reply(interaction, "Bouton inconnu.")
            return
        try:
            await handler(interaction, custom, arg)
        except LolAmisError as exc:
            logger.warning(f"Customs {cid} {action}: lol-amis failed: {exc}")
            await self.reply(interaction, DOWN)

    def _organiser(self, interaction: discord.Interaction, custom) -> bool:
        return interaction.user.id == custom["organiser_id"]

    # --- linking ----------------------------------------------------------

    async def link_member(self, interaction: discord.Interaction, discord_id: int, person_id: int) -> int | None:
        """Link `discord_id` to `person_id`, moving it off any player it was on. None = refused."""
        assert self.client is not None
        did = str(discord_id)
        persons = await self.persons(fresh=True)
        current = next((p for p in persons.values() if p.discord_id == did), None)
        if current is not None and current.id == person_id:
            return person_id
        target = persons.get(person_id)
        # A player already linked to someone else is theirs: only a mod may reassign it, or any
        # member could quietly play under somebody else's ratings.
        if target is not None and target.discord_id and target.discord_id != did and not self.is_mod(interaction):
            await self.reply(interaction, f"**{target.name}** est déjà relié à un autre membre. Demande à un modo.")
            return None
        try:
            if current is not None:
                await self.client.link(current.id, None)
            await self.client.link(person_id, did)
        except LolAmisError as exc:
            if current is not None:  # don't leave the member unlinked by a half-done move
                try:
                    await self.client.link(current.id, did)
                except LolAmisError:
                    logger.warning(f"Customs: could not restore link {did} -> {current.id}")
            if isinstance(exc, LinkTaken):
                await self.reply(interaction, f"Ce compte est déjà relié à **{exc.holder_name}**. Demande à un modo.")
                return None
            raise
        await self.persons(fresh=True)
        return person_id

    async def not_listed(self, interaction: discord.Interaction, discord_id: int) -> int | None:
        """« Je ne suis pas dans la liste » — create the player, unless an earlier click already did."""
        assert self.client is not None
        # Two pickers resolved together must not both create: serialise per member.
        async with self._create_locks.setdefault(discord_id, asyncio.Lock()):
            existing = await self.person_of(discord_id)
            if existing is not None:
                return existing.id
            member = interaction.user if interaction.user.id == discord_id else None
            name = getattr(member, "display_name", None) or str(discord_id)
            try:
                person = await self.client.create_person(name, str(discord_id))
            except LinkTaken as exc:
                return exc.holder_id
            await self.persons(fresh=True)
            return person.id

    async def _ensure_linked(self, interaction: discord.Interaction, then) -> Person | None:
        """The member's player, or None after showing them the picker (which calls `then`)."""
        person = await self.person_of(interaction.user.id)
        if person is not None:
            return person
        free = sorted((p for p in self._persons.values() if p.discord_id is None), key=lambda p: p.name.lower())

        async def picked(i: discord.Interaction, value: int | str) -> None:
            await i.response.defer(ephemeral=True)
            pid = (
                await self.not_listed(i, i.user.id)
                if value == NOT_LISTED
                else await self.link_member(i, i.user.id, value)
            )
            if pid is not None:
                await then(i, pid)

        await self.reply(
            interaction,
            "Première fois : choisis ton joueur lol-amis (une seule fois, c'est retenu).",
            view=PersonPicker(free, self.guarded(picked), placeholder="Qui es-tu ?", allow_new=True),
        )
        return None

    # --- signup-phase actions ---------------------------------------------

    async def _join(self, interaction, custom, _arg) -> None:
        await interaction.response.defer(ephemeral=True)

        async def sign(i: discord.Interaction, pid: int) -> None:
            ok = await service.add_signup(custom["id"], i.user.id, pid)
            self.mark_dirty(custom["id"])
            await self.reply(i, "✅ Inscrit !" if ok else "Tu es déjà inscrit.")

        person = await self._ensure_linked(interaction, sign)
        if person is not None:
            await sign(interaction, person.id)

    async def _leave(self, interaction, custom, _arg) -> None:
        removed = await service.remove_signup(custom["id"], interaction.user.id)
        await service.remove_votes(custom["id"], interaction.user.id)
        self.mark_dirty(custom["id"])
        await self.reply(interaction, "🚪 Retiré." if removed else "Tu n'étais pas inscrit.")

    async def _vote(self, interaction, custom, arg) -> None:
        await interaction.response.defer(ephemeral=True)
        slot = dt.datetime.fromtimestamp(arg, dt.UTC)

        async def cast(i: discord.Interaction, _pid: int) -> None:
            on = await service.toggle_vote(custom["id"], slot, i.user.id)
            await self._maybe_auto_win(custom["id"])
            self.mark_dirty(custom["id"])
            await self.reply(i, f"{'✅' if on else '↩️'} {embeds.hhmm(slot)}")

        person = await self._ensure_linked(interaction, cast)
        if person is not None:
            await cast(interaction, person.id)

    async def _maybe_auto_win(self, cid: int) -> None:
        votes = await service.votes(cid)
        winner = rules.vote_winner({slot: len(users) for slot, users in votes.items()})
        if winner is not None:
            await service.claim_vote_win(cid, winner, await self._persons_by_user())

    async def _persons_by_user(self) -> dict[int, int]:
        return {int(p.discord_id): p.id for p in (await self.persons(fresh=True)).values() if p.discord_id}

    async def _validate(self, interaction, custom, arg) -> None:
        if not self._organiser(interaction, custom):
            await self.reply(interaction, ORGANISER_ONLY)
            return
        await interaction.response.defer(ephemeral=True)
        slot = dt.datetime.fromtimestamp(arg, dt.UTC)
        ok = await service.claim_vote_win(custom["id"], slot, await self._persons_by_user())
        self.mark_dirty(custom["id"])
        await self.reply(interaction, f"✅ Va pour {embeds.hhmm(slot)}." if ok else "Le créneau est déjà choisi.")

    async def _guest(self, interaction, custom, _arg) -> None:
        if not self._organiser(interaction, custom):
            await self.reply(interaction, ORGANISER_ONLY)
            return
        await interaction.response.defer(ephemeral=True)
        taken = {s["person_id"] for s in await service.signups(custom["id"])}
        free = sorted(
            (p for p in (await self.persons(fresh=True)).values() if p.id not in taken), key=lambda p: p.name.lower()
        )

        async def picked(i: discord.Interaction, value: int | str) -> None:
            ok = await service.add_signup(custom["id"], None, int(value))
            self.mark_dirty(custom["id"])
            await i.response.send_message("✅ Ajouté." if ok else "Déjà inscrit.", ephemeral=True)

        await self.reply(
            interaction,
            "Ajouter un joueur sans Discord :",
            view=PersonPicker(free, self.guarded(picked), placeholder="Joueur…", allow_new=False),
        )

    async def _cancel(self, interaction, custom, _arg) -> None:
        if not (self._organiser(interaction, custom) or self.is_mod(interaction)):
            await self.reply(interaction, ORGANISER_OR_MOD)
            return
        await service.end(custom["id"], "cancelled")
        self.mark_dirty(custom["id"])
        await self.reply(interaction, "❌ Custom annulée.")

    # --- teams-phase actions (the evening) --------------------------------

    async def _roster_ids(self, cid: int) -> list[int]:
        players, _ = rules.split_roster(list(await service.signups(cid)))
        return [s["person_id"] for s in players]

    async def _current_game(self, cid: int):
        games = await service.games(cid)
        return games[-1] if games else None

    async def _generate(self, interaction, custom, _arg) -> None:
        if not self._organiser(interaction, custom):
            await self.reply(interaction, ORGANISER_ONLY)
            return
        if custom["status"] != "signup":
            await self.reply(interaction, "Les équipes sont déjà générées.")
            return
        ids = await self._roster_ids(custom["id"])
        if len(ids) != rules.PLAYERS:
            await self.reply(interaction, f"Il faut {rules.PLAYERS} inscrits (là : {len(ids)}).")
            return
        await interaction.response.defer(ephemeral=True)
        assert self.client is not None
        teams = await self.client.generate(ids)
        if await service.add_game(custom["id"], teams.to_json()) is None:
            await self.reply(interaction, "Une partie est déjà en cours (double clic ?).")
            return
        self.mark_dirty(custom["id"])
        await self.reply(interaction, "⚔️ Équipes générées.")

    async def _regenerate(self, interaction, custom, _arg) -> None:
        if not self._organiser(interaction, custom):
            await self.reply(interaction, ORGANISER_ONLY)
            return
        game = await self._current_game(custom["id"])
        if game is None or game["winner"] is not None:
            await self.reply(interaction, "Cette partie est déjà jouée.")
            return
        await interaction.response.defer(ephemeral=True)
        assert self.client is not None
        teams = await self.client.generate(Teams.from_json(_teams_json(game)).person_ids())
        await service.set_game_teams(custom["id"], game["number"], teams.to_json())
        self.mark_dirty(custom["id"])
        await self.reply(interaction, "🔄 Nouvelles équipes.")

    async def _swap(self, interaction, custom, _arg) -> None:
        if not self._organiser(interaction, custom):
            await self.reply(interaction, ORGANISER_ONLY)
            return
        game = await self._current_game(custom["id"])
        if game is None or game["winner"] is not None:
            await self.reply(interaction, "Cette partie est déjà jouée.")
            return
        await interaction.response.defer(ephemeral=True)  # persons() may call lol-amis: answer first
        current = Teams.from_json(_teams_json(game))
        persons = await self.persons()
        players = [(pid, persons[pid].name if pid in persons else str(pid)) for pid in current.person_ids()]

        async def do_swap(i: discord.Interaction, a: int, b: int) -> None:
            if not i.response.is_done():
                await i.response.defer(ephemeral=True)
            await self.swap_and_score(custom["id"], game["number"], current, a, b)
            await self.reply(i, "🔁 Échangés.")

        await self.reply(interaction, "Qui échange sa place ?", view=SwapPicker(players, self.guarded(do_swap)))

    async def swap_and_score(self, cid: int, number: int, current: Teams, a: int, b: int) -> Teams:
        assert self.client is not None
        scored = await self.client.evaluate(rules.swap(current, a, b))
        await service.set_game_teams(cid, number, scored.to_json())
        self.mark_dirty(cid)
        return scored

    async def _win(self, interaction, custom, arg) -> None:
        if not (self._organiser(interaction, custom) or self.is_mod(interaction)):
            await self.reply(interaction, ORGANISER_OR_MOD)
            return
        game = await self._current_game(custom["id"])
        if game is None:
            await self.reply(interaction, "Aucune partie en cours.")
            return
        winner = "blue" if arg == 0 else "red"
        await interaction.response.defer(ephemeral=True)
        # Claim first: two clicks (or two mods) must record the game once.
        if not await service.claim_winner(custom["id"], game["number"], winner):
            await self.reply(interaction, "Le gagnant de cette partie est déjà enregistré.")
            return
        ok = await self._push_result(custom["id"], game["number"], Teams.from_json(_teams_json(game)), winner, None)
        self.mark_dirty(custom["id"])
        await self.reply(
            interaction,
            "🏆 Enregistré." if ok else "🏆 Noté ici, mais lol-amis ne répond pas — `/custom corriger` réessaiera.",
        )

    async def _push_result(self, cid: int, number: int, teams: Teams, winner: str, lolamis_game_id: int | None) -> bool:
        """Send the result to lol-amis. False = lol-amis down; the local winner stands and is retryable."""
        assert self.client is not None
        try:
            if lolamis_game_id:
                await self.client.set_winner(lolamis_game_id, teams, winner)  # type: ignore[arg-type]
            else:
                # A timeout does not mean lol-amis didn't commit: look for this exact game before
                # posting it again, skipping games already tied to another game of the evening.
                known = {g["lolamis_game_id"] for g in await service.games(cid) if g["lolamis_game_id"]}
                gid = await self.client.find_game(teams, exclude=known)
                if gid is not None:
                    await self.client.set_winner(gid, teams, winner)  # type: ignore[arg-type]
                else:
                    gid = await self.client.record_game(teams, winner)  # type: ignore[arg-type]
                await service.set_lolamis_game_id(cid, number, gid)
        except LolAmisError as exc:
            logger.warning(f"Customs {cid} game {number}: result not pushed to lol-amis: {exc}")
            return False
        return True

    async def _next(self, interaction, custom, _arg) -> None:
        if not self._organiser(interaction, custom):
            await self.reply(interaction, ORGANISER_ONLY)
            return
        game = await self._current_game(custom["id"])
        if game is not None and game["winner"] is None:
            await self.reply(interaction, "Donne d'abord le gagnant de la partie en cours.")
            return
        ids = await self._roster_ids(custom["id"])
        if len(ids) != rules.PLAYERS:
            await self.reply(interaction, f"Il faut {rules.PLAYERS} inscrits (là : {len(ids)}).")
            return
        await interaction.response.defer(ephemeral=True)
        assert self.client is not None
        teams = await self.client.generate(ids)
        if await service.add_game(custom["id"], teams.to_json()) is None:
            await self.reply(interaction, "Une partie est déjà en cours (double clic ?).")
            return
        self.mark_dirty(custom["id"])
        await self.reply(interaction, "▶️ Partie suivante.")

    async def _end(self, interaction, custom, _arg) -> None:
        if not (self._organiser(interaction, custom) or self.is_mod(interaction)):
            await self.reply(interaction, ORGANISER_OR_MOD)
            return
        await service.end(custom["id"], "done")
        self.mark_dirty(custom["id"])
        await self.reply(interaction, "🏁 Fin de soirée.")

    # --- rendering --------------------------------------------------------

    async def render(self, cid: int) -> tuple[discord.Embed, discord.ui.View | None]:
        custom = await service.get(cid)
        try:
            persons = await self.persons()
        except LolAmisError:
            persons = self._persons  # degraded: stale names, or ids
        names = {pid: p.name for pid, p in persons.items()}
        ranks = {pid: p.rank_label for pid, p in persons.items()}
        games = list(await service.games(cid))
        score = rules.evening_score(games)

        if custom["status"] in ("done", "cancelled"):
            reason = "🏁 Fin de soirée." if custom["status"] == "done" else "❌ Annulée ou expirée."
            return embeds.build_closed_embed(custom, score=score, games=len(games), reason=reason), None

        if custom["status"] == "teams" and games:
            g = games[-1]
            decided = g["winner"] is not None
            embed = embeds.build_teams_embed(
                custom,
                number=g["number"],
                teams=Teams.from_json(_teams_json(g)),
                names=names,
                ranks=ranks,
                winner=g["winner"],
                score=score,
                unrecorded=decided and g["lolamis_game_id"] is None,
            )
            if decided:
                # Join/guest stay here: after a withdrawal with no waitlist the evening would
                # otherwise be stuck at 9, with nobody able to fill the gap.
                buttons = [
                    CustomButton("next", cid, label="Partie suivante", emoji="▶️", style=discord.ButtonStyle.primary),
                    CustomButton("join", cid, label="Je joue", emoji="✋", style=discord.ButtonStyle.success),
                    CustomButton("guest", cid, label="Invité", emoji="➕"),
                    CustomButton("leave", cid, label="Me retirer", emoji="🚪"),
                    CustomButton("end", cid, label="Fin de soirée", emoji="🏁", style=discord.ButtonStyle.danger),
                ]
            else:
                buttons = [
                    CustomButton("regen", cid, label="Regénérer", emoji="🔄"),
                    CustomButton("swap", cid, label="Échanger", emoji="🔁"),
                    CustomButton("win", cid, 0, label="Bleu gagne", emoji="🏆", style=discord.ButtonStyle.primary),
                    CustomButton("win", cid, 1, label="Rouge gagne", emoji="🏆", style=discord.ButtonStyle.danger),
                    CustomButton("end", cid, label="Fin de soirée", emoji="🏁"),
                ]
            return embed, card_view(buttons)

        signups = list(await service.signups(cid))
        players, waiting = rules.split_roster(signups)

        def label(s) -> str:
            return names.get(s["person_id"], f"#{s['person_id']}") + ("" if s["user_id"] else " (invité)")

        if custom["mode"] == "vote":
            slots = await service.slots(cid)
            votes = await service.votes(cid)
            counts = [(slot, len(votes.get(slot, []))) for slot in slots]
            embed = embeds.build_signup_embed(custom, players=[], waiting=[], votes=counts)
            buttons = [CustomButton("vote", cid, int(slot.timestamp()), label=embeds.hhmm(slot)) for slot in slots]
            buttons += [
                CustomButton("validate", cid, int(slot.timestamp()), label=f"Valider {embeds.hhmm(slot)}", emoji="✅")
                for slot in slots
            ]
            buttons.append(CustomButton("cancel", cid, label="Annuler", emoji="❌", style=discord.ButtonStyle.danger))
            return embed, card_view(buttons)

        embed = embeds.build_signup_embed(
            custom, players=[label(s) for s in players], waiting=[label(s) for s in waiting], votes=None
        )
        buttons = [
            CustomButton("join", cid, label="Je joue", emoji="✋", style=discord.ButtonStyle.success),
            CustomButton("leave", cid, label="Me retirer", emoji="🚪"),
            CustomButton("guest", cid, label="Invité", emoji="➕"),
            CustomButton(
                "gen",
                cid,
                label="Générer les équipes",
                emoji="⚔️",
                style=discord.ButtonStyle.primary,
                disabled=len(players) < rules.PLAYERS,
            ),
            CustomButton("cancel", cid, label="Annuler", emoji="❌", style=discord.ButtonStyle.danger),
        ]
        return embed, card_view(buttons)

    async def redraw(self, cid: int) -> None:
        custom = await service.get(cid)
        if custom is None or custom["message_id"] is None:
            return
        channel = self.bot.get_channel(custom["channel_id"])
        if channel is None:
            return
        embed, view = await self.render(cid)
        await edit_if_changed(
            channel.get_partial_message(custom["message_id"]), label="customs", embed=embed, view=view
        )

    # --- tickers ----------------------------------------------------------

    @tasks.loop(seconds=3)
    async def card_ticker(self) -> None:
        if self._ticking:
            return
        self._ticking = True
        try:
            dirty, self._dirty = self._dirty, set()
            for cid in dirty:
                try:
                    await self.redraw(cid)
                except Exception:
                    logger.exception(f"Customs: could not redraw card {cid}")
        finally:
            self._ticking = False

    @card_ticker.before_loop
    async def _before_cards(self) -> None:
        await self.bot.wait_until_ready()

    @card_ticker.error
    async def _cards_error(self, exc: BaseException) -> None:
        logger.opt(exception=exc).error("Customs card ticker crashed — restarting.")
        self.card_ticker.restart()

    @tasks.loop(minutes=1)
    async def minute_ticker(self) -> None:
        now = discord.utils.utcnow()
        for custom in await service.claim_reminders(now):
            await self._remind(custom)
        for custom in await service.expire_due(now):
            self.mark_dirty(custom["id"])

    async def _remind(self, custom) -> None:
        channel = self.bot.get_channel(custom["channel_id"])
        if channel is None:
            return
        players, _ = rules.split_roster(list(await service.signups(custom["id"])))
        mentions = " ".join(f"<@{s['user_id']}>" for s in players if s["user_id"])
        if mentions:
            await channel.send(
                f"⏰ Custom dans 15 min ({embeds.hhmm(custom['start_at'])}) : {mentions}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )

    @minute_ticker.before_loop
    async def _before_minute(self) -> None:
        await self.bot.wait_until_ready()

    @minute_ticker.error
    async def _minute_error(self, exc: BaseException) -> None:
        logger.opt(exception=exc).error("Customs minute ticker crashed — restarting.")
        self.minute_ticker.restart()


def _teams_json(game) -> dict:
    teams = game["teams"]
    if isinstance(teams, str):  # asyncpg returns jsonb as text without a codec
        import json

        return json.loads(teams)
    return teams


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomsCog(bot))
