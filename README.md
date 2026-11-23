# RoundForge

A public Discord bot for Codeforces communities: verified handles, problem recommendations,
daily challenges, training tracks, upsolving goals, and multiplayer Blitz.

**[How to use the bot](USAGE.md)** · **[Blitz details](BLITZ.md)** ·
**[Tournaments and profile cards](TOURNAMENTS.md)** ·
**[Publish the source yourself](PUBLISHING.md)** · **[Data and privacy](PRIVACY.md)**

## Features

- Public/global slash commands, with independent accounts, scores, settings, and games per server.
- Codeforces account ownership checks and server rating/monthly daily-points leaderboards.
- Unsolved problems filtered by difficulty and topic, with native slash-command suggestions.
- Daily problems and contest reminders in each server's configured timezone.
- Beginner tracks, post-contest goals, and optional Codeforces rating roles.
- Blitz for 2–8 players, concurrent games, unlimited or timed rounds, and solo testing.
- Hosted Blitz: mention the players and optionally delegate a manager who does not have to play.
- Player picker, multi-topic/rating/voice menus, manual problems, and mute/deafen/both/off options.
- Accept/decline buttons, visible 10-second countdowns, and persistent game controls.
- Toggle everyone-finishes mode and live/final standings with tied ranks and solve times.
- Single-elimination tournaments for 2–32 players, signup buttons, byes, and automatic advancement.
- Tournament/champion artwork and Discord-username ~ Codeforces-handle avatar cards.
- Reuse existing text/voice channels; restore only voice flags the bot applied.
- SQLite persistence and recovery, Docker, and a credential-free automated test suite.

![Blitz tournament artwork](just_bot/assets/tournament-banner.png)

RoundForge is inspired by [PSUT TLE](https://github.com/PSUT-ACM-Student-Chapter/psut-discord-tle)
and the TLE project. It is a fresh implementation with a smaller feature set, not an official
university or Codeforces service. MIT licensed; upstream notices remain in [LICENSE](LICENSE).

## Run a public instance

Requirements: Python 3.11+ (3.12 recommended), outbound HTTPS, and a Discord bot application.

1. In the [Discord Developer Portal](https://discord.com/developers/applications), name your
   application/bot **RoundForge**, enable **Public Bot**, and enable **Guild Install**.
2. Keep privileged Members and Message Content intents off. The code enables ordinary Guilds
   and Voice States intents; it does not join or record voice audio.
3. Install using the `bot` and `applications.commands` scopes. Minimum permissions are
   View Channels, Send Messages, and Embed Links. Add Attach Files for tournament artwork and profile cards.
   Add Connect/Move Members/Mute Members/Deafen Members
   for the voice modes you enable, Manage Roles for rank roles, and Manage Channels only for fallback rooms.
4. Copy `.env.example` to `.env` and set `DISCORD_TOKEN` locally.
5. **Leave `DISCORD_GUILD_ID` empty for public mode.** A positive ID restricts the instance to one
   development server. Use `/admin channel` for per-server command channel restrictions.
6. Start one instance against persistent storage. Publishing this repository does not host the bot:
   the Python process/container must stay running for anyone to use it.

Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Fill in .env locally, then:
.\start.ps1
```

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Fill in .env locally, then:
.venv/bin/python -m just_bot
```

The `just_bot` Python module path is retained for compatibility. Installing the package also
provides the `roundforge` executable. The Windows launcher installs missing dependencies,
runs the tests, and starts the bot. Keep its window open; Ctrl+C stops it and attempts voice recovery.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | required | Bot token; never put it in Git or chat |
| `DISCORD_GUILD_ID` | empty | Public/global commands; set a positive ID only for development |
| `LEGACY_GUILD_ID` | empty | Clear stale server-only command copies when upgrading to public mode |
| `DATABASE_PATH` | `data/roundforge.sqlite3` | Persistent SQLite database |
| `LOG_LEVEL` | `INFO` | Console logging level |
| `BOT_CHANNEL_IDS` | empty | Legacy development-only allowlist, requires `DISCORD_GUILD_ID` |

Global commands may take time to appear in clients after registration. If migrating from a
server-only instance, set `LEGACY_GUILD_ID` to that old server ID for the first public startup.
It removes the old guild copies that would otherwise shadow the new global commands.

## Server setup

A server organizer with Manage Server can run:

```text
/admin configure reminders_channel:#contests daily_channel:#daily timezone:Asia/Amman daily_rating:1200 daily_hour:9
/admin setup_roles
/admin channel action:add channel:#bot-commands
```

New servers default to UTC. Existing pre-public instances retain their Jordan timezone.
Move the bot role above the `CF · …` roles it creates. It manages recorded role IDs only.
`/admin configure` replaces feed settings: omitted channels disable those feeds. Pass both
channels when changing other feed settings if you want to retain them.

## Docker

Create `.env`, then:

```sh
docker compose up -d --build
docker compose logs -f bot
```

The named `just-data` volume retains its historical name to preserve existing installs.
Keep it when rebuilding. `docker compose down -v` deletes that stored data.
The container runs as a non-root user. Use one container per database.

## Verify changes

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q just_bot tests
```

Run these with the Python interpreter in your virtual environment. Tests cover account ownership,
scoring, timezone boundaries, migrations, server isolation, manager permissions, menu callbacks,
room reuse, voice recovery, and API retries. They simulate Discord effects; they do not establish
a live login or prove actual server permissions. CI runs on Python 3.11, 3.12, and 3.13.

## Operations and limits

- Requests to Codeforces share one rate-limited client (at least 2.1 seconds between starts).
  A bounded queue returns a busy message rather than allowing unbounded waiting.
- Ratings refresh hourly, with two community refresh jobs at a time. Active-game clocks run
  independently from slow solve checks; submission timestamps determine eligibility.
- Submission history is checked up to 20,000 entries. An incomplete history produces a clear
  error instead of silently selecting an already-solved problem.
- This is a single-process public community bot, not a horizontally scalable hosted service.
  Large installations will need a shared database and distributed scheduling/rate limiting.
- Friendly practice scoring does not provide anti-cheat or a basis for prize eligibility.
- Delivery is recorded after Discord accepts a message. A crash between sending and recording
  can cause one duplicate reminder; normal restarts preserve recorded deliveries.
- Back up SQLite with its backup API or while the bot is stopped; copying only a live `.sqlite3`
  file can miss its WAL. Keep backups outside the container and out of Git.
- Schema v4 preserves existing accounts, games, mute/deafen recovery, and old server timezone
  settings. Keep `DATABASE_PATH` pointed at the existing database when upgrading.
- Do not use a PSUT/TLE database directly; this project has its own schema.
- Before removing the bot from a server, cancel its games so it can restore owned voice changes.
  It cannot act while offline or after losing server access.

See [USAGE.md](USAGE.md) for the complete member/manager guide and [REVIEW.md](REVIEW.md) for
the release review and validation record.
