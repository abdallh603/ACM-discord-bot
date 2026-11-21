from __future__ import annotations

import re
import time
from typing import Literal

import discord
from discord import app_commands

from .domain import UserError, problem_key, problem_url
from .blitz_options import VOICE_LABELS, duration_label, voice_mode as selected_voice_mode
from .blitz_options import parse_mentions
from .command_options import rating_choices, topic_choices
from .practice import TRACKS
from .presentation import embed, safe


def goal_lines(problems, solved):
    return "\n".join(
        f"{'✅' if problem_key(p) in solved else '⬜'} "
        f"[{p['contestId']}{p['index']} · {safe(p['name'])}]({problem_url(p)}) "
        f"({p.get('rating', 'unrated')})" for p in problems
    )


def training_embed(goal, solved=()):
    track = TRACKS[goal["track"]]
    stage = goal["stage"]
    current = goal["stages"][stage]
    return embed(
        f"{track['title']} · {'Complete!' if goal['complete'] else f'Stage {stage + 1}/3'}",
        f"**{len(solved)}/3 solved** · rating {track['ratings'][stage]}\n"
        f"{track['tips'][stage]}\n\n{goal_lines(current['problems'], solved)}\n\n"
        "Solve in individual practice, then use `/training next` to advance. "
        "Your assignments and progress survive restarts.",
    )


def upsolve_embed(goal, solved=()):
    expired = int(time.time()) >= goal["ends_at"]
    return embed(
        f"Upsolve · {goal['contest_name']}"[:256],
        f"**{len(solved)}/{len(goal['problems'])} completed** · {'Deadline passed' if expired else 'Deadline'} "
        f"<t:{goal['ends_at']}:F>\n\n{goal_lines(goal['problems'], solved)}\n\n"
        f"[Contest page and published editorial links](https://codeforces.com/contest/{goal['contest_id']})\n"
        "Goal: get accepted individual practice submissions before the deadline. "
        "Use `/upsolve check` to refresh progress.",
    )


