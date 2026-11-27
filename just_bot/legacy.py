"""TRANSITIONAL: features that have not moved to extensions/ yet.

They register through the same ExtensionContext API that extensions use. Each migration step
moves one install_* function into its extension; this module is deleted after the last one.
"""
from __future__ import annotations

import discord
from discord import app_commands

from .core.extensions import ExtensionContext, Manifest


def install_legacy(bot):
    if "blitz" not in bot.extensions.apis:
        return  # tournaments and the Activity need the blitz extension
    ext = ExtensionContext(bot, Manifest("legacy", "0", "Features not yet moved", None, __name__), bot.extensions)
    install_tournament(ext)
    install_activity(ext)


# -- tournaments -------------------------------------------------------------------------
def install_tournament(ext):
    from .tournament import Tournaments
    from .tournament_commands import DiscordTournaments, TournamentCommands
    bot = ext.bot
    blitz = bot.extensions.api("blitz")
    bot.blitz_runtime, bot.blitz, bot.activities = blitz, blitz.engine, blitz.store
    bot.tournaments = Tournaments(bot.community, bot.blitz, DiscordTournaments(bot))
    blitz.add_reservation_check(bot.tournaments.check_reservation)
    blitz.on_tick(bot.tournaments.tick)
    ext.add_command(TournamentCommands(bot), order=120)
    ext.help("`/tournament create` — organizer: signup buttons, 2–32 players, automatic brackets",
             block="events", order=10)
    ext.help("`/tournament list` / `bracket` — view events; `retry` / `resolve` — manager decisions",
             block="events", order=20)
    ext.help("Each tournament duel has Accept buttons and the Blitz countdown.", block="events", order=30)


# -- voice activity ----------------------------------------------------------------------
def install_activity(ext):
    from .activity_data import ActivityData
    from .activity_launch import launch_activity
    bot = ext.bot
    bot.activity_data = ActivityData(bot)
    bot.activity_runner = None

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
        from .tournament_commands import restore_tournaments
        restore_tournaments(bot)

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

    async def shutdown():
        await bot.tournaments.close()

    ext.on_startup(startup)
    ext.on_started(started)
    ext.on_stopping(stopping)
    ext.on_shutdown(shutdown)
