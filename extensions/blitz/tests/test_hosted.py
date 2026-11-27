import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from extensions.blitz.engine import Blitz
from extensions.blitz.tests.support import FakeVoice, blitz_store
from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from just_bot.core.domain import UserError
from just_bot.core.service import Community
from just_bot.core.storage import Store
from tests.support import problem


class BlitzChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = RoundForgeBot(Settings("private-test-token", None, Path(":memory:"), frozenset(), "INFO",
                                          extensions=("blitz",)))

    async def asyncTearDown(self):
        await self.bot.close()

    def interaction(self, guild, channel=20, user=1):
        return SimpleNamespace(guild_id=guild, channel_id=channel, user=SimpleNamespace(id=user),
                               permissions=SimpleNamespace(manage_guild=False),
                               response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))

    async def test_pending_lobby_channel_exception_includes_manager_and_players_only(self):
        for user in (1, 2):
            handle = f"player{user}"
            self.bot.store.start_link(10, user, handle, "n", 9999999999)
            self.bot.store.complete_link(10, user, "n", {"handle": handle}, 1)
        self.bot.store.channel_rule(10, "add", 20)
        self.bot.store.channel_rule(11, "add", 20)
        match = await self.bot.extensions.api("blitz").engine.host(10, 99, [1, 2], {"manager_id": 88, "text_channel_id": 30})
        self.assertIsNone(match["text_id"])
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(10, channel=30, user=88)))
        self.assertTrue(await self.bot.tree.interaction_check(self.interaction(10, channel=30, user=1)))
        self.assertFalse(await self.bot.tree.interaction_check(self.interaction(10, channel=30, user=77)))
        self.assertFalse(await self.bot.tree.interaction_check(self.interaction(11, channel=30, user=88)))

    async def test_unlink_is_refused_during_a_game(self):
        for user in (1, 2):
            self.bot.store.start_link(10, user, f"player{user}", "n", 9999999999)
            self.bot.store.complete_link(10, user, "n", {"handle": f"player{user}"}, 1)
        await self.bot.extensions.api("blitz").engine.host(10, 99, [1, 2], {})
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        group = self.bot.tree.get_command("handle")
        with self.assertRaisesRegex(UserError, "Cancel your blitz game and finish voice recovery before unlinking."):
            await group.get_command("unlink").callback(group, interaction)
        self.assertIsNotNone(self.bot.store.member(10, 1))


class HostedBlitzTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.data = blitz_store(self.store)
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