class Training(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="training", description="Beginner training tracks with saved progress")
        self.bot = bot

    @app_commands.command(description="Start a three-stage beginner track with three problems per stage")
    async def start(self, interaction: discord.Interaction,
                    track: Literal["fundamentals", "greedy", "binary_search", "dp"] = "fundamentals"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal = await self.bot.practice.start_training(interaction.guild_id, interaction.user.id, track)
        await interaction.followup.send(embed=training_embed(goal), ephemeral=True)

    @app_commands.command(description="Check your saved stage and accepted submissions")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal, solved = await self.bot.practice.training(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(embed=training_embed(goal, solved), ephemeral=True)

    @app_commands.command(description="Advance after solving all three problems, or finish the final stage")
    async def next(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal, solved = await self.bot.practice.training(interaction.guild_id, interaction.user.id, advance=True)
        await interaction.followup.send(embed=training_embed(goal, solved), ephemeral=True)

    @app_commands.command(description="Discard your current training track so you can start another")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.practice.stop(interaction.guild_id, interaction.user.id, "training")
        await interaction.followup.send("Training track cleared.", ephemeral=True)


class Upsolve(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="upsolve", description="Post-contest practice goals with a deadline")
        self.bot = bot

    @app_commands.command(description="Pick your next unsolved problems from a finished Codeforces contest")
    async def start(self, interaction: discord.Interaction, contest_id: app_commands.Range[int, 1, 99999],
                    hours: app_commands.Range[int, 1, 168] = 48,
                    problems: app_commands.Range[int, 1, 5] = 3):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal = await self.bot.practice.start_upsolve(
            interaction.guild_id, interaction.user.id, contest_id, hours, problems,
        )
        await interaction.followup.send(embed=upsolve_embed(goal), ephemeral=True)

    @app_commands.command(description="Check accepted submissions for your post-contest goal")
    async def check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal, solved = await self.bot.practice.upsolve(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(embed=upsolve_embed(goal, solved), ephemeral=True)

    @app_commands.command(description="Clear your current upsolving goal")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.practice.stop(interaction.guild_id, interaction.user.id, "upsolve")
        await interaction.followup.send("Upsolving goal cleared.", ephemeral=True)


def blitz_embed(bot, match):
    options = match["options"]
    revision = options["revision"]
    players = bot.activities.players(match["id"])
    accepted = sum(p["accepted_revision"] == revision for p in players)
    roster = "\n".join(
        f"{'✅' if p['accepted_revision'] == revision else '⬜'} <@{p['user_id']}> · {safe(p['handle'])}"
        + (" · solved" if p["solved_at"] is not None else "") for p in players
    )
    filters = (f"Manual: {safe(options['manual'])}" if options["manual"] else
               f"Rating: {options['rating_min'] or options['rating_max'] or 'automatic'}–"
               f"{options['rating_max'] or options['rating_min'] or 'automatic'} · "
               f"Topic: {safe(options['tag'] or ('random topic' if options['random_topic'] else 'any'))}")
    details = (
        f"Game **`{match['id']}`** · **{match['state']}** · settings revision **{revision}**\n"
        f"{duration_label(options['minutes'])} · {options['end_rule']}\n{filters}\n"
        f"Voice: **{VOICE_LABELS[selected_voice_mode(options)]}** · move to selected voice: **{options['move_voice']}**\n\n"
        f"Host: <@{match['creator_id']}> · Manager: <@{options.get('manager_id') or match['creator_id']}>\n"
        f"**Players ({accepted}/{len(players)} accepted)**\n{roster}\n\n"
    )
    if match["state"] == "pending":
        details += (f"Every invited player accepts with `/blitz accept match_id:{match['id']} revision:{revision}`.\n"
                    f"Invite expires <t:{match['invite_expires']}:R>. Editing resets the other players' acceptance.")
    elif match["starts_at"]:
        deadline = f"deadline <t:{match['ends_at']}:T>" if match["ends_at"] is not None else "unlimited time"
        details += f"Starts <t:{match['starts_at']}:T> · {deadline}.\n"
    if match["text_id"]:
        details += f"\nGame text channel: <#{match['text_id']}>"
    if match["voice_id"]:
        details += f"\nGame voice channel: <#{match['voice_id']}>"
    if match["outcome"]:
        details += f"\n{match['outcome']}"
    if match["winner_id"]:
        details += f"\nWinner: <@{match['winner_id']}>"
    recovering = [p for p in players if p["owns_mute"] or p["owns_deaf"] or p["original_voice"] is not None]
    if match["state"] in {"finished", "cancelled", "timed_out", "unresolved", "settling"} and recovering:
        details += "\nVoice recovery pending: " + ", ".join(f"<@{p['user_id']}>" for p in recovering)
    return embed("RoundForge Solo Blitz Test" if options.get("solo") else "RoundForge Multiplayer Blitz", details)


def option_changes(rating, rating_min, rating_max, **options):
    if rating is not None:
        if rating_min is not None or rating_max is not None:
            raise UserError("Use either an exact rating or a rating range.")
        rating_min = rating_max = rating
    return {key: value for key, value in {
        "rating_min": rating_min, "rating_max": rating_max, **options,
    }.items() if value is not None}


class BlitzCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="blitz", description="Multiplayer Codeforces games with room reuse and voice control")
        self.bot = bot

    def owned_match(self, interaction, match_id=None):
        if not match_id:
            slot = self.bot.activities.slot(interaction.guild_id, interaction.user.id)
            if slot:
                match_id = slot["match_id"]
            else:
                managed = [m for m in self.bot.activities.matches(interaction.guild_id)
                           if interaction.user.id in self.bot.blitz.managers(m)
                           and m["state"] in {"pending", "starting", "active", "settling"}]
                if len(managed) != 1:
                    raise UserError("Pass a match_id from /blitz games to select the game.")
                match_id = managed[0]["id"]
        match = self.bot.activities.match(interaction.guild_id, match_id)
        players = self.bot.activities.players(match_id)
        if not self.bot.blitz.can_access(match, interaction.user.id) and not interaction.permissions.manage_guild:
            raise UserError("Only this game's players and organizers can access its details.")
        return match

    async def resolve_members(self, guild, users):
        for user in users:
            member = await guild.fetch_member(user)
            if member.bot:
                raise UserError("Players and managers must be human server members.")

    @app_commands.command(description="Choose a topic, rating, and voice mode from dropdown menus")
    @app_commands.describe(players="Optional: mention 1–7 opponents; omit for an organizer solo test")
    async def menu(
        self, interaction: discord.Interaction, players: str | None = None,
        minutes: app_commands.Range[int, 1, 180] | None = None,
        manual_problem: str | None = None,
        end_rule: Literal["first_solve", "all_solve"] = "first_solve",
        move_voice: bool = True, voice_channel: discord.VoiceChannel | None = None,
        hosted: bool = False, manager: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        opponents = []
        if players:
            opponents = parse_mentions(players, 2 if hosted else 1, 8 if hosted else 7,
                                       None if hosted else interaction.user.id)
            await self.resolve_members(interaction.guild, opponents)
            for user in opponents:
                self.bot.community.require_member(interaction.guild_id, user)
        elif hosted:
            raise UserError("Mention 2–8 players for a hosted game.")
        elif not interaction.permissions.manage_guild:
            raise UserError("Mention opponents, or use Manage Server permission for a solo test.")
        if not hosted:
            self.bot.community.require_member(interaction.guild_id, interaction.user.id)
        if manager:
            await self.resolve_members(interaction.guild, [manager.id])
        from .blitz_menu import BlitzMenu
        options = {"minutes": minutes, "manual": manual_problem, "end_rule": end_rule,
                   "move_voice": move_voice, "text_channel_id": interaction.channel_id,
                   "voice_channel_id": voice_channel.id if voice_channel else None,
                   "hosted": hosted, "manager_id": manager.id if manager else interaction.user.id}
        view = BlitzMenu(self.bot, interaction.user.id, interaction.guild_id, opponents, options)
        view.message = await interaction.followup.send(embed=view.summary(), view=view, ephemeral=True, wait=True)

    @app_commands.command(description="Organizer: test blitz alone with real rooms, voice controls, and solves")
    @app_commands.autocomplete(rating=rating_choices, topic=topic_choices)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def solo(
        self, interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 180] | None = None,
        rating: app_commands.Range[int, 800, 3500] | None = None,
        topic: str | None = None, manual_problem: str | None = None,
        mute_voice: bool = True, move_voice: bool = True,
        voice_channel: discord.VoiceChannel | None = None,
        voice_mode: Literal["none", "mute", "deafen", "both"] | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = option_changes(
            rating, None, None, minutes=minutes, tag=topic, manual=manual_problem,
            mute_voice=mute_voice, move_voice=move_voice, text_channel_id=interaction.channel_id,
            voice_channel_id=voice_channel.id if voice_channel else None,
            voice_mode=voice_mode,
        )
        match = await self.bot.blitz.solo(interaction.guild_id, interaction.user.id, options)
        await interaction.followup.send(embed=blitz_embed(self.bot, match), ephemeral=True)

    @app_commands.command(description="Invite 1–7 other players; everyone accepts before the game starts")
    @app_commands.autocomplete(topic=topic_choices, rating=rating_choices, rating_min=rating_choices, rating_max=rating_choices)
    @app_commands.describe(players="Mention 1–7 other members, separated by spaces: @Alice @Bob @Charlie")
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def create(
        self, interaction: discord.Interaction, players: str,
        minutes: app_commands.Range[int, 1, 180] | None = None,
        rating: app_commands.Range[int, 800, 3500] | None = None,
        rating_min: app_commands.Range[int, 800, 3500] | None = None,
        rating_max: app_commands.Range[int, 800, 3500] | None = None,
        topic: str | None = None, random_topic: bool = False, manual_problem: str | None = None,
        end_rule: Literal["first_solve", "all_solve"] = "first_solve",
        mute_voice: bool = True, move_voice: bool = True,
        voice_channel: discord.VoiceChannel | None = None,
        voice_mode: Literal["none", "mute", "deafen", "both"] | None = None,
        manager: discord.Member | None = None,
    ):
        await interaction.response.defer(thinking=True)
        opponents = parse_mentions(players, exclude=interaction.user.id)
        await self.resolve_members(interaction.guild, [*opponents, *([manager.id] if manager else [])])
        options = option_changes(
            rating, rating_min, rating_max, minutes=minutes, tag=topic, random_topic=random_topic,
            manual=manual_problem, end_rule=end_rule, mute_voice=mute_voice, move_voice=move_voice,
            text_channel_id=interaction.channel_id, voice_channel_id=voice_channel.id if voice_channel else None,
            voice_mode=voice_mode,
            manager_id=manager.id if manager else None,
        )
        match = await self.bot.blitz.challenge(interaction.guild_id, interaction.user.id, opponents, options)
        await interaction.followup.send(
            content="Invited: " + " ".join(f"<@{user}>" for user in opponents),
            embed=blitz_embed(self.bot, match),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user) for user in opponents]),
        )

    @app_commands.command(description="Host a game for 2–8 mentioned players without joining; optionally assign a manager")
    @app_commands.autocomplete(topic=topic_choices, rating=rating_choices, rating_min=rating_choices, rating_max=rating_choices)
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def host(
        self, interaction: discord.Interaction, players: str,
        manager: discord.Member | None = None,
        minutes: app_commands.Range[int, 1, 180] | None = None,
        rating: app_commands.Range[int, 800, 3500] | None = None,
        rating_min: app_commands.Range[int, 800, 3500] | None = None,
        rating_max: app_commands.Range[int, 800, 3500] | None = None,
        topic: str | None = None, random_topic: bool = False, manual_problem: str | None = None,
        end_rule: Literal["first_solve", "all_solve"] = "first_solve",
        voice_mode: Literal["none", "mute", "deafen", "both"] = "mute",
        move_voice: bool = True, voice_channel: discord.VoiceChannel | None = None,
    ):
        await interaction.response.defer(thinking=True)
        roster = parse_mentions(players, 2, 8)
        await self.resolve_members(interaction.guild, [*roster, *([manager.id] if manager else [])])
        options = option_changes(
            rating, rating_min, rating_max, minutes=minutes, tag=topic, random_topic=random_topic,
            manual=manual_problem, end_rule=end_rule, voice_mode=voice_mode, move_voice=move_voice,
            text_channel_id=interaction.channel_id, voice_channel_id=voice_channel.id if voice_channel else None,
            manager_id=manager.id if manager else None,
        )
        match = await self.bot.blitz.host(interaction.guild_id, interaction.user.id, roster, options)
        invited = set(roster) | ({manager.id} if manager else set())
        await interaction.followup.send(
            content="Invited: " + " ".join(f"<@{user}>" for user in sorted(invited)),
            embed=blitz_embed(self.bot, match),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user) for user in invited]),
        )

    @app_commands.command(description="Accept the current settings; the game starts when everyone accepts")
    async def accept(self, interaction: discord.Interaction, match_id: str,
                     revision: app_commands.Range[int, 1, 10000] = 1):
        await interaction.response.defer(ephemeral=True, thinking=True)
        match = await self.bot.blitz.accept(interaction.guild_id, match_id, interaction.user.id, revision)
        await interaction.followup.send(embed=blitz_embed(self.bot, match), ephemeral=True)

    @app_commands.command(description="Edit a pending game's settings; all other players must accept again")
    @app_commands.autocomplete(topic=topic_choices, rating=rating_choices, rating_min=rating_choices, rating_max=rating_choices)
    async def edit(
        self, interaction: discord.Interaction, match_id: str,
        minutes: app_commands.Range[int, 1, 180] | None = None,
        rating: app_commands.Range[int, 800, 3500] | None = None,
        rating_min: app_commands.Range[int, 800, 3500] | None = None,
        rating_max: app_commands.Range[int, 800, 3500] | None = None,
        topic: str | None = None, random_topic: bool | None = None, manual_problem: str | None = None,
        end_rule: Literal["first_solve", "all_solve"] | None = None,
        mute_voice: bool | None = None, move_voice: bool | None = None,
        clear_filters: bool = False, clear_problem: bool = False,
        unlimited: bool = False,
        voice_mode: Literal["none", "mute", "deafen", "both"] | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = {}
        if clear_filters:
            options.update(rating_min=None, rating_max=None, tag=None, random_topic=False)
        if clear_problem:
            options["manual"] = None
        options.update(option_changes(
            rating, rating_min, rating_max, minutes=minutes, tag=topic, random_topic=random_topic,
            manual=manual_problem, end_rule=end_rule, mute_voice=mute_voice, move_voice=move_voice,
            voice_mode=voice_mode,
        ))
        if unlimited:
            if minutes is not None:
                raise UserError("Choose either a duration or unlimited:true.")
            options["minutes"] = None
        if not options:
            raise UserError("Choose at least one setting to change.")
        match = await self.bot.blitz.edit(interaction.guild_id, match_id, interaction.user.id, options)
        await interaction.followup.send(embed=blitz_embed(self.bot, match), ephemeral=True)

    @app_commands.command(description="Show your current game or a specific game you participated in")
    async def status(self, interaction: discord.Interaction, match_id: str | None = None):
        match = self.owned_match(interaction, match_id)
        await interaction.response.send_message(embed=blitz_embed(self.bot, match), ephemeral=True)

    @app_commands.command(description="List simultaneous pending and running blitz games in this server")
    async def games(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 1000] = 1):
        matches = [m for m in self.bot.activities.matches(interaction.guild_id)
                   if m["state"] in {"pending", "starting", "active", "settling"}]
        rows = matches[(page - 1) * 15:page * 15]
        lines = [f"`{m['id']}` · **{m['state']}** · {'solo test' if m['options'].get('solo') else str(len(self.bot.activities.players(m['id']))) + ' players'} · "
                 f"{duration_label(m['options']['minutes'])}" for m in rows]
        await interaction.response.send_message(embed=embed(
            f"RoundForge Blitz games · page {page}", "\n".join(lines) or "No games on this page.",
        ))

    @app_commands.command(description="Request a Codeforces solve check for your running game")
    @app_commands.checks.cooldown(1, 15, key=lambda i: (i.guild_id, i.user.id))
    async def check(self, interaction: discord.Interaction, match_id: str | None = None):
        match = self.owned_match(interaction, match_id)
        self.bot.blitz.request_check(interaction.guild_id, match["id"])
        await interaction.response.send_message("Solve check queued. Results appear in the game's text channel.", ephemeral=True)

    @app_commands.command(description="Cancel your game, decline an invite, or retry voice recovery")
    async def cancel(self, interaction: discord.Interaction, match_id: str | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        match = self.owned_match(interaction, match_id)
        await self.bot.blitz.cancel(interaction.guild_id, match["id"], interaction.user.id,
                                   organizer=interaction.permissions.manage_guild)
        await interaction.followup.send("Game stopped. Bot-owned voice changes are being restored.", ephemeral=True)

    @app_commands.command(description="Organizer: set total duration; omit minutes to remove the time limit")
    async def time(self, interaction: discord.Interaction, match_id: str,
                   minutes: app_commands.Range[int, 1, 180] | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.owned_match(interaction, match_id)
        match = await self.bot.blitz.set_time(interaction.guild_id, match_id, minutes,
                                             actor=interaction.user.id, organizer=interaction.permissions.manage_guild)
        await interaction.followup.send(embed=blitz_embed(self.bot, match), ephemeral=True)

    @app_commands.command(description="Organizer: retry cleanup of interrupted or finished blitz games")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def recover(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.blitz.recover(interaction.guild_id)
        await interaction.followup.send("Recovery attempted. Disconnected muted players are retried on voice rejoin.", ephemeral=True)


def install_activity_commands(bot):
    for group in (Training(bot), Upsolve(bot), BlitzCommands(bot)):
        bot.tree.add_command(group)
