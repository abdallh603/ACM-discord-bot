from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import tasks

from .activities_store import Activities
from .activity_commands import install_activity_commands
from .activity_data import ActivityData
from .activity_launch import install_activity_launch
from .blitz import Blitz
from .blitz_discord import DiscordBlitz
from .codeforces import Codeforces
from .commands import install_commands
from .config import Settings
from .domain import UserError, rank_for, upcoming_contests
from .presentation import contest_embed, problem_embed, safe
from .practice import Practice
from .profile_card import ProfileCards
from .service import Community
from .storage import Store
from .tournament import Tournaments
from .tournament_commands import DiscordTournaments, TournamentCommands, restore_tournaments

log = logging.getLogger(__name__)


async def reply_error(interaction: discord.Interaction, message: str):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class CommunityTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: RoundForgeBot = self.client
        if interaction.guild_id is None:
            await reply_error(interaction, "Run RoundForge commands inside a Discord server.")
            return False
        if not bot.guild_enabled(interaction.guild_id):
            await reply_error(interaction, "This instance is restricted to its configured development server.")
            return False
        allowed = bot.store.allowed_channels(interaction.guild_id)
        if not allowed and bot.settings.guild_id == interaction.guild_id:
            allowed = bot.settings.channel_ids
        if allowed and interaction.channel_id not in allowed:
            # Organizers may configure the bot from another channel.
            private_game = any(
                bot.blitz.can_access(match, interaction.user.id)
                for match in bot.activities.in_channel(interaction.guild_id, interaction.channel_id)
            )
            if not interaction.permissions.manage_guild and not private_game:
                await reply_error(interaction, "Use a designated bot channel for commands.")
                return False
        now = time.monotonic()
        key = (interaction.guild_id, interaction.user.id)
        last = bot.last_command.get(key, 0)
        if now - last < 2:
            await reply_error(interaction, "Please wait two seconds between commands.")
            return False
        bot.last_command[key] = now
        if len(bot.last_command) > 10000:
            bot.last_command = {key: value for key, value in bot.last_command.items() if now - value < 60}
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            message = safe(original, 1200)
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Try this command again in {error.retry_after:.0f} seconds."
        elif isinstance(error, (app_commands.MissingPermissions, app_commands.BotMissingPermissions)):
            message = "This command needs server permissions: " + ", ".join(error.missing_permissions)
        elif isinstance(original, discord.Forbidden):
            message = "I lack permission. Check channel access, voice moderation permissions, and my role position."
        elif isinstance(original, discord.HTTPException):
            message = "Discord could not complete that action. Please try again shortly."
        else:
            log.error("Command failed: %s", interaction.command, exc_info=(type(original), original, original.__traceback__))
            message = "Something went wrong. An organizer can check the bot's logs."
        try:
            await reply_error(interaction, message)
        except discord.HTTPException:
            log.warning("Could not send command error (interaction may have expired)")


