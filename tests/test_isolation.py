"""Architecture rules: core knows no extension; extensions use core's API and declared dependencies."""
import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord.ext import tasks

from just_bot.core import schema
from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from just_bot.core.extensions import discover

ROOT = Path(__file__).resolve().parents[1]
FEATURE_WORDS = ("blitz", "tournament", "activity_", "practice_", "daily_", "rank_roles", "profile_card")


def imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


class IsolationTests(unittest.TestCase):
    def test_core_never_imports_or_names_an_extension(self):
        for path in (ROOT / "just_bot").rglob("*.py"):
            with self.subTest(path=path.relative_to(ROOT)):
                modules = list(imported_modules(path))
                self.assertFalse([m for m in modules if m == "extensions" or m.startswith("extensions.")])
                source = path.read_text(encoding="utf-8").lower()
                self.assertFalse([word for word in FEATURE_WORDS if word in source])

    def test_extensions_use_core_only_through_the_api_module(self):
        for path in (ROOT / "extensions").rglob("*.py"):
            if "tests" in path.relative_to(ROOT).parts:
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                core = [m for m in imported_modules(path) if m == "just_bot" or m.startswith("just_bot.")]
                self.assertEqual(set(core) - {"just_bot.core.api"}, set())

    def test_extensions_import_only_extensions_they_require(self):
        manifests = discover()
        for name, manifest in manifests.items():
            allowed = {name, *manifest.requires}
            for path in manifest.path.rglob("*.py"):
                with self.subTest(extension=name, path=path.relative_to(ROOT)):
                    used = {m.split(".")[1] for m in imported_modules(path)
                            if m.startswith("extensions.") and len(m.split(".")) > 1}
                    self.assertEqual(used - allowed, set())


CORE_HELP = (
    "**A Codeforces community bot for every server**\n\n"
    "`/handle link` → `/handle verify` — prove account ownership\n"
    "`/handle show` — Discord ~ Codeforces avatar card and profile\n"
    "`/problem` — unsolved problem by rating and topic\n"
    "`/leaderboard` — server ratings\n"
    "`/handle unlink` — remove your link\n\n"
    "Organizers: `/admin channel`, `/admin refresh`, `/admin status`.\n"
    "Account verification does not certify real-world identity or organizational affiliation."
)


class CoreOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=()))

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_core_alone_registers_only_core_commands(self):
        names = {c.qualified_name for c in self.bot.tree.walk_commands()}
        self.assertEqual(names, {"handle", "handle link", "handle verify", "handle unlink", "handle show",
                                 "admin", "admin refresh", "admin status", "admin channel",
                                 "problem", "leaderboard", "help"})
        self.assertEqual(self.bot.extensions.names(), [])
        self.assertFalse(self.bot.intents.voice_states)
        self.assertTrue(self.bot.intents.guilds)

    async def test_core_alone_creates_only_core_tables(self):
        tables = schema.objects(self.bot.store.db)
        self.assertTrue(tables)
        self.assertEqual({t for t in tables if not t.startswith("core_")}, set())

    async def test_core_alone_help_and_status(self):
        send = AsyncMock()
        interaction = SimpleNamespace(guild_id=10, response=SimpleNamespace(send_message=send))
        await self.bot.tree.get_command("help").callback(interaction)
        self.assertEqual(send.call_args.kwargs["embed"].description, CORE_HELP)
        await self.bot.tree.get_command("admin").get_command("status").callback(interaction)
        self.assertEqual(send.call_args.kwargs["embed"].description,
                         "Verified handles: **0**\nLast refresh: Not run since startup")

    async def test_core_alone_starts_and_stops(self):
        self.bot.tree.sync = AsyncMock(return_value=[])
        with patch.object(tasks.Loop, "start") as start:
            await self.bot.setup_hook()
        self.bot.tree.sync.assert_awaited_once_with()
        self.assertEqual(start.call_count, 1)  # the hourly rating refresh
        await self.bot.close()
        self.assertTrue(self.bot._store_closed)

    async def test_core_alone_unlink_and_verify_replies(self):
        self.bot.store.start_link(10, 1, "tester", "n", 9999999999)
        self.bot.store.complete_link(10, 1, "n", {"handle": "tester"}, 1)
        interaction = SimpleNamespace(guild_id=10, user=SimpleNamespace(id=1),
                                      response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        group = self.bot.tree.get_command("handle")
        await group.get_command("unlink").callback(group, interaction)
        self.assertEqual(interaction.followup.send.call_args.args[0], "Account link and pending verification removed.")
        self.bot.community.verify = AsyncMock(return_value={"handle": "tester"})
        await group.get_command("verify").callback(group, interaction)
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         "Linked **tester**. You can restore your Codeforces First name.")


class StartupErrorTests(unittest.TestCase):
    def run_main(self, env):
        from just_bot.__main__ import main
        with patch.dict(os.environ, env, clear=True), patch("just_bot.__main__.load_dotenv"), \
                patch("logging.basicConfig"), self.assertRaises(SystemExit) as raised:
            main()
        return str(raised.exception)

    def test_missing_dependency_is_reported_before_connecting(self):
        message = self.run_main({"DISCORD_TOKEN": "fixture", "DATABASE_PATH": ":memory:",
                                 "ENABLED_EXTENSIONS": "tournament"})
        self.assertEqual(message, "Extension 'tournament' requires 'blitz'. Add 'blitz' to ENABLED_EXTENSIONS, "
                                  "or remove 'tournament'.")

    def test_unknown_extension_and_missing_token_are_reported(self):
        self.assertIn("Unknown extension(s) in ENABLED_EXTENSIONS: nope", self.run_main(
            {"DISCORD_TOKEN": "fixture", "DATABASE_PATH": ":memory:", "ENABLED_EXTENSIONS": "nope"}))
        self.assertEqual(self.run_main({}), "Set DISCORD_TOKEN in .env before starting the bot.")

    def test_core_only_and_full_configurations_construct(self):
        from just_bot.core.config import Settings as RealSettings
        for value, expected in (("none", []), ("", [m.name for m in discover().values() if m.default_enabled])):
            with patch.dict(os.environ, {"DISCORD_TOKEN": "fixture", "DATABASE_PATH": ":memory:",
                                         "ENABLED_EXTENSIONS": value}, clear=True):
                settings = RealSettings.from_env()
            bot = RoundForgeBot(settings)
            try:
                self.assertEqual(sorted(bot.extensions.names()), sorted(expected))
            finally:
                bot.store.close()
