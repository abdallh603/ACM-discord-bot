# RoundForge public release review

Reviewed the complete first-public-release source against [CONTRIBUTING.md](CONTRIBUTING.md)
and the user-derived [requirements scope](docs/REVIEW_SCOPE.md), using separate read-only
standards and specification reviewers. The baseline was empty commit `08e820d`; the initial
source snapshot was `3508a02`. Fixes were checked independently after implementation.

## Standards

**One confirmed P1 defect, fixed; no remaining material standards finding.**

Voice cleanup originally trusted cached gateway mute/deafen flags. If a REST edit succeeded
before the gateway cache updated, cancellation could drop ownership without issuing a restoring
request, stranding a server mute. This violated the documented ownership/recovery rule.

`4721478` fixes cleanup to send explicit, idempotent `False` for each owned flag while connected,
and clear ownership only after Discord accepts restoration. Stale-cache success/failure tests
cover both paths. The standards reviewer verified this fix and the subsequent authorization/
recovery changes, finding no additional material ownership or authorization regression.

## Specification

**Four confirmed P2 defects, fixed; no remaining material finding from the completed reviews.**

1. **Stale first-solve snapshots:** a player queried first could solve while another player's
   later history was loading, making a later solve appear to win. `ada1fd7` adds a second fresh
   pass after observing a candidate before immediately awarding a winner.
2. **Pending-lobby channel authorization:** `text_id` is unset until startup, so a delegated
   manager or player could be blocked from their pending lobby outside the server allowlist.
   `ada1fd7` includes the original lobby channel from options, keeps the lookup guild scoped,
   and still requires a participant/manager/server-organizer authorization.
3. **Active restart policy:** active games did not reapply configured voice effects changed
   while offline. `ada1fd7` re-enters only unsolved roster players for unexpired games and
   releases expired games instead. Non-playing managers are not included.
4. **Partial all-solve finalization:** the first confirmation fix incorrectly required a complete
   roster of solves before partial timed results could be confirmed. `04f74f3` accepts a complete,
   successful history pass started after the eligible submission window closed. Immediate
   active-game winners still require the second phase. The exact reported regression now
   finishes with the correct winner instead of an incorrect unresolved result.

The specification reviewer confirmed that the original three issues were resolved and separately
retested the follow-up timeout fix. The standards reviewer also verified the main fix set.

## Validation

- **132 unit/integration-style tests passed, no skips**, on local Python 3.12 with discord.py 2.7.1.
- Python compilation passed for application, tests, and scripts.
- Command payloads serialize as guild-only installs/contexts; public mode covers multiple servers.
- Tests cover native suggestions, dropdown paging/callbacks, hosted/delegated manager authority,
  individual player acceptance, guild isolation, simultaneous games, SQLite migration, old timezone
  preservation, timed/unlimited games, and owned voice-state recovery.
- Docker Compose configuration validation passed; a Docker image was not built/run.
- `scripts/check_release.py` passed against the Git index: no runtime files or recognized
  credential patterns. `.env`, databases, logs, and the virtual environment are not tracked.
- CI is configured for Python 3.11/3.12/3.13 with read-only repository permissions. Remote CI
  has not run because the user elected to publish the repository themselves.
- Ruff could not be installed in the restricted environment; no lint/format pass is claimed.

These tests simulate Discord effects and Codeforces responses. They do not prove live Discord
login, permissions, gateway timing, app-public settings, production capacity, or complete security.
Confirm the Public Bot/Guild Install settings and restart the configured instance before live use.
The running process must remain online; uploading source alone does not host it.

## Release outcome

Standards: **1 finding, fixed**; worst initial issue: P1 voice recovery.
Specification: **4 findings, fixed**; worst initial issue: P2 correctness/authorization/recovery.
No remote repository was created or uploaded. [USAGE.md](USAGE.md) and [PUBLISHING.md](PUBLISHING.md)
provide the user-facing guide and manual publication steps.

## Version 0.5.0 usability review

Added a no-arguments player picker, multi-topic dropdowns with ANY matching and selections
preserved across pages, a duration modal, and separate everyone-finishes/standings toggles.
Invitations now show persistent Accept/Decline/Status buttons with shared readiness updates.
Players still consent individually; changes invalidate old revisions.

The startup lifecycle now has a durable countdown phase. The visible 10-second countdown
keeps the problem hidden and applies no voice effects until it completes. Cancellation is
available during the countdown without blocking unrelated games. Shutdown cancels/awaits
startup tasks, and restart recovery cancels interrupted countdowns. Active panels offer solve
checks, standings, cancellation, and status. Pending and active panels are registered again
after restart. No database schema migration was needed; message IDs are stored in game options.

