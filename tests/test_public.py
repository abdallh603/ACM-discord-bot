import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import tasks

from just_bot.core.bot import RoundForgeBot
from just_bot.core.options import rating_choices, timezone_choices, topic_choices
from just_bot.core.config import Settings


class PublicBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = RoundForgeBot(Settings("private-test-token", None, Path(":memory:"), frozenset(), "INFO"))

    async def asyncTearDown(self):
        await self.bot.close()

    def interaction(self, guild, channel=20, user=1):
        return SimpleNamespace(guild_id=guild, channel_id=channel, user=SimpleNamespace(id=user),
                               permissions=SimpleNamespace(manage_guild=False),
                               response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))

    async def test_public_mode_accepts_multiple_servers_and_rejects_dms(self):
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(10)))
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(11)))
        self.assertFalse(await self.bot.tree.interaction_check(self.interaction(None)))

    async def test_channel_rules_do_not_leak_to_other_servers(self):
        self.bot.store.channel_rule(10, "add", 30)
        self.assertFalse(await self.bot.tree.interaction_check(self.interaction(10, channel=20)))
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(11, channel=20)))

    async def test_global_sync_does_not_copy_commands_to_one_server(self):
        self.bot.tree.sync = AsyncMock(return_value=[])
        with patch.object(tasks.Loop, "start") as start:
            await self.bot.setup_hook()
        self.bot.tree.sync.assert_awaited_once_with()
        self.assertEqual(start.call_count, 1 + len(self.bot.extensions.loops))

    async def test_background_refresh_covers_every_server(self):
        guilds = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
        self.bot.active_guilds = lambda: guilds
        self.bot.request_refresh = MagicMock()
        await self.bot.hourly_refresh.coro(self.bot)
        self.assertEqual([c.args[0].id for c in self.bot.request_refresh.call_args_list], [10, 11])

    async def test_refresh_lock_is_server_scoped(self):
        self.assertIsNot(self.bot.refresh_lock(10), self.bot.refresh_lock(11))

    async def test_native_command_options_include_topics_ratings_and_timezones(self):
        self.assertEqual((await rating_choices(None, "35"))[0].value, 3500)
        self.assertIn("dp", [c.value for c in await topic_choices(None, "dp")])
        self.assertIn("Asia/Amman", [c.value for c in await timezone_choices(None, "amman")])
        self.assertLessEqual(len(await topic_choices(None, "")), 25)

    async def test_token_is_excluded_from_configuration_repr(self):
        self.assertNotIn("private-test-token", repr(self.bot.settings))


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_only_rating_leaderboard(self):
        bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=()))
        self.addAsyncCleanup(bot.close)
        command = bot.tree.get_command("leaderboard")
        self.assertEqual([c.value for c in command.get_parameter("mode").choices], ["rating"])
        self.assertEqual(command.description, "RoundForge server rankings by Codeforces rating")
        send = AsyncMock()
        interaction = SimpleNamespace(guild_id=10, response=SimpleNamespace(send_message=send))
        await command.callback(interaction, "rating", 1)
        send.assert_awaited_once_with("No linked members on this page. Start with /handle link.")
        bot.store.start_link(10, 1, "tester", "n", 9999999999)
        bot.store.complete_link(10, 1, "n", {"handle": "tester", "rating": None}, 1)
        await command.callback(interaction, "rating", 1)
        result = send.call_args.kwargs["embed"]
        self.assertEqual(result.title, "RoundForge · Codeforces ratings")
        self.assertEqual(result.description, "**1.** [tester](https://codeforces.com/profile/tester) — **Unrated**")
        self.assertEqual([field.name for field in result.fields], ["Page", "Rating cache"])
