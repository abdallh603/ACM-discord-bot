"""Launch in the player's actual voice room, including from a text-channel button."""
from __future__ import annotations

import discord

from .core.domain import UserError


async def launch_activity(bot, interaction, tournament_id=None):
    if not bot.settings.activity_enabled:
        raise UserError("The voice activity is not configured yet. The operator can follow ACTIVITY.md; the bot's existing commands still work.")
    if interaction.guild_id is None or not bot.guild_enabled(interaction.guild_id):
        raise UserError("Open the activity in a server where RoundForge is enabled.")
    voice = getattr(interaction.user, "voice", None)
    channel = voice.channel if voice else None
    if channel is None or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        raise UserError("Join a server voice channel first, then open the activity.")
    if tournament_id:
        tournament = bot.tournaments.data.get(interaction.guild_id, tournament_id)
        text = interaction.guild.get_channel(tournament["channel_id"])
        if text is None or not text.permissions_for(interaction.user).view_channel:
            raise UserError("You cannot view this tournament's text channel.")
        target = tournament["options"].get("voice_channel_id")
        if tournament["options"].get("move_voice", True) and target and target != channel.id:
            raise UserError(f"Join <#{target}> first so the activity stays in the tournament's game voice room.")
    if interaction.channel_id == channel.id:
        if tournament_id:
            bot.activity_data.remember_launch(interaction.guild_id, channel.id, tournament_id)
        await interaction.response.launch_activity()
        return
    if not channel.permissions_for(interaction.user).create_instant_invite:
        raise UserError("Run /activity in the voice channel's chat to launch directly. You cannot create a voice-room invite here.")
    if not channel.permissions_for(interaction.guild.me).create_instant_invite:
        raise UserError("Grant Create Invite in your voice room, or run /activity in that voice channel's chat.")
    if tournament_id:
        bot.activity_data.remember_launch(interaction.guild_id, channel.id, tournament_id)
    await interaction.response.defer(ephemeral=True, thinking=True)
    invite = await channel.create_invite(target_type=discord.InviteTarget.embedded_application,
        target_application_id=int(bot.settings.client_id), max_age=900, max_uses=0,
        unique=False, reason="Launch RoundForge in the player's existing voice room")
    view = discord.ui.View(timeout=900)
    view.add_item(discord.ui.Button(label=f"Open in {channel.name}"[:80], url=invite.url, style=discord.ButtonStyle.link))
    await interaction.followup.send("Open the shared Blitz room. Friends can join it from the same voice channel.", view=view, ephemeral=True)


def install_activity_launch(bot):
    @bot.tree.command(name="activity", description="Open the shared Blitz tournament activity in your voice room")
    async def activity(interaction: discord.Interaction, tournament_id: str | None = None):
        await launch_activity(bot, interaction, tournament_id)


async def register_entrypoint(bot):
    # discord.py's tree sync only knows slash/context commands and replaces the global list.
    # Recreate Discord's handled entry point after every sync so the Activity stays in App Launcher.
    await bot.http.upsert_global_command(bot.application_id, payload={
        "name": "Launch", "description": "Open the RoundForge Blitz room", "type": 4, "handler": 2,
        "contexts": [0], "integration_types": [0],
    })
