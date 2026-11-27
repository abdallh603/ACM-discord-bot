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

    async def test_unlink_and_verify_replies_combine_extension_notes_as_before(self):
        import discord
        roles = self.bot.extensions.api("rank")
        roles.clear_rank_roles = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"))
        self.bot.store.start_link(10, 1, "tester", "n", 9999999999)
        self.bot.store.complete_link(10, 1, "n", {"handle": "tester"}, 1)
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        group = self.bot.tree.get_command("handle")
        await group.get_command("unlink").callback(group, interaction)
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         "Account link and pending verification removed. Daily claim history remains to prevent "
                         "duplicate rewards. An organizer must remove your CF rank role because I lack permission.")
        self.bot.community.verify = AsyncMock(return_value={"handle": "tester"})
        await group.get_command("verify").callback(group, interaction)
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         "Linked **tester**. You can restore your Codeforces First name. "
                         "Use `/rank sync` to update your rating role.")

    async def test_admin_status_lists_every_line_in_the_original_order(self):
        send = AsyncMock()
        interaction = SimpleNamespace(guild_id=10, response=SimpleNamespace(send_message=send))
        status = self.bot.tree.get_command("admin").get_command("status")
        await status.callback(interaction)
        self.assertEqual(send.call_args.kwargs["embed"].description,
                         "Verified handles: **0**\nContest feed: Off\nDaily feed: Off\n"
                         "Daily: rating **1200**, 09:00 UTC\nRank roles configured: **0**\n"
                         "Last refresh: Not run since startup")
