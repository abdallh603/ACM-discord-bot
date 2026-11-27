"""Extension discovery, dependency resolution, and the registration API extensions use.

An extension is a folder ``extensions/<name>/`` with an ``extension.toml`` manifest and an
``__init__.py`` that defines ``setup(ext)``. Only manifests are read during discovery; the code
of an extension is imported only when it is enabled. ``setup`` receives an
:class:`ExtensionContext` and may return an object, which becomes the extension's public API
for extensions that declare it in ``requires``.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import itertools
import logging
import re
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import discord
from discord.ext import tasks

from . import schema
from .help import HelpBook

log = logging.getLogger(__name__)

API_VERSION = 1
PACKAGE = "extensions"
MANIFEST = "extension.toml"
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
PRIVILEGED_INTENTS = frozenset({"members", "presences", "message_content"})
FIELDS = {"name", "version", "description", "requires", "permissions", "intents", "default_enabled",
          "api_version"}
PHASES = ("startup", "started", "stopping", "shutdown")


class ExtensionError(Exception):
    """A configuration or extension problem reported at startup, before connecting to Discord."""


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    description: str
    path: Path
    module: str
    requires: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    default_enabled: bool = False
    api_version: int = API_VERSION


def _strings(data, key, source):
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ExtensionError(f"{source}: '{key}' must be a list of strings.")
    return tuple(value)


def read_manifest(folder: Path, package: str = PACKAGE) -> Manifest:
    source = folder / MANIFEST
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ExtensionError(f"{source}: cannot read the manifest ({exc}).") from exc
    unknown = sorted(set(data) - FIELDS)
    if unknown:
        raise ExtensionError(f"{source}: unknown field(s) {', '.join(unknown)}. Allowed: {', '.join(sorted(FIELDS))}.")
    for key in ("name", "version", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ExtensionError(f"{source}: '{key}' is required and must be a non-empty string.")
    name = data["name"]
    if not NAME.fullmatch(name) or name.startswith("core"):
        raise ExtensionError(f"{source}: invalid name '{name}'. Use lowercase letters, digits and "
                             "underscores (starting with a letter); names starting with 'core' are reserved.")
    if name != folder.name:
        raise ExtensionError(f"{source}: name '{name}' must match its folder name '{folder.name}'.")
    requires = _strings(data, "requires", source)
    if name in requires:
        raise ExtensionError(f"{source}: an extension cannot require itself.")
    permissions = _strings(data, "permissions", source)
    invalid = sorted(set(permissions) - set(discord.Permissions.VALID_FLAGS))
    if invalid:
        raise ExtensionError(f"{source}: unknown Discord permission(s) {', '.join(invalid)}.")
    intents = _strings(data, "intents", source)
    invalid = sorted(set(intents) - set(discord.Intents.VALID_FLAGS))
    if invalid:
        raise ExtensionError(f"{source}: unknown gateway intent(s) {', '.join(invalid)}.")
    privileged = sorted(set(intents) & PRIVILEGED_INTENTS)
    if privileged:
        raise ExtensionError(f"{source}: privileged intents are not supported ({', '.join(privileged)}).")
    default_enabled = data.get("default_enabled", False)
    if not isinstance(default_enabled, bool):
        raise ExtensionError(f"{source}: 'default_enabled' must be true or false.")
    api_version = data.get("api_version", API_VERSION)
    if api_version != API_VERSION:
        raise ExtensionError(f"{source}: '{name}' needs extension API version {api_version}; this core "
                             f"provides version {API_VERSION}.")
    return Manifest(name, data["version"], data["description"], folder, f"{package}.{name}",
                    requires, permissions, intents, default_enabled, api_version)


def discover(package: str = PACKAGE) -> dict[str, Manifest]:
    """Read every manifest in the extensions package without importing extension code."""
    try:
        root = importlib.import_module(package)
    except ImportError as exc:
        raise ExtensionError(f"Cannot import the '{package}' folder. Run RoundForge from the repository "
                             "root, or set ENABLED_EXTENSIONS=none for core only.") from exc
    found: dict[str, Manifest] = {}
    for directory in map(Path, getattr(root, "__path__", [])):
        for folder in sorted(directory.iterdir()):
            if folder.is_dir() and (folder / MANIFEST).is_file():
                manifest = read_manifest(folder, package)
                found.setdefault(manifest.name, manifest)
    return found


def resolve(available: dict[str, Manifest], requested: Sequence[str] | None) -> list[Manifest]:
    """Choose the enabled manifests and order them so dependencies load first.

    ``None`` selects every manifest with ``default_enabled = true``. Ties are broken alphabetically,
    so the load order never depends on the order names were written in the configuration.
    """
    if requested is None:
        names = [name for name, manifest in available.items() if manifest.default_enabled]
    else:
        names = list(dict.fromkeys(requested))
    unknown = [name for name in names if name not in available]
    if unknown:
        listing = ", ".join(sorted(available)) or "none found"
        raise ExtensionError(f"Unknown extension(s) in ENABLED_EXTENSIONS: {', '.join(unknown)}. "
                             f"Installed extensions: {listing}.")
    chosen = set(names)
    for name in sorted(chosen):
        for dependency in available[name].requires:
            if dependency not in available:
                raise ExtensionError(f"Extension '{name}' requires '{dependency}', which is not installed in extensions/.")
            if dependency not in chosen:
                raise ExtensionError(f"Extension '{name}' requires '{dependency}'. Add '{dependency}' to "
                                     "ENABLED_EXTENSIONS, or remove '" + name + "'.")
    order, done, remaining = [], set(), set(chosen)
    while remaining:
        ready = sorted(name for name in remaining if set(available[name].requires) <= done)
        if not ready:
            raise ExtensionError("Extension dependency cycle between: " + ", ".join(sorted(remaining)) + ".")
        order.append(ready[0])
        done.add(ready[0])
        remaining.remove(ready[0])
    return [available[name] for name in order]


@dataclass(frozen=True)
class LeaderboardMode:
    name: str
    help: str  # phrase for the /help line, e.g. "server ratings"
    description: str  # phrase for the command description, e.g. "Codeforces rating"
    fetch: Callable


class Registry:
    """Everything core and extensions registered, in a deterministic order."""

    def __init__(self, bot):
        self.bot = bot
        self.enabled: list[Manifest] = []
        self.apis: dict[str, object] = {}
        self.help = HelpBook()
        self._seq = itertools.count()
        self.commands: list = []
        self.admin_commands: list = []
        self.leaderboard_modes: list = []
        self.hooks: dict[str, list] = {}
        self.listeners: dict[str, list] = {}
        self.listener_tasks: set[asyncio.Task] = set()
        self.loops: list[tasks.Loop] = []
        self.phases: dict[str, list] = {phase: [] for phase in PHASES}
        self._finished_phases: set[str] = set()

    def next(self):
        return next(self._seq)

    # -- loading ---------------------------------------------------------
    def load(self, manifests: Iterable[Manifest]):
        for manifest in manifests:
            try:
                module = importlib.import_module(manifest.module)
            except Exception as exc:
                raise ExtensionError(f"Extension '{manifest.name}' failed to import: {exc}") from exc
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise ExtensionError(f"Extension '{manifest.name}' must define setup(ext) in its __init__.py.")
            context = ExtensionContext(self.bot, manifest, self)
            try:
                api = setup(context)
            except ExtensionError:
                raise
            except Exception as exc:
                raise ExtensionError(f"Extension '{manifest.name}' failed during setup: {exc}") from exc
            if inspect.isawaitable(api):
                raise ExtensionError(f"Extension '{manifest.name}': setup(ext) must be a regular function; "
                                     "use ext.on_startup for asynchronous work.")
            self.apis[manifest.name] = api
            self.enabled.append(manifest)

    def api(self, name: str):
        if name not in self.apis:
            raise ExtensionError(f"Extension '{name}' is not enabled.")
        return self.apis[name]

    def names(self) -> list[str]:
        return [manifest.name for manifest in self.enabled]

    # -- hooks -----------------------------------------------------------
    def hooks_for(self, name: str) -> list[Callable]:
        return [fn for *_, fn in sorted(self.hooks.get(name, []), key=lambda entry: entry[:2])]

    def call_hooks(self, name: str, *args, **kwargs) -> list:
        return [fn(*args, **kwargs) for fn in self.hooks_for(name)]

    async def run_hooks(self, name: str, *args, **kwargs) -> list:
        results = []
        for fn in self.hooks_for(name):
            value = fn(*args, **kwargs)
            results.append(await value if inspect.isawaitable(value) else value)
        return results

    # -- lifecycle -------------------------------------------------------
    async def run_phase(self, phase: str):
        """Run lifecycle callbacks; stopping/shutdown run once, in reverse load order."""
        reverse = phase in {"stopping", "shutdown"}
        if reverse and phase in self._finished_phases:
            return
        self._finished_phases.add(phase)
        entries = sorted(self.phases[phase], key=lambda entry: entry[0], reverse=reverse)
        for _, owner, fn in entries:
            if not reverse:
                await fn()
                continue
            try:
                await fn()
            except Exception:
                log.exception("Extension '%s' failed during %s", owner, phase)

    # -- startup information -----------------------------------------------
    def required_permissions(self) -> discord.Permissions:
        flags = {flag for manifest in self.enabled for flag in manifest.permissions}
        return discord.Permissions(**{flag: True for flag in flags})


class ExtensionContext:
    """The API an extension's ``setup(ext)`` uses to register itself with core."""

    def __init__(self, bot, manifest: Manifest, registry: Registry):
        self.bot = bot
        self.manifest = manifest
        self.name = manifest.name
        self.path = manifest.path
        self._registry = registry

    @property
    def db(self):
        """The shared SQLite connection. Use only this extension's tables (and read core tables)."""
        return self.bot.store.db

    @property
    def env(self):
        """Raw configuration (the process environment for a real run); read extension settings here."""
        return self.bot.settings.env

    def require(self, name: str):
        """Return the public API of an extension listed in this manifest's ``requires``."""
        if name not in self.manifest.requires:
            raise ExtensionError(f"Extension '{self.name}' uses '{name}' without listing it in requires.")
        return self._registry.api(name)

    def migrate(self, migrations: Sequence[schema.Migration]) -> int:
        """Create or upgrade this extension's tables; every name must start with '<name>_'."""
        try:
            return schema.migrate(self.bot.store.db, self.name, migrations)
        except schema.SchemaError as exc:
            raise ExtensionError(str(exc)) from exc
        except Exception as exc:
            raise ExtensionError(f"Extension '{self.name}' migration failed: {exc}") from exc

    def add_command(self, command, *, order: int = 1000):
        """Register a top-level slash command or group."""
        self._registry.commands.append((order, self._registry.next(), command))
        return command

    def add_admin_command(self, command, *, order: int = 1000):
        """Register a subcommand of core's /admin group (organizers with Manage Server)."""
        self._registry.admin_commands.append((order, self._registry.next(), command))
        return command

    def add_leaderboard_mode(self, name: str, *, help: str, description: str, fetch: Callable, order: int = 1000):
        """Add a /leaderboard mode. ``fetch(guild_id, page)`` returns ``(title, rows, score)``.

        ``help`` and ``description`` are phrases joined with "or" into /help and the command description.
        """
        mode = LeaderboardMode(name, help, description, fetch)
        self._registry.leaderboard_modes.append((order, self._registry.next(), mode))

    def add_loop(self, fn: Callable, *, seconds: float = 0, minutes: float = 0, hours: float = 0,
                 before: Callable | None = None) -> tasks.Loop:
        """Run ``fn`` periodically once the bot is ready; it starts after command sync.

        ``before`` runs once before the first iteration. Exceptions are logged and the loop continues.
        """
        if not inspect.iscoroutinefunction(fn):
            raise ExtensionError(f"Extension '{self.name}': loop callbacks must be async functions.")
        owner = self.name

        async def body():
            try:
                await fn()
            except Exception:
                log.exception("Extension '%s' loop %s failed; will retry", owner, getattr(fn, "__name__", fn))

        loop = tasks.loop(seconds=seconds, minutes=minutes, hours=hours)(body)

        @loop.before_loop
        async def wait_until_ready():
            await self.bot.wait_until_ready()
            if before is not None:
                await before()

        self._registry.loops.append(loop)
        return loop

    def listen(self, event: str, fn: Callable):
        """Receive a Discord gateway event, e.g. ``listen("voice_state_update", handler)``."""
        if event.startswith("on_"):
            event = event[3:]
        if not inspect.iscoroutinefunction(fn):
            raise ExtensionError(f"Extension '{self.name}': event listeners must be async functions.")
        self._registry.listeners.setdefault(event, []).append(fn)

    def hook(self, name: str, fn: Callable, *, order: int = 1000):
        """Attach to a named extension point (see extensions/README.md for the core hooks)."""
        self._registry.hooks.setdefault(name, []).append((order, self._registry.next(), fn))

    def help(self, text, *, block: str, order: int = 1000):
        """Add a line to /help; ``text`` may be a string or a function returning one."""
        self._registry.help.line(text, block=block, order=order)

    def help_item(self, text, *, line: str, order: int = 1000):
        """Add an item to a composite /help line, such as ``admin`` or ``notes``."""
        self._registry.help.item(text, line=line, order=order)

    def _phase(self, phase: str, fn: Callable):
        if not inspect.iscoroutinefunction(fn):
            raise ExtensionError(f"Extension '{self.name}': lifecycle callbacks must be async functions.")
        position = len(self._registry.enabled) if self.name != "core" else -1
        self._registry.phases[phase].append((position, self.name, fn))
        return fn

    def on_startup(self, fn):
        """Before commands are synced: validate configuration, restore persistent views."""
        return self._phase("startup", fn)

    def on_started(self, fn):
        """After commands are synced, before loops start: start servers, register extra entry points."""
        return self._phase("started", fn)

    def on_stopping(self, fn):
        """First step of shutdown, before loops are cancelled: stop accepting outside input."""
        return self._phase("stopping", fn)

    def on_shutdown(self, fn):
        """After loops are cancelled and before the database closes: finish and release work."""
        return self._phase("shutdown", fn)


def schedule_listeners(bot, registry: Registry, event: str, args, kwargs):
    """Run extension listeners like discord.py's own handlers: one task each, errors to on_error."""
    for fn in registry.listeners.get(event, ()):
        task = asyncio.create_task(bot._run_event(fn, "on_" + event, *args, **kwargs), name=f"roundforge: {event}")
        registry.listener_tasks.add(task)  # keep a reference until the listener finishes
        task.add_done_callback(registry.listener_tasks.discard)
