"""Customs buttons and pickers.

Card buttons are one `DynamicItem` that decodes its custom id from `customs:<action>:<id>[:<arg>]`
and hands the click to `CustomsCog.handle` — one registration routes every card, including
cards posted before a restart (CLAUDE.md: per-row views don't scale). Pickers are ephemeral and
keep a timeout.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import discord

from bot.cogs.customs.lolamis import Person

TEMPLATE = r"customs:(?P<action>[a-z]+):(?P<cid>\d+)(?::(?P<arg>\d+))?"
NOT_LISTED = "not_listed"
PAGE = 24  # 25 options per select, one kept for « Je ne suis pas dans la liste »


class CustomButton(discord.ui.DynamicItem[discord.ui.Button], template=TEMPLATE):
    def __init__(
        self,
        action: str,
        cid: int,
        arg: int | None = None,
        *,
        label: str = "…",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label, style=style, emoji=emoji, disabled=disabled, custom_id=self.make_id(action, cid, arg)
            )
        )
        self.action, self.cid, self.arg = action, cid, arg

    @staticmethod
    def make_id(action: str, cid: int, arg: int | None = None) -> str:
        return f"customs:{action}:{cid}" + (f":{arg}" if arg is not None else "")

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ) -> CustomButton:
        arg = int(match["arg"]) if match["arg"] else None
        return cls(match["action"], int(match["cid"]), arg, label=item.label or "…", style=item.style)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("CustomsCog")  # type: ignore[attr-defined]
        if cog is None:
            await interaction.response.send_message("Les customs ne sont pas disponibles.", ephemeral=True)
            return
        await cog.handle(interaction, self.action, self.cid, self.arg)


def card_view(buttons: list[CustomButton]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for b in buttons[:25]:
        view.add_item(b)
    return view


OnPick = Callable[[discord.Interaction, int | str], Awaitable[None]]


class PersonPicker(discord.ui.View):
    """Choose a lol-amis player, 24 at a time, optionally with « Je ne suis pas dans la liste »."""

    def __init__(self, persons: list[Person], on_pick: OnPick, *, placeholder: str, allow_new: bool) -> None:
        super().__init__(timeout=180)
        self.persons, self.on_pick, self.placeholder, self.allow_new = persons, on_pick, placeholder, allow_new
        self.page = 0
        self._draw()

    def _draw(self) -> None:
        self.clear_items()
        chunk = self.persons[self.page * PAGE : (self.page + 1) * PAGE]
        options = [
            discord.SelectOption(label=p.name[:100], value=str(p.id), description=(p.rank_label or None)) for p in chunk
        ]
        if self.allow_new:
            options.append(discord.SelectOption(label="Je ne suis pas dans la liste", value=NOT_LISTED, emoji="➕"))
        if options:
            select = discord.ui.Select(placeholder=self.placeholder, options=options)
            select.callback = self._picked
            self.add_item(select)
        if len(self.persons) > PAGE:
            for label, delta in (("◀", -1), ("▶", 1)):
                b = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
                b.callback = self._pager(delta)
                self.add_item(b)

    def _pager(self, delta: int):
        async def turn(interaction: discord.Interaction) -> None:
            last = (len(self.persons) - 1) // PAGE
            self.page = max(0, min(last, self.page + delta))
            self._draw()
            await interaction.response.edit_message(view=self)

        return turn

    async def _picked(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]  # type: ignore[index]
        await self.on_pick(interaction, value if value == NOT_LISTED else int(value))


class SwapPicker(discord.ui.View):
    """Two selects (player A, player B) over the ten players of the current game."""

    def __init__(
        self, players: list[tuple[int, str]], on_swap: Callable[[discord.Interaction, int, int], Awaitable[None]]
    ):
        super().__init__(timeout=180)
        self.on_swap, self.a, self.b = on_swap, None, None
        for attr, placeholder in (("a", "Joueur 1"), ("b", "Joueur 2")):
            select = discord.ui.Select(
                placeholder=placeholder, options=[discord.SelectOption(label=n[:100], value=str(p)) for p, n in players]
            )
            select.callback = self._chosen(attr)
            self.add_item(select)

    def _chosen(self, attr: str):
        async def chosen(interaction: discord.Interaction) -> None:
            setattr(self, attr, int(interaction.data["values"][0]))  # type: ignore[index]
            if self.a is not None and self.b is not None:
                await self.on_swap(interaction, self.a, self.b)
            else:
                await interaction.response.defer()

        return chosen
