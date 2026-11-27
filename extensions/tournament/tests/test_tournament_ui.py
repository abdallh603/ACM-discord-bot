import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image

from extensions.tournament.commands import ASSETS, DiscordTournaments, TournamentCommands, TournamentPanel, restore_tournaments, tournament_embed
import extensions.tournament.tests.test_tournament as fixtures


class TournamentUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Share fixture setup, without inheriting and repeating the engine test suite.
        await fixtures.TournamentTests.asyncSetUp(self)
        self.event = await self.engine.create(10, 99, "Community Cup", 16, 123, {"manager_id": 88})
        self.bot = SimpleNamespace(engine=self.engine, store=self.engine.data,
                                   blitz=SimpleNamespace(engine=self.blitz, store=self.activities),
                                   guild_enabled=lambda guild: guild == 10, add_view=Mock())
        self.view = TournamentPanel(self.bot, self.event)
        self.interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1, bot=False),
            permissions=SimpleNamespace(manage_guild=False), response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))

    async def asyncTearDown(self):
        self.view.stop()
        await fixtures.TournamentTests.asyncTearDown(self)

    async def test_signup_buttons_are_persistent_and_join_leave_update_membership(self):
        self.assertTrue(self.view.is_persistent())
        self.assertLessEqual(len(self.view.children), 5)
        self.assertTrue(all(len(child.custom_id) <= 100 for child in self.view.children))
        await self.view.act(self.interaction, "join")
        self.assertEqual([e["user_id"] for e in self.engine.data.entries(self.event["id"])], [1])
        await self.view.act(self.interaction, "leave")
        self.assertEqual(self.engine.data.entries(self.event["id"]), [])

    async def test_cross_server_and_unprivileged_management_buttons_are_rejected(self):
        from just_bot.core.domain import UserError
        for action in ("start", "cancel"):
            with self.assertRaises(UserError):
                await self.view.act(self.interaction, action)
        self.interaction.guild_id = 20
        with self.assertRaises(UserError):
            await self.view.act(self.interaction, "join")

    async def test_existing_signup_panel_restores_after_restart(self):
        self.engine.data.update(self.event["id"], message_id=543)
        restore_tournaments(self.bot)
        restored = self.bot.add_view.call_args.args[0]
        self.assertTrue(restored.is_persistent())
        self.assertEqual(self.bot.add_view.call_args.kwargs["message_id"], 543)
        restored.stop()

    async def test_bracket_image_is_attached_to_signup_and_updates_for_champion(self):
        message = SimpleNamespace(edit=AsyncMock())
        room = SimpleNamespace(get_partial_message=lambda mid: message)
        self.bot.get_guild = lambda gid: SimpleNamespace(get_channel=lambda cid: room)
        adapter = DiscordTournaments(self.bot)
        self.engine.data.update(self.event["id"], message_id=543)
        await adapter.update(self.engine.data.get(10, self.event["id"]))
        file = message.edit.call_args.kwargs["attachments"][0]
        self.assertEqual(file.filename, "tournament-bracket.png")
        file.close()
        await adapter.update(self.engine.data.get(10, self.event["id"]))
        self.assertEqual(message.edit.await_count, 1)
        self.engine.data.update(self.event["id"], state="finished", champion_id=1)
        await adapter.update(self.engine.data.get(10, self.event["id"]))
        file = message.edit.call_args.kwargs["attachments"][0]
        self.assertEqual(file.filename, "tournament-bracket.png")
        self.assertIn("Champion", message.edit.call_args.kwargs["embed"].description)
        self.assertEqual([item.label for item in message.edit.call_args.kwargs["view"].children], ["Bracket / status"])
        file.close()

    async def test_images_exist_and_bracket_fits_discord_embed_at_maximum_field(self):
        for filename in ("tournament-banner.png", "tournament-champion.png"):
            with Image.open(ASSETS / filename) as image:
                image.verify()
            self.assertLess((ASSETS / filename).stat().st_size, 8 * 1024 * 1024)
        # Use direct capacity update only in this fixture to exercise the largest public embed.
        self.store.db.execute("UPDATE tournament_events SET capacity=32 WHERE id=?", (self.event["id"],))
        self.store.db.commit()
        for user in range(1, 33):
            await self.engine.join(10, self.event["id"], user)
        await self.engine.start(10, self.event["id"], 99)
        await self.engine.close()
        await self.engine.advance(10, self.event["id"])
        current = self.engine.data.get(10, self.event["id"])
        result = tournament_embed(self.bot, current)
        self.assertLess(len(result.description), 4096)
        self.assertEqual(len(self.engine.data.pairs(self.event["id"], 1)), 16)

    async def test_bracket_button_and_slash_command_send_the_rendered_image(self):
        self.bot.get_guild = lambda gid: None
        self.engine.platform = DiscordTournaments(self.bot)
        self.interaction.app_permissions = SimpleNamespace(attach_files=True)
        captured = []
        async def send(**kwargs):
            captured.append(kwargs["file"].fp.read())
        self.interaction.followup.send.side_effect = send
        await self.view.act(self.interaction, "bracket")
        group = TournamentCommands(self.bot)
        command = group.get_command("bracket")
        await command.callback(group, self.interaction, self.event["id"], None)
        self.assertEqual(len(captured), 2)
        for raw in captured:
            with Image.open(io.BytesIO(raw)) as image:
                self.assertEqual(image.format, "PNG")
        self.assertEqual(self.interaction.followup.send.call_args.kwargs["embed"].image.url, "attachment://tournament-bracket.png")

    async def test_registered_panel_buttons_keep_their_custom_id_and_answer_first(self):
        import discord
        from extensions.tournament import PanelButton
        handler = AsyncMock()
        self.bot.panel_buttons = [PanelButton("activity", "Open voice activity", discord.ButtonStyle.primary, 1, handler)]
        view = TournamentPanel(self.bot, self.event)
        button = next(child for child in view.children if child.label == "Open voice activity")
        self.assertEqual(button.custom_id, f"rf:tournament:10:{self.event['id']}:activity")
        self.assertEqual(button.row, 1)
        await view.act(self.interaction, "activity")
        handler.assert_awaited_once_with(self.interaction, view)
        self.interaction.response.defer.assert_not_awaited()
        view.stop()
