# Multiplayer blitz, training, and upsolving

For the complete public-bot guide and manager commands, see [USAGE.md](USAGE.md).

## Hosted games and delegated managers

`/blitz host players:@Alice @Bob manager:@Carol` makes Alice and Bob the players while
the command author and Carol manage the game. Neither manager is automatically added to
the roster, moved, muted, or deafened. Players must accept themselves. Hosts/managers do
not need linked CF accounts unless they are also listed as players.

The equivalent dropdown flow is `/blitz menu hosted:true players:@Alice @Bob manager:@Carol`.
Normal `/blitz create` can also delegate a manager. Managers can host multiple games, inspect
their games, edit pending rules, request checks, change active timers, and cancel. This grants
no server-wide permissions and does not authorize accepting for a player.

## Topic, rating, and voice menus

Run `/blitz menu players:@Alice @Bob` to open a private setup panel. Use the **Topic**,
**Rating**, and **Voice** dropdowns, then click **Create lobby**. The challenger is included
automatically. Everyone else still accepts the displayed lobby revision before voice effects begin.

The Topic menu includes **Any topic**, **Random topic**, and named Codeforces topics.
The Rating menu includes **Automatic** and every 100-point step from 800 through 3500.
Use **More topics** or **More ratings** for the next page; your selection stays in the preview.
Voice choices are **No voice effects**, **Mute**, **Deafen**, and **Mute and deafen**.

Omit `minutes` for **unlimited time**, or pass it explicitly for a timed game:

```text
/blitz menu players:@Alice @Bob
/blitz menu players:@Alice @Bob minutes:25
/blitz menu
```

The last example is an organizer solo test and has a **Start solo test** button.
Only the person who opened a menu can change it. It expires after 10 minutes without
reserving players or changing voice states. A manual problem disables topic/rating selectors.
Existing room reuse works the same way as direct commands.

## Test alone without extra accounts

An organizer with **Manage Server** can use `/blitz solo`. It starts a clearly marked one-player
test through the same problem selection, room creation, voice control, timer, and solve-checking
code used by multiplayer games. No extra Discord or Codeforces account is needed.

1. Start/restart the bot so the new command registers, and verify your own CF handle.
2. Join a normal Discord voice channel. Leave your personal microphone mute off if you want
   to distinguish it from the bot's server mute.
3. Run `/blitz solo minutes:1 rating:800`.
4. Confirm that the bot posts in your current text channel and server-mutes you in your existing
   voice channel. It does not create another room when suitable existing rooms are available.
   Let the minute expire to test timeout release, or run `/blitz cancel` to test cancellation.
5. For solve detection, start another solo game with a longer time, such as
   `/blitz solo minutes:10 rating:800`, and submit an accepted practice solution after the countdown.
   The problem must still be unsolved on your CF account, just like a normal blitz game.

You can disable voice effects with `voice_mode:none move_voice:false`, or supply a manual problem
instead of a rating. Solo tests use real voice changes and cleanup, including persistent recovery.
They verify your own account's live flow; the automated test suite simulates larger rosters and
concurrent games. Real interactions between multiple people still require other participants.

## Multiplayer blitz

Blitz supports **2–8 verified players per game** and **multiple concurrent games** in the server.
Every game has its own ID, roster, problem, settings, timer, and result. It reuses the text channel
where you start the game. For voice it prefers an explicitly selected `voice_channel`, then the
challenger's current room, another player's current room, or another accessible existing room.
It creates a private voice room only if no suitable existing voice room is available. Players must
all be able to view the chosen text channel and access the chosen voice room. Existing channels
keep their names and permissions; messages are visible to whoever already has access to that channel.
Players can be in one pending/running game at a time. A player awaiting voice recovery stays
reserved until that recovery succeeds, so another game cannot take over their mute state.

### Create and accept

In Discord, select `/blitz create` and put actual member mentions in the `players` field.
Separate mentions with spaces. The challenger is included automatically; do not mention yourself.

```text
/blitz create players:@Alice @Bob @Charlie minutes:20 rating:1200 topic:dp
```

