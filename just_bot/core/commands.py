"""Core slash commands. Extensions add their own through the registry (see extensions.py)."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands

from .domain import UserError, rank_for
from .options import rating_choices, topic_choices
from .presentation import embed, problem_embed, safe

if TYPE_CHECKING:
    from .bot import RoundForgeBot

# Order keys reproduce the pre-extension registration order; extensions slot in between.
COMMAND_ORDER = {"handle": 10, "admin": 40, "problem": 50, "leaderboard": 70, "help": 80}
ADMIN_ORDER = {"refresh": 30, "status": 40, "channel": 50}


async def defer(interaction: discord.Interaction, *, private: bool = False):
    await interaction.response.defer(ephemeral=private, thinking=True)


def notes(values) -> str:
    """Sentences contributed by hooks, appended after the core message."""
    return "".join(" " + value for value in values if value)


class Handles(app_commands.Group):
    def __init__(self, bot: RoundForgeBot):
        super().__init__(name="handle", description="Link and verify your Codeforces account")
        self.bot = bot

    @app_commands.command(description="Start proving ownership of your Codeforces handle")
    async def link(self, interaction: discord.Interaction, handle: str):
        await defer(interaction, private=True)
        nonce, expires = await self.bot.community.start_link(
            interaction.guild_id, interaction.user.id, handle
        )
        await interaction.followup.send(embed=embed(
            "Verify your Codeforces handle",
            "1. Open [Codeforces profile settings](https://codeforces.com/settings/social).\n"
            f"2. Temporarily set **First name** to `{nonce}` and save.\n"
            f"3. Run `/handle verify` before <t:{expires}:t>.\n\n"
            "After verification you can restore your first name. "
            "This verifies account ownership; it does not verify university enrollment.",
        ), ephemeral=True)

    @app_commands.command(description="Finish ownership verification after saving your profile code")
    async def verify(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        profile = await self.bot.community.verify(interaction.guild_id, interaction.user.id)
        extra = await self.bot.extensions.run_hooks("link_verified", interaction.guild_id, interaction.user.id, profile)
        await interaction.followup.send(
            f"Linked **{safe(profile['handle'])}**. You can restore your Codeforces First name." + notes(extra),
            ephemeral=True,
        )

    @app_commands.command(description="Unlink your account and remove bot-managed rank roles")
    async def unlink(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        await self.bot.extensions.run_hooks("before_unlink", interaction.guild_id, interaction.user.id)
        self.bot.store.unlink(interaction.guild_id, interaction.user.id)
        extra = await self.bot.extensions.run_hooks("after_unlink", interaction.user)
        await interaction.followup.send("Account link and pending verification removed." + notes(extra), ephemeral=True)

    @app_commands.command(description="View a linked member's Codeforces profile")
    async def show(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await defer(interaction)
        member = member or interaction.user
        linked = self.bot.community.require_member(interaction.guild_id, member.id)
        profile = (await self.bot.cf.users([linked["handle"]]))[0]
        self.bot.store.update_profile(interaction.guild_id, profile, int(time.time()))
        rank, color = rank_for(profile.get("rating"))
        result = embed(
            f"{profile['handle']} · {rank}", url=f"https://codeforces.com/profile/{profile['handle']}"
        )
        result.color = discord.Color(color)
        result.add_field(name="Rating", value=str(profile.get("rating", "Unrated")))
        result.add_field(name="Best rating", value=str(profile.get("maxRating", "Unrated")))
        result.add_field(name="Community member", value=safe(member.display_name))
        result.add_field(name="Organization (self-reported)", value=safe(profile.get("organization", "—")))
        card = None
        for provider in self.bot.extensions.hooks_for("profile_image"):
            card = await provider(interaction, member, profile)
            if card:
                break
        if card:
            result.set_image(url=f"attachment://{card.filename}")
            await interaction.followup.send(embed=result, file=card)
        else:
            await interaction.followup.send(embed=result)


@app_commands.default_permissions(manage_guild=True)
class Admin(app_commands.Group):
    """Subcommands come from core and extensions, ordered by their registration keys."""

    def __init__(self):
        super().__init__(name="admin", description="Configure the RoundForge community bot")


def admin_commands(bot: RoundForgeBot):
    @app_commands.command(name="refresh", description="Refresh linked ratings and rank roles now")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.guild_id)
    async def refresh(interaction: discord.Interaction):
        await defer(interaction, private=True)
        if bot.refresh_lock(interaction.guild_id).locked():
            raise UserError("A community refresh is already running. Check /admin status later.")
        # This can outlive an interaction for a large server; report completion in status/logs.
        bot.request_refresh(interaction.guild)
        await interaction.followup.send(
            "Community refresh queued. See /admin status for the result.", ephemeral=True
        )

    @app_commands.command(name="status", description="Show feed settings, member count, and the latest refresh result")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(interaction: discord.Interaction):
        lines = await bot.extensions.run_hooks("admin_status", interaction.guild_id)
        await interaction.response.send_message(embed=embed(
            "RoundForge bot status", "\n".join(line for line in lines if line),
        ), ephemeral=True)

    @app_commands.command(name="channel", description="Add/remove a bot command channel, or clear restrictions for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(interaction: discord.Interaction, action: Literal["add", "remove", "clear"],
                      channel: discord.TextChannel | None = None):
        if action != "clear" and channel is None:
            raise UserError("Select a channel for add/remove.")
        bot.store.channel_rule(interaction.guild_id, action, channel.id if channel else None)
        allowed = bot.store.allowed_channels(interaction.guild_id)
        text = " ".join(f"<#{cid}>" for cid in sorted(allowed)) if allowed else "All server channels"
        await interaction.response.send_message(f"Command channels: {text}", ephemeral=True)

    return refresh, status, channel


def rating_mode(bot: RoundForgeBot):
    def fetch(guild_id, page):
        rows = bot.store.leaderboard(guild_id, page)
        return "Codeforces ratings", rows, lambda row: str(row["rating"] if row["rating"] is not None else "Unrated")
    return fetch


def leaderboard_command(bot: RoundForgeBot, modes):
    by_name = {mode.name: mode for mode in modes}

    async def leaderboard(interaction: discord.Interaction, mode: str = "rating",
                          page: app_commands.Range[int, 1, 1000] = 1):
        title, rows, score = by_name[mode].fetch(interaction.guild_id, page)
        if not rows:
            await interaction.response.send_message("No linked members on this page. Start with /handle link.")
            return
        lines = []
        for index, row in enumerate(rows, start=(page - 1) * 15 + 1):
            lines.append(f"**{index}.** [{safe(row['handle'])}](https://codeforces.com/profile/{row['handle']}) — **{score(row)}**")
        oldest = min(row["refreshed_at"] for row in rows)
        result = embed(f"RoundForge · {title}", "\n".join(lines))
        result.add_field(name="Page", value=str(page))
        result.add_field(name="Rating cache", value=f"Oldest profile on page: <t:{oldest}:R> · Refreshes hourly")
        await interaction.response.send_message(embed=result)

    # The choices follow the enabled modes; a Literal keeps the registered payload unchanged.
    leaderboard.__annotations__["mode"] = Literal[tuple(by_name)]
    return app_commands.command(
        name="leaderboard",
        description="RoundForge server rankings by " + " or ".join(mode.description for mode in modes),
    )(leaderboard)


def install(ext):
    """Register core commands, /help lines, and /admin status lines via core's own context."""
    bot = ext.bot
    ext.add_command(Handles(bot), order=COMMAND_ORDER["handle"])
    bot.admin_group = Admin()
    ext.add_command(bot.admin_group, order=COMMAND_ORDER["admin"])
    for command in admin_commands(bot):
        ext.add_admin_command(command, order=ADMIN_ORDER[command.name])

    @app_commands.command(description="Find an unsolved Codeforces problem near your rating")
    @app_commands.describe(rating="Difficulty (multiple of 100); defaults to your rating", tag="Optional Codeforces topic, e.g. dp")
    @app_commands.autocomplete(rating=rating_choices, tag=topic_choices)
    @app_commands.checks.cooldown(1, 20, key=lambda i: (i.guild_id, i.user.id))
    async def problem(interaction: discord.Interaction,
                      rating: app_commands.Range[int, 800, 3500] | None = None,
                      tag: str | None = None):
        await defer(interaction)
        if rating is not None and rating % 100:
            raise UserError("Use a rating that is a multiple of 100.")
        if tag and len(tag) > 50:
            raise UserError("Use a Codeforces topic such as dp, greedy, or binary search.")
        result = await bot.community.recommend(interaction.guild_id, interaction.user.id, rating, tag)
        await interaction.followup.send(embed=problem_embed(result))

    ext.add_command(problem, order=COMMAND_ORDER["problem"])
    ext.add_leaderboard_mode("rating", help="server ratings", description="Codeforces rating",
                             fetch=rating_mode(bot), order=10)

    @app_commands.command(description="Show RoundForge community commands and how to get started")
    async def help(interaction: discord.Interaction):
        await interaction.response.send_message(embed=embed(
            "Welcome to RoundForge Competitive Programming", bot.extensions.help.render(),
        ), ephemeral=True)

    ext.add_command(help, order=COMMAND_ORDER["help"])

    book = bot.extensions.help
    book.composite("admin", block="organizers", order=20, prefix="Organizers: ", joiner=", ", suffix=".")
    book.composite("notes", block="organizers", order=30, joiner=" ")
    ext.help("**A Codeforces community bot for every server**", block="intro")
    ext.help("`/handle link` → `/handle verify` — prove account ownership", block="commands", order=10)
    ext.help("`/handle show` — Discord ~ Codeforces avatar card and profile", block="commands", order=20)
    ext.help("`/problem` — unsolved problem by rating and topic", block="commands", order=30)
    ext.help(lambda: "`/leaderboard` — " + " or ".join(mode.help for mode in bot.leaderboard_modes),
             block="commands", order=50)
    ext.help("`/handle unlink` — remove your link", block="commands", order=80)
    for order, name in ((20, "channel"), (40, "refresh"), (50, "status")):
        ext.help_item(f"`/admin {name}`", line="admin", order=order)
    ext.help_item("Account verification does not certify real-world identity or organizational affiliation.",
                  line="notes", order=20)

    ext.hook("admin_status", lambda guild: f"Verified handles: **{len(bot.store.members(guild))}**", order=10)
    ext.hook("admin_status", lambda guild: "Last refresh: "
             + safe(bot.refresh_statuses.get(guild, "Not run since startup"), 500), order=60)
