# How to use RoundForge

RoundForge works in Discord servers. Each server has its own linked accounts, settings,
leaderboards, and Blitz games. Use `/help` for a quick list of commands.

## 1. Link your Codeforces account

```text
/handle link handle:your_handle
```

The bot privately gives you a verification code. In [Codeforces profile settings](https://codeforces.com/settings/social),
temporarily set **First name** to that exact code, save, then run `/handle verify` within ten minutes.
Restore your original first name after verification. Your password/API key is never requested.
This verifies account ownership, not university enrollment or real-world identity.

- `/handle show` shows your linked profile and an avatar card:
  **Discord avatar · Discord username ~ CF handle · CF avatar**. Add `member` to select someone else.
  Missing avatars use initials; without Attach Files permission the ordinary profile embed still works.
- `/rank sync` refreshes your rating and optional rank role.
- `/handle unlink` removes the link and owned CF rank roles. Leave/cancel your tournament, cancel your Blitz games, and
  complete voice recovery first. Historical game/claim records remain as described in [PRIVACY.md](PRIVACY.md).

## 2. Find problems and see rankings

```text
/problem rating:1200 tag:dp
/leaderboard mode:rating
/leaderboard mode:daily
/contests
```

Topic and rating fields provide native Discord suggestions as you type. All ratings use multiples
of 100 from 800 to 3500. Type the leading digits to find high ratings that are beyond the first
suggestion page. `/blitz menu` offers paged dropdowns for every Blitz rating/topic.

Problem recommendations exclude your accepted solves. Without a rating, difficulty is near your
current CF rating, or 800 for an unrated account. Leaderboards show 15 users per page.
Contest times include Discord-local timestamps and the server's configured timezone.

## 3. Daily challenges

```text
/daily show
/daily claim
```

Every server gets a shared daily problem. Verify your account first, then submit an accepted solo
**PRACTICE** solution during that local calendar day and claim before its deadline for 10 points.
One reward per member/handle/day. Team, virtual, and live-contest submissions do not count.
Previously solved problems may be solved again with a new practice submission: this rewards
participation. Monthly daily-points standings use the server's timezone.

## 4. Beginner training and upsolving

```text
/training start track:fundamentals
/training status
/training next
```

Tracks: fundamentals (800/900/1000), greedy (900/1100/1300), binary search (1000/1200/1400),
and DP (1000/1200/1400). Each stage assigns three unsolved problems and a short learning prompt.
Solve all three in solo practice after assignment, then use `/training next`. `/training stop` resets the track.

```text
/upsolve start contest_id:1700 hours:48 problems:3
/upsolve check
```

Choose a finished contest, 1–5 next unsolved problems, and a deadline of 1–168 hours.
Defaults are three problems and 48 hours. Accepted solo practice submissions within the goal window
count toward progress. `/upsolve stop` clears the goal. Published editorial links are on the linked
contest page; the bot does not generate or scrape editorials.

## 5. Play Blitz yourself

```text
/blitz menu
```

Pick 1–7 opponents from the player dropdown; you are included automatically. You can also pass
`players:@Alice @Bob` to skip the picker. Select topics, rating, and voice mode, then choose
**Create lobby**. Use **More topics**/**More ratings** to see the next page.

The topic dropdown supports multiple selections, including across pages. A problem must match
**at least one** selected topic. Select **Any topic** or **Random topic** on its own to reset the
selection. The preview always shows all chosen topics. Manual problems disable these filters.

Use **Set time limit** for 1–180 minutes; leave the field blank for unlimited time.
**Everyone finishes: ON** keeps the game running until all players solve or time runs out.
Each solver is released from the bot's voice effects immediately. **OFF** ends the game on the
first verified winning solve. **Standings: ON/OFF** controls the live and final standings message
and standings button. Choose these settings before creating the lobby.

Direct-command equivalent:

```text
/blitz create players:@Alice @Bob rating:1200 topic:dp voice_mode:both
```

The public invitation has **Accept & ready**, **Decline**, and **Status** buttons. Read the settings
and click **Accept & ready**; the readiness count updates for everyone. The creator of a normal
game is already ready; all players in a hosted game must accept. Managers cannot accept for players.
The command alternative still works:

```text
/blitz accept match_id:GAME_ID revision:1
```

Invites expire after ten minutes. After everyone accepts, the bot selects an eligible problem,
reuses suitable existing rooms, and posts a visible **10 → 5 → 3 → 2 → 1 → GO** countdown.
The problem stays hidden and voice effects are deferred until the countdown completes.
Use **Cancel blitz** during the countdown to stop. At GO, the same message reveals the problem
and offers **Check solves**, **Standings** (when enabled), **Cancel blitz**, and **Status** buttons.
Pending invitation and active game buttons survive bot restarts. Interrupted countdowns are
cancelled safely rather than revealing a problem without warning.

## 6. Manage a game for other people

You can host without playing or linking your own CF account:

```text
/blitz host players:@Alice @Bob rating:1400 topic:greedy
```

Only Alice and Bob play. You are the host/manager and are not given a player slot, moved, muted,
or deafened. Both players must have verified handles and must accept themselves.

To delegate another manager:

```text
/blitz host players:@Alice @Bob manager:@Carol voice_mode:both
/blitz menu hosted:true players:@Alice @Bob manager:@Carol
```

The host and Carol can inspect the game, edit a pending lobby, request solve checks, change
the active timer, and cancel. Neither can accept on a player's behalf. These rights apply only
to this game, not the server. A normal `/blitz create` also accepts an optional `manager`.
One manager can host several games with different rosters simultaneously.

Use actual Discord mentions in the players field, separated by spaces. In a normal game do
not mention yourself; in a hosted game list exactly the 2–8 people who will play.

## 7. Blitz options

| Option | Meaning |
| --- | --- |
| `minutes` | Optional 1–180; omit for unlimited time |
| `rating` | One exact difficulty |
| `rating_min`, `rating_max` | Inclusive range; use instead of exact rating |
| no rating | Near the lowest-rated player's rating |
| `topic` | Choose a topic from suggestions |
| `random_topic:true` | Random topic, then a matching problem |
| no topic | Any matching topic |
| `manual_problem` | ID such as `4A`, `1700/C`, or a public CF problem URL |
| `end_rule:first_solve` | Earliest accepted solve wins; release all players' voices |
| `end_rule:all_solve` | Release each solver; others keep playing until all solve or time expires |
| `show_standings:true/false` | Enable/disable standings when creating or editing an invitation |
| `voice_mode` | `none`, `mute`, `deafen`, or `both` |
| `voice_channel` | Optional existing voice channel to use |
| `move_voice` | Bring already-connected players into the selected room; default true |
| `manager` | Optional game manager selected from server members |

Manual problems cannot be combined with rating/topic filters. Problems must be from finished
regular public contests and unsolved by every player. Native suggestions and dropdowns supplement
the direct command options; uncommon valid CF topics can still be typed in a direct command.

Blitz reuses the text channel where you create it. It prefers the selected voice channel or a
player's current channel, then another suitable existing channel. It creates a private fallback
only when necessary. Existing room names, permissions, and rooms are never deleted or changed.
Game messages are visible to whoever already has access to the chosen text channel; hosts,
managers, and players need access to that channel.

## 8. During the game

```text
/blitz status match_id:GAME_ID
/blitz games
/blitz check match_id:GAME_ID
/blitz cancel match_id:GAME_ID
```

The bot checks accepted solo CF practice submissions after the countdown and before the deadline,
if any. It confirms candidate results against another fresh set of player histories before finalizing
the winner, then compares submission timestamps; equal earliest timestamps mean a draw.
Every player may cancel their own game at any time. A player can have one pending/running game
at a time to avoid conflicting voice effects; managers who are not players are not reserved.

Managers can edit pending rules, resetting player acceptance:

```text
/blitz edit match_id:GAME_ID rating:1500 minutes:30
/blitz edit match_id:GAME_ID unlimited:true voice_mode:deafen
```

For active games, only game managers or server organizers can change the timer:

```text
/blitz time match_id:GAME_ID minutes:45
/blitz time match_id:GAME_ID
```

The first sets 45 minutes **total from the original start**. The second removes the time limit.
Changing active rules/problem/roster requires cancelling and creating a new game.

Voice changes are restored on solve/end/cancel/failure and attempted during clean shutdown.
Mute and deafen are tracked separately; pre-existing server flags and personal voice settings
remain yours. A disconnected player's server flags may not be editable until rejoining voice;
the recovery record remains and is retried. Organizers can use `/blitz recover` after fixing permissions.
Discord exposes only one flag per server effect: cancel a game before applying independent
moderation mute/deafen actions during it.

If Codeforces is unavailable, detection may be delayed; clocks release owned voice effects at a
timed deadline independently of solve checks. Unfinished judging/API errors can produce an
**unresolved** result rather than an invented winner. Unlimited matches stay active until their
solve condition, cancellation, or clean shutdown. This is friendly practice, not anti-cheat.

## 9. Test with one account

An organizer with Manage Server can test alone with:

```text
/blitz solo minutes:1 rating:800 voice_mode:both
```

Join voice first. Test cancellation with `/blitz cancel`, timeout by waiting one minute, or solve
detection with a longer duration and a fresh accepted practice submission. Omit minutes for unlimited.
Solo tests use the same recovery and room-reuse code as multiplayer games.

## Blitz tournaments

Organizers can run a 2–32-player single-elimination event:

```text
/tournament create name:Community Cup capacity:16 minutes:20 rating:1200
```

Players use **Join** on the tournament panel. The host chooses **Start tournament** to generate
the random bracket and automatic byes. Each duel has its own **Accept** buttons and countdown.
Verified winners advance automatically; draws and inconclusive games pause for manager action.
The public panel displays an actual bracket image with player cards, round connections,
live/pending states, winners, and a champion slot. It updates as the event progresses.
The **Bracket / status** button and `/tournament bracket` send the bracket image too.

Use `/tournament list`, `/tournament bracket`, `/tournament retry`, `/tournament resolve`, and
`/tournament cancel` to manage events. Omit minutes for unlimited time. Give the bot **Attach Files**
and **Embed Links** permissions. See [TOURNAMENTS.md](TOURNAMENTS.md) for all options, reservations,
forfeits, restart behavior, artwork, and profile-card examples.

When the operator has enabled the voice Activity, join an existing voice channel and use
`/activity tournament_id:ID` or **Open voice activity**. The room shares an interactive bracket
and match desk. Players accept for themselves; the Activity shows the countdown and then the
problem. From voice-channel chat, the command launches natively. From text chat it uses a
short-lived Activity invite, requiring Create Invite for both you and the bot. Without that
permission, use the voice channel's chat. See [ACTIVITY.md](ACTIVITY.md) for local review and setup.
Hosts/managers can use **Manage this match** in the Activity to retry a blocked pairing,
choose and confirm a manual winner, change an active timer (including unlimited), or stop
one duel. The server applies the same permissions and recovery rules as the bot commands.

## 10. Server organizer setup

Requires Manage Server:

```text
/admin configure reminders_channel:#contests daily_channel:#daily timezone:Asia/Amman daily_rating:1200 daily_hour:9
/admin channel action:add channel:#bot-commands
/admin channel action:remove channel:#bot-commands
/admin channel action:clear
/admin setup_roles
/admin refresh
/admin status
```

New servers default to UTC; choose any IANA timezone using suggestions. Feed settings are replaced
when configuring: omitted channels turn off those feeds. Rank-role setup needs Manage Roles and
the bot role above its `CF · …` roles. Mute/deafen/move modes need their corresponding permissions
in source and destination voice channels. Manage Channels is needed only for fallback room creation.
No Administrator permission or privileged gateway intent is required.
