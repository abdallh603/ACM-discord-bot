"""The extension system: manifests, enabling, dependency resolution, registration, lifecycle."""
import asyncio
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord.ext import tasks

from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings
from just_bot.core.extensions import ExtensionError, discover, read_manifest, resolve


def manifest(name, *, requires=(), default_enabled=True, extra=""):
    listing = ", ".join(f'"{item}"' for item in requires)
    return (f'name = "{name}"\nversion = "1.0.0"\ndescription = "Fixture {name}"\n'
            f"requires = [{listing}]\ndefault_enabled = {str(default_enabled).lower()}\n{extra}")


class FixturePackage:
    """A throwaway extensions package on sys.path, removed after the test."""

    def __init__(self, case):
        self.root = Path(tempfile.mkdtemp())
        self.name = "rf_fixture_" + uuid.uuid4().hex[:10]
        (self.root / self.name).mkdir()
        (self.root / self.name / "__init__.py").write_text("")
        sys.path.insert(0, str(self.root))
        case.addCleanup(self.cleanup)

    def add(self, name, code="def setup(ext):\n    return None\n", toml=None, **options):
        folder = self.root / self.name / name
        folder.mkdir()
        (folder / "extension.toml").write_text(toml if toml is not None else manifest(name, **options))
        (folder / "__init__.py").write_text(textwrap.dedent(code))
        return folder

    def cleanup(self):
        sys.path.remove(str(self.root))
        for module in [m for m in sys.modules if m == self.name or m.startswith(self.name + ".")]:
            del sys.modules[module]


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.package = FixturePackage(self)

    def check_invalid(self, folder_name, toml, message):
        folder = self.package.add(folder_name, toml=toml)
        with self.assertRaisesRegex(ExtensionError, message):
            read_manifest(folder, self.package.name)

    def test_valid_manifest_fields(self):
        folder = self.package.add("alpha", requires=["beta"], extra='permissions = ["send_messages"]\nintents = ["voice_states"]\n')
        result = read_manifest(folder, self.package.name)
        self.assertEqual((result.name, result.version, result.requires, result.permissions, result.intents),
                         ("alpha", "1.0.0", ("beta",), ("send_messages",), ("voice_states",)))
        self.assertEqual(result.module, f"{self.package.name}.alpha")
        self.assertTrue(result.default_enabled)

    def test_invalid_manifests_name_the_problem(self):
        for folder, toml, message in [
            ("missing", 'name = "missing"\nversion = "1"\n', "'description' is required"),
            ("folder_a", manifest("other"), "must match its folder name"),
            ("core_x", manifest("core_x"), "reserved"),
            ("Upper", manifest("Upper"), "invalid name"),
            ("typo", manifest("typo") + 'dependencies = ["x"]\n', "unknown field.*dependencies"),
            ("perm", manifest("perm") + 'permissions = ["fly"]\n', "unknown Discord permission.*fly"),
            ("priv", manifest("priv") + 'intents = ["message_content"]\n', "privileged intents"),
            ("api", manifest("api") + "api_version = 99\n", "API version 99"),
            ("selfish", manifest("selfish", requires=["selfish"]), "cannot require itself"),
            ("shape", manifest("shape").replace("requires = []", 'requires = "x"'), "must be a list"),
            ("broken", "name = ", "cannot read the manifest"),
        ]:
            with self.subTest(folder=folder):
                self.check_invalid(folder, toml, message)


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.package = FixturePackage(self)
        self.package.add("blitz")
        self.package.add("tournament", requires=["blitz"])
        self.package.add("activity", requires=["tournament", "blitz"])
        self.package.add("zeta")
        self.package.add("example", default_enabled=False)
        self.available = discover(self.package.name)

    def names(self, requested):
        return [m.name for m in resolve(self.available, requested)]

    def test_discovery_reads_manifests_without_importing_extension_code(self):
        self.package.add("explosive", code="raise RuntimeError('imported')\n", default_enabled=False)
        self.assertIn("explosive", discover(self.package.name))
        self.assertNotIn(f"{self.package.name}.explosive", sys.modules)

    def test_unset_selects_default_enabled_extensions_in_dependency_order(self):
        self.assertEqual(self.names(None), ["blitz", "tournament", "activity", "zeta"])

    def test_explicit_list_and_core_only(self):
        self.assertEqual(self.names(["example"]), ["example"])
        self.assertEqual(self.names([]), [])

    def test_order_does_not_depend_on_configuration_order(self):
        self.assertEqual(self.names(["zeta", "activity", "tournament", "blitz"]),
                         self.names(["blitz", "tournament", "activity", "zeta"]))

    def test_missing_dependency_is_a_clear_startup_error(self):
        with self.assertRaisesRegex(ExtensionError, "Extension 'tournament' requires 'blitz'. Add 'blitz' to ENABLED_EXTENSIONS"):
            resolve(self.available, ["tournament"])

    def test_unknown_and_uninstalled_names_are_reported(self):
        with self.assertRaisesRegex(ExtensionError, "Unknown extension.*nope.*Installed extensions: activity, blitz"):
            resolve(self.available, ["nope"])
        self.package.add("orphan", requires=["ghost"])
        with self.assertRaisesRegex(ExtensionError, "requires 'ghost', which is not installed"):
            resolve(discover(self.package.name), ["orphan"])

    def test_dependency_cycles_are_rejected(self):
        self.package.add("left", requires=["right"])
        self.package.add("right", requires=["left"])
        with self.assertRaisesRegex(ExtensionError, "cycle between: left, right"):
            resolve(discover(self.package.name), ["left", "right"])


