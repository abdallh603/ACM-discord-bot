# Public release review scope

This first public release is reviewed from an empty repository baseline. The user's conversation
is the originating specification; no issue tracker or external issue is involved.

1. Rebuild the Codeforces community bot, retaining credits/license, and suggest future features.
2. Provide verified handles, recommendations, daily scoring, contests, roles, training, and upsolving.
3. Support 2–8-player Blitz and several independent concurrent games, with configurable difficulty,
   topic/random topic/manual problems, unlimited or timed play, and first-solve/all-solve rules.
4. Add private setup menus and native slash suggestions for topic, rating, and other appropriate options.
5. Support mute, deafen, both, or no effects; restore only owned effects, including after failures,
   disconnection, cancellation, timeout, shutdown, and restart. Reuse existing rooms without deleting them.
6. Provide organizer solo testing without fake participants or bypassing handle ownership.
7. Make the bot public-ready across servers, with isolated data, authorization, configuration,
   background jobs, roles, and per-server calendars. Avoid university-specific product branding.
8. Name the bot RoundForge and provide Markdown usage, hosting, privacy, and manual publishing guides.
9. Support a non-playing host, mentioned player rosters, and optional delegated managers. Managers
   can inspect/edit/check/time/cancel their games but cannot accept for participants. Managers can
   host independent games without player-slot or voice-state conflicts.
10. Review the whole source and validate meaningful behavior. Keep all tokens, runtime data,
    and private local files out of Git.

The user subsequently asked to publish to GitHub themselves. Do not create or upload a remote
repository or use GitHub further. Prepare only the reviewed local commits and instructions.

Excluded: running a paid hosted service, horizontal scaling, official institutional verification,
prize-grade anti-cheat, migrating the PSUT/TLE database, or inventing proof of live Discord testing.
