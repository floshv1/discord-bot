from __future__ import annotations

import datetime
import json

import discord
from discord.ext import commands
from loguru import logger

from bot.core.config import Config
from bot.db.client import get_pool


def make_embed(color: discord.Color, title: str, details: str) -> discord.Embed:
    embed = discord.Embed(
        description=f"**{title}** — {details}",
        color=color,
        timestamp=datetime.datetime.now(datetime.UTC),
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

    def _is_ignored(self, channel_id: int) -> bool:
        return channel_id in self.config.log_ignored_channel_ids

    async def _log_to_db(
        self,
        guild_id: int,
        event_type: str,
        actor_id: int | None = None,
        target_id: int | None = None,
        channel_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        try:
            pool = get_pool()
            await pool.execute(
                """INSERT INTO audit_logs (guild_id, event_type, actor_id, target_id, channel_id, details)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                guild_id,
                event_type,
                actor_id,
                target_id,
                channel_id,
                json.dumps(details) if details is not None else None,
            )
        except Exception:
            logger.exception(f"Failed to persist audit log event '{event_type}' to DB")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or self._is_ignored(message.channel.id):
            return
        parts = []
        if message.content:
            parts.append(repr(message.content[:200]))
        if message.attachments:
            parts.append(", ".join(f"📎 {a.filename}" for a in message.attachments))
        content_str = " + ".join(parts) if parts else "''"
        details = f"{message.channel.mention} — {message.author.mention} — {content_str}"
        await self._send(make_embed(discord.Color.light_grey(), "Message Sent", details))
        await self._log_to_db(
            guild_id=message.guild.id,
            event_type="message_sent",
            actor_id=message.author.id,
            channel_id=message.channel.id,
            details={
                "content": message.content[:200],
                "attachments": [a.filename for a in message.attachments],
            },
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author.bot or before.guild is None or self._is_ignored(before.channel.id):
            return
        if before.content == after.content:
            return
        details = (
            f"{before.channel.mention} — {before.author.mention} — "
            f"before: {before.content[:100]!r} → after: {after.content[:100]!r}"
        )
        await self._send(make_embed(discord.Color.yellow(), "Message Edited", details))
        await self._log_to_db(
            guild_id=before.guild.id,
            event_type="message_edited",
            actor_id=before.author.id,
            channel_id=before.channel.id,
            details={
                "before": before.content[:200],
                "after": after.content[:200],
            },
        )

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or self._is_ignored(message.channel.id):
            return
        parts = []
        if message.content:
            parts.append(repr(message.content[:200]))
        if message.attachments:
            parts.append(", ".join(f"📎 {a.filename}" for a in message.attachments))
        content_str = " + ".join(parts) if parts else "''"
        details = f"{message.channel.mention} — {message.author.mention} — {content_str}"
        await self._send(make_embed(discord.Color.orange(), "Message Deleted", details))
        await self._log_to_db(
            guild_id=message.guild.id,
            event_type="message_deleted",
            actor_id=message.author.id,
            channel_id=message.channel.id,
            details={
                "content": message.content[:200],
                "attachments": [a.filename for a in message.attachments],
            },
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or messages[0].guild is None or self._is_ignored(messages[0].channel.id):
            return
        channel = messages[0].channel
        details = f"{channel.mention} — {len(messages)} messages deleted"
        await self._send(make_embed(discord.Color.orange(), "Bulk Delete", details))
        await self._log_to_db(
            guild_id=messages[0].guild.id,
            event_type="bulk_message_deleted",
            channel_id=messages[0].channel.id,
            details={"count": len(messages)},
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        age = (discord.utils.utcnow() - member.created_at).days
        details = f"{member.mention} ({member.id}) — account {age} days old"
        await self._send(make_embed(discord.Color.green(), "Member Joined", details))
        await self._log_to_db(
            guild_id=member.guild.id,
            event_type="member_joined",
            target_id=member.id,
            details={"account_age_days": age},
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        details = f"{member.mention} ({member.id})"
        await self._send(make_embed(discord.Color.red(), "Member Left", details))
        await self._log_to_db(
            guild_id=member.guild.id,
            event_type="member_left",
            target_id=member.id,
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        details = f"{user.mention} ({user.id})"
        await self._send(make_embed(discord.Color.dark_red(), "Member Banned", details))
        await self._log_to_db(
            guild_id=guild.id,
            event_type="member_banned",
            target_id=user.id,
            details={"guild_id_ctx": guild.id},
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        details = f"{user.mention} ({user.id})"
        await self._send(make_embed(discord.Color.teal(), "Member Unbanned", details))
        await self._log_to_db(
            guild_id=guild.id,
            event_type="member_unbanned",
            target_id=user.id,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            details = f"{after.mention} — nickname: {before.nick!r} → {after.nick!r}"
            await self._send(make_embed(discord.Color.purple(), "Nickname Changed", details))
            await self._log_to_db(
                guild_id=after.guild.id,
                event_type="nickname_changed",
                actor_id=after.id,
                details={"before": before.nick, "after": after.nick},
            )

        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        for role in added:
            await self._send(make_embed(discord.Color.purple(), "Role Added", f"{after.mention} — {role.mention}"))
            await self._log_to_db(
                guild_id=after.guild.id,
                event_type="role_added",
                actor_id=after.id,
                target_id=role.id,
                details={"role_name": role.name},
            )
        for role in removed:
            await self._send(make_embed(discord.Color.purple(), "Role Removed", f"{after.mention} — {role.mention}"))
            await self._log_to_db(
                guild_id=after.guild.id,
                event_type="role_removed",
                actor_id=after.id,
                target_id=role.id,
                details={"role_name": role.name},
            )

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
            await self._log_to_db(
                guild_id=member.guild.id,
                event_type="voice_joined",
                actor_id=member.id,
                channel_id=after.channel.id,
            )
        elif before.channel is not None and after.channel is None:
            details = f"{member.mention} ← {before.channel.mention}"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice Left", details))
            await self._log_to_db(
                guild_id=member.guild.id,
                event_type="voice_left",
                actor_id=member.id,
                channel_id=before.channel.id,
            )
        elif before.channel != after.channel and before.channel and after.channel:
            details = f"{member.mention} — {before.channel.mention} → {after.channel.mention}"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice Moved", details))
            await self._log_to_db(
                guild_id=member.guild.id,
                event_type="voice_moved",
                actor_id=member.id,
                details={"from_channel": before.channel.id, "to_channel": after.channel.id},
            )
        elif before.self_mute != after.self_mute:
            state = "muted" if after.self_mute else "unmuted"
            details = f"{member.mention} {state} themselves"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice State", details))
            await self._log_to_db(
                guild_id=member.guild.id,
                event_type="voice_muted" if after.self_mute else "voice_unmuted",
                actor_id=member.id,
            )
        elif before.self_deaf != after.self_deaf:
            state = "deafened" if after.self_deaf else "undeafened"
            details = f"{member.mention} {state} themselves"
            await self._send(make_embed(discord.Color.dark_blue(), "Voice State", details))
            await self._log_to_db(
                guild_id=member.guild.id,
                event_type="voice_deafened" if after.self_deaf else "voice_undeafened",
                actor_id=member.id,
            )

    # --- Channels ---
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(make_embed(discord.Color.blurple(), "Channel Created", f"{channel.mention} ({channel.name})"))
        await self._log_to_db(
            guild_id=channel.guild.id,
            event_type="channel_created",
            channel_id=channel.id,
            details={"name": channel.name},
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(make_embed(discord.Color.blurple(), "Channel Deleted", f"#{channel.name}"))
        await self._log_to_db(
            guild_id=channel.guild.id,
            event_type="channel_deleted",
            details={"name": channel.name},
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if before.name != after.name:
            details = f"{after.mention} — #{before.name} → #{after.name}"
            await self._send(make_embed(discord.Color.blurple(), "Channel Renamed", details))
            await self._log_to_db(
                guild_id=after.guild.id,
                event_type="channel_renamed",
                channel_id=after.id,
                details={"before": before.name, "after": after.name},
            )

    # --- Roles ---
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self._send(make_embed(discord.Color.purple(), "Role Created", role.mention))
        await self._log_to_db(
            guild_id=role.guild.id,
            event_type="role_created",
            target_id=role.id,
            details={"name": role.name},
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._send(make_embed(discord.Color.purple(), "Role Deleted", f"@{role.name}"))
        await self._log_to_db(
            guild_id=role.guild.id,
            event_type="role_deleted",
            target_id=role.id,
            details={"name": role.name},
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if before.name != after.name:
            details = f"{after.mention} — @{before.name} → @{after.name}"
            await self._send(make_embed(discord.Color.purple(), "Role Renamed", details))
            await self._log_to_db(
                guild_id=after.guild.id,
                event_type="role_renamed",
                target_id=after.id,
                details={"before": before.name, "after": after.name},
            )

    # --- Invites ---
    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        inviter = invite.inviter.mention if invite.inviter else "unknown"
        details = f"{invite.url} — by {inviter} — uses: {invite.max_uses or '∞'}"
        await self._send(make_embed(discord.Color.light_grey(), "Invite Created", details))
        await self._log_to_db(
            guild_id=self.config.guild_id,
            event_type="invite_created",
            actor_id=invite.inviter.id if invite.inviter else None,
            details={"url": invite.url, "max_uses": invite.max_uses},
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        await self._send(make_embed(discord.Color.light_grey(), "Invite Deleted", invite.url))
        await self._log_to_db(
            guild_id=self.config.guild_id,
            event_type="invite_deleted",
            details={"url": invite.url},
        )

    # --- Threads ---
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        details = f"{thread.mention} in {thread.parent.mention if thread.parent else '#unknown'}"
        await self._send(make_embed(discord.Color.teal(), "Thread Created", details))
        await self._log_to_db(
            guild_id=thread.guild.id,
            event_type="thread_created",
            channel_id=thread.id,
            details={"name": thread.name, "parent_id": thread.parent_id},
        )

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        parent = thread.parent.mention if thread.parent else "#unknown"
        await self._send(make_embed(discord.Color.teal(), "Thread Deleted", f"#{thread.name} in {parent}"))
        await self._log_to_db(
            guild_id=thread.guild.id,
            event_type="thread_deleted",
            details={"name": thread.name, "parent_id": thread.parent_id},
        )

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        if before.archived != after.archived:
            state = "archived" if after.archived else "unarchived"
            await self._send(make_embed(discord.Color.teal(), f"Thread {state.title()}", after.mention))
            await self._log_to_db(
                guild_id=after.guild.id,
                event_type="thread_archived" if after.archived else "thread_unarchived",
                channel_id=after.id,
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.application_command:
            return
        if interaction.guild_id is None:
            return
        name = interaction.command.qualified_name if interaction.command else "unknown"
        ns = vars(interaction.namespace) if interaction.namespace else {}
        opts = ", ".join(f"{k}={v}" for k, v in ns.items() if v is not None) or "—"
        channel_mention = getattr(interaction.channel, "mention", "unknown")
        details = f"{interaction.user.mention} → `/{name}` in {channel_mention} | {opts}"
        await self._send(make_embed(discord.Color.og_blurple(), "⌨️ Command", details))
        await self._log_to_db(
            guild_id=interaction.guild_id,
            event_type="slash_command",
            actor_id=interaction.user.id,
            channel_id=interaction.channel_id,
            details={"command": name, "options": {k: str(v) for k, v in ns.items() if v is not None}},
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LogsCog(bot))
