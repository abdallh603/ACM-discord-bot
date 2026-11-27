import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from extensions.blitz.engine import Blitz
from just_bot.core.domain import UserError
from just_bot.core.service import Community
from just_bot.core.storage import Store
from extensions.blitz.tests.support import FakeVoice, blitz_store
from tests.support import problem


@unittest.skipUnless(importlib.util.find_spec("discord"), "Install requirements.txt for Discord controls")
class BlitzPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from extensions.blitz.views import BlitzPanel
        self.store = Store(":memory:")
        self.data = blitz_store(self.store)
        for user in (1, 2, 3):
            handle = f"player{user}"
            self.store.start_link(10, user, handle, "nonce", 9999999999)
            self.store.complete_link(10, user, "nonce", {"handle": handle, "rating": 1200}, 1)
        cf = AsyncMock()
        cf.submissions.return_value = []
        cf.problems.return_value = [problem()]
        cf.contests.return_value = [{"id": 100, "phase": "FINISHED"}]
        self.platform = FakeVoice(self.data)
        self.blitz = Blitz(Community(self.store, cf), self.data, self.platform)
        self.runtime = SimpleNamespace(engine=self.blitz, store=self.data, add_view=Mock(), community=self.blitz.community)
        self.match = await self.blitz.challenge(10, 1, [2, 3], {})
        self.panel = BlitzPanel(self.runtime, self.match)
        self.interaction = SimpleNamespace(
            guild_id=10, user=SimpleNamespace(id=2), permissions=SimpleNamespace(manage_guild=False),
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
        )

    async def asyncTearDown(self):
        self.panel.stop()
        await self.blitz.close()
        self.store.close()

    async def test_accept_button_requires_every_invitee_and_keeps_shared_buttons_usable(self):
        self.assertTrue(self.panel.is_persistent())
        await self.panel.act(self.interaction, "accept")
        self.assertEqual(self.data.match(10, self.match["id"])["state"], "pending")
        self.assertTrue(all(not child.disabled for child in self.panel.children))
        self.assertFalse(self.platform.muted)
        self.interaction.user.id = 3
        await self.panel.act(self.interaction, "accept")
        self.assertEqual(self.data.match(10, self.match["id"])["state"], "active")
        self.assertEqual(self.platform.muted, {1, 2, 3})

    async def test_stale_revision_buttons_cannot_accept_or_decline_changed_settings(self):
        await self.blitz.edit(10, self.match["id"], 1, {"end_rule": "all_solve"})
        for action in ("accept", "decline"):
            with self.assertRaisesRegex(UserError, "Settings changed"):
                await self.panel.act(self.interaction, action)
        self.assertEqual(self.data.players(self.match["id"])[1]["accepted_revision"], 0)

    async def test_organizers_managers_and_outsiders_cannot_accept_for_players(self):
        for actor, organizer in ((99, False), (99, True)):
            self.interaction.user.id = actor
            self.interaction.permissions.manage_guild = organizer
            with self.assertRaises(UserError):
                await self.panel.act(self.interaction, "accept")
        await self.blitz.cancel(10, self.match["id"], 1)
        from extensions.blitz.views import BlitzPanel
        hosted = await self.blitz.host(10, 99, [1, 2], {})
        view = BlitzPanel(self.runtime, hosted)
        with self.assertRaisesRegex(UserError, "Only invited players"):
            await view.act(self.interaction, "accept")
        view.stop()

    async def test_decline_cancels_invitation_and_releases_all_player_slots(self):
        await self.panel.act(self.interaction, "decline")
        self.assertEqual(self.data.match(10, self.match["id"])["state"], "cancelled")
        self.assertTrue(all(self.data.slot(10, user) is None for user in (1, 2, 3)))
        self.assertFalse(self.platform.muted)

    async def test_cross_server_buttons_are_rejected(self):
        self.interaction.guild_id = 20
        with self.assertRaisesRegex(UserError, "different server"):
            await self.panel.act(self.interaction, "accept")

    async def test_failed_invitation_delivery_does_not_reserve_players_for_ten_minutes(self):
        import discord
        from extensions.blitz.views import send_invite
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No send permission")
        send = AsyncMock(side_effect=error)
        with self.assertRaises(discord.Forbidden):
            await send_invite(self.runtime, self.match, send, [2, 3])
        self.assertEqual(self.data.match(10, self.match["id"])["state"], "cancelled")
        self.assertIsNone(self.data.slot(10, 2))

    async def test_acceptance_and_edit_refresh_public_readiness_with_current_revision(self):
        self.platform.update_lobby = AsyncMock()
        await self.panel.act(self.interaction, "accept")
        snapshot = self.platform.update_lobby.await_args.args[0]
        self.assertEqual(snapshot["state"], "pending")
        await self.blitz.edit(10, self.match["id"], 1, {"tags": ["dp", "graphs"]})
        snapshot = self.platform.update_lobby.await_args.args[0]
        self.assertEqual(snapshot["options"]["revision"], 2)
        self.assertEqual(self.data.players(self.match["id"])[1]["accepted_revision"], 0)

    async def test_persistent_controls_restore_with_current_revision_and_message_ids(self):
        from extensions.blitz.views import restore_panels
        updated = await self.blitz.edit(10, self.match["id"], 1, {})
        self.data.update_match(self.match["id"], options={**updated["options"], "lobby_message_id": 1234})
        restore_panels(self.runtime)
        view = self.runtime.add_view.call_args.args[0]
        self.assertEqual(self.runtime.add_view.call_args.kwargs["message_id"], 1234)
        self.assertEqual(view.revision, 2)
        self.assertTrue(view.is_persistent())
        view.stop()
        self.data.update_match(self.match["id"], state="active", options={**updated["options"], "game_message_id": 5678})
        restore_panels(self.runtime)
        game = self.runtime.add_view.call_args.args[0]
        self.assertEqual(self.runtime.add_view.call_args.kwargs["message_id"], 5678)
        self.assertIn("Check solves", [c.label for c in game.children])
        game.stop()

    async def test_standings_sort_solvers_share_tied_rank_and_list_unsolved(self):
        from extensions.blitz.views import standings_embed
        self.data.update_match(self.match["id"], starts_at=1000)
        self.data.update_player(self.match["id"], 1, solved_at=1065)
        self.data.update_player(self.match["id"], 2, solved_at=1065)
        match = self.data.match(10, self.match["id"])
        description = standings_embed(self.data, match).description
        self.assertEqual(description.count("**1.**"), 2)
        self.assertEqual(description.count("**1:05**"), 2)
        self.assertIn("<@3> · player3 · no verified solve", description)
        self.data.update_match(self.match["id"], options={**match["options"], "show_standings": False})
        with self.assertRaisesRegex(UserError, "disabled"):
            await self.panel.act(self.interaction, "standings")

    async def test_no_argument_player_picker_builds_menu_without_creating_lobby(self):
        from extensions.blitz.menu import BlitzPlayers
        picker = BlitzPlayers(self.runtime, 1, 10, {"text_channel_id": 20})
        self.interaction.user.id = 1
        self.interaction.guild = SimpleNamespace(fetch_member=AsyncMock(return_value=SimpleNamespace(bot=False)))
        self.interaction.edit_original_response = AsyncMock()
        picker.picker._values = [SimpleNamespace(id=2), SimpleNamespace(id=3)]
        await picker.selected(self.interaction)
        view = self.interaction.edit_original_response.call_args.kwargs["view"]
        self.assertEqual(view.opponents, [2, 3])
        self.assertEqual(len(self.data.matches(10)), 1)  # only the fixture's existing game
        self.assertTrue(picker.started)
        view.stop()
        picker.stop()
