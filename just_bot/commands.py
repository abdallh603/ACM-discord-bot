from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from .command_options import rating_choices, timezone_choices, topic_choices
from .domain import RANKS, UserError, rank_for, upcoming_contests
from .presentation import contest_embed, embed, problem_embed, safe

if TYPE_CHECKING:
    from .bot import RoundForgeBot


async def defer(interaction: discord.Interaction, *, private: bool = False):
    await interaction.response.defer(ephemeral=private, thinking=True)


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
        await interaction.followup.send(
            f"Linked **{safe(profile['handle'])}**. You can restore your Codeforces First name. "
            "Use `/rank sync` to update your rating role.", ephemeral=True,
        )

    @app_commands.command(description="Unlink your account and remove bot-managed rank roles")
    async def unlink(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        if self.bot.activities.slot(interaction.guild_id, interaction.user.id):
            raise UserError("Cancel your blitz game and finish voice recovery before unlinking.")
        self.bot.store.unlink(interaction.guild_id, interaction.user.id)
        notice = ""
        try:
            await self.bot.clear_rank_roles(interaction.user)
        except discord.HTTPException:
            notice = " An organizer must remove your CF rank role because I lack permission."
        await interaction.followup.send(
            "Account link and pending verification removed. Daily claim history remains to "
            "prevent duplicate rewards." + notice, ephemeral=True,
        )

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
        try:
            card = await self.bot.profile_cards.build(member, profile) if interaction.app_permissions.attach_files else None
        except (TimeoutError, OSError, ValueError):
            card = None
        if card:
            result.set_image(url="attachment://roundforge-profile.png")
            await interaction.followup.send(embed=result, file=card)
        else:
            await interaction.followup.send(embed=result)


class Daily(app_commands.Group):
    def __init__(self, bot: RoundForgeBot):
        super().__init__(name="daily", description="The RoundForge daily programming challenge")
        self.bot = bot

    @app_commands.command(description="Show today's shared RoundForge problem")
    async def show(self, interaction: discord.Interaction):
        await defer(interaction)
        daily = await self.bot.community.daily(interaction.guild_id)
        await interaction.followup.send(embed=problem_embed(daily["problem"], daily=daily))

    @app_commands.command(description="Verify your accepted practice submission and earn 10 points")
    @app_commands.checks.cooldown(1, 30, key=lambda i: (i.guild_id, i.user.id))
    async def claim(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        submission = await self.bot.community.claim_daily(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(
            f"Accepted! **+10 daily points** for RoundForge. "
            f"[Submission](https://codeforces.com/contest/{submission['problem']['contestId']}"
            f"/submission/{submission['id']}) · See `/leaderboard mode:daily`.", ephemeral=True,
        )


class Rank(app_commands.Group):
    def __init__(self, bot: RoundForgeBot):
        super().__init__(name="rank", description="Refresh your Codeforces rating and rank role")
        self.bot = bot

    @app_commands.command(description="Refresh your linked rating and update your CF role")
    @app_commands.checks.cooldown(1, 60, key=lambda i: (i.guild_id, i.user.id))
    async def sync(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        member = self.bot.community.require_member(interaction.guild_id, interaction.user.id)
        handle = member["handle"]
        profile = (await self.bot.cf.users([handle], fresh=True))[0]
        current = self.bot.store.member(interaction.guild_id, interaction.user.id)
        if not current or current["handle"].casefold() != handle.casefold():
            raise UserError("Your account link changed. Try /rank sync again.")
        self.bot.store.update_profile(interaction.guild_id, profile, int(time.time()))
        rank, _ = rank_for(profile.get("rating"))
        changed = await self.bot.sync_rank_role(interaction.user, profile.get("rating"))
        await interaction.followup.send(
            f"Rating refreshed: **{profile.get('rating', 'Unrated')} · {rank}**. "
            + ("Rank role synced." if changed else "An organizer can enable roles with /admin setup_roles."),
            ephemeral=True,
        )


@app_commands.default_permissions(manage_guild=True)
class Admin(app_commands.Group):
    def __init__(self, bot: RoundForgeBot):
        super().__init__(name="admin", description="Configure the RoundForge community bot")
        self.bot = bot

    @app_commands.command(description="Set server feeds and timezone; omitted channels turn those feeds off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.autocomplete(timezone=timezone_choices, daily_rating=rating_choices)
    async def configure(
        self, interaction: discord.Interaction,
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
        self.bot.store.configure(
            interaction.guild_id,
            reminders_channel.id if reminders_channel else None,
            daily_channel.id if daily_channel else None, daily_rating, daily_hour,
        )
        if timezone:
            self.bot.store.set_timezone(interaction.guild_id, timezone)
        zone = self.bot.store.timezone(interaction.guild_id).key
        await interaction.response.send_message(
            f"Saved. Contest reminders: {reminders_channel.mention if reminders_channel else 'off'}. "
            f"Daily post: {daily_channel.mention if daily_channel else 'off'} at {daily_hour:02}:00 "
            f"{zone}, rating {daily_rating}. An already selected daily stays fixed until its deadline.",
            ephemeral=True,
        )

    @app_commands.command(description="Create the bot's CF rank roles (requires Manage Roles)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def setup_roles(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        async with self.bot.role_setup_lock(interaction.guild_id):
            mapping = self.bot.store.roles(interaction.guild_id)
            ranks = [("Unrated", 0x6B7280)] + [(name, color) for _, name, color in RANKS]
            created = 0
            for name, color in ranks:
                if interaction.guild.get_role(mapping.get(name, 0)):
                    continue
                role = await interaction.guild.create_role(
                    name=f"CF · {name}", color=discord.Color(color), permissions=discord.Permissions.none(),
                    reason="RoundForge bot rank role setup", hoist=True, mentionable=False,
                )
                self.bot.store.save_role(interaction.guild_id, name, role.id)
                created += 1
        await interaction.followup.send(
            f"Created {created} CF roles. Put my bot role above all CF roles in Server Settings → Roles. "
            "Members can use /rank sync; automatic refresh runs hourly.", ephemeral=True,
        )

    @app_commands.command(description="Refresh linked ratings and rank roles now")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.guild_id)
    async def refresh(self, interaction: discord.Interaction):
        await defer(interaction, private=True)
        if self.bot.refresh_lock(interaction.guild_id).locked():
            raise UserError("A community refresh is already running. Check /admin status later.")
        # This can outlive an interaction for a large server; report completion in status/logs.
        self.bot.request_refresh(interaction.guild)
        await interaction.followup.send(
            "Community refresh queued. See /admin status for the result.", ephemeral=True
        )

    @app_commands.command(description="Show feed settings, member count, and the latest refresh result")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        config = self.bot.store.settings(interaction.guild_id)
        def channel(value):
            return f"<#{value}>" if value else "Off"
        await interaction.response.send_message(embed=embed(
            "RoundForge bot status",
            f"Verified handles: **{len(self.bot.store.members(interaction.guild_id))}**\n"
            f"Contest feed: {channel(config['reminders_channel_id'])}\n"
            f"Daily feed: {channel(config['daily_channel_id'])}\n"
            f"Daily: rating **{config['daily_rating']}**, {config['daily_hour']:02}:00 {config['timezone']}\n"
            f"Rank roles configured: **{len(self.bot.store.roles(interaction.guild_id))}**\n"
            f"Last refresh: {safe(self.bot.refresh_statuses.get(interaction.guild_id, 'Not run since startup'), 500)}",
        ), ephemeral=True)

    @app_commands.command(description="Add/remove a bot command channel, or clear restrictions for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(self, interaction: discord.Interaction, action: Literal["add", "remove", "clear"],
                      channel: discord.TextChannel | None = None):
        if action != "clear" and channel is None:
            raise UserError("Select a channel for add/remove.")
        self.bot.store.channel_rule(interaction.guild_id, action, channel.id if channel else None)
        allowed = self.bot.store.allowed_channels(interaction.guild_id)
        text = " ".join(f"<#{cid}>" for cid in sorted(allowed)) if allowed else "All server channels"
        await interaction.response.send_message(f"Command channels: {text}", ephemeral=True)


def install_commands(bot: RoundForgeBot):
    for group in (Handles(bot), Daily(bot), Rank(bot), Admin(bot)):
        bot.tree.add_command(group)

    @bot.tree.command(description="Find an unsolved Codeforces problem near your rating")
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

    @bot.tree.command(description="See upcoming Codeforces contests in your server timezone")
    async def contests(interaction: discord.Interaction):
        await defer(interaction)
        upcoming = upcoming_contests(await bot.cf.contests(), int(time.time()))[:5]
        if not upcoming:
            await interaction.followup.send("No upcoming Codeforces contests are listed right now.")
            return
        await interaction.followup.send(embeds=[contest_embed(c, bot.store.timezone(interaction.guild_id)) for c in upcoming])

    @bot.tree.command(description="RoundForge server rankings by Codeforces rating or this month's daily points")
    async def leaderboard(interaction: discord.Interaction,
                          mode: Literal["rating", "daily"] = "rating",
                          page: app_commands.Range[int, 1, 1000] = 1):
        month = datetime.now(bot.store.timezone(interaction.guild_id)).strftime("%Y-%m") if mode == "daily" else None
        rows = bot.store.leaderboard(interaction.guild_id, page, month)
        if not rows:
            await interaction.response.send_message("No linked members on this page. Start with /handle link.")
            return
        lines = []
        for index, row in enumerate(rows, start=(page - 1) * 15 + 1):
            score = f"{row['points']} pts" if month else str(row["rating"] if row["rating"] is not None else "Unrated")
            lines.append(f"**{index}.** [{safe(row['handle'])}](https://codeforces.com/profile/{row['handle']}) — **{score}**")
        oldest = min(row["refreshed_at"] for row in rows)
        result = embed(f"RoundForge · {'Daily points · ' + month if month else 'Codeforces ratings'}", "\n".join(lines))
        result.add_field(name="Page", value=str(page))
        result.add_field(name="Rating cache", value=f"Oldest profile on page: <t:{oldest}:R> · Refreshes hourly")
        await interaction.response.send_message(embed=result)

    @bot.tree.command(description="Show RoundForge community commands and how to get started")
    async def help(interaction: discord.Interaction):
        await interaction.response.send_message(embed=embed(
            "Welcome to RoundForge Competitive Programming",
            "**A Codeforces community bot for every server**\n\n"
            "`/handle link` → `/handle verify` — prove account ownership\n"
            "`/handle show` — Discord ~ Codeforces avatar card and profile\n"
            "`/problem` — unsolved problem by rating and topic\n"
            "`/daily show` → `/daily claim` — daily challenge, 10 points\n"
            "`/leaderboard` — server ratings or monthly daily points\n"
            "`/contests` — upcoming rounds in your server timezone\n"
            "`/rank sync` — refresh your rating and CF role\n"
            "`/handle unlink` — remove your link\n\n"
            "`/training start` → `/training next` — beginner tracks\n"
            "`/upsolve start` → `/upsolve check` — post-contest goals\n"
            "`/blitz menu` → select players → choose settings → **Accept & ready** — multiplayer races\n"
            "`/blitz host` — manage a game for mentioned players; optional manager\n"
            "Select multiple topics, set time, and toggle everyone-finishes and standings in the menu.\n"
            "A visible 10-second countdown comes before the problem and voice effects.\n"
            "`/blitz standings` — verified solve times and ranks\n"
            "Omit blitz minutes for unlimited time; `/blitz cancel` stops the game.\n"
            "`/blitz games` — simultaneous games; `/blitz cancel` releases voices\n\n"
            "`/tournament create` — organizer: signup buttons, 2–32 players, automatic brackets\n"
            "`/tournament list` / `bracket` — view events; `retry` / `resolve` — manager decisions\n"
            "Each tournament duel has Accept buttons and the Blitz countdown.\n\n"
            "Organizers can use `/blitz solo` to test voice controls without another account.\n"
            "Organizers: `/admin configure`, `/admin channel`, `/admin setup_roles`, `/admin refresh`, `/admin status`.\n"
            "Daily points reward participation; previously solved problems may be solved again. "
            "Account verification does not certify real-world identity or organizational affiliation.",
        ), ephemeral=True)
