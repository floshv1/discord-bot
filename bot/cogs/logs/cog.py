from __future__ import annotations

import datetime
import discord
from discord.ext import commands
from loguru import logger

from bot.core.config import Config
from bot.db.client import get_pool


def make_embed(color: discord.Color, title: str, details: str) -> discord.Embed:
    embed = discord.Embed(
        description=f"**{title}** — {details}",
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    return embed


class LogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: Config = bot.config  # type: ignore[attr-defined]

    async def _send(self, embed: discord.Embed) -> None:
        channel = self.bot.get_channel(self.config.log_channel_id)
        if channel is None:
            logger.warning(f"Log channel {self.config.log_channel_id} not found.")
            return
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        details = f"{message.channel.mention} — {message.author.mention} — {message.content[:200]!r}"
        await self._send(make_embed(discord.Color.light_grey(), "Message Sent", details))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author.bot or before.guild is None:
            return
        if before.content == after.content:
            return
        details = (
            f"{before.channel.mention} — {before.author.mention} — "
            f"before: {before.content[:100]!r} → after: {after.content[:100]!r}"
        )
        await self._send(make_embed(discord.Color.yellow(), "Message Edited", details))

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        details = f"{message.channel.mention} — {message.author.mention} — {message.content[:200]!r}"
        await self._send(make_embed(discord.Color.orange(), "Message Deleted", details))

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or messages[0].guild is None:
            return
        channel = messages[0].channel
        details = f"{channel.mention} — {len(messages)} messages deleted"
        await self._send(make_embed(discord.Color.orange(), "Bulk Delete", details))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        age = (discord.utils.utcnow() - member.created_at).days
        details = f"{member.mention} ({member.id}) — account {age} days old"
        await self._send(make_embed(discord.Color.green(), "Member Joined", details))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        details = f"{member.mention} ({member.id})"
        await self._send(make_embed(discord.Color.red(), "Member Left", details))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        details = f"{user.mention} ({user.id})"
        await self._send(make_embed(discord.Color.dark_red(), "Member Banned", details))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        details = f"{user.mention} ({user.id})"
        await self._send(make_embed(discord.Color.teal(), "Member Unbanned", details))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            details = f"{after.mention} — nickname: {before.nick!r} → {after.nick!r}"
            await self._send(make_embed(discord.Color.purple(), "Nickname Changed", details))

        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        for role in added:
            await self._send(make_embed(discord.Color.purple(), "Role Added", f"{after.mention} — {role.mention}"))
        for role in removed:
            await self._send(make_embed(discord.Color.purple(), "Role Removed", f"{after.mention} — {role.mention}"))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel is None and after.channel is not None:
            details = f"{member.mention} → {after.channel.mention}"
            await self._send(make_embed(discord.Color.blue(), "Voice Joined", details))
        elif before.channel is not None and after.channel is None:
            details = f"{member.mention} ← {before.channel.mention}"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice Left", details))
        elif before.channel != after.channel and before.channel and after.channel:
            details = f"{member.mention} — {before.channel.mention} → {after.channel.mention}"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice Moved", details))
        elif before.self_mute != after.self_mute:
            state = "muted" if after.self_mute else "unmuted"
            details = f"{member.mention} {state} themselves"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice State", details))
        elif before.self_deaf != after.self_deaf:
            state = "deafened" if after.self_deaf else "undeafened"
            details = f"{member.mention} {state} themselves"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice State", details))

    # --- Channels ---
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(make_embed(discord.Color.blurple(), "Channel Created", f"{channel.mention} ({channel.name})"))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(make_embed(discord.Color.blurple(), "Channel Deleted", f"#{channel.name}"))

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if before.name != after.name:
            details = f"{after.mention} — #{before.name} → #{after.name}"
            await self._send(make_embed(discord.Color.blurple(), "Channel Renamed", details))

    # --- Roles ---
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self._send(make_embed(discord.Color.purple(), "Role Created", role.mention))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._send(make_embed(discord.Color.purple(), "Role Deleted", f"@{role.name}"))

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if before.name != after.name:
            details = f"{after.mention} — @{before.name} → @{after.name}"
            await self._send(make_embed(discord.Color.purple(), "Role Renamed", details))

    # --- Invites ---
    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        details = f"{invite.url} — by {invite.inviter.mention if invite.inviter else 'unknown'} — uses: {invite.max_uses or '∞'}"
        await self._send(make_embed(discord.Color.light_grey(), "Invite Created", details))

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        await self._send(make_embed(discord.Color.light_grey(), "Invite Deleted", invite.url))

    # --- Threads ---
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        details = f"{thread.mention} in {thread.parent.mention if thread.parent else '#unknown'}"
        await self._send(make_embed(discord.Color.teal(), "Thread Created", details))

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        parent = thread.parent.mention if thread.parent else '#unknown'
        await self._send(make_embed(discord.Color.teal(), "Thread Deleted", f"#{thread.name} in {parent}"))

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        if before.archived != after.archived:
            state = "archived" if after.archived else "unarchived"
            await self._send(make_embed(discord.Color.teal(), f"Thread {state.title()}", after.mention))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LogsCog(bot))
