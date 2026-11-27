import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from just_bot.activities_store import Activities
from just_bot.blitz import Blitz
from just_bot.core.options import RATINGS, TOPICS
from just_bot.core.service import Community
from just_bot.core.storage import Store
from tests.test_blitz import FakeVoice
from tests.support import problem

HAS_DISCORD = importlib.util.find_spec("discord") is not None


@unittest.skipUnless(HAS_DISCORD, "Install requirements.txt to test Discord menus")
class BlitzMenuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from just_bot.blitz_menu import BlitzMenu
        self.store = Store(":memory:")
        self.data = Activities(self.store)
        for user in (1, 2, 3):
            handle = f"player{user}"
            self.store.start_link(10, user, handle, "nonce", 9999999999)
            self.store.complete_link(10, user, "nonce", {"handle": handle, "rating": 1200}, 1)
        cf = AsyncMock()
        cf.submissions.return_value = []
        cf.problems.return_value = [problem()]
        cf.contests.return_value = [{"id": 100, "phase": "FINISHED"}]
        self.blitz = Blitz(Community(self.store, cf), self.data, FakeVoice(self.data))
        self.bot = SimpleNamespace(blitz=self.blitz, activities=self.data)
        self.view = BlitzMenu(self.bot, 1, 10, [2, 3], {"minutes": None, "text_channel_id": 20})
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=1), guild_id=10, permissions=SimpleNamespace(manage_guild=True),
            response=SimpleNamespace(edit_message=AsyncMock(), defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
            channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123, channel=SimpleNamespace(id=20)))),
        )

    async def asyncTearDown(self):
        self.view.stop()
        await self.blitz.close()
        self.store.close()

    async def test_every_topic_and_rating_is_reachable_without_exceeding_discord_limit(self):
        import discord
        for field, action, expected in [
            ("topic", "topics", {"any", "random", *TOPICS}),
            ("rating", "ratings", {"auto", *(str(r) for r in RATINGS)}),
        ]:
            values = set()
            for _ in range(2):
                select = next(item for item in self.view.children if isinstance(item, discord.ui.Select)
                              and item.field == field)
                self.assertLessEqual(len(select.options), 25)
                values.update(option.value for option in select.options)
                await self.view.action(self.interaction, action)
            self.assertEqual(values, expected)
        self.assertEqual(len(self.view.to_components()), 5)

    async def test_choices_create_a_real_pending_lobby_with_unlimited_time_and_combined_voice(self):
        await self.view.choose(self.interaction, "topic", "dp")
        await self.view.choose(self.interaction, "rating", "1400")
        await self.view.choose(self.interaction, "voice", "both")
        await self.view.action(self.interaction, "start")
        match = self.data.matches(10)[0]
        self.assertEqual(match["state"], "pending")
        self.assertEqual((match["options"]["tag"], match["options"]["rating_min"]), ("dp", 1400))
        self.assertIsNone(match["options"]["minutes"])
        self.assertTrue(match["options"]["mute_voice"] and match["options"]["deaf_voice"])
        self.interaction.channel.send.assert_awaited_once()
        self.assertTrue(self.view.started)

    async def test_another_member_cannot_change_menu_settings(self):
        self.interaction.user.id = 2
        self.assertFalse(await self.view.interaction_check(self.interaction))
        self.interaction.response.send_message.assert_awaited_once()
        self.assertEqual(self.view.rating, "auto")

    async def test_multi_topic_selection_survives_paging_and_clearing_one_page(self):
        await self.view.choose(self.interaction, "topic", ["dp", "graphs"])
        await self.view.action(self.interaction, "topics")
        await self.view.choose(self.interaction, "topic", ["trees", "strings"])
        self.assertEqual(self.view.match_options()["tags"], ["dp", "graphs", "strings", "trees"])
        await self.view.choose(self.interaction, "topic", [])
        self.assertEqual(self.view.match_options()["tags"], ["dp", "graphs"])
        await self.view.action(self.interaction, "topics")
        await self.view.choose(self.interaction, "topic", ["random"])
        self.assertEqual(self.view.match_options()["tags"], [])
        self.assertTrue(self.view.match_options()["random_topic"])

    async def test_any_and_random_are_exclusive_and_rule_and_standings_toggle(self):
        from just_bot.core.domain import UserError
        with self.assertRaises(UserError):
            await self.view.choose(self.interaction, "topic", ["any", "dp"])
        await self.view.action(self.interaction, "rule")
        await self.view.action(self.interaction, "standings")
        self.assertEqual(self.view.match_options()["end_rule"], "all_solve")
        self.assertFalse(self.view.match_options()["show_standings"])
        await self.view.action(self.interaction, "rule")
        await self.view.action(self.interaction, "standings")
        self.assertEqual(self.view.match_options()["end_rule"], "first_solve")
        self.assertTrue(self.view.match_options()["show_standings"])

    async def test_duration_modal_accepts_blank_as_unlimited_and_rejects_bad_values(self):
        from just_bot.blitz_menu import BlitzTime
        modal = BlitzTime(self.view)
        modal.minutes._value = "25"
        await modal.on_submit(self.interaction)
        self.assertEqual(self.view.match_options()["minutes"], 25)
        modal.minutes._value = "999"
        await modal.on_submit(self.interaction)
        self.assertEqual(self.view.match_options()["minutes"], 25)
        modal.minutes._value = ""
        await modal.on_submit(self.interaction)
        self.assertIsNone(self.view.match_options()["minutes"])

    async def test_cancel_and_expiration_do_not_create_a_game(self):
        await self.view.action(self.interaction, "cancel")
        self.assertEqual(self.data.matches(10), [])
        self.assertFalse(await self.view.interaction_check(self.interaction))
        self.view.message = SimpleNamespace(edit=AsyncMock())
        await self.view.on_timeout()
        self.assertTrue(all(item.disabled for item in self.view.children))
        self.view.message.edit.assert_awaited_once()

    async def test_manual_problem_disables_incompatible_filter_selects(self):
        self.view.options["manual"] = "100A"
        self.view.rebuild()
        selects = [item for item in self.view.children if hasattr(item, "field")]
        self.assertTrue(all(item.disabled for item in selects if item.field in {"topic", "rating"}))
        self.assertFalse(next(item for item in selects if item.field == "voice").disabled)
        self.assertNotIn("rating_min", self.view.match_options())

    async def test_solo_menu_rechecks_organizer_permissions_before_starting(self):
        self.view.opponents = []
        self.interaction.permissions.manage_guild = False
        from just_bot.core.domain import UserError
        with self.assertRaisesRegex(UserError, "Manage Server"):
            await self.view.action(self.interaction, "start")
        self.assertEqual(self.data.matches(10), [])

    async def test_hosted_menu_excludes_host_from_the_player_roster(self):
        self.view.options.update(hosted=True, manager_id=99)
        self.view.opponents = [2, 3]
        await self.view.action(self.interaction, "start")
        match = self.data.matches(10)[0]
        self.assertEqual({p["user_id"] for p in self.data.players(match["id"])}, {2, 3})
        self.assertTrue(self.bot.blitz.can_access(match, 99))
        self.assertIsNone(self.data.slot(10, 1))
