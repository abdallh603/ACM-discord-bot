import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from tests.support import problem


class DailyBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = RoundForgeBot(Settings("private-test-token", None, Path(":memory:"), frozenset(), "INFO",
                                          extensions=("daily",)))
        self.daily = self.bot.extensions.api("daily")

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_background_feeds_cover_every_server(self):
        guilds = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
        self.bot.active_guilds = lambda: guilds
        self.daily.feeds.feeds_for = AsyncMock()
        await self.daily.feeds.feeds()
        self.assertEqual([c.args[0].id for c in self.daily.feeds.feeds_for.await_args_list], [10, 11])

    async def test_daily_calendars_are_independent(self):
        self.bot.store.set_timezone(11, "Asia/Amman")
        self.bot.cf.problems = AsyncMock(return_value=[problem(100), problem(101)])
        now = datetime(2026, 11, 20, 22, tzinfo=timezone.utc)
        first = await self.daily.service.daily(10, now=now)
        second = await self.daily.service.daily(11, now=now)
        self.assertEqual((first["day"], second["day"]), ("2026-11-20", "2026-11-21"))

    async def test_daily_post_and_contest_reminder_are_sent_once_after_discord_accepts(self):
        import discord
        import time
        from unittest.mock import MagicMock, patch
        self.daily.store.configure(10, 555, 444, 1200, 0)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        guild = SimpleNamespace(id=10, get_channel=lambda cid: channel if cid in {444, 555} else None)
        self.bot.cf.problems = AsyncMock(return_value=[problem(100)])
        soon = int(time.time()) + 600
        self.bot.cf.contests = AsyncMock(return_value=[{"id": 7, "name": "Round 7", "phase": "BEFORE",
                                                        "startTimeSeconds": soon, "durationSeconds": 7200}])
        with patch("extensions.daily.feeds.log"):
            await self.daily.feeds.feeds_for(guild)
            await self.daily.feeds.feeds_for(guild)
        embeds = [call.kwargs["embed"] for call in channel.send.await_args_list]
        self.assertEqual(len(embeds), 2)
        self.assertTrue(embeds[0].description.startswith("**RoundForge Daily · "))
        self.assertTrue(embeds[1].description.startswith("**Contest starts within 30 minutes!**\n"))
        self.assertTrue(self.daily.store.was_sent(10, f"contest/7/{soon}/555"))

    async def test_admin_configure_saves_feeds_and_timezone(self):
        configure = self.bot.tree.get_command("admin").get_command("configure")
        channel = SimpleNamespace(id=444, mention="<#444>",
                                  permissions_for=lambda me: SimpleNamespace(view_channel=True, send_messages=True, embed_links=True))
        send = AsyncMock()
        interaction = SimpleNamespace(guild_id=10, guild=SimpleNamespace(me=object()),
                                      response=SimpleNamespace(send_message=send))
        await configure.callback(interaction, None, channel, 1400, 7, "Asia/Amman")
        self.assertEqual(send.call_args.args[0],
                         "Saved. Contest reminders: off. Daily post: <#444> at 07:00 Asia/Amman, rating 1400. "
                         "An already selected daily stays fixed until its deadline.")
        settings = self.daily.store.settings(10)
        self.assertEqual((settings["daily_channel_id"], settings["daily_rating"], settings["daily_hour"]), (444, 1400, 7))
        self.assertEqual(self.bot.store.timezone(10).key, "Asia/Amman")

    async def test_daily_leaderboard_mode_shows_this_months_points(self):
        from datetime import datetime as dt
        self.bot.store.start_link(10, 1, "tester", "n", 9999999999)
        self.bot.store.complete_link(10, 1, "n", {"handle": "tester", "rating": 1500}, 1)
        day = dt.now(self.bot.store.timezone(10)).date().isoformat()
        self.daily.store.save_daily(10, day, problem(), 0, 9999999999)
        self.daily.store.claim(10, day, 1, "tester", 77, 5)
        send = AsyncMock()
        interaction = SimpleNamespace(guild_id=10, response=SimpleNamespace(send_message=send))
        await self.bot.tree.get_command("leaderboard").callback(interaction, "daily", 1)
        result = send.call_args.kwargs["embed"]
        self.assertEqual(result.title, f"RoundForge · Daily points · {day[:7]}")
        self.assertEqual(result.description, "**1.** [tester](https://codeforces.com/profile/tester) — **10 pts**")
