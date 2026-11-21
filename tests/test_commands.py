import importlib.util
import inspect
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

HAS_DISCORD = importlib.util.find_spec("discord") is not None


@unittest.skipUnless(HAS_DISCORD, "Install requirements.txt to test Discord command registration")
class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from just_bot.bot import RoundForgeBot
        from just_bot.config import Settings
        self.bot = RoundForgeBot(Settings("test-token", 10, Path(":memory:"), frozenset({20}), "INFO"))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_all_commands_serialize_without_a_discord_connection(self):
        import discord
        names = {c.qualified_name for c in self.bot.tree.walk_commands()
                 if not isinstance(c, discord.app_commands.Group)}
        self.assertEqual(names, {
            "handle link", "handle verify", "handle unlink", "handle show", "daily show",
            "daily claim", "rank sync", "admin configure", "admin setup_roles", "admin refresh",
            "admin status", "admin channel", "problem", "contests", "leaderboard", "help",
            "training start", "training status", "training next", "training stop",
            "upsolve start", "upsolve check", "upsolve stop",
            "blitz create", "blitz accept", "blitz edit", "blitz status", "blitz games",
            "blitz check", "blitz cancel", "blitz time", "blitz recover", "blitz solo", "blitz menu", "blitz host",
        })
        for command in self.bot.tree.get_commands():
            payload = command.to_dict(self.bot.tree)
            self.assertLessEqual(len(payload["description"]), 100)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.message_content)
        self.assertTrue(self.bot.intents.voice_states)

    async def test_every_admin_command_enforces_permissions_at_runtime(self):
        import discord
        group = self.bot.tree.get_command("admin")
        interaction = SimpleNamespace(
            permissions=discord.Permissions.none(), app_permissions=discord.Permissions.all(),
            guild_id=10, created_at=datetime.now(timezone.utc), user=SimpleNamespace(id=1),
        )
        blitz = self.bot.tree.get_command("blitz")
        restricted = [*group.commands, blitz.get_command("recover"),
                      blitz.get_command("solo")]
        for command in restricted:
            self.assertTrue(command.checks)
            with self.assertRaises(discord.app_commands.MissingPermissions):
                for check in command.checks:
                    result = check(interaction)
                    if inspect.isawaitable(result):
                        await result

    async def test_other_servers_and_non_bot_channels_are_rejected(self):
        for guild_id, channel_id in [(11, 20), (10, 21)]:
            response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
            interaction = SimpleNamespace(guild_id=guild_id, channel_id=channel_id,
                                          response=response, permissions=SimpleNamespace(manage_guild=False),
                                          user=SimpleNamespace(id=1))
            self.assertFalse(await self.bot.tree.interaction_check(interaction))
            response.send_message.assert_awaited_once()
