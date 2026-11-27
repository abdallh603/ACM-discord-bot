# Contributing

## Where code goes

- **Core** (`just_bot/core/`) is what every instance needs: startup, configuration, storage of
  members/server settings/channels, the Codeforces client, linking and verification, `/handle`,
  `/problem`, `/leaderboard`, `/admin channel`/`status`/`refresh`, and `/help`. Keep it small.
- **Features** belong in an extension under `extensions/<name>/`; see
  [extensions/README.md](extensions/README.md). A new feature is a new extension (or part of an
  existing one), not a change to core.
- Core never imports or names an extension. When core must let features take part in something,
  add a documented hook or registration method to `ExtensionContext`, not a feature check.
- Extensions import core only through `just_bot.core.api`, and import other extensions only if
  they list them in `requires`. When a lower layer must call into a higher one, give the lower
  layer a registration API (as blitz's `add_reservation_check` and `on_tick` do).
- Each owner's tables are named `<owner>_...` and change only through its append-only migration
  list. Never edit a released migration; add a new one.
- `tests/test_isolation.py` enforces these import rules, and `tests/test_bundle.py` pins the
  default bundle's command payload and `/help` text. If you intend a user-visible change, update
  `tests/fixtures/` in the same commit and say so in the pull request.

## Behavior and safety

- Keep Discord, Codeforces, and storage logic separated where practical.
- Scope server records, authorization, timers, settings, and background jobs by guild ID.
- Game managers may control only their games. Every player must personally accept the current
  revision before play; changing pending rules invalidates prior acceptance.
- Track ownership before applying voice effects and restore only effects the bot owns.
  Preserve recovery records on failures, disconnects, and restarts. Never delete reused channels.
- Never commit secrets or runtime/user data. Use an empty `.env.example` for configuration examples.
- Respect the Codeforces API interval, bound retries/queueing/history, and keep game clocks
  independent from slow API queries.

## Tests

- Add meaningful tests for behavior or regression risk; avoid tests that merely repeat implementation.
  Put an extension's tests in `extensions/<name>/tests/`, and core tests in `tests/`.
- Install requirements, then from the repository root run `python -m unittest discover -v`,
  `node --test extensions/activity/tests/activity_model.test.mjs`, and
  `python -m compileall -q just_bot extensions tests` before a pull request.
- Update USAGE.md when changing commands, permissions, consent, defaults, or cleanup behavior.
- Keep the MIT license and upstream notices. Do not claim official Discord, university, or Codeforces affiliation.
