import asyncio
import logging

import discord
from discord import app_commands
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

        self.tree.on_error = self.on_app_command_error
        await self.add_cog(General(self))
        await self.add_cog(Moderation(self, self.database))
        synced = await self.tree.sync()
        logger.info("Synchronized %s application commands.", len(synced))

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original_error = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        command_name = (
            interaction.command.qualified_name
            if interaction.command is not None
            else "unknown"
        )
        logger.error(
            "Application command /%s failed with %s: %s",
            command_name,
            type(original_error).__name__,
            original_error,
            exc_info=(
                type(original_error),
                original_error,
                original_error.__traceback__,
            ),
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "The command could not be completed because of an internal error.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "The command could not be completed because of an internal error.",
                    ephemeral=True,
                )
        except discord.DiscordException:
            logger.exception(
                "Discord rejected the error response for application command /%s.",
                command_name,
            )
        except Exception:
            logger.exception(
                "Could not send an error response for application command /%s.",
                command_name,
            )


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