Standings reuse one message, show solve times and shared ranks for tied timestamps, and distinguish
live, final, and incomplete results. The menu and direct commands can disable them before play.
Failed invitation delivery releases reserved player slots. Existing room reuse, per-server
isolation, winner confirmation, deadlines, and ownership-based voice recovery remain covered.

Validation: **153 automated tests passed, no skips** with the installed discord.py dependency;
`git diff --check` passed. These checks include button authorization and stale revisions,
countdown rendering/cancellation, shutdown/restart recovery, concurrent games, multi-topic
selection, duration input, failed delivery, and standings updates/ties. No live multi-account
Discord test was performed. Restart the bot to load the code and refresh its command registration.

## Version 0.6.0 tournaments and profile cards

Added durable single-elimination tournaments for 2–32 verified players: registration buttons,
randomized saved seeding, distributed byes, simultaneous 1v1 Blitz pairings, automatic advancement,
and a confirmed champion. Per-duel rules retain consent, countdowns, room reuse, independent clocks,
and ownership-based voice restoration. Tournaments use first-solve games and fresh eligible problems.
Draws, cancellations, and inconclusive outcomes block advancement for an explicit manager retry or
decision; manual decisions are visible in the bracket. Tournament and Blitz reservations prevent
overlapping games, and eliminated players are released. A next-round winner with pending voice
recovery waits safely before getting another invitation.

Added tournament/champion PNG artwork, embedded in public tournament panels and included in package
data/Docker images. Artwork prompts/provenance are in `just_bot/assets/README.md`. The profile command
renders an actual Discord-avatar / username ~ Codeforces-handle / CF-avatar PNG card with Pillow.
Only allowlisted HTTPS CF avatar hosts are fetched, redirects are refused, bytes and image pixels
are bounded, and missing/invalid images fall back to initials. Requests have timeouts and limited
concurrency. Photos are processed in memory; there is no avatar disk cache. Lack of Attach Files
permission retains the ordinary profile embed. Docker includes a Unicode font for card labels.

Tournament persistence uses additive tables in the existing SQLite database. Full rounds and final
reservation release are transactional. Recovery relinks a Blitz committed before its bracket link,
retries interrupted cancellation, restores persistent signup controls, and retains failed final
artwork delivery for retry. Clean bot shutdown cancels current duels via normal Blitz shutdown;
the tournament manager can retry those blocked pairings after restarting.

Validation: **176 automated tests passed, no skips**, including a complete five-player tournament
with real Blitz acceptance/polling, all supported seeding sizes, 32-player embed bounds, concurrent
guilds/events, byes, authorization, retry/resolve, reservation conflicts, recovery, artwork swapping,
avatar host/size checks, profile rendering, and missing-file-permission fallback. `pip check` and
`git diff --check` passed. The profile-card example was rendered and visually inspected, as were
both generated artwork assets. Pillow 13 was available in the local runtime and was installed into
the ignored virtual environment from that local copy; outbound pip downloads were unavailable.
No live multi-account Discord tournament, real CDN avatar download, fresh dependency install,
remote CI, or Docker build was performed. The bot must be restarted to register the new commands.

## Version 0.6.1 live bracket images

Tournament embeds now attach a data-driven PNG of the actual bracket. Player rows show Discord
avatar / username ~ CF handle / CF avatar, with initials when unavailable. The image includes
all rounds, known advancement paths, match states, byes, explicit manager decisions, future-player
placeholders, and a champion slot. Future rounds only receive recorded winners; the renderer
never selects or infers a result. Registration uses a player grid. Public panels update as games
change, and both the bracket button and slash command send the image. The original AI-generated
branding files remain available as decorative assets, separate from the live bracket.

Avatar lookups batch CF profiles, restrict image hosts and sizes via the existing fetcher, and use
four download slots, a five-second collection budget, small normalized thumbnails, and a bounded
memory cache. Image renders are serialized to bound peak memory across simultaneous events.
The 32-player image fits within 4096 pixels in either dimension and an 8 MiB attachment budget
in the automated fixture. Source images and photographs are not stored to disk.

Validation: **183 automated tests passed**, including image attachments through the bracket button
and slash command, real avatar placement, status rendering, winner propagation, byes, full fields,
and identity-cache/failure behavior. The fictional eight-player sample at
`docs/tournament-bracket-preview.png` was generated and visually inspected. Recreate it with
`python scripts/preview_tournament.py`. No live Discord session or real avatar download was tested.
