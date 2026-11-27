import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from just_bot.core import schema


def bot(extensions):
    return RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=extensions))


class ExampleExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabling_registers_command_table_loop_listener_and_help(self):
        enabled = bot(("example",))
        self.addAsyncCleanup(enabled.close)
        self.assertIn("hello", [c.name for c in enabled.tree.get_commands()])
        self.assertIn("example_greetings", schema.objects(enabled.store.db))
        self.assertEqual(schema.version(enabled.store.db, "example"), 1)
        self.assertIn("guild_join", enabled.extensions.listeners)
        self.assertTrue(any(loop.hours == 1 for loop in enabled.extensions.loops))
        self.assertIn("`/hello` — a greeting from the example extension", enabled.extensions.help.render())

    async def test_hello_counts_greetings_per_member(self):
        enabled = bot(("example",))
        self.addAsyncCleanup(enabled.close)
        send = AsyncMock()
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(send_message=send))
        command = enabled.tree.get_command("hello")
        await command.callback(interaction)
        await command.callback(interaction)
        self.assertEqual(send.call_args.args[0], "Hello! You have said hello 2 times.")
        self.assertEqual(enabled.extensions.api("example").total(), 2)

    async def test_default_configuration_leaves_it_disabled(self):
        default = bot(None)
        self.addAsyncCleanup(default.close)
        self.assertIsNone(default.tree.get_command("hello"))
        self.assertNotIn("example_greetings", schema.objects(default.store.db))
