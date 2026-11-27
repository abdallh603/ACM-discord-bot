"""TRANSITIONAL: features that have not moved to extensions/ yet.

They register through the same ExtensionContext API that extensions use. Each migration step
moves one install_* function into its extension; this module is deleted after the last one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from .core.domain import RANKS, UserError, rank_for, upcoming_contests
from .core.extensions import ExtensionContext, Manifest
from .core.options import rating_choices, timezone_choices
from .core.presentation import contest_embed, problem_embed

log = logging.getLogger(__name__)


async def defer(interaction: discord.Interaction, *, private: bool = False):
    await interaction.response.defer(ephemeral=private, thinking=True)


def install_legacy(bot):
    ext = ExtensionContext(bot, Manifest("legacy", "0", "Features not yet moved", None, __name__), bot.extensions)
    install_rank(ext)
    install_daily(ext)
    install_blitz(ext)
    install_tournament(ext)
    install_activity(ext)


# -- rank roles --------------------------------------------------------------------------
class RankRoles:
    def __init__(self, bot):
        self.bot = bot
        self.rank_locks = {}
        self.role_setup_locks = {}

    def rank_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self.rank_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def role_setup_lock(self, guild_id):
        return self.role_setup_locks.setdefault(guild_id, asyncio.Lock())

    async def clear_rank_roles(self, member: discord.Member):
        async with self.rank_lock(member.guild.id, member.id):
            # Fetch current state; the member cache is intentionally disabled.
            member = await member.guild.fetch_member(member.id)
            managed = set(self.bot.store.roles(member.guild.id).values())
            roles = [role for role in member.roles if role.id in managed]
            if roles:
                await member.remove_roles(*roles, reason="Codeforces account unlinked")

    async def sync_rank_role(self, member: discord.Member, rating: int | None) -> bool:
        async with self.rank_lock(member.guild.id, member.id):
            # Re-read the verified link after each REST wait, preventing stale rank grants.
            member = await member.guild.fetch_member(member.id)
            linked = self.bot.store.member(member.guild.id, member.id)
            if not linked:
                return False
            rating = linked["rating"]
            rank, _ = rank_for(rating)
            mapping = self.bot.store.roles(member.guild.id)
            if not mapping:
                return False
            desired = member.guild.get_role(mapping.get(rank, 0))
            if not desired:
                raise UserError("A CF rank role is missing. Ask an organizer to run /admin setup_roles.")
            managed = set(mapping.values())
            # Add/remove only recorded CF roles; do not replace the user's whole role list.
            old_roles = [role for role in member.roles if role.id in managed and role.id != desired.id]
            if desired not in member.roles:
                await member.add_roles(desired, reason="Verified Codeforces rating sync")
            if old_roles:
                await member.remove_roles(*old_roles, reason="Codeforces rank changed")
            return True


class Rank(app_commands.Group):
    def __init__(self, bot, roles: RankRoles):
        super().__init__(name="rank", description="Refresh your Codeforces rating and rank role")
        self.bot = bot
        self.roles = roles

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
        changed = await self.roles.sync_rank_role(interaction.user, profile.get("rating"))
        await interaction.followup.send(
            f"Rating refreshed: **{profile.get('rating', 'Unrated')} · {rank}**. "
            + ("Rank role synced." if changed else "An organizer can enable roles with /admin setup_roles."),
            ephemeral=True,
        )


def setup_roles_command(bot, roles: RankRoles):
    @app_commands.command(name="setup_roles", description="Create the bot's CF rank roles (requires Manage Roles)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def setup_roles(interaction: discord.Interaction):
        await defer(interaction, private=True)
        async with roles.role_setup_lock(interaction.guild_id):
            mapping = bot.store.roles(interaction.guild_id)
            ranks = [("Unrated", 0x6B7280)] + [(name, color) for _, name, color in RANKS]
            created = 0
            for name, color in ranks:
                if interaction.guild.get_role(mapping.get(name, 0)):
                    continue
                role = await interaction.guild.create_role(
                    name=f"CF · {name}", color=discord.Color(color), permissions=discord.Permissions.none(),
                    reason="RoundForge bot rank role setup", hoist=True, mentionable=False,
                )
                bot.store.save_role(interaction.guild_id, name, role.id)
                created += 1
        await interaction.followup.send(
            f"Created {created} CF roles. Put my bot role above all CF roles in Server Settings → Roles. "
            "Members can use /rank sync; automatic refresh runs hourly.", ephemeral=True,
        )
    return setup_roles


def install_rank(ext):
    bot = ext.bot
    roles = RankRoles(bot)
    bot.rank_roles = roles
    ext.add_command(Rank(bot, roles), order=30)
    ext.add_admin_command(setup_roles_command(bot, roles), order=20)

    async def member_refreshed(member, linked):
        await roles.sync_rank_role(member, linked["rating"])

    async def after_unlink(member):
        try:
            await roles.clear_rank_roles(member)
        except discord.HTTPException:
            return "An organizer must remove your CF rank role because I lack permission."
        return None

    ext.hook("member_refreshed", member_refreshed)
    ext.hook("after_unlink", after_unlink, order=20)
    ext.hook("link_verified", lambda guild, user, profile: "Use `/rank sync` to update your rating role.")
    ext.hook("admin_status", lambda guild: f"Rank roles configured: **{len(bot.store.roles(guild))}**", order=50)
    ext.help("`/rank sync` — refresh your rating and CF role", block="commands", order=70)
    ext.help_item("`/admin setup_roles`", line="admin", order=30)


# -- daily problems and contest reminders -------------------------------------------------
class Daily(app_commands.Group):
    def __init__(self, bot):
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


def configure_command(bot):
    @app_commands.command(name="configure", description="Set server feeds and timezone; omitted channels turn those feeds off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.autocomplete(timezone=timezone_choices, daily_rating=rating_choices)
    async def configure(
        interaction: discord.Interaction,
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
        bot.store.configure(
            interaction.guild_id,
            reminders_channel.id if reminders_channel else None,
            daily_channel.id if daily_channel else None, daily_rating, daily_hour,
        )
        if timezone:
            bot.store.set_timezone(interaction.guild_id, timezone)
        zone = bot.store.timezone(interaction.guild_id).key
        await interaction.response.send_message(
            f"Saved. Contest reminders: {reminders_channel.mention if reminders_channel else 'off'}. "
            f"Daily post: {daily_channel.mention if daily_channel else 'off'} at {daily_hour:02}:00 "
            f"{zone}, rating {daily_rating}. An already selected daily stays fixed until its deadline.",
            ephemeral=True,
        )
    return configure


class Feeds:
    def __init__(self, bot):
        self.bot = bot

    async def send_feed(self, guild: discord.Guild, channel_id: int, key: str, result: discord.Embed):
        store = self.bot.store
        if store.was_sent(guild.id, key):
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Configured feed channel %s is unavailable", channel_id)
            return
        await channel.send(embed=result)
        # Persist only after Discord accepts the message; transient failures retry next minute.
        store.mark_sent(guild.id, key)

    async def feeds(self):
        for guild in self.bot.active_guilds():
            try:
                await self.feeds_for(guild)
            except Exception:
                log.exception("Guild %s feed failed", guild.id)

    async def feeds_for(self, guild):
        store = self.bot.store
        config = store.settings(guild.id)
        zone = store.timezone(guild.id)
        now = datetime.now(zone)
        daily_channel = config["daily_channel_id"]
        if daily_channel and now.hour >= config["daily_hour"]:
            key = f"daily/{now.date().isoformat()}/{daily_channel}"
            if not store.was_sent(guild.id, key):
                try:
                    daily = await self.bot.community.daily(guild.id)
                    await self.send_feed(guild, daily_channel, key, problem_embed(daily["problem"], daily=daily))
                except Exception:
                    log.exception("Daily feed failed; will retry next minute")
        reminder_channel = config["reminders_channel_id"]
        if reminder_channel:
            try:
                stamp = int(time.time())
                for contest in upcoming_contests(await self.bot.cf.contests(), stamp):
                    if not 0 < contest["startTimeSeconds"] - stamp <= 1800:
                        continue
                    key = f"contest/{contest['id']}/{contest['startTimeSeconds']}/{reminder_channel}"
                    result = contest_embed(contest, zone)
                    result.description = "**Contest starts within 30 minutes!**\n" + result.description
                    await self.send_feed(guild, reminder_channel, key, result)
            except Exception:
                log.exception("Contest reminders failed; will retry next minute")


def install_daily(ext):
    bot = ext.bot
    ext.add_command(Daily(bot), order=20)

    @app_commands.command(description="See upcoming Codeforces contests in your server timezone")
    async def contests(interaction: discord.Interaction):
        await defer(interaction)
        upcoming = upcoming_contests(await bot.cf.contests(), int(time.time()))[:5]
        if not upcoming:
            await interaction.followup.send("No upcoming Codeforces contests are listed right now.")
            return
        await interaction.followup.send(embeds=[contest_embed(c, bot.store.timezone(interaction.guild_id)) for c in upcoming])

    ext.add_command(contests, order=60)
    ext.add_admin_command(configure_command(bot), order=10)

    def daily_mode(guild_id, page):
        month = datetime.now(bot.store.timezone(guild_id)).strftime("%Y-%m")
        return "Daily points · " + month, bot.store.leaderboard(guild_id, page, month), lambda row: f"{row['points']} pts"

    ext.add_leaderboard_mode("daily", help="monthly daily points", fetch=daily_mode, order=20)
    bot.daily_feeds = Feeds(bot)
    ext.add_loop(bot.daily_feeds.feeds, minutes=1)

    def channel(value):
        return f"<#{value}>" if value else "Off"

    ext.hook("admin_status", lambda guild: f"Contest feed: {channel(bot.store.settings(guild)['reminders_channel_id'])}", order=20)
    ext.hook("admin_status", lambda guild: f"Daily feed: {channel(bot.store.settings(guild)['daily_channel_id'])}", order=30)

    def schedule(guild):
        config = bot.store.settings(guild)
        return f"Daily: rating **{config['daily_rating']}**, {config['daily_hour']:02}:00 {config['timezone']}"

    ext.hook("admin_status", schedule, order=40)
    ext.hook("after_unlink", lambda member: "Daily claim history remains to prevent duplicate rewards.", order=10)
    ext.help("`/daily show` → `/daily claim` — daily challenge, 10 points", block="commands", order=40)
    ext.help("`/contests` — upcoming rounds in your server timezone", block="commands", order=60)
    ext.help_item("`/admin configure`", line="admin", order=10)
    ext.help_item("Daily points reward participation; previously solved problems may be solved again.",
                  line="notes", order=10)


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
