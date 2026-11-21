# Contributing

- Keep Discord, Codeforces, and storage logic separated where practical.
- Scope server records, authorization, timers, settings, and background jobs by guild ID.
- Game managers may control only their games. Every player must personally accept the current
  revision before play; changing pending rules invalidates prior acceptance.
- Track ownership before applying voice effects and restore only effects the bot owns.
  Preserve recovery records on failures, disconnects, and restarts. Never delete reused channels.
- Never commit secrets or runtime/user data. Use an empty `.env.example` for configuration examples.
- Respect the Codeforces API interval, bound retries/queueing/history, and keep game clocks
  independent from slow API queries.
- Add meaningful tests for behavior or regression risk; avoid tests that merely repeat implementation.
  Install requirements and run `python -m unittest discover -s tests -v` and
  `python -m compileall -q just_bot tests` before a pull request.
- Update USAGE.md when changing commands, permissions, consent, defaults, or cleanup behavior.
- Keep the MIT license and upstream notices. Do not claim official Discord, university, or Codeforces affiliation.
