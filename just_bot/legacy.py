"""TRANSITIONAL: features that have not moved to extensions/ yet.

They register through the same ExtensionContext API that extensions use. Each migration step
moves one install_* function into its extension; this module is deleted after the last one.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from .core.domain import UserError
from .core.extensions import ExtensionContext, Manifest

log = logging.getLogger(__name__)


async def defer(interaction: discord.Interaction, *, private: bool = False):
    await interaction.response.defer(ephemeral=private, thinking=True)


def install_legacy(bot):
    ext = ExtensionContext(bot, Manifest("legacy", "0", "Features not yet moved", None, __name__), bot.extensions)
    install_blitz(ext)
    install_tournament(ext)
    install_activity(ext)


# -- blitz -------------------------------------------------------------------------------
def install_blitz(ext):
    from .activities_store import Activities
    from .activity_commands import BlitzCommands
    from .blitz import Blitz
    from .blitz_discord import DiscordBlitz
    bot = ext.bot
    bot.activities = Activities(bot.store)
    bot.blitz = Blitz(bot.community, bot.activities, DiscordBlitz(bot))
    ext.add_command(BlitzCommands(bot), order=110)

    def private_game(interaction):
        return any(bot.blitz.can_access(match, interaction.user.id)
                   for match in bot.activities.in_channel(interaction.guild_id, interaction.channel_id))

    async def before_unlink(guild, user):
        if bot.activities.slot(guild, user):
            raise UserError("Cancel your blitz game and finish voice recovery before unlinking.")

    ext.hook("channel_exemption", private_game)
    ext.hook("before_unlink", before_unlink)

    async def voice_state_update(member, before, after):
        if not bot.guild_enabled(member.guild.id):
            return
        if before.channel != after.channel or (before.mute and not after.mute) or (before.deaf and not after.deaf):
            try:
                await bot.blitz.voice_changed(member.guild.id, member.id)
            except Exception:
                log.exception("Blitz voice update failed for %s", member.id)

    ext.listen("voice_state_update", voice_state_update)

    async def clock():
        try:
            for guild in bot.active_guilds():
                await bot.blitz.tick(guild.id)
                await bot.tournaments.tick(guild.id)
        except Exception:
            log.exception("Blitz clock failed; will retry")

    async def recover():
        await asyncio.gather(*(bot.blitz.recover(g.id) for g in bot.active_guilds()))

    ext.add_loop(clock, seconds=5, before=recover)
    for order, text in enumerate((
        "`/blitz menu` → select players → choose settings → **Accept & ready** — multiplayer races",
        "`/blitz host` — manage a game for mentioned players; optional manager",
        "Select multiple topics, set time, and toggle everyone-finishes and standings in the menu.",
        "A visible 10-second countdown comes before the problem and voice effects.",
        "`/blitz standings` — verified solve times and ranks",
        "Omit blitz minutes for unlimited time; `/blitz cancel` stops the game.",
        "`/blitz games` — simultaneous games; `/blitz cancel` releases voices",
    ), start=30):
        ext.help(text, block="games", order=order)
    ext.help("Organizers can use `/blitz solo` to test voice controls without another account.",
             block="organizers", order=10)


# -- tournaments -------------------------------------------------------------------------
def install_tournament(ext):
    from .tournament import Tournaments
    from .tournament_commands import DiscordTournaments, TournamentCommands
    bot = ext.bot
    bot.tournaments = Tournaments(bot.community, bot.blitz, DiscordTournaments(bot))
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
        from .blitz_views import restore_panels
        from .tournament_commands import restore_tournaments
        restore_panels(bot)
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
        await bot.blitz.close()
        for guild in bot.active_guilds():
            await bot.blitz.close(guild.id)

    ext.on_startup(startup)
    ext.on_started(started)
    ext.on_stopping(stopping)
    ext.on_shutdown(shutdown)
