"""Tournament commands, persistent signup controls, and Discord artwork attachments."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from .blitz_options import VOICE_LABELS, duration_label, voice_mode
from .core.options import TOPICS
from .blitz_views import send_invite
from .core.options import rating_choices
from .core.domain import UserError
from .core.presentation import embed, safe
from .tournament_image import TournamentPictures

log = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name("assets")


async def tournament_topics(interaction, current):
    parts = [part.strip().lower() for part in current.split(",")]
    previous, query = parts[:-1], parts[-1]
    values = [", ".join([*previous, topic]) for topic in TOPICS if query in topic and topic not in previous]
    return [app_commands.Choice(name=value, value=value) for value in values if len(value) <= 100][:25]


def tournament_embed(bot, tournament, round_no=None):
    data = bot.tournaments.data
    entries = data.entries(tournament["id"])
    options = tournament["options"]
    details = (f"**{safe(tournament['name'])}** · `{tournament['id']}` · **{tournament['state']}**\n"
               f"Host: <@{tournament['creator_id']}> · Manager: <@{options.get('manager_id') or tournament['creator_id']}>\n"
               f"Players: **{len(entries)}/{tournament['capacity']}** · single elimination · random seeding\n"
               f"Per match: **{duration_label(options['minutes'])}** · first solve wins\n"
               f"Rating: **{options['rating_min'] or options['rating_max'] or 'automatic'}–"
               f"{options['rating_max'] or options['rating_min'] or 'automatic'}** · Topics: **"
               f"{safe(', '.join(options.get('tags', [])) or options.get('tag') or ('random' if options['random_topic'] else 'any'))}**\n"
               f"Voice: **{VOICE_LABELS[voice_mode(options)]}**\n\n")
    if tournament["state"] == "registration":
        details += ("Click **Join** to register with your verified Codeforces account. "
                    "You can leave before the host starts. Each duel still needs both players to accept, "
                    "then shows the usual countdown.\n\n")
        details += " ".join(f"<@{e['user_id']}>" for e in entries) or "No players yet."
    else:
        shown = round_no or tournament["current_round"]
        pairs = data.pairs(tournament["id"], shown)
        details += f"**Round {shown}**\n"
        for pair in pairs:
            names = " vs ".join(f"<@{p}>" if p else "BYE" for p in (pair["player_a"], pair["player_b"]))
            result = f" → <@{pair['winner_id']}>" if pair["winner_id"] else ""
            game = f" · Blitz `{pair['blitz_id']}`" if pair["blitz_id"] else ""
            details += f"**{pair['position']}.** {names} · **{pair['state']}**{result}{game}\n"
            if (pair["state"] in {"ready", "blocked"} or pair["note"].startswith("Manager decision")) and pair["note"]:
                details += f"  {safe(pair['note'], 120)}\n"
        if not pairs:
            details += "No matches in this round.\n"
        if tournament["champion_id"]:
            details += f"\n🏆 **Champion: <@{tournament['champion_id']}>**"
        if any(p["state"] == "blocked" for p in pairs):
            details += "\nManager: use `/tournament retry` or `/tournament resolve` for blocked matches."
    return embed("RoundForge Blitz Tournament", details)


class TournamentButton(discord.ui.Button):
    def __init__(self, panel, action, label, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style, custom_id=f"rf:tournament:{panel.guild_id}:{panel.tid}:{action}")
        self.panel, self.action_name = panel, action

    async def callback(self, interaction):
        await self.panel.act(interaction, self.action_name)


class TournamentPanel(discord.ui.View):
    def __init__(self, bot, tournament):
        super().__init__(timeout=None)
        self.bot, self.guild_id, self.tid = bot, tournament["guild_id"], tournament["id"]
        if tournament["state"] == "registration":
            for action, label, style in [("join", "Join", discord.ButtonStyle.success),
                                         ("leave", "Leave", discord.ButtonStyle.secondary),
                                         ("start", "Start tournament", discord.ButtonStyle.primary)]:
                self.add_item(TournamentButton(self, action, label, style))
        if tournament["state"] in {"registration", "running"}:
            self.add_item(TournamentButton(self, "cancel", "Cancel tournament", discord.ButtonStyle.danger))
        self.add_item(TournamentButton(self, "bracket", "Bracket / status"))
        if getattr(getattr(bot, "settings", None), "activity_enabled", False):
            button = TournamentButton(self, "activity", "Open voice activity", discord.ButtonStyle.primary)
            button.row = 1
            self.add_item(button)

    async def act(self, interaction, action):
        if action == "activity":
            if interaction.guild_id != self.guild_id:
                raise UserError("This tournament belongs to another server.")
            from .activity_launch import launch_activity
            await launch_activity(self.bot, interaction, self.tid)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id != self.guild_id or not self.bot.guild_enabled(self.guild_id):
            raise UserError("This tournament belongs to a different or disabled server.")
        engine, actor = self.bot.tournaments, interaction.user.id
        manager = {"organizer": interaction.permissions.manage_guild}
        if action == "join":
            if interaction.user.bot:
                raise UserError("Only human players can join.")
            await engine.join(self.guild_id, self.tid, actor)
        elif action == "leave":
            await engine.leave(self.guild_id, self.tid, actor)
        elif action == "start":
            await engine.start(self.guild_id, self.tid, actor, **manager)
        elif action == "cancel":
            await engine.cancel(self.guild_id, self.tid, actor, **manager)
        elif action == "bracket":
            tournament = engine.data.get(self.guild_id, self.tid)
            await send_bracket(self.bot, tournament, interaction.followup.send,
                               with_image=interaction.app_permissions.attach_files)
            return
        await interaction.followup.send(embed=tournament_embed(self.bot, engine.data.get(self.guild_id, self.tid)), ephemeral=True)

    async def on_error(self, interaction, error, item):
        if not isinstance(error, UserError):
            log.error("Tournament control failed", exc_info=(type(error), error, error.__traceback__))
        message = safe(error, 1000) if isinstance(error, UserError) else "Could not update the tournament. Try /tournament bracket."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class DiscordTournaments:
    def __init__(self, bot):
        self.bot = bot
        self.rendered = {}
        self.pictures = TournamentPictures(bot)

    async def invite(self, tournament, match):
        guild = self.bot.get_guild(tournament["guild_id"])
        room = guild.get_channel(tournament["channel_id"]) if guild else None
        if room is None:
            raise UserError("Tournament channel is unavailable. Restore access, or cancel the tournament.")
        await send_invite(self.bot, match, room.send, [p["user_id"] for p in self.bot.activities.players(match["id"])])

    async def update(self, tournament):
        data = self.bot.tournaments.data
        entries, pairs = data.entries(tournament["id"]), data.pairs(tournament["id"])
        games = game_states(self.bot, tournament, pairs)
        signature = str((tournament["state"], tournament["current_round"], tournament["champion_id"], entries, pairs, games))
        if self.rendered.get(tournament["id"]) == signature:
            return True
        if not tournament["message_id"]:
            return False
        guild = self.bot.get_guild(tournament["guild_id"])
        room = guild.get_channel(tournament["channel_id"]) if guild else None
        if room is None:
            return False
        result = tournament_embed(self.bot, tournament)
        result.set_image(url="attachment://tournament-bracket.png")
        file = await self.pictures.file(tournament, entries, pairs, games)
        try:
            await room.get_partial_message(tournament["message_id"]).edit(
                embed=result, view=TournamentPanel(self.bot, tournament),
                attachments=[file], allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            file.close()
        self.rendered[tournament["id"]] = signature
        return True


def game_states(bot, tournament, pairs):
    return {p["blitz_id"]: bot.activities.match(tournament["guild_id"], p["blitz_id"])["state"]
            for p in pairs if p["blitz_id"]}


async def send_bracket(bot, tournament, send, *, with_image=True, round_no=None):
    result = tournament_embed(bot, tournament, round_no)
    if not with_image:
        await send(embed=result, ephemeral=True)
        return
    data = bot.tournaments.data
    pairs = data.pairs(tournament["id"])
    file = await bot.tournaments.platform.pictures.file(tournament, data.entries(tournament["id"]),
                                                       pairs, game_states(bot, tournament, pairs))
    result.set_image(url="attachment://tournament-bracket.png")
    try:
        await send(embed=result, file=file, ephemeral=True)
    finally:
        file.close()


class TournamentCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="tournament", description="Blitz signup, brackets, and tournament management")
        self.bot = bot

    @app_commands.command(description="Organizer: create a single-elimination Blitz tournament with signup buttons")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.autocomplete(rating=rating_choices, topics=tournament_topics)
    @app_commands.describe(topics="Comma-separated topics, e.g. dp,graphs; matches ANY selected topic",
                           minutes="Minutes per duel; omit for unlimited time")
    async def create(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80],
                     capacity: app_commands.Range[int, 2, 32] = 16,
                     minutes: app_commands.Range[int, 1, 180] | None = None,
                     rating: app_commands.Range[int, 800, 3500] | None = None,
                     topics: str | None = None, random_topic: bool = False,
                     voice_mode: Literal["none", "mute", "deafen", "both"] = "mute",
                     voice_channel: discord.VoiceChannel | None = None,
                     move_voice: bool = True, manager: discord.Member | None = None):
        await interaction.response.defer(thinking=True)
        if not interaction.app_permissions.attach_files or not interaction.app_permissions.embed_links:
            raise UserError("Grant this bot Attach Files and Embed Links in this channel for tournament artwork.")
        if manager and manager.bot:
            raise UserError("Choose a human tournament manager.")
        options = {"minutes": minutes, "rating_min": rating, "rating_max": rating,
                   "tags": [t.strip().lower() for t in topics.split(",")] if topics else [],
                   "random_topic": random_topic, "voice_mode": voice_mode,
                   "voice_channel_id": voice_channel.id if voice_channel else None,
                   "move_voice": move_voice, "manager_id": manager.id if manager else interaction.user.id}
        tournament = await self.bot.tournaments.create(interaction.guild_id, interaction.user.id,
                                                       name, capacity, interaction.channel_id, options)
        result = tournament_embed(self.bot, tournament)
        result.set_image(url="attachment://tournament-bracket.png")
        file = await self.bot.tournaments.platform.pictures.file(tournament, [], [], {})
        try:
            message = await interaction.followup.send(embed=result, view=TournamentPanel(self.bot, tournament),
                                                      file=file, wait=True)
        except Exception:
            await self.bot.tournaments.cancel(interaction.guild_id, tournament["id"], interaction.user.id)
            raise
        finally:
            file.close()
        async with self.bot.tournaments.lock(tournament["id"]):
            self.bot.tournaments.data.update(tournament["id"], message_id=message.id)
            await self.bot.tournaments.notify(self.bot.tournaments.data.get(interaction.guild_id, tournament["id"]))

    @app_commands.command(description="Join a tournament before registration closes")
    async def join(self, interaction: discord.Interaction, tournament_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.tournaments.join(interaction.guild_id, tournament_id, interaction.user.id)
        await interaction.followup.send("Registered! Your match will have its own Accept buttons and countdown.", ephemeral=True)

    @app_commands.command(description="Leave a tournament before its bracket starts")
    async def leave(self, interaction: discord.Interaction, tournament_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.tournaments.leave(interaction.guild_id, tournament_id, interaction.user.id)
        await interaction.followup.send("You left the registration list.", ephemeral=True)

    @app_commands.command(description="Host/manager: close signup and create a randomly seeded bracket")
    async def start(self, interaction: discord.Interaction, tournament_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.tournaments.start(interaction.guild_id, tournament_id, interaction.user.id,
                                         organizer=interaction.permissions.manage_guild)
        await interaction.followup.send("Bracket started. Players must accept their individual match invitations.", ephemeral=True)

    @app_commands.command(description="Show the current bracket, or a previous round")
    async def bracket(self, interaction: discord.Interaction, tournament_id: str,
                      round_no: app_commands.Range[int, 1, 5] | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        tournament = self.bot.tournaments.data.get(interaction.guild_id, tournament_id)
        await send_bracket(self.bot, tournament, interaction.followup.send,
                           with_image=interaction.app_permissions.attach_files, round_no=round_no)

    @app_commands.command(description="List open tournaments in this server")
    async def list(self, interaction: discord.Interaction):
        tournaments = self.bot.tournaments.data.active(interaction.guild_id)
        lines = [f"`{t['id']}` · **{safe(t['name'])}** · {t['state']}" for t in tournaments]
        await interaction.response.send_message(embed=embed("Blitz tournaments", "\n".join(lines) or "No open tournaments."), ephemeral=True)

    @app_commands.command(description="Host/manager: retry a blocked match with a fresh invitation and problem")
    async def retry(self, interaction: discord.Interaction, tournament_id: str,
                    round_no: app_commands.Range[int, 1, 5], match: app_commands.Range[int, 1, 16]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.tournaments.retry(interaction.guild_id, tournament_id, interaction.user.id, round_no, match,
                                         organizer=interaction.permissions.manage_guild)
        await interaction.followup.send("Retry requested. Previous voice recovery must finish before a new duel can start.", ephemeral=True)

    @app_commands.command(description="Host/manager: explicitly resolve a blocked match, including a draw or forfeit")
    async def resolve(self, interaction: discord.Interaction, tournament_id: str,
                      round_no: app_commands.Range[int, 1, 5], match: app_commands.Range[int, 1, 16],
                      winner: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.tournaments.resolve(interaction.guild_id, tournament_id, interaction.user.id,
                                           round_no, match, winner.id, organizer=interaction.permissions.manage_guild)
        await interaction.followup.send("Manager decision recorded in the bracket.", ephemeral=True)

    @app_commands.command(description="Host/manager: cancel a tournament and release its running Blitz games")
    async def cancel(self, interaction: discord.Interaction, tournament_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.tournaments.cancel(interaction.guild_id, tournament_id, interaction.user.id,
                                          organizer=interaction.permissions.manage_guild)
        await interaction.followup.send("Tournament cancelled. Bot-owned voice effects are being restored.", ephemeral=True)


def restore_tournaments(bot):
    for row in bot.tournaments.data.db.execute(
        "SELECT guild_id,id FROM tournaments WHERE message_id IS NOT NULL AND state IN ('registration','running','cancelling')"
    ).fetchall():
        tournament = bot.tournaments.data.get(row[0], row[1])
        bot.add_view(TournamentPanel(bot, tournament), message_id=tournament["message_id"])
