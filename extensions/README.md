# RoundForge extensions

RoundForge is a small core (`just_bot/core/`) plus optional extensions in this folder. The core
handles startup, configuration, storage of linked members and server settings, the Codeforces
client, handle verification, `/problem`, `/leaderboard`, `/admin` and `/help`. Everything else
is an extension.

| Extension | Requires | Adds | Tables |
| --- | --- | --- | --- |
| `daily` | — | `/daily`, `/contests`, `/admin configure`, daily posts, contest reminders, `/leaderboard mode:daily` | `daily_*` |
| `rank` | — | `/rank sync`, `/admin setup_roles`, role sync during the hourly refresh | `rank_roles` |
| `practice` | — | `/training`, `/upsolve` | `practice_goals` |
| `profile_cards` | — | the avatar card on `/handle show` | — |
| `blitz` | — | `/blitz` games, menus, panels, voice effects | `blitz_*` |
| `tournament` | `blitz` | `/tournament`, signup panels, bracket images | `tournament_*` |
| `activity` | `tournament`, `blitz` | `/activity`, the voice Activity web app | `activity_*` |
| `example` | — | `/hello` — a minimal template, **disabled by default** | `example_greetings` |

## Enabling extensions

Set `ENABLED_EXTENSIONS` in `.env`:

| Value | Result |
| --- | --- |
| unset or empty | every extension whose manifest says `default_enabled = true` (all bundled ones except `example`) |
| `none` | core only |
| `daily,rank,blitz` | exactly the listed extensions |

Dependencies are never enabled implicitly. If you list `tournament` without `blitz`, the bot stops
at startup with `Extension 'tournament' requires 'blitz'. Add 'blitz' to ENABLED_EXTENSIONS, or remove 'tournament'.`
Unknown names, dependency cycles and invalid manifests are also reported before the bot connects.
Disabled extensions are not imported, and their tables are left untouched.

**Installing a third-party extension:** copy its folder into `extensions/`, add its name to
`ENABLED_EXTENSIONS` (listing the bundled ones you want as well), and restart. With
`ENABLED_EXTENSIONS` unset, only extensions marked `default_enabled = true` load, so third-party
extensions should ship with `default_enabled = false`. Extension code runs inside the bot process
with access to its database and token: install only code you trust.

## Anatomy of an extension

```
extensions/my_feature/
├── extension.toml      # manifest (required)
├── __init__.py         # defines setup(ext) (required)
├── store.py, ...       # any modules you like; import them relatively
└── tests/
    ├── __init__.py
    └── test_my_feature.py
```

The [`example`](example/) extension is a complete, minimal template: a command, a table with a
migration, a loop, a listener and a `/help` line. Copy it to start.

### Manifest (`extension.toml`)

```toml
name = "my_feature"            # must equal the folder name: lowercase letters, digits, underscores
version = "1.0.0"
description = "What it adds, in one line"
requires = ["blitz"]           # other extensions this one uses (optional)
permissions = ["send_messages", "embed_links"]  # discord.Permissions flags it needs (optional)
intents = ["voice_states"]     # extra gateway intents (optional; privileged intents are rejected)
default_enabled = false        # load when ENABLED_EXTENSIONS is unset (optional, default false)
api_version = 1                # core extension API version (optional, default 1)
```

Unknown fields are errors, so a typo like `dependencies = [...]` is reported instead of ignored.
At startup the bot logs the enabled extensions and the union of their `permissions`, including the
permissions integer for an invite URL.

### `setup(ext)`

`setup` is a regular (non-async) function, called once while the bot is constructed. Whatever it
returns becomes the extension's public API for extensions that list it in `requires`.

```python
def setup(ext):
    ext.migrate(MIGRATIONS)
    store = MyStore(ext.db)
    ext.add_command(MyCommands(store))
    ext.help("`/mine` — what it does", block="commands", order=90)
    return store
```