class RoundForgeBot(discord.Client):
    def __init__(self, settings: Settings):
        # Slash commands and per-member REST lookups need no privileged intents.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = Store(settings.database_path)
        self.cf = Codeforces()
        self.community = Community(self.store, self.cf)
        self.activities = Activities(self.store)
        self.practice = Practice(self.community, self.activities)
        self.profile_cards = ProfileCards()
        self.blitz = Blitz(self.community, self.activities, DiscordBlitz(self))
        self.tournaments = Tournaments(self.community, self.blitz, DiscordTournaments(self))
        self.activity_data = ActivityData(self)
        self.activity_runner = None
        self.tree = CommunityTree(
            self, allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
        )
        self.last_command: dict[tuple[int, int], float] = {}
        self.refresh_locks = {}
        self.role_setup_locks = {}
        self.refresh_statuses = {}
        self.refresh_tasks = {}
        self.refresh_semaphore = asyncio.Semaphore(2)
        self.rank_locks = {}
        self._store_closed = False
        install_commands(self)
        install_activity_commands(self)
        self.tree.add_command(TournamentCommands(self))
        install_activity_launch(self)

    async def setup_hook(self):
        from .blitz_views import restore_panels
        restore_panels(self)
        restore_tournaments(self)
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
            if self.settings.legacy_guild_id:
                legacy = discord.Object(id=self.settings.legacy_guild_id)
                self.tree.clear_commands(guild=legacy)
                try:
                    await self.tree.sync(guild=legacy)
                except discord.HTTPException:
                    log.warning("Could not clear legacy development commands; public commands were registered")
        log.info("Registered %s command groups/commands", len(synced))
        if self.settings.activity_enabled:
            if str(self.application_id) != self.settings.client_id:
                raise ValueError("DISCORD_CLIENT_ID must match the bot application's ID.")
            from .activity_launch import register_entrypoint
            await register_entrypoint(self)
            from .activity_server import start_server
            self.activity_runner = await start_server(self)
        self.feeds.start()
        self.hourly_refresh.start()
        self.blitz_clock.start()

    async def on_ready(self):
        log.info("Connected as %s", self.user)
        await self.change_presence(activity=discord.Game(name="RoundForge · /help"))

    def guild_enabled(self, guild_id):
        return self.settings.guild_id is None or guild_id == self.settings.guild_id

    def active_guilds(self):
        return [guild for guild in self.guilds if self.guild_enabled(guild.id)]

    def refresh_lock(self, guild_id):
        return self.refresh_locks.setdefault(guild_id, asyncio.Lock())

    def role_setup_lock(self, guild_id):
        return self.role_setup_locks.setdefault(guild_id, asyncio.Lock())

    async def close(self):
        if self.activity_runner:
            runner, self.activity_runner = self.activity_runner, None
            await runner.cleanup()
        jobs = [self.feeds.get_task(), self.hourly_refresh.get_task(),
                self.blitz_clock.get_task(), *self.refresh_tasks.values()]
        self.feeds.cancel()
        self.hourly_refresh.cancel()
        self.blitz_clock.cancel()
        for task in self.refresh_tasks.values():
            task.cancel()
        await asyncio.gather(*(job for job in jobs if job), return_exceptions=True)
        if not self._store_closed:
            await self.tournaments.close()
            await self.blitz.close()
            for guild in self.active_guilds():
                await self.blitz.close(guild.id)
            self.store.close()
            self._store_closed = True
        await super().close()

    def rank_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self.rank_locks.setdefault((guild_id, user_id), asyncio.Lock())

    async def clear_rank_roles(self, member: discord.Member):
        async with self.rank_lock(member.guild.id, member.id):
            # Fetch current state; the member cache is intentionally disabled.
            member = await member.guild.fetch_member(member.id)
            managed = set(self.store.roles(member.guild.id).values())
            roles = [role for role in member.roles if role.id in managed]
            if roles:
                await member.remove_roles(*roles, reason="Codeforces account unlinked")

    async def sync_rank_role(self, member: discord.Member, rating: int | None) -> bool:
        async with self.rank_lock(member.guild.id, member.id):
            # Re-read the verified link after each REST wait, preventing stale rank grants.
            member = await member.guild.fetch_member(member.id)
            linked = self.store.member(member.guild.id, member.id)
            if not linked:
                return False
            rating = linked["rating"]
            rank, _ = rank_for(rating)
            mapping = self.store.roles(member.guild.id)
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

    def request_refresh(self, guild: discord.Guild):
        task = self.refresh_tasks.get(guild.id)
        if task and not task.done():
            return
        self.refresh_statuses[guild.id] = "Queued"
        self.refresh_tasks[guild.id] = asyncio.create_task(self.refresh_community(guild))

    async def refresh_community(self, guild: discord.Guild):
        async with self.refresh_semaphore, self.refresh_lock(guild.id):
            self.refresh_statuses[guild.id] = "Running"
            errors = 0
            try:
                failures = await self.community.refresh(guild.id)
                errors += len(failures)
                for linked in self.store.members(guild.id):
                    try:
                        member = await guild.fetch_member(linked["user_id"])
                        # A failed profile lookup leaves its cached rating but never grants roles.
                        if linked["handle"] not in failures:
                            await self.sync_rank_role(member, linked["rating"])
                    except discord.NotFound:
                        self.store.unlink(guild.id, linked["user_id"])
                    except (discord.HTTPException, UserError):
                        errors += 1
                        log.warning("Could not sync rank for member %s", linked["user_id"])
                stamp = datetime.now(self.store.timezone(guild.id)).isoformat(timespec="minutes")
                self.refresh_statuses[guild.id] = f"{stamp} · finished · {errors} unavailable profiles/role errors"
                log.info("Guild %s refresh: %s", guild.id, self.refresh_statuses[guild.id])
            except Exception:
                self.refresh_statuses[guild.id] = "Failed; check the console logs"
                log.exception("Community refresh failed")

    @tasks.loop(hours=1)
    async def hourly_refresh(self):
        for guild in self.active_guilds():
            self.request_refresh(guild)

    @hourly_refresh.before_loop
    async def before_refresh(self):
        await self.wait_until_ready()

    async def send_feed(self, guild: discord.Guild, channel_id: int, key: str, result: discord.Embed):
        if self.store.was_sent(guild.id, key):
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("Configured feed channel %s is unavailable", channel_id)
            return
        await channel.send(embed=result)
        # Persist only after Discord accepts the message; transient failures retry next minute.
        self.store.mark_sent(guild.id, key)

    @tasks.loop(minutes=1)
    async def feeds(self):
        for guild in self.active_guilds():
            try:
                await self.feeds_for(guild)
            except Exception:
                log.exception("Guild %s feed failed", guild.id)

    async def feeds_for(self, guild):
        config = self.store.settings(guild.id)
        zone = self.store.timezone(guild.id)
        now = datetime.now(zone)
        daily_channel = config["daily_channel_id"]
        if daily_channel and now.hour >= config["daily_hour"]:
            key = f"daily/{now.date().isoformat()}/{daily_channel}"
            if not self.store.was_sent(guild.id, key):
                try:
                    daily = await self.community.daily(guild.id)
                    await self.send_feed(guild, daily_channel, key, problem_embed(daily["problem"], daily=daily))
                except Exception:
                    log.exception("Daily feed failed; will retry next minute")
        reminder_channel = config["reminders_channel_id"]
        if reminder_channel:
            try:
                stamp = int(time.time())
                for contest in upcoming_contests(await self.cf.contests(), stamp):
                    if not 0 < contest["startTimeSeconds"] - stamp <= 1800:
                        continue
                    key = f"contest/{contest['id']}/{contest['startTimeSeconds']}/{reminder_channel}"
                    result = contest_embed(contest, zone)
                    result.description = "**Contest starts within 30 minutes!**\n" + result.description
                    await self.send_feed(guild, reminder_channel, key, result)
            except Exception:
                log.exception("Contest reminders failed; will retry next minute")

    @feeds.before_loop
    async def before_feeds(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=5)
    async def blitz_clock(self):
        try:
            for guild in self.active_guilds():
                await self.blitz.tick(guild.id)
                await self.tournaments.tick(guild.id)
        except Exception:
            log.exception("Blitz clock failed; will retry")

    @blitz_clock.before_loop
    async def before_blitz(self):
        await self.wait_until_ready()
        await asyncio.gather(*(self.blitz.recover(g.id) for g in self.active_guilds()))

    async def on_voice_state_update(self, member, before, after):
        if not self.guild_enabled(member.guild.id):
            return
        if before.channel != after.channel or (before.mute and not after.mute) or (before.deaf and not after.deaf):
            try:
                await self.blitz.voice_changed(member.guild.id, member.id)
            except Exception:
                log.exception("Blitz voice update failed for %s", member.id)
