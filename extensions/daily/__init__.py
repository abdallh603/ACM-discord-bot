"""Daily problems and contest reminders: /daily, /contests, /admin configure, and feeds."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from .commands import Daily, configure_command, contests_command
from .feeds import Feeds
from .service import DailyService
from .store import MIGRATIONS, DailyStore


def setup(ext):
    ext.migrate(MIGRATIONS)
    bot = ext.bot
    store = DailyStore(bot.store)
    service = DailyService(bot.community, store)
    feeds = Feeds(bot, store, service)
    ext.add_command(Daily(service), order=20)
    ext.add_command(contests_command(bot), order=60)
    ext.add_admin_command(configure_command(bot, store), order=10)

    def daily_mode(guild_id, page):
        month = datetime.now(bot.store.timezone(guild_id)).strftime("%Y-%m")
        return "Daily points · " + month, store.leaderboard(guild_id, page, month), lambda row: f"{row['points']} pts"

    ext.add_leaderboard_mode("daily", help="monthly daily points", fetch=daily_mode, order=20)
    ext.add_loop(feeds.feeds, minutes=1)

    def channel(value):
        return f"<#{value}>" if value else "Off"

    ext.hook("admin_status", lambda guild: f"Contest feed: {channel(store.settings(guild)['reminders_channel_id'])}", order=20)
    ext.hook("admin_status", lambda guild: f"Daily feed: {channel(store.settings(guild)['daily_channel_id'])}", order=30)

    def schedule(guild):
        config = store.settings(guild)
        return (f"Daily: rating **{config['daily_rating']}**, {config['daily_hour']:02}:00 "
                f"{bot.store.settings(guild)['timezone']}")

    ext.hook("admin_status", schedule, order=40)
    ext.hook("after_unlink", lambda member: "Daily claim history remains to prevent duplicate rewards.", order=10)
    ext.help("`/daily show` → `/daily claim` — daily challenge, 10 points", block="commands", order=40)
    ext.help("`/contests` — upcoming rounds in your server timezone", block="commands", order=60)
    ext.help_item("`/admin configure`", line="admin", order=10)
    ext.help_item("Daily points reward participation; previously solved problems may be solved again.",
                  line="notes", order=10)
    return SimpleNamespace(store=store, service=service, feeds=feeds)
