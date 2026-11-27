import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import tasks

from just_bot.activities_store import Activities
from just_bot.blitz import Blitz
from just_bot.core.bot import RoundForgeBot
from just_bot.core.options import rating_choices, timezone_choices, topic_choices
from just_bot.core.config import Settings
from just_bot.core.domain import UserError
from just_bot.core.service import Community
from just_bot.core.storage import Store
from tests.test_blitz import FakeVoice
from tests.support import problem


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

    async def test_pending_lobby_channel_exception_includes_manager_and_players_only(self):
        for user in (1, 2):
            handle = f"player{user}"
            self.bot.store.start_link(10, user, handle, "n", 9999999999)
            self.bot.store.complete_link(10, user, "n", {"handle": handle}, 1)
        self.bot.store.channel_rule(10, "add", 20)
        self.bot.store.channel_rule(11, "add", 20)
        match = await self.bot.blitz.host(10, 99, [1, 2], {"manager_id": 88, "text_channel_id": 30})
        self.assertIsNone(match["text_id"])
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(10, channel=30, user=88)))
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(10, channel=30, user=1)))
        self.assertFalse(await self.bot.tree.interaction_check(self.interaction(10, channel=30, user=77)))
        self.assertFalse(await self.bot.tree.interaction_check(self.interaction(11, channel=30, user=88)))

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


class HostedBlitzTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.data = Activities(self.store)
        for user in (1, 2, 3, 4):
            handle = f"player{user}"
            self.store.start_link(10, user, handle, "n", 9999999999)
            self.store.complete_link(10, user, "n", {"handle": handle, "rating": 1200}, 1)
        cf = AsyncMock()
        cf.submissions.return_value = []
        cf.problems.return_value = [problem()]
        cf.contests.return_value = [{"id": 100, "phase": "FINISHED"}]
        self.voice = FakeVoice(self.data)
        self.blitz = Blitz(Community(self.store, cf), self.data, self.voice)

    async def asyncTearDown(self):
        await self.blitz.close()
        self.store.close()

    async def test_unregistered_host_is_not_a_player_and_all_players_must_accept(self):
        match = await self.blitz.host(10, 99, [1, 2], {})
        self.assertIsNone(self.store.member(10, 99))
        self.assertEqual({p["user_id"] for p in self.data.players(match["id"])}, {1, 2})
        self.assertTrue(all(p["accepted_revision"] == 0 for p in self.data.players(match["id"])))
        with self.assertRaises(UserError):
            await self.blitz.accept(10, match["id"], 99)
        self.assertEqual((await self.blitz.accept(10, match["id"], 1))["state"], "pending")
        self.assertEqual((await self.blitz.accept(10, match["id"], 2))["state"], "active")
        self.assertEqual(self.voice.muted, {1, 2})
        self.assertIsNone(self.data.slot(10, 99))

    async def test_delegated_manager_can_edit_time_and_cancel_but_cannot_accept_for_players(self):
        match = await self.blitz.host(10, 99, [1, 2], {"manager_id": 88})
        match = await self.blitz.edit(10, match["id"], 88, {"minutes": 30})
        with self.assertRaises(UserError):
            await self.blitz.accept(10, match["id"], 88, revision=2)
        await self.blitz.accept(10, match["id"], 1, revision=2)
        await self.blitz.accept(10, match["id"], 2, revision=2)
        await self.blitz.set_time(10, match["id"], None, actor=88)
        self.assertIsNone(self.data.match(10, match["id"])["ends_at"])
        await self.blitz.cancel(10, match["id"], 88)
        self.assertFalse(self.voice.muted)

    async def test_unrelated_members_and_regular_players_cannot_change_a_managers_timer(self):
        match = await self.blitz.host(10, 99, [1, 2], {})
        with self.assertRaises(UserError):
            await self.blitz.edit(10, match["id"], 77, {"minutes": 30})
        await self.blitz.accept(10, match["id"], 1)
        await self.blitz.accept(10, match["id"], 2)
        for actor in (77, 1):
            with self.assertRaises(UserError):
                await self.blitz.set_time(10, match["id"], 30, actor=actor)

    async def test_one_host_can_manage_independent_simultaneous_games(self):
        first = await self.blitz.host(10, 99, [1, 2], {})
        second = await self.blitz.host(10, 99, [3, 4], {})
        await self.blitz.cancel(10, first["id"], 99)
        self.assertEqual(self.data.match(10, second["id"])["state"], "pending")
        self.assertIsNotNone(self.data.slot(10, 3))

    async def test_manager_rights_do_not_cross_server_boundaries(self):
        match = await self.blitz.host(10, 99, [1, 2], {"manager_id": 88})
        with self.assertRaises(UserError):
            await self.blitz.cancel(11, match["id"], 88)
        self.assertEqual(self.data.match(10, match["id"])["state"], "pending")


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_only_rating_leaderboard(self):
        bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=()))
        self.addAsyncCleanup(bot.close)
        command = bot.tree.get_command("leaderboard")
        self.assertEqual([c.value for c in command.get_parameter("mode").choices], ["rating"])
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
