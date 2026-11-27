from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from . import commands
from .codeforces import Codeforces
from .config import Settings
from .domain import UserError
from .extensions import PACKAGE, ExtensionContext, Manifest, Registry, discover, resolve, schedule_listeners
from .presentation import safe
from .service import Community
from .storage import Store

log = logging.getLogger(__name__)
# Core registers its own commands, help lines and hooks through the same API as extensions.
CORE_MANIFEST = Manifest("core", "1", "RoundForge core", Path(__file__).parent, __package__)


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
            # Organizers may configure the bot from another channel; extensions may exempt
            # channels that belong to an activity the member takes part in.
            exempt = any(bot.extensions.call_hooks("channel_exemption", interaction))
            if not interaction.permissions.manage_guild and not exempt:
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
    def __init__(self, settings: Settings, *, extension_package: str = PACKAGE):
        # Resolve extensions first: their manifests decide the gateway intents, and a missing
        # dependency must stop startup before anything connects.
        manifests = [] if settings.extensions == () else resolve(discover(extension_package), settings.extensions)
        # Slash commands and per-member REST lookups need no privileged intents.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True  # TRANSITIONAL: legacy blitz/activity; moves to their manifests
        for manifest in manifests:
            for flag in manifest.intents:
                setattr(intents, flag, True)
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = Store(settings.database_path)
        self.cf = Codeforces()
        self.community = Community(self.store, self.cf)
        self.tree = CommunityTree(
            self, allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
        )
        self.last_command: dict[tuple[int, int], float] = {}
        self.refresh_locks = {}
        self.refresh_statuses = {}
        self.refresh_tasks = {}
        self.refresh_semaphore = asyncio.Semaphore(2)
        self._store_closed = False
        self.extensions = Registry(self)
        try:
            commands.install(ExtensionContext(self, CORE_MANIFEST, self.extensions))
            self.extensions.load(manifests)
            from ..legacy import install_legacy  # TRANSITIONAL until every feature is an extension
            install_legacy(self)
            self.install_commands()
        except BaseException:
            self.store.close()
            self._store_closed = True
            raise

    def install_commands(self):
        registry = self.extensions
        for _, _, command in sorted(registry.admin_commands, key=lambda entry: entry[:2]):
            self.admin_group.add_command(command)
        self.leaderboard_modes = [mode for *_, mode in sorted(registry.leaderboard_modes, key=lambda entry: entry[:2])]
        registry.commands.append((commands.COMMAND_ORDER["leaderboard"], -1,
                                  commands.leaderboard_command(self, self.leaderboard_modes)))
        for _, _, command in sorted(registry.commands, key=lambda entry: entry[:2]):
            self.tree.add_command(command)

    def dispatch(self, event_name: str, /, *args, **kwargs):
        super().dispatch(event_name, *args, **kwargs)
        schedule_listeners(self, self.extensions, event_name, args, kwargs)

    async def setup_hook(self):
        await self.extensions.run_phase("startup")
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
        await self.extensions.run_phase("started")
        self.hourly_refresh.start()
        for loop in self.extensions.loops:
            loop.start()
        enabled = ", ".join(f"{m.name} {m.version}" for m in self.extensions.enabled) or "none (core only)"
        permissions = self.extensions.required_permissions()
        log.info("Extensions: %s. Bot permissions needed by enabled features: %s (permissions integer %s)",
                 enabled, ", ".join(name for name, value in permissions if value) or "none beyond core", permissions.value)

    async def on_ready(self):
        log.info("Connected as %s", self.user)
        await self.change_presence(activity=discord.Game(name="RoundForge · /help"))

    def guild_enabled(self, guild_id):
        return self.settings.guild_id is None or guild_id == self.settings.guild_id

    def active_guilds(self):
        return [guild for guild in self.guilds if self.guild_enabled(guild.id)]

    def refresh_lock(self, guild_id):
        return self.refresh_locks.setdefault(guild_id, asyncio.Lock())

    async def close(self):
        await self.extensions.run_phase("stopping")
        loops = [self.hourly_refresh, *self.extensions.loops]
        jobs = [loop.get_task() for loop in loops] + list(self.refresh_tasks.values())
        for loop in loops:
            loop.cancel()
        for task in self.refresh_tasks.values():
            task.cancel()
        await asyncio.gather(*(job for job in jobs if job), return_exceptions=True)
        if not self._store_closed:
            await self.extensions.run_phase("shutdown")
            self.store.close()
            self._store_closed = True
        await super().close()

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
                hooks = self.extensions.hooks_for("member_refreshed")
                for linked in self.store.members(guild.id):
                    try:
                        # Fetching also prunes links of members who left the server.
                        member = await guild.fetch_member(linked["user_id"])
                        # A failed profile lookup leaves its cached rating but never runs member hooks.
                        if linked["handle"] not in failures:
                            for hook in hooks:
                                await hook(member, linked)
                    except discord.NotFound:
                        self.store.unlink(guild.id, linked["user_id"])
                    except (discord.HTTPException, UserError):
                        errors += 1
                        log.warning("Could not refresh member %s", linked["user_id"])
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
