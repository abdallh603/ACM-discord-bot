"""Bots can never be Blitz players or managers (each entry point checks member.bot)."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from extensions.blitz.commands import BlitzCommands
from extensions.blitz.discord_adapter import DiscordBlitz
from extensions.blitz.engine import validate_options
from extensions.blitz.menu import BlitzPlayers
from extensions.blitz.tests.support import blitz_store
from just_bot.core.domain import UserError
from just_bot.core.storage import Store


class HumansOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.data = blitz_store(self.store)
        self.robot = SimpleNamespace(id=2, bot=True, voice=None)
        self.guild = SimpleNamespace(fetch_member=AsyncMock(return_value=self.robot),
                                     me=SimpleNamespace(guild_permissions=discord.Permissions.all()))

    async def asyncTearDown(self):
        self.store.close()

    async def test_voice_adapter_refuses_bot_opponents_before_any_voice_change(self):
        match = self.data.create_match(10, 1, validate_options({}), [(1, "one"), (2, "two")])
        adapter = DiscordBlitz(SimpleNamespace(store=self.data, get_guild=lambda gid: self.guild))
        with self.assertRaisesRegex(UserError, "Blitz opponents must be human members."):
            await adapter.preflight(match)

    async def test_commands_refuse_bot_players_and_managers(self):
        group = BlitzCommands(SimpleNamespace())
        with self.assertRaisesRegex(UserError, "Players and managers must be human server members."):
            await group.resolve_members(self.guild, [2])

    async def test_player_picker_refuses_bots(self):
        picker = BlitzPlayers(SimpleNamespace(community=SimpleNamespace(require_member=lambda g, u: None)), 1, 10, {})
        picker.picker._values = [self.robot]
        interaction = SimpleNamespace(guild=self.guild, response=SimpleNamespace(defer=AsyncMock()))
        with self.assertRaisesRegex(UserError, "Choose human players, not bots."):
            await picker.selected(interaction)
        self.assertFalse(picker.started)
        picker.stop()
