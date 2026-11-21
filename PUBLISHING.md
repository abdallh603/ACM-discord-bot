# Publish RoundForge yourself

Publishing the source and hosting the running Discord bot are separate steps.
The project is prepared locally; no remote repository is created by these instructions.

## Before uploading

1. Reset any Discord token previously shared in chat. Put the replacement only in your local `.env`.
2. Verify `.env`, `data/`, `.venv/`, logs, databases, and backups are not tracked. They are ignored.
3. Run the tests with your virtual-environment Python:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git status --short
git ls-files
git check-ignore .env
.\.venv\Scripts\python.exe scripts/check_release.py
```

The reviewed commit contains source, tests, documentation, and `.env.example` with an empty token.
Do not force-add ignored runtime files. Keep the MIT license and original TLE notices.

## Push to GitHub

1. Sign in to GitHub and create an empty public repository, for example `roundforge-bot`.
   Do not initialize another README/license/gitignore if pushing the prepared local commits.
2. Copy its HTTPS repository URL and use your normal GitHub authentication:

```sh
git remote add origin YOUR_REPOSITORY_URL
git push -u origin main
```

If `origin` already exists, inspect `git remote -v` and update the URL deliberately instead of
adding another remote. Do not put a token in the remote URL or a command that you share.
Review GitHub Actions after pushing; the test workflow runs automatically.

## Make the Discord bot inviteable

In Discord Developer Portal, set the application and bot name to **RoundForge**, enable **Public Bot**,
and configure Guild Install with `bot` and `applications.commands` scopes. Leave privileged intents off.
Use your application ID in an install URL, and grant only the features' necessary permissions:

- Basic commands: View Channels, Send Messages, Embed Links.
- Voice games: Connect, Move Members, Mute Members and/or Deafen Members as configured.
- Optional fallback rooms: Manage Channels.
- Optional rank roles: Manage Roles, with the bot's role placed above its created CF roles.

Leave `DISCORD_GUILD_ID` empty for global commands, then restart the bot. The owner must keep
one process/container running with persistent data for everyone else's server to use it.
Global command registration may take time to propagate. Use `LEGACY_GUILD_ID` once when
upgrading from old development-only commands that could shadow the new global commands.
