"""TRANSITIONAL: features that have not moved to extensions/ yet.

They register through the same ExtensionContext API that extensions use. Each migration step
moves one install_* function into its extension; this module is deleted after the last one.
"""
from __future__ import annotations

import discord
from discord import app_commands

from .core.extensions import ExtensionContext, Manifest


def install_legacy(bot):
    if "tournament" not in bot.extensions.apis:
        return  # the Activity needs the tournament and blitz extensions
    ext = ExtensionContext(bot, Manifest("legacy", "0", "Features not yet moved", None, __name__), bot.extensions)
    install_activity(ext)


# -- voice activity ----------------------------------------------------------------------
def install_activity(ext):
    from .activity_data import ActivityData
    from .activity_launch import launch_activity
    from .core.domain import UserError
    bot = ext.bot
    tournaments = bot.extensions.api("tournament")
    blitz = tournaments.blitz
    bot.tournaments, bot.blitz, bot.activities = tournaments.engine, blitz.engine, blitz.store
    bot.activity_data = ActivityData(bot)
    bot.activity_runner = None
    if bot.settings.activity_enabled:
        async def open_activity(interaction, panel):
            if interaction.guild_id != panel.guild_id:
                raise UserError("This tournament belongs to another server.")
            await launch_activity(bot, interaction, panel.tid)
        tournaments.add_panel_button("activity", "Open voice activity", open_activity,
                                     style=discord.ButtonStyle.primary, row=1)

    @app_commands.command(name="activity", description="Open the shared Blitz tournament activity in your voice room")
    async def activity(interaction: discord.Interaction, tournament_id: str | None = None):
        await launch_activity(bot, interaction, tournament_id)

    ext.add_command(activity, order=130)
    ext.help("`/activity` — open the shared tournament Activity in your current voice room (operator setup required)",
             block="voice", order=10)

    async def startup():
        if bot.settings.activity_enabled:
            from .activity_setup import sdk_status
            ready, message = sdk_status()
            if not ready:
                raise ValueError(message)

    async def started():
        if bot.settings.activity_enabled:
            if str(bot.application_id) != bot.settings.client_id:
                raise ValueError("DISCORD_CLIENT_ID must match the bot application's ID.")
            from .activity_launch import register_entrypoint
            await register_entrypoint(bot)
            from .activity_server import start_server
            bot.activity_runner = await start_server(bot)

    async def stopping():
        if bot.activity_runner:
            runner, bot.activity_runner = bot.activity_runner, None
            await runner.cleanup()

    ext.on_startup(startup)
    ext.on_started(started)
    ext.on_stopping(stopping)
