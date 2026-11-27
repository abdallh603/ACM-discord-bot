"""/daily, /contests and /admin configure."""
from __future__ import annotations

import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from just_bot.core.api import UserError, rating_choices, timezone_choices

from .presentation import contest_embed, daily_embed
from .service import DailyService, upcoming_contests
from .store import DailyStore


async def defer(interaction: discord.Interaction, *, private: bool = False):
    await interaction.response.defer(ephemeral=private, thinking=True)


class Daily(app_commands.Group):
    def __init__(self, service: DailyService):
        super().__init__(name="daily", description="The RoundForge daily programming challenge")
        self.service = service

    @app_commands.command(description="Show today's shared RoundForge problem")
    async def show(self, interaction: discord.Interaction):
        await defer(interaction)
        daily = await self.service.daily(interaction.guild_id)
        await interaction.followup.send(embed=daily_embed(daily))

    @app_commands.command(description="Verify your accepted practice submission and earn 10 points")
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def claim(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        submission = await self.service.claim(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(
            f"Accepted! **+10 daily points** for RoundForge. "
            f"[Submission](https://codeforces.com/contest/{submission['problem']['contestId']}"
            f"/submission/{submission['id']}) · See `/leaderboard mode:daily`.", ephemeral=True,
        )


def configure_command(bot, store: DailyStore):
    @app_commands.command(name="configure", description="Set server feeds and timezone; omitted channels turn those feeds off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.autocomplete(timezone=timezone_choices, daily_rating=rating_choices)
    async def configure(
        interaction: discord.Interaction,
        reminders_channel: discord.TextChannel | None = None,
        daily_channel: discord.TextChannel | None = None,
        daily_rating: app_commands.Range[int, 800, 3500] = 1200,
        daily_hour: app_commands.Range[int, 0, 23] = 9,
        timezone: str | None = None,
    ):
        if daily_rating % 100:
            raise UserError("Problem rating must be a multiple of 100, such as 1000 or 1200.")
        if timezone:
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise UserError("Choose an IANA timezone such as UTC or Asia/Amman.") from exc
        for channel in (reminders_channel, daily_channel):
            if channel:
                permissions = channel.permissions_for(interaction.guild.me)
                if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                    raise UserError(f"I need View Channel, Send Messages, and Embed Links in {channel.mention}.")
        store.configure(
            interaction.guild_id,
            reminders_channel.id if reminders_channel else None,
            daily_channel.id if daily_channel else None, daily_rating, daily_hour,
        )
        if timezone:
            bot.store.set_timezone(interaction.guild_id, timezone)
        zone = bot.store.timezone(interaction.guild_id).key
        await interaction.response.send_message(
            f"Saved. Contest reminders: {reminders_channel.mention if reminders_channel else 'off'}. "
            f"Daily post: {daily_channel.mention if daily_channel else 'off'} at {daily_hour:02}:00 "
            f"{zone}, rating {daily_rating}. An already selected daily stays fixed until its deadline.",
            ephemeral=True,
        )
    return configure


def contests_command(bot):
    @app_commands.command(description="See upcoming Codeforces contests in your server timezone")
    async def contests(interaction: discord.Interaction):
        await defer(interaction)
        upcoming = upcoming_contests(await bot.cf.contests(), int(time.time()))[:5]
        if not upcoming:
            await interaction.followup.send("No upcoming Codeforces contests are listed right now.")
            return
        await interaction.followup.send(embeds=[contest_embed(c, bot.store.timezone(interaction.guild_id)) for c in upcoming])
    return contests
