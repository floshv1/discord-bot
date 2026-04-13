import discord
from discord.ext import commands
from loguru import logger

from bot.core.config import Config
from bot.db.client import create_pool, run_migrations
from bot.db.models import load_migration


COGS = [
    "bot.cogs.logs.cog",
    "bot.cogs.moderation.cog",
]


class DiscordBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        self.config = config

        intents = discord.Intents.default()
        intents.members = True          # privileged — enable in Dev Portal
        intents.message_content = True  # privileged — enable in Dev Portal
        intents.voice_states = True
        intents.invites = True
        intents.moderation = True

        super().__init__(command_prefix=[], intents=intents)

    async def setup_hook(self) -> None:
        pool = await create_pool(self.config.database_url)
        migration_sql = load_migration("001_initial.sql")
        await run_migrations(pool, migration_sql)

        for cog in COGS:
            await self.load_extension(cog)
            logger.info(f"Loaded cog: {cog}")

        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info(f"Slash commands synced to guild {self.config.guild_id}.")

    async def on_ready(self) -> None:
        logger.info(f"Bot ready — logged in as {self.user} ({self.user.id})")
