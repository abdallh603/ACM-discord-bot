from __future__ import annotations

import logging
import time
from datetime import datetime

import discord

from .presentation import contest_embed, daily_embed
from .service import DailyService, upcoming_contests
from .store import DailyStore

log = logging.getLogger(__name__)


class Feeds:
    """Post the daily problem after the configured hour and remind 30 minutes before contests."""

    def __init__(self, bot, store: DailyStore, service: DailyService):
        self.bot = bot
        self.store = store
        self.service = service

    async def send_feed(self, guild: discord.Guild, channel_id: int, key: str, result: discord.Embed):
        store = self.store
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
        store = self.store
        config = store.settings(guild.id)
        zone = self.bot.store.timezone(guild.id)
        now = datetime.now(zone)
        daily_channel = config["daily_channel_id"]
        if daily_channel and now.hour >= config["daily_hour"]:
            key = f"daily/{now.date().isoformat()}/{daily_channel}"
            if not store.was_sent(guild.id, key):
                try:
                    daily = await self.service.daily(guild.id)
                    await self.send_feed(guild, daily_channel, key, daily_embed(daily))
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
