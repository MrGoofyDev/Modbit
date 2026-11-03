import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database.database import Case, Database

logger = logging.getLogger("modex.moderation")
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60
MAX_REASON_LENGTH = 500
DURATION_PATTERN = re.compile(r"^([1-9][0-9]*)([smhd])$")
DURATION_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: str) -> int | None:
    match = DURATION_PATTERN.fullmatch(value.strip().lower())
    if match is None:
        return None
    seconds = int(match.group(1)) * DURATION_MULTIPLIERS[match.group(2)]
    return seconds if 1 <= seconds <= MAX_TIMEOUT_SECONDS else None


def _format_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot, database: Database) -> None:
        self.bot = bot
        self.database = database

    async def _defer(
        self, interaction: discord.Interaction, *, ephemeral: bool = False
    ) -> None:
        await interaction.response.defer(ephemeral=ephemeral)

    async def _reply(
        self,
        interaction: discord.Interaction,
        message: str,
        *,
        ephemeral: bool | None = None,
    ) -> None:
        if ephemeral is None:
            ephemeral = not interaction.response.is_done()
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(message, ephemeral=ephemeral)

    async def _send_confirmation(
        self,
        interaction: discord.Interaction,
        action: str,
        target: discord.Member,
        moderator: discord.Member,
        case_id: str,
        duration_seconds: int | None = None,
    ) -> None:
        if action == "BAN":
            message = f"{target.mention} has been banned by {moderator.mention}"
        elif action == "KICK":
            message = f"{target.mention} has been kicked by {moderator.mention}"
        elif action == "TIMEOUT":
            duration = self._format_duration(duration_seconds or 0)
            message = (
                f"{target.mention} has been timed out for {duration} "
                f"by {moderator.mention}"
            )
        elif action == "WARN":
            message = f"{target.mention} has been warned by {moderator.mention}"
        elif action == "UNWARN":
            message = f"A warning has been removed from {target.mention} by {moderator.mention}"
        else:
            raise ValueError(f"Unsupported confirmation action: {action}")
        await interaction.followup.send(f"{message} • Case: {case_id}")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        units = (
            (86400, "day"),
            (3600, "hour"),
            (60, "minute"),
            (1, "second"),
        )
        for unit_seconds, unit_name in units:
            if seconds >= unit_seconds:
                value = seconds // unit_seconds
                return f"{value} {unit_name}{'' if value == 1 else 's'}"
        return "0 seconds"

    async def _guild_and_actor(
        self, interaction: discord.Interaction
    ) -> tuple[discord.Guild, discord.Member] | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await self._reply(interaction, "This command can only be used in a server.")
            return None
        return interaction.guild, interaction.user

    async def _authorize_member_action(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        permission: str,
        bot_permission: str,
    ) -> tuple[discord.Guild, discord.Member] | None:
        context = await self._guild_and_actor(interaction)
        if context is None:
            return None
        guild, actor = context

        if not getattr(actor.guild_permissions, permission):
            await self._reply(
                interaction, f"You need the **{permission.replace('_', ' ')}** permission."
            )
            return None
        if target.id == actor.id:
            await self._reply(interaction, "You cannot moderate yourself.")
            return None
        if self.bot.user is not None and target.id == self.bot.user.id:
            await self._reply(interaction, "You cannot moderate Modex.")
            return None
        if target.id == guild.owner_id:
            await self._reply(interaction, "The server owner cannot be moderated by the bot.")
            return None
        if actor.id != guild.owner_id and target.top_role >= actor.top_role:
            await self._reply(
                interaction, "You cannot moderate a member with an equal or higher role."
            )
            return None

        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            try:
                bot_member = await guild.fetch_member(self.bot.user.id)
            except discord.HTTPException:
                logger.exception("Could not retrieve Modex's server member.")
                await self._reply(interaction, "I could not verify my server permissions.")
                return None
        if bot_member is None or not getattr(bot_member.guild_permissions, bot_permission):
            await self._reply(
                interaction, f"I need the **{bot_permission.replace('_', ' ')}** permission."
            )
            return None
        if target.top_role >= bot_member.top_role:
            await self._reply(
                interaction, "I cannot moderate a member with an equal or higher role than me."
            )
            return None
        return guild, actor

    async def _authorize_channel_action(
        self, interaction: discord.Interaction, permission: str
    ) -> tuple[discord.Guild, discord.Member, discord.TextChannel] | None:
        context = await self._guild_and_actor(interaction)
        if context is None:
            return None
        guild, actor = context
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await self._reply(interaction, "Use this command in a server text channel.")
            return None
        channel_permissions = channel.permissions_for(actor)
        if not getattr(channel_permissions, permission):
            await self._reply(
                interaction, f"You need the **{permission.replace('_', ' ')}** permission here."
            )
            return None

        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            try:
                bot_member = await guild.fetch_member(self.bot.user.id)
            except discord.HTTPException:
                logger.exception("Could not retrieve Modex's server member.")
                await self._reply(interaction, "I could not verify my server permissions.")
                return None
        if bot_member is None or not getattr(channel.permissions_for(bot_member), permission):
            await self._reply(
                interaction, f"I need the **{permission.replace('_', ' ')}** permission here."
            )
            return None
        return guild, actor, channel

    async def _create_pending_case(
        self,
        interaction: discord.Interaction,
        action: str,
        reason: str,
        target: discord.Member | None = None,
        duration_seconds: int | None = None,
        expires_at: datetime | None = None,
        ephemeral_error: bool = False,
    ) -> Case | None:
        try:
            return await self.database.create_pending_case(
                server_id=interaction.guild_id or 0,
                target_user_id=target.id if target is not None else None,
                moderator_id=interaction.user.id,
                action=action,
                reason=reason,
                duration_seconds=duration_seconds,
                expires_at=expires_at.isoformat(timespec="seconds") if expires_at else None,
            )
        except sqlite3.Error:
            logger.exception("Could not save a pending %s case.", action)
            await self._reply(
                interaction,
                "The moderation action was not attempted because its case could not be saved.",
                ephemeral=ephemeral_error,
            )
            return None

    async def _finish_case(
        self, interaction: discord.Interaction, case: Case, status: str
    ) -> bool:
        try:
            await self.database.finish_case(case.case_id, status)
            return True
        except sqlite3.Error:
            logger.exception("Could not set case %s to %s.", case.case_id, status)
            return False

    async def _send_member_dm(
        self,
        target: discord.Member,
        guild: discord.Guild,
        moderator: discord.Member,
        action: str,
        reason: str,
        case: Case,
        duration: str | None = None,
        expires_at: datetime | None = None,
        details: str | None = None,
        dm_channel: discord.DMChannel | None = None,
    ) -> None:
        action_colors = {
            "BAN": discord.Color.red(),
            "KICK": discord.Color.orange(),
            "TIMEOUT": discord.Color.gold(),
            "WARN": discord.Color.orange(),
            "UNWARN": discord.Color.green(),
        }
        server_url = f"https://discord.com/channels/{guild.id}"
        server_name = discord.utils.escape_markdown(guild.name)
        server_value = f"[{server_name}]({server_url})"
        moderator_name = discord.utils.escape_markdown(moderator.display_name)
        embed = discord.Embed(
            title=f"Moderation Notice · {action}",
            description="If you are not a member of this server, you may need a valid server invite to join.",
            color=action_colors.get(action, discord.Color.blurple()),
            timestamp=datetime.fromisoformat(case.timestamp),
        )
        embed.add_field(name="Server", value=server_value, inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(
            name="Moderator",
            value=f"[{moderator_name}](https://discord.com/users/{moderator.id})",
            inline=True,
        )
        embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
        if duration is not None:
            embed.add_field(name="Duration", value=duration, inline=True)
        if expires_at is not None:
            embed.add_field(
                name="Expires",
                value=expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                inline=True,
            )
        if details:
            embed.add_field(name="Details", value=details, inline=False)
        embed.add_field(name="Case ID", value=case.case_id, inline=False)
        embed.set_footer(text=f"Modex · {_format_timestamp(case.timestamp)}")
        recipient = dm_channel or target
        try:
            await recipient.send(embed=embed)
        except discord.Forbidden as error:
            logger.warning(
                "DM delivery forbidden for case %s (user %s): %s: %s "
                "[HTTP %s, Discord code %s].",
                case.case_id,
                target.id,
                type(error).__name__,
                error.text,
                error.status,
                error.code,
            )
        except discord.HTTPException as error:
            logger.warning(
                "Discord API error delivering DM for case %s (user %s): %s: %s "
                "[HTTP %s, Discord code %s].",
                case.case_id,
                target.id,
                type(error).__name__,
                error.text,
                error.status,
                error.code,
            )
        except (discord.DiscordException, OSError) as error:
            logger.warning(
                "Could not deliver DM for case %s (user %s): %s: %s.",
                case.case_id,
                target.id,
                type(error).__name__,
                error,
            )
        except Exception:
            logger.exception(
                "Unexpected error delivering DM for case %s (user %s).",
                case.case_id,
                target.id,
            )

    async def _run_member_action(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        action: str,
        reason: str,
        permission: str,
        bot_permission: str,
        operation,
        duration: str | None = None,
        duration_seconds: int | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        authorized = await self._authorize_member_action(
            interaction, target, permission, bot_permission
        )
        if authorized is None:
            return
        guild, actor = authorized
        if len(reason) > MAX_REASON_LENGTH:
            await self._reply(interaction, f"Reason must be {MAX_REASON_LENGTH} characters or fewer.")
            return

        await self._defer(interaction)
        if action == "BAN":
            try:
                case = await self.database.reserve_case_id()
            except sqlite3.Error:
                logger.exception("Could not reserve a case ID for a ban.")
                await self._reply(
                    interaction,
                    "The ban was not attempted because a case ID could not be reserved.",
                )
                return

            await self._send_member_dm(
                target, guild, actor, action, reason, case, duration, expires_at
            )
            try:
                await operation()
            except discord.Forbidden:
                await self._reply(
                    interaction,
                    "Discord denied the ban. No moderation case was recorded.",
                )
                return
            except discord.HTTPException:
                await self._reply(
                    interaction,
                    "Discord could not complete the ban. No moderation case was recorded.",
                )
                return

            try:
                await self.database.create_completed_case(
                    case,
                    guild.id,
                    target.id,
                    actor.id,
                    action,
                    reason,
                    duration_seconds,
                    expires_at.isoformat(timespec="seconds") if expires_at else None,
                )
            except sqlite3.Error:
                logger.exception("Ban succeeded but case %s could not be saved.", case.case_id)
                await self._reply(
                    interaction,
                    f"The ban succeeded, but case {case.case_id} could not be saved.",
                )
                return

            await self._send_confirmation(
                interaction,
                "BAN",
                target,
                actor,
                case.case_id,
            )
            return

        case = await self._create_pending_case(
            interaction, action, reason, target, duration_seconds, expires_at
        )
        if case is None:
            return

        kick_dm_channel = None
        if action == "KICK":
            try:
                kick_dm_channel = await target.create_dm()
            except discord.Forbidden as error:
                logger.warning(
                    "Could not prepare kick DM for case %s (user %s): %s: %s "
                    "[HTTP %s, Discord code %s].",
                    case.case_id,
                    target.id,
                    type(error).__name__,
                    error.text,
                    error.status,
                    error.code,
                )
            except discord.HTTPException as error:
                logger.warning(
                    "Discord API error preparing kick DM for case %s (user %s): "
                    "%s: %s [HTTP %s, Discord code %s].",
                    case.case_id,
                    target.id,
                    type(error).__name__,
                    error.text,
                    error.status,
                    error.code,
                )
            except (discord.DiscordException, OSError) as error:
                logger.warning(
                    "Could not prepare kick DM for case %s (user %s): %s: %s.",
                    case.case_id,
                    target.id,
                    type(error).__name__,
                    error,
                )
            except Exception:
                logger.exception(
                    "Unexpected error preparing kick DM for case %s (user %s).",
                    case.case_id,
                    target.id,
                )
            if kick_dm_channel is not None:
                await self._send_member_dm(
                    target,
                    guild,
                    actor,
                    action,
                    reason,
                    case,
                    duration,
                    expires_at,
                    dm_channel=kick_dm_channel,
                )

        try:
            await operation()
        except discord.Forbidden:
            await self._finish_case(interaction, case, "failed")
            await self._reply(interaction, "Discord denied that action. Check my permissions and role position.")
            return
        except discord.HTTPException:
            await self._finish_case(interaction, case, "failed")
            await self._reply(interaction, "Discord could not complete that action. No change was confirmed.")
            return
        saved = await self._finish_case(interaction, case, "completed")
        if action != "KICK":
            await self._send_member_dm(
                target,
                guild,
                actor,
                action,
                reason,
                case,
                duration,
                expires_at,
            )
        if not saved:
            logger.error("Case %s remains pending after successful %s.", case.case_id, action)
        await self._send_confirmation(
            interaction,
            action,
            target,
            actor,
            case.case_id,
            duration_seconds,
        )

    @app_commands.command(name="ban", description="Ban a member from this server.")
    @app_commands.describe(user="The member to ban", reason="Why the member is being banned")
    async def ban(
        self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None
    ) -> None:
        actual_reason = (reason or "").strip() or "No reason provided"
        await self._run_member_action(
            interaction,
            user,
            "BAN",
            actual_reason,
            "ban_members",
            "ban_members",
            lambda: user.ban(reason=actual_reason),
        )

    @app_commands.command(name="kick", description="Kick a member from this server.")
    @app_commands.describe(user="The member to kick", reason="Why the member is being kicked")
    async def kick(
        self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None
    ) -> None:
        actual_reason = (reason or "").strip() or "No reason provided"
        await self._run_member_action(
            interaction,
            user,
            "KICK",
            actual_reason,
            "kick_members",
            "kick_members",
            lambda: user.kick(reason=actual_reason),
        )

    @app_commands.command(name="timeout", description="Temporarily prevent a member from interacting.")
    @app_commands.describe(user="The member to timeout", duration="Duration such as 30m, 2h, or 7d", reason="Why the member is being timed out")
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: str,
        reason: str | None = None,
    ) -> None:
        duration_seconds = parse_duration(duration)
        if duration_seconds is None:
            await self._reply(
                interaction,
                "Invalid duration. Use a positive number followed by `s`, `m`, `h`, or `d`, up to 28 days.",
            )
            return
        actual_reason = (reason or "").strip() or "No reason provided"
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        await self._run_member_action(
            interaction,
            user,
            "TIMEOUT",
            actual_reason,
            "moderate_members",
            "moderate_members",
            lambda: user.timeout(expires_at, reason=actual_reason),
            duration=duration.strip(),
            duration_seconds=duration_seconds,
            expires_at=expires_at,
        )

    @app_commands.command(name="warn", description="Issue a warning to a member.")
    @app_commands.describe(user="The member to warn", reason="Why the member is being warned")
    async def warn(
        self, interaction: discord.Interaction, user: discord.Member, reason: str
    ) -> None:
        authorized = await self._authorize_member_action(
            interaction, user, "moderate_members", "moderate_members"
        )
        if authorized is None:
            return
        if not reason.strip() or len(reason) > MAX_REASON_LENGTH:
            await self._reply(interaction, f"Enter a reason of 1–{MAX_REASON_LENGTH} characters.")
            return
        guild, actor = authorized
        await self._defer(interaction)
        try:
            case = await self.database.create_warning(
                guild.id, user.id, actor.id, reason.strip()
            )
        except sqlite3.Error:
            logger.exception("Could not save warning for user %s.", user.id)
            await self._reply(interaction, "The warning could not be saved; no warning was issued.")
            return
        await self._send_member_dm(user, guild, actor, "WARN", reason.strip(), case)
        await self._send_confirmation(
            interaction, "WARN", user, actor, case.case_id
        )

    @app_commands.command(name="unwarn", description="Remove a member's most recent active warning.")
    @app_commands.describe(user="The member whose warning to remove", reason="Why the warning is being removed")
    async def unwarn(
        self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None
    ) -> None:
        authorized = await self._authorize_member_action(
            interaction, user, "moderate_members", "moderate_members"
        )
        if authorized is None:
            return
        actual_reason = (reason or "").strip() or "No reason provided"
        if len(actual_reason) > MAX_REASON_LENGTH:
            await self._reply(interaction, f"Reason must be {MAX_REASON_LENGTH} characters or fewer.")
            return
        guild, actor = authorized
        await self._defer(interaction)
        try:
            result = await self.database.remove_latest_warning(
                guild.id, user.id, actor.id, actual_reason
            )
        except sqlite3.Error:
            logger.exception("Could not remove warning for user %s.", user.id)
            await self._reply(interaction, "The warning could not be removed.")
            return
        if result is None:
            await self._reply(interaction, f"{user.mention} has no active warnings.")
            return
        case, removed_warning_case = result
        await self._send_member_dm(
            user,
            guild,
            actor,
            "UNWARN",
            actual_reason,
            case,
            details=f"Removed warning case {removed_warning_case}.",
        )
        await self._send_confirmation(
            interaction,
            "UNWARN",
            user,
            actor,
            case.case_id,
        )

    @app_commands.command(name="purge", description="Delete recent messages from this channel.")
    @app_commands.describe(amount="Number of recent messages to delete (1–100)")
    async def purge(
        self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]
    ) -> None:
        if not 1 <= amount <= 100:
            await self._reply(interaction, "Amount must be between 1 and 100.")
            return
        authorized = await self._authorize_channel_action(interaction, "manage_messages")
        if authorized is None:
            return
        guild, _, channel = authorized
        await self._defer(interaction, ephemeral=True)
        reason = "No reason provided"
        case = await self._create_pending_case(
            interaction, "PURGE", reason, ephemeral_error=True
        )
        if case is None:
            return
        try:
            deleted = await channel.purge(limit=amount, reason=reason)
        except discord.Forbidden:
            await self._finish_case(interaction, case, "failed")
            await self._reply(
                interaction,
                "Discord denied message deletion. Check my channel permissions.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await self._finish_case(interaction, case, "failed")
            await self._reply(
                interaction, "Discord could not delete those messages.", ephemeral=True
            )
            return
        saved = await self._finish_case(interaction, case, "completed")
        status_note = "" if saved else " The case status could not be updated."
        await self._reply(
            interaction,
            f"Deleted {len(deleted)} message(s). Case: {case.case_id}.{status_note}",
            ephemeral=True,
        )

    @app_commands.command(name="slowmode", description="Set this channel's message slowmode.")
    @app_commands.describe(seconds="Slowmode in seconds (0–21600)")
    async def slowmode(
        self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]
    ) -> None:
        if not 0 <= seconds <= 21600:
            await self._reply(interaction, "Slowmode must be between 0 and 21600 seconds.")
            return
        authorized = await self._authorize_channel_action(interaction, "manage_channels")
        if authorized is None:
            return
        guild, _, channel = authorized
        await self._defer(interaction, ephemeral=True)
        reason = f"Slowmode set to {seconds} seconds by moderator"
        case = await self._create_pending_case(
            interaction, "SLOWMODE", reason, ephemeral_error=True
        )
        if case is None:
            return
        try:
            await channel.edit(slowmode_delay=seconds, reason=reason)
        except discord.Forbidden:
            await self._finish_case(interaction, case, "failed")
            await self._reply(
                interaction, "Discord denied that channel update.", ephemeral=True
            )
            return
        except discord.HTTPException:
            await self._finish_case(interaction, case, "failed")
            await self._reply(
                interaction, "Discord could not update this channel.", ephemeral=True
            )
            return
        saved = await self._finish_case(interaction, case, "completed")
        status_note = "" if saved else " The case status could not be updated."
        await self._reply(
            interaction,
            f"Slowmode set to {seconds} seconds. Case: {case.case_id}.{status_note}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    database = getattr(bot, "database", None)
    if not isinstance(database, Database):
        raise RuntimeError("The moderation cog requires an initialized Modex database.")
    await bot.add_cog(Moderation(bot, database))
