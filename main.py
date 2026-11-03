import asyncio
import logging

import discord
from discord.ext import commands

from config import get_discord_token
from database.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("modex")


class Modex(commands.Bot):
    def __init__(self, database: Database) -> None:
        intents = discord.Intents.default()
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.database = database

    async def setup_hook(self) -> None:
        from cogs.general import General
        from cogs.moderation import Moderation

        await self.add_cog(General(self))
        await self.add_cog(Moderation(self, self.database))
        synced = await self.tree.sync()
        logger.info("Synchronized %s application commands.", len(synced))


async def run_bot(token: str) -> None:
    database = Database()
    await database.initialize()
    async with Modex(database) as bot:
        await bot.start(token)


def main() -> None:
    try:
        token = get_discord_token()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error

    try:
        asyncio.run(run_bot(token))
    except KeyboardInterrupt:
        logger.info("Modex stopped.")


if __name__ == "__main__":
    main()