| Member | Purpose |
| --- | --- |
| `ext.name`, `ext.manifest`, `ext.path` | identity, parsed manifest, folder (for assets) |
| `ext.bot` | the bot: `settings`, `store` (core store), `cf` (Codeforces client), `community` (linking/recommendations), `guild_enabled()`, `active_guilds()`, `get_guild()`, `add_view()`, `wait_until_ready()` |
| `ext.db` | the shared SQLite connection (use your own tables; you may read `core_*` tables) |
| `ext.env` | raw configuration (the environment when started normally); parse your own settings from it |
| `ext.require(name)` | the API returned by a dependency's `setup`; the name must be in `requires` |
| `ext.migrate(migrations)` | create/upgrade your tables (see [Database](#database)) |
| `ext.add_command(cmd, order=)` | a top-level slash command or group |
| `ext.add_admin_command(cmd, order=)` | a subcommand of core's `/admin` (hidden from members without Manage Server; also add `@app_commands.checks.has_permissions(manage_guild=True)`) |
| `ext.add_leaderboard_mode(name, help=, description=, fetch=, order=)` | a `/leaderboard mode` choice; `fetch(guild_id, page)` returns `(title, rows, score)` |
| `ext.add_loop(fn, seconds=/minutes=/hours=, before=)` | a background loop; starts after commands sync and the bot is ready; `before` runs once first; errors are logged and the loop continues |
| `ext.listen(event, fn)` | a Discord gateway event, e.g. `"voice_state_update"`, `"guild_join"` |
| `ext.hook(name, fn, order=)` | a core extension point (below) |
| `ext.help(text, block=, order=)` | a `/help` line; `text` may be a function returning the text |
| `ext.help_item(text, line=, order=)` | an item of a composite `/help` line |
| `ext.on_startup(fn)` | before commands sync: validate configuration, re-register persistent views |
| `ext.on_started(fn)` | after commands sync: start servers, register extra entry points |
| `ext.on_stopping(fn)` | first step of shutdown, before loops stop: stop accepting outside input |
| `ext.on_shutdown(fn)` | after loops stop, before the database closes: finish or release work |

Loops, listeners and lifecycle callbacks must be `async` functions. `startup` and `started` run in
load order. `stopping` and `shutdown` run in reverse, so dependents stop before what they depend
on. Load order puts dependencies first, then breaks ties alphabetically.

### Core hooks

| Hook | Called | Your function | Bundled use |
| --- | --- | --- | --- |
| `channel_exemption` | when `/admin channel` restrictions would refuse a command | `(interaction) -> bool`, **a plain function**; `True` allows it | blitz: players in their game's channel |
| `before_unlink` | at the start of `/handle unlink` | `(guild_id, user_id)`; raise `UserError` to refuse | blitz: active game or voice recovery |
| `after_unlink` | after the link is removed | `(member) -> str \| None`; a sentence appended to the reply | daily, rank |
| `link_verified` | after `/handle verify` succeeds | `(guild_id, user_id, profile) -> str \| None`; appended | rank |
| `profile_image` | while building `/handle show` | `(interaction, member, profile) -> discord.File \| None`; first file wins | profile_cards |
| `member_refreshed` | for each linked member during the hourly or `/admin refresh` rating refresh | `(member, linked_row)`; raising `discord.HTTPException`/`UserError` counts as an error in the status | rank |
| `admin_status` | building `/admin status` | `(guild_id) -> str`; one line, placed by `order` | daily, rank |

Hook functions may be plain or `async`, except `channel_exemption`, which must be plain because
it runs inside Discord's interaction check. Lower `order` values run (and appear) first. Core's own
status lines use orders 10 ("Verified handles") and 60 ("Last refresh").

### Ordering and `/help`

Order keys reproduce the original bot's layout exactly, so pick values that slot in where you want:

- **Top-level commands:** handle 10, daily 20, rank 30, admin 40, problem 50, contests 60,
  leaderboard 70, help 80, training 90, upsolve 100, blitz 110, tournament 120, activity 130.
  The default is 1000.
- **`/admin` subcommands:** configure 10, setup_roles 20, refresh 30, status 40, channel 50.
- **`/help` blocks**, separated by blank lines: `intro` (0), `commands` (10), `games` (20),
  `events` (30), `voice` (40), `organizers` (100). A new block name is created automatically and
  sorts before `organizers`.
- **Composite `/help` lines** in the `organizers` block: `admin` ("Organizers: `/admin …`, …."),
  and `notes` (sentences joined with spaces). Add to them with `ext.help_item`.

### Database

All extensions share one SQLite database. Every table and index you create must be named
`<extension name>_...`. Core's tables are `core_*`. Never create, alter or drop another owner's
objects. Reading `core_*` tables is allowed (e.g. joining `core_members`).

Schema changes are an append-only list of migrations; entry *i* upgrades version *i* to *i + 1*:

```python
MIGRATIONS = [
    """CREATE TABLE my_feature_items (guild_id INTEGER NOT NULL, item TEXT NOT NULL);""",
    """ALTER TABLE my_feature_items ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0;""",
]
```

`ext.migrate` records each extension's version in `core_schema`. It runs only the entries that
have not been applied yet, each in one transaction together with its version bump, and rolls
back on failure. A migration that touches unprefixed or foreign objects is rolled back with an
error. A database newer than the code is refused. An entry may also be a function
`migrate(connection)` for data changes; it runs inside the transaction and must not commit.
**Never edit a migration that has been released; add a new one.**

### Depending on another extension

List the dependency in `requires`, then:

- call `ext.require("blitz")` for the object its `setup` returned, and
- import its modules directly where convenient (`from extensions.blitz.options import voice_mode`).

When the lower layer must call into yours, use the registration API it offers instead of having
it know about you. The bundled examples:

- blitz `add_reservation_check(fn)` runs `fn(guild, user, handle, options)` inside game creation.
- blitz `on_tick(fn)` runs `fn(guild_id)` after each 5-second clock pass.
- tournament `add_panel_button(action, label, handler)` adds a persistent button to every panel.

### Rules (checked by `tests/test_isolation.py`)

1. Core never imports or names an extension.
2. Extension code imports core only through `just_bot.core.api`. Tests may import anything.
3. An extension imports only itself and the extensions in its `requires`.

The manifest validation and migration runner enforce the naming, intent and dependency rules at
startup.

## Testing an extension

Put tests in `extensions/<name>/tests/` (with an `__init__.py`). From the repository root,
`python -m unittest discover -v` runs the core and every extension's tests. To exercise only your
extension inside a real bot (no Discord connection is made):

```python
from pathlib import Path
from just_bot.core.bot import RoundForgeBot
from just_bot.core.config import Settings

bot = RoundForgeBot(Settings("token", None, Path(":memory:"), frozenset(), "INFO",
                             extensions=("my_feature",), env={"MY_SETTING": "1"}))
api = bot.extensions.api("my_feature")
command = bot.tree.get_command("mine")
```

For unit tests of a store, create its tables with `just_bot.core.schema.migrate(db, "my_feature",
MIGRATIONS)` on a `just_bot.core.storage.Store(":memory:")`. Shared fixtures live in
`tests/support.py` (`problem()`, `submission()`, `avatar()`), `extensions/blitz/tests/support.py`
(`FakeVoice`, `blitz_store()`) and `extensions/tournament/tests/test_tournament.py`.