This is a four-player game: you, Alice, Bob, and Charlie. All must have verified CF handles.
The lobby response shows its game ID and settings revision. Each invited player runs:

```text
/blitz accept match_id:GAME_ID revision:1
```

Nothing is moved or muted before every player has accepted the current revision. Invites expire
after 10 minutes. The last acceptance triggers selection, room reuse (or creation if needed), voice setup,
and a five-second countdown. An invited player may decline with `/blitz cancel`.

### Configure problems and rules

| Setting | Behavior |
| --- | --- |
| `minutes` | Optional 1–180 minutes; omitted means unlimited time, including solo tests |
| `rating` | Exact rating, e.g. 1200 |
| `rating_min`, `rating_max` | Inclusive range; use these instead of `rating` |
| no rating filters | Exact difficulty near the lowest-rated player; unrated defaults to 800 |
| `topic` | Selected CF topic, e.g. `greedy`, `dp`, or `binary search` |
| `random_topic:true` | Pick a random available topic, then a matching problem |
| neither topic filter | Random problem across any topic matching the rating |
| `manual_problem` | ID such as `4A`, `1700/C`, or a public CF problem URL |
| `end_rule:first_solve` | Earliest accepted solve wins; release every player's voice |
| `end_rule:all_solve` | Release each solver's voice, let others continue until all solve or time expires |
| `mute_voice` | Server-mute connected players during play; default true |
| `voice_mode` | `none`, `mute`, `deafen`, or `both`; overrides `mute_voice` when supplied |
| `move_voice` | Bring connected players into the selected existing voice room; default true |
| `voice_channel` | Optional existing voice room to use instead of automatic selection |

All rating filters use multiples of 100. A single minimum/maximum means that exact rating.
A manual problem cannot be combined with rating/topic filters. Every selected problem must be
from a finished regular public contest and unsolved by **every** player. Gym, special, team,
live contest, and virtual submissions are not supported as blitz solves in this version.

More examples:

```text
/blitz create players:@Alice @Bob minutes:30 rating_min:1000 rating_max:1400 random_topic:true
/blitz create players:@Alice @Bob @Charlie manual_problem:4A minutes:10 end_rule:all_solve
/blitz create players:@Dana @Eli minutes:15 rating:1000 mute_voice:false move_voice:false
/blitz solo minutes:1 rating:800 voice_channel:General
/blitz create players:@Alice @Bob rating:1200 voice_mode:both
/blitz solo rating:800 voice_mode:deafen
```

The third example can run at the same time as another game with a different roster.
Use `/blitz games` to list active lobbies and games. Gameplay commands work in the game's text
channel even if `BOT_CHANNEL_IDS` limits normal commands to other channels.

### Edit settings and timers

The host or delegated manager can edit a pending lobby:

```text
/blitz edit match_id:GAME_ID minutes:35 rating:1300
/blitz edit match_id:GAME_ID clear_filters:true manual_problem:4A
/blitz edit match_id:GAME_ID clear_problem:true rating:1200 topic:greedy
/blitz edit match_id:GAME_ID unlimited:true voice_mode:both
```

Each edit increments the revision and resets the other players' acceptance. Review the new
settings with `/blitz status match_id:GAME_ID` and accept the displayed revision. Old revisions
are rejected. Changes do not silently alter what someone previously accepted.

Once running, the game's host/manager or an organizer with **Manage Server** can change the total duration from the start:

```text
/blitz time match_id:GAME_ID minutes:45
/blitz time match_id:GAME_ID
```

This sets a 45-minute total match, not an additional 45 minutes. Shortening past the current
elapsed time releases voices immediately and begins final submission verification.
The second command removes the time limit. For a pending lobby, `unlimited:true` clears its
duration; omitting minutes from an edit preserves the existing setting. Unlimited matches keep
checking submissions and end on their configured solve condition or `/blitz cancel`.
Changing the problem, roster, or other rules during play requires cancelling and creating a new game.

### Results and voice recovery

