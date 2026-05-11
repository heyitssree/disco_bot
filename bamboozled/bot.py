"""Entry point for the Bamboozled Discord bot."""
import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db.database import get_orphaned_channels, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()
_TOKEN = os.getenv("DISCORD_TOKEN")


class BamboozledBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        await self.load_extension("cogs.game")
        # Sync slash commands globally.
        # For faster dev iteration, replace with: await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        synced = await self.tree.sync()
        logger.info("Synced %d slash command(s).", len(synced))

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)

        # Announce orphaned games from before the last restart
        orphaned = await get_orphaned_channels()
        for cid_str in orphaned:
            cid = int(cid_str)
            channel = self.get_channel(cid)
            if channel:
                try:
                    await channel.send(
                        "⚠️ **The bot restarted mid-game.** "
                        "The previous Bamboozled game has been cancelled. "
                        "Use `/bamboozled join` to start a new one!"
                    )
                except discord.HTTPException:
                    logger.warning("Could not send restart notice to channel %s", cid)

        logger.info("Bamboozled bot is ready!")


def main():
    if not _TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN not set. Create a .env file with DISCORD_TOKEN=your_token_here"
        )
    bot = BamboozledBot()
    bot.run(_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
