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
