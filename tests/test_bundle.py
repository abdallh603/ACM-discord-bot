"""The default bundle (core + every bundled extension) must look exactly like the pre-split bot.

The fixtures were captured from the code before the core/extension split. Discord users see
command names, options, descriptions, permissions and the /help text, so any drift here is a
user-facing change.
"""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

FIXTURES = Path(__file__).with_name("fixtures")


def make_bot():
    from just_bot.core.bot import RoundForgeBot
    from just_bot.core.config import Settings
    return RoundForgeBot(Settings("test-token", None, Path(":memory:"), frozenset(), "INFO"))


class DefaultBundleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = make_bot()

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_command_payload_is_identical_to_the_pre_split_bot(self):
        expected = json.loads((FIXTURES / "commands.json").read_text(encoding="utf-8"))
        actual = json.loads(json.dumps([c.to_dict(self.bot.tree) for c in self.bot.tree.get_commands()]))
        self.assertEqual([c["name"] for c in actual], [c["name"] for c in expected])
        for want, got in zip(expected, actual):
            with self.subTest(command=want["name"]):
                self.assertEqual(got, want)

    async def test_help_embed_is_identical_to_the_pre_split_bot(self):
        expected = json.loads((FIXTURES / "help.json").read_text(encoding="utf-8"))
        send = AsyncMock()
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=send))
        await self.bot.tree.get_command("help").callback(interaction)
        embed = send.call_args.kwargs["embed"]
        self.assertEqual({"title": embed.title, "description": embed.description, "color": embed.color.value,
                          "footer": embed.footer.text, "ephemeral": send.call_args.kwargs.get("ephemeral")},
                         expected)
