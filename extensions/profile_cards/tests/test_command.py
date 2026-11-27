import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings


class ProfileCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = RoundForgeBot(Settings("test-token", 10, Path(":memory:"), frozenset({20}), "INFO",
                                          extensions=("profile_cards",)))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_profile_command_attaches_card_and_keeps_text_when_files_are_not_allowed(self):
        profile = {"handle": "player1", "rating": 1400}
        self.bot.store.start_link(10, 1, "player1", "nonce", 9999999999)
        self.bot.store.complete_link(10, 1, "nonce", profile, 1)
        self.bot.cf.users = AsyncMock(return_value=[profile])
        member = SimpleNamespace(id=1, name="discord_user", display_name="Discord User")
        interaction = SimpleNamespace(guild_id=10, user=member,
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
            app_permissions=SimpleNamespace(attach_files=True))
        group = self.bot.tree.get_command("handle")
        command = group.get_command("show")
        card = discord.File(io.BytesIO(b"png fixture"), filename="roundforge-profile.png")
        cards = self.bot.extensions.api("profile_cards")
        cards.build = AsyncMock(return_value=card)
        await command.callback(group, interaction, None)
        self.assertEqual(interaction.followup.send.call_args.kwargs["file"], card)
        self.assertEqual(interaction.followup.send.call_args.kwargs["embed"].image.url, "attachment://roundforge-profile.png")
        card.close()
        interaction.app_permissions.attach_files = False
        cards.build.reset_mock()
        await command.callback(group, interaction, None)
        cards.build.assert_not_awaited()
        self.assertNotIn("file", interaction.followup.send.call_args.kwargs)

    async def test_without_the_extension_the_profile_embed_has_no_card(self):
        await self.bot.close()
        self.bot = RoundForgeBot(Settings("test-token", 10, Path(":memory:"), frozenset({20}), "INFO", extensions=()))
        profile = {"handle": "player1", "rating": 1400}
        self.bot.store.start_link(10, 1, "player1", "nonce", 9999999999)
        self.bot.store.complete_link(10, 1, "nonce", profile, 1)
        self.bot.cf.users = AsyncMock(return_value=[profile])
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1, name="u", display_name="U"),
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
            app_permissions=SimpleNamespace(attach_files=True))
        group = self.bot.tree.get_command("handle")
        await group.get_command("show").callback(group, interaction, None)
        self.assertNotIn("file", interaction.followup.send.call_args.kwargs)
        self.assertEqual(interaction.followup.send.call_args.kwargs["embed"].title, "player1 · Specialist")
