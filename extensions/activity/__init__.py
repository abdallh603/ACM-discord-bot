"""Voice Activity: a shared tournament bracket inside a Discord voice room.

The /activity command is always registered. ACTIVITY_ENABLED=true (with DISCORD_CLIENT_ID and
DISCORD_CLIENT_SECRET) additionally starts the Activity web server and the App Launcher entry point.
"""
from __future__ import annotations

import discord
from discord import app_commands

from just_bot.core.api import ExtensionError, UserError

from . import setup_cli
from .config import ActivitySettings
from .data import MIGRATIONS, ActivityData
from .launch import launch_activity, register_entrypoint


class ActivityRuntime:
    """What the Activity server, snapshots and launcher use."""

    def __init__(self, bot, settings: ActivitySettings, tournaments, blitz):
        self.bot = bot
        self.settings = settings
        self.tournaments = tournaments
        self.blitz = blitz
        self.data = ActivityData(self)
        self.runner = None

    @property
    def db(self):
        return self.bot.store.db

    def get_guild(self, guild_id):
        return self.bot.get_guild(guild_id)

    def guild_enabled(self, guild_id):
        return self.bot.guild_enabled(guild_id)


def setup(ext):
    try:
        settings = ActivitySettings.from_env(ext.env, token=ext.bot.settings.token)
    except ValueError as exc:
        raise ExtensionError(str(exc)) from exc
    ext.migrate(MIGRATIONS)
    bot = ext.bot
    activity = ActivityRuntime(bot, settings, ext.require("tournament"), ext.require("blitz"))

    if settings.enabled:
        async def open_activity(interaction, panel):
            if interaction.guild_id != panel.guild_id:
                raise UserError("This tournament belongs to another server.")
            await launch_activity(activity, interaction, panel.tid)

        activity.tournaments.add_panel_button("activity", "Open voice activity", open_activity,
                                              style=discord.ButtonStyle.primary, row=1)

    @app_commands.command(name="activity", description="Open the shared Blitz tournament activity in your voice room")
    async def command(interaction: discord.Interaction, tournament_id: str | None = None):
        await launch_activity(activity, interaction, tournament_id)

    ext.add_command(command, order=130)
    ext.help("`/activity` — open the shared tournament Activity in your current voice room (operator setup required)",
             block="voice", order=10)

    async def startup():
        # Fail before command sync if the SDK bundle is missing or was changed.
        if settings.enabled:
            ready, message = setup_cli.sdk_status()
            if not ready:
                raise ValueError(message)

    async def started():
        if settings.enabled:
            if str(bot.application_id) != settings.client_id:
                raise ValueError("DISCORD_CLIENT_ID must match the bot application's ID.")
            await register_entrypoint(bot)
            from .server import start_server
            activity.runner = await start_server(activity)

    async def stopping():
        if activity.runner:
            runner, activity.runner = activity.runner, None
            await runner.cleanup()

    ext.on_startup(startup)
    ext.on_started(started)
    ext.on_stopping(stopping)
    return activity
