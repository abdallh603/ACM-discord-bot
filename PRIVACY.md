# RoundForge data and privacy

Each bot operator hosts their own instance and SQLite database. Server data is keyed by Discord
server ID. RoundForge sends requests only to Discord and Codeforces (including the public API and avatar CDNs) for its features;
it does not use analytics, advertising, or AI services.

Stored data includes Discord server/member/channel/role IDs and game message IDs (to restore buttons
and update standings), linked Codeforces handles and ratings,
verification codes valid for ten minutes, server settings, practice goals, daily claims, game rosters/results,
and voice recovery ownership. Tournament records also include signup lists, bracket pairings, reservations,
game links, manager decisions, champions, and pending artwork delivery. It does not request Codeforces passwords/API keys, read message content,
record voice audio, or store payment information. The bot's Discord token belongs in local environment
configuration, not the database or repository.

Linking and verification happen by command. Game participants accept the displayed game settings
before voice changes begin. Menus are private to the opener. Game messages use the selected text
channel's existing audience; they are not automatically confidential.

The profile command fetches public Discord and Codeforces avatars to render the requested
Discord-username ~ CF-handle card. Photos are processed in memory and are not retained on the
bot's disk. The resulting image is uploaded to the command's Discord channel and follows Discord's
message/attachment retention. Missing photos use initials. Generated tournament/champion artwork
contains no player photos. Live bracket images use players' Discord/CF avatars and labels;
small avatar thumbnails use a bounded in-memory cache (at most 256 identities), with a
15-minute freshness period. Entries are evicted as the cache fills or when the bot stops.
No photos are cached to disk. Bracket images are uploaded to Discord and follow its attachment retention.
Tournament registrations/brackets persist in SQLite until the operator
removes them; leave registration or cancel an event to release its active reservation.

`/handle unlink` removes the current handle link and pending verification. It does not erase historical
claims/games, which remain for duplicate prevention, results, and recovery. Expired verification
rows remain until replaced by a new link attempt or removed by unlinking. `/training stop` and
`/upsolve stop` remove those current goals. Departed members' current links are pruned during community
refresh; historic activity records remain. Contact the instance operator for additional data removal.
There is no automatic history retention expiry in this version. Backups can retain older data.

Only server organizers can configure server feeds/channel restrictions and rank roles. Hosts and
assigned managers control their own games; they cannot accept for players or manage another server's
games. Bot owners with database access can inspect their instance's stored records.

Operators should keep backups private, restrict host access, and cancel active games before removing
the bot or deleting recovery data. Never publish `.env`, tokens, databases, logs, or backups.