- Submit individually in **Codeforces practice** after the countdown and before the deadline, if set.
  The bot checks every participant and compares Codeforces submission timestamps, rather than
  the order in which API responses arrive. Equal earliest timestamps produce a draw.
- Checks run automatically. `/blitz check` requests another check; `/blitz status` shows the result.
- In `all_solve` mode, each solver is unmuted and returned to their original voice channel when
  possible. Everyone keeps their game slot until the game ends, preventing overlapping games.
- Voices are released on a winning solve, all-solve progress, timeout, cancellation, or failed setup.
  Timeouts release voices before waiting for Codeforces; judging may continue for up to two minutes.
  If verification cannot complete, the result is **unresolved**, rather than a fabricated winner.
- Only server mute/deafen flags applied by this bot are cleared. Each is tracked separately,
  so pre-existing server mutes/deafens and personal microphone/headphone settings are preserved.
  Discord exposes one server flag for each effect; cancel the game before applying separate
  moderation mutes/deafens during play.
- A disconnected player's mute may not be editable. The recovery record persists and the bot retries
  on voice rejoin and on subsequent clock ticks. `/blitz cancel` or organizer `/blitz recover` also retries.
  Restore missing permissions if recovery failed. This does not clear unrelated moderation mutes.
- The bot returns a moved player only if they are still in the match voice room. It does not drag
  someone back from a different channel. If the original channel was deleted, the player may leave
  manually. Existing text and voice channels are never deleted. If the bot created a fallback
  voice room, it removes it once empty. A game-created fallback text room is kept for 24 hours.
- Clean shutdown cancels matches and attempts voice restoration. After a crash, SQLite restores
  active games with their original deadlines and cancels interrupted setup. Pending mute recovery
  survives restarts. No process can perform Discord actions while it is offline.

### Discord permissions

Enable the regular **Voice States** gateway intent (the code does this). No privileged Members or
Message Content intent is needed, and the bot does not join or record voice audio.

For blitz, grant **View Channels**, **Send Messages**, **Embed Links**, and **Connect**.
**Manage Channels** is needed only if the bot must create a fallback room. For the default voice
behavior, also grant **Mute Members** and **Move Members** in
players' source voice channels and the game room. **Deafen Members** is additionally required
for `deafen` or `both` mode. Restore these permissions before retrying a failed
cleanup. Reused channels retain their existing visibility; server administrators can also see any
private fallback channels through their normal admin privileges.

### Practical limits

Codeforces requests share one rate-limited client. Large/many games can delay solve detection;
their deadlines and voice cleanup run independently from those API checks. Submission timestamps
still determine eligibility. This first version is for friendly practice and does not provide
anti-cheat, prizes, seasonal ratings, or a public match-history dashboard.

## Beginner training tracks

```text
/training start track:fundamentals
/training status
/training next
```

Tracks contain three stages with three unsolved problems each, selected from Codeforces:

| Track | Topic | Stage ratings |
| --- | --- | --- |
| `fundamentals` | implementation | 800 → 900 → 1000 |
| `greedy` | greedy | 900 → 1100 → 1300 |
| `binary_search` | binary search | 1000 → 1200 → 1400 |
| `dp` | dynamic programming | 1000 → 1200 → 1400 |

Each stage includes a short learning prompt. Solve all three assigned problems in individual
practice after assignment, then run `/training next` to advance. Stage three marks the track
complete. `/training stop` discards the current track so you can start another. Assignments persist
through restarts, and changing CF accounts cannot transfer another account's progress.

## Post-contest upsolving

```text
/upsolve start contest_id:1700 hours:48 problems:3
/upsolve check
```

The contest must have finished. The bot picks your next unsolved supported problems, easiest
first, and stores a deadline. Select 1–5 problems and 1–168 hours; defaults are three problems
and 48 hours. `/upsolve check` counts accepted individual practice submissions made during the
goal window. The contest page is linked for its published editorial/tutorial links; this bot
does not scrape or generate editorials. `/upsolve stop` clears the goal.

Training and upsolving can run alongside blitz, with separate saved progress and no extra muting.
