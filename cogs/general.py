import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Show Modex's response latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"Pong! Latency: **{latency_ms} ms**"
        )

    @app_commands.command(name="help", description="Show Modex's available commands.")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Modex commands",
            description="Moderation commands are available to moderators with the required server permissions.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="General",
            value="`/ping` — Show bot latency.\n`/help` — Show this command list.",
            inline=False,
        )
        embed.add_field(
            name="Moderation",
            value=(
                "`/ban user [reason]` — Ban a member.\n"
                "`/kick user [reason]` — Kick a member.\n"
                "`/timeout user duration [reason]` — Timeout a member (for example `30m`, `2h`, `7d`).\n"
                "`/warn user reason` — Add a warning.\n"
                "`/unwarn user [reason]` — Remove the member's most recent active warning.\n"
                "`/purge amount` — Delete 1–100 recent messages.\n"
                "`/slowmode seconds` — Set channel slowmode (0–21600 seconds)."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
