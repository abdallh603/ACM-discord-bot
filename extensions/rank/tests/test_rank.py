import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from extensions.rank.roles import MIGRATIONS, RankRoles, RankStore
from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from just_bot.core.domain import UserError
from just_bot.core.schema import migrate
from just_bot.core.storage import Store


def rank_store(store):
    migrate(store.db, "rank", MIGRATIONS)
    return RankStore(store.db)


class Role(SimpleNamespace):
    def __hash__(self):
        return hash(self.id)


class RankStoreTests(unittest.TestCase):
    def test_role_mapping_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.sqlite3"
            store = Store(path)
            rank_store(store).save_role(10, "Pupil", 333)
            store.close()
            store = Store(path)
            try:
                self.assertEqual(rank_store(store).roles(10), {"Pupil": 333})
                self.assertEqual(rank_store(store).roles(11), {})
            finally:
                store.close()


class RankRoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.roles = RankRoles(SimpleNamespace(store=self.store), rank_store(self.store))
        self.store.start_link(10, 1, "tester", "n", 9999999999)
        self.store.complete_link(10, 1, "n", {"handle": "tester", "rating": 1450}, 1)
        self.pupil, self.specialist, self.unmanaged = Role(id=1), Role(id=2), Role(id=99)
        for name, role in (("Pupil", self.pupil), ("Specialist", self.specialist)):
            self.roles.store.save_role(10, name, role.id)
        roles = {1: self.pupil, 2: self.specialist}
        self.member = SimpleNamespace(id=1, roles=[self.pupil, self.unmanaged], add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.member.guild = SimpleNamespace(id=10, get_role=roles.get, fetch_member=AsyncMock(return_value=self.member))

    async def asyncTearDown(self):
        self.store.close()

    def test_locks_are_server_scoped(self):
        self.assertIsNot(self.roles.rank_lock(10, 1), self.roles.rank_lock(11, 1))
        self.assertIsNot(self.roles.role_setup_lock(10), self.roles.role_setup_lock(11))

    async def test_sync_grants_the_verified_rank_and_removes_only_managed_roles(self):
        self.assertTrue(await self.roles.sync_rank_role(self.member, 1450))
        self.member.add_roles.assert_awaited_once_with(self.specialist, reason="Verified Codeforces rating sync")
        self.member.remove_roles.assert_awaited_once_with(self.pupil, reason="Codeforces rank changed")

    async def test_missing_rank_role_asks_for_setup_and_unlinked_members_are_skipped(self):
        self.store.update_profile(10, {"handle": "tester", "rating": 2000}, 2)
        with self.assertRaisesRegex(UserError, "/admin setup_roles"):
            await self.roles.sync_rank_role(self.member, 2000)
        self.store.unlink(10, 1)
        self.assertFalse(await self.roles.sync_rank_role(self.member, 2000))

    async def test_unlink_removes_only_bot_managed_roles(self):
        await self.roles.clear_rank_roles(self.member)
        self.member.remove_roles.assert_awaited_once_with(self.pupil, reason="Codeforces account unlinked")


class RankHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=("rank",)))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_unlink_reply_reports_a_role_it_could_not_remove(self):
        roles = self.bot.extensions.api("rank")
        roles.clear_rank_roles = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"))
        self.bot.store.start_link(10, 1, "tester", "n", 9999999999)
        self.bot.store.complete_link(10, 1, "n", {"handle": "tester"}, 1)
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        group = self.bot.tree.get_command("handle")
        await group.get_command("unlink").callback(group, interaction)
        reply = interaction.followup.send.call_args.args[0]
        self.assertTrue(reply.startswith("Account link and pending verification removed. "))
        self.assertTrue(reply.endswith(" An organizer must remove your CF rank role because I lack permission."))
        self.assertIsNone(self.bot.store.member(10, 1))

    async def test_verify_reply_points_to_rank_sync(self):
        self.bot.community.verify = AsyncMock(return_value={"handle": "tester"})
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        group = self.bot.tree.get_command("handle")
        await group.get_command("verify").callback(group, interaction)
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         "Linked **tester**. You can restore your Codeforces First name. "
                         "Use `/rank sync` to update your rating role.")