REGISTERING = '''
import discord
from discord import app_commands

MIGRATIONS = ["CREATE TABLE alpha_rows (id INTEGER PRIMARY KEY, note TEXT);"]
events = []


def setup(ext):
    ext.migrate(MIGRATIONS)

    @app_commands.command(name="alpha", description="Fixture command")
    async def alpha(interaction: discord.Interaction):
        pass

    @app_commands.command(name="alpha_admin", description="Fixture admin command")
    async def alpha_admin(interaction: discord.Interaction):
        pass

    ext.add_command(alpha, order=5)
    ext.add_admin_command(alpha_admin, order=45)
    ext.help("`/alpha` — fixture line", block="alpha_block")
    ext.help_item("`/admin alpha_admin`", line="admin", order=45)
    ext.hook("admin_status", lambda guild: f"Alpha status for {guild}", order=55)

    async def tick():
        events.append("tick")

    ext.add_loop(tick, seconds=30)

    async def on_typing(*args):
        events.append(("typing", args))

    ext.listen("on_typing", on_typing)
    for phase in ("startup", "started", "stopping", "shutdown"):
        async def record(phase=phase):
            events.append(("alpha", phase))
        getattr(ext, "on_" + phase)(record)
    return {"api": "alpha"}
'''

DEPENDENT = '''
def setup(ext):
    alpha = ext.require("alpha")
    for phase in ("startup", "shutdown"):
        async def record(phase=phase):
            alpha["log"].append(("beta", phase))
        getattr(ext, "on_" + phase)(record)
'''


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.package = FixturePackage(self)
        self.package.add("alpha", code=REGISTERING, extra='permissions = ["attach_files"]\nintents = ["guild_reactions"]\n')
        self.package.add("beta", code=DEPENDENT, requires=["alpha"])

    def bot(self, extensions=("alpha",)):
        bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=extensions),
                            extension_package=self.package.name)
        self.addAsyncCleanup(bot.close)
        return bot

    async def test_commands_admin_commands_and_help_are_registered_in_order(self):
        bot = self.bot()
        names = [c.name for c in bot.tree.get_commands()]
        self.assertEqual(names[0], "alpha")
        admin = [c.name for c in bot.tree.get_command("admin").commands]
        self.assertEqual(admin[admin.index("status") + 1], "alpha_admin")  # status=40 < 45 < channel=50
        text = bot.extensions.help.render()
        self.assertIn("`/alpha` — fixture line", text)
        self.assertIn("`/admin refresh`, `/admin alpha_admin`, `/admin status`.", text)

    async def test_hooks_feed_core_admin_status(self):
        bot = self.bot()
        lines = await bot.extensions.run_hooks("admin_status", 10)
        self.assertIn("Alpha status for 10", lines)
        self.assertLess(lines.index("Alpha status for 10"), len(lines) - 1)  # before "Last refresh"
        self.assertTrue(lines[-1].startswith("Last refresh"))

    async def test_tables_are_created_and_versioned_per_extension(self):
        from just_bot.core import schema
        bot = self.bot()
        self.assertIn("alpha_rows", schema.objects(bot.store.db))
        self.assertEqual(schema.version(bot.store.db, "alpha"), 1)

    async def test_manifest_intents_and_permissions_are_collected(self):
        bot = self.bot()
        self.assertTrue(bot.intents.guild_reactions)
        self.assertTrue(bot.extensions.required_permissions().attach_files)

    async def test_loops_start_after_sync_and_stop_on_close(self):
        bot = self.bot()
        loop = bot.extensions.loops[-1]
        bot.tree.sync = AsyncMock(return_value=[])
        with patch.object(tasks.Loop, "start") as start:
            await bot.setup_hook()
        self.assertEqual(start.call_count, 1 + len(bot.extensions.loops))  # core refresh + extensions
        self.assertIsInstance(loop, tasks.Loop)
        self.assertEqual(loop.seconds, 30)
        with patch.object(tasks.Loop, "cancel") as cancel:
            await bot.close()
        self.assertGreaterEqual(cancel.call_count, len(bot.extensions.loops))

    async def test_gateway_events_reach_listeners(self):
        bot = self.bot()
        module = sys.modules[f"{self.package.name}.alpha"]
        module.events.clear()
        bot.dispatch("typing", "channel", "user", "when")
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertIn(("typing", ("channel", "user", "when")), module.events)

    async def test_dependencies_get_the_returned_api_and_lifecycle_order(self):
        bot = self.bot(("alpha", "beta"))
        alpha_api = bot.extensions.api("alpha")
        self.assertEqual(alpha_api["api"], "alpha")
        alpha_api["log"] = []
        module = sys.modules[f"{self.package.name}.alpha"]
        module.events.clear()
        bot.tree.sync = AsyncMock(return_value=[])
        with patch.object(tasks.Loop, "start"):
            await bot.setup_hook()
        await bot.close()
        phases = [e for e in module.events if isinstance(e, tuple) and e[0] == "alpha"]
        self.assertEqual(phases, [("alpha", "startup"), ("alpha", "started"), ("alpha", "stopping"), ("alpha", "shutdown")])
        self.assertEqual(alpha_api["log"], [("beta", "startup"), ("beta", "shutdown")])
        # Dependents start after and shut down before what they depend on.
        self.assertEqual(bot.extensions.names(), ["alpha", "beta"])

    async def test_undeclared_dependency_use_is_rejected(self):
        self.package.add("gamma", code="def setup(ext):\n    ext.require('alpha')\n")
        with self.assertRaisesRegex(ExtensionError, "'gamma' uses 'alpha' without listing it in requires"):
            self.bot(("alpha", "gamma"))

    async def test_setup_failures_name_the_extension_and_close_the_database(self):
        self.package.add("broken", code="raise ImportError('missing library')\n")
        with self.assertRaisesRegex(ExtensionError, "'broken' failed to import: missing library"):
            self.bot(("broken",))
        self.package.add("failing", code="def setup(ext):\n    raise ValueError('bad config')\n")
        with self.assertRaisesRegex(ExtensionError, "'failing' failed during setup: bad config"):
            self.bot(("failing",))
        self.package.add("nosetup", code="VALUE = 1\n")
        with self.assertRaisesRegex(ExtensionError, "must define setup"):
            self.bot(("nosetup",))
        self.package.add("asyncsetup", code="async def setup(ext):\n    pass\n")
        with self.assertRaisesRegex(ExtensionError, "must be a regular function"):
            self.bot(("asyncsetup",))

    async def test_migrations_cannot_create_unprefixed_tables(self):
        self.package.add("sneaky", code="def setup(ext):\n    ext.migrate(['CREATE TABLE members_copy (id INTEGER);'])\n")
        with self.assertRaisesRegex(ExtensionError, "members_copy.*'sneaky_...'"):
            self.bot(("sneaky",))

    async def test_disabled_extensions_are_not_imported(self):
        self.bot(("alpha",))
        self.assertNotIn(f"{self.package.name}.beta", sys.modules)

    def test_registration_api_rejects_sync_callbacks(self):
        self.package.add("syncy", code="def setup(ext):\n    ext.add_loop(lambda: None, seconds=1)\n")
        with self.assertRaisesRegex(ExtensionError, "async functions"):
            RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=("syncy",)),
                          extension_package=self.package.name)


class ExampleExtensionTests(unittest.TestCase):
    def test_example_is_installed_but_disabled_by_default(self):
        example = discover()["example"]
        self.assertFalse(example.default_enabled)
        self.assertEqual(example.requires, ())


class HookCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_hooks_may_be_plain_or_async_functions(self):
        bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO", extensions=()))
        self.addAsyncCleanup(bot.close)
        seen = []

        async def async_refreshed(member, linked):
            seen.append(("async", member.id))

        def plain_refreshed(member, linked):
            seen.append(("plain", member.id))

        bot.extensions.hooks["member_refreshed"] = [(1, 0, plain_refreshed), (2, 1, async_refreshed)]
        bot.store.start_link(10, 1, "tester", "n", 9999999999)
        bot.store.complete_link(10, 1, "n", {"handle": "tester"}, 1)
        bot.community.refresh = AsyncMock(return_value=[])
        guild = SimpleNamespace(id=10, fetch_member=AsyncMock(return_value=SimpleNamespace(id=1)))
        await bot.refresh_community(guild)
        self.assertEqual(seen, [("plain", 1), ("async", 1)])
        self.assertIn("finished · 0 unavailable", bot.refresh_statuses[10])
        self.assertEqual(await bot.extensions.run_hooks("missing"), [])
