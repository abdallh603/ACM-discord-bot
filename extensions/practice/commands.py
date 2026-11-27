"""/training and /upsolve commands."""
from __future__ import annotations

import time
from typing import Literal

import discord
from discord import app_commands

from just_bot.core.api import embed, problem_key, problem_url, safe

from .engine import TRACKS


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
    def __init__(self, practice):
        super().__init__(name="training", description="Beginner training tracks with saved progress")
        self.practice = practice

    @app_commands.command(description="Start a three-stage beginner track with three problems per stage")
    async def start(self, interaction: discord.Interaction,
                    track: Literal["fundamentals", "greedy", "binary_search", "dp"] = "fundamentals"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal = await self.practice.start_training(interaction.guild_id, interaction.user.id, track)
        await interaction.followup.send(embed=training_embed(goal), ephemeral=True)

    @app_commands.command(description="Check your saved stage and accepted submissions")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal, solved = await self.practice.training(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(embed=training_embed(goal, solved), ephemeral=True)

    @app_commands.command(description="Advance after solving all three problems, or finish the final stage")
    async def next(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal, solved = await self.practice.training(interaction.guild_id, interaction.user.id, advance=True)
        await interaction.followup.send(embed=training_embed(goal, solved), ephemeral=True)

    @app_commands.command(description="Discard your current training track so you can start another")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.practice.stop(interaction.guild_id, interaction.user.id, "training")
        await interaction.followup.send("Training track cleared.", ephemeral=True)


class Upsolve(app_commands.Group):
    def __init__(self, practice):
        super().__init__(name="upsolve", description="Post-contest practice goals with a deadline")
        self.practice = practice

    @app_commands.command(description="Pick your next unsolved problems from a finished Codeforces contest")
    async def start(self, interaction: discord.Interaction, contest_id: app_commands.Range[int, 1, 99999],
                    hours: app_commands.Range[int, 1, 168] = 48,
                    problems: app_commands.Range[int, 1, 5] = 3):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal = await self.practice.start_upsolve(
            interaction.guild_id, interaction.user.id, contest_id, hours, problems,
        )
        await interaction.followup.send(embed=upsolve_embed(goal), ephemeral=True)

    @app_commands.command(description="Check accepted submissions for your post-contest goal")
    async def check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        goal, solved = await self.practice.upsolve(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(embed=upsolve_embed(goal, solved), ephemeral=True)

    @app_commands.command(description="Clear your current upsolving goal")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.practice.stop(interaction.guild_id, interaction.user.id, "upsolve")
        await interaction.followup.send("Upsolving goal cleared.", ephemeral=True)
