# Features worth adding for the RoundForge community

Version 0.2 adds **beginner training tracks**, **post-contest upsolving goals**, and
**multiplayer blitz** with concurrent games and voice controls. The remaining ideas below are proposals.

| Priority | Feature | How it helps RoundForge | Suggested scope |
| --- | --- | --- | --- |
| 1 | Weekly improvement leaderboard | Recognizes progress across skill levels | Rating gain and newly solved problems; separate rated/unrated categories |
| 1 | RoundForge contest standings | Makes Codeforces rounds feel like community events | Show only verified server handles; final rank and rating delta |
| 2 | Blitz seasons and rematches | Builds on multiplayer blitz | Match history UI, seasonal scoring, and one-command rematches |
| 2 | Weekly virtual contest night | Creates a predictable practice habit | Organizer-selected past rounds, opt-in reminder role, server-timezone schedule |
| 2 | ICPC team finder | Helps students assemble teams of three | Skill tags, availability, goals, opt-in profiles; coach approval for official rosters |
| 2 | Mentor and help queue | Makes it easier to ask focused questions | Topic, problem, attempted approach, spoiler-safe thread; mentors claim requests |
| 3 | Arabic and English responses | Makes onboarding more accessible | Per-member language preference; bilingual help and verification instructions |
| 3 | Progress graphs and milestones | Makes long-term growth visible | Rating history, topic coverage, personal bests, opt-in milestone announcements |

## Recommended next release

Next add **RoundForge contest standings + weekly improvement rankings**. These complement the training,
upsolving, and blitz workflows already implemented. Exercise voice permissions and recovery on a
test Discord server before enabling blitz for the full community.

## Design notes before expanding

- Keep all leaderboards server-scoped. A Codeforces organization field is self-reported and must not
  grant a university-verified role. If student-only features are needed, use an organizer-reviewed
  process or a separately designed university verification flow.
- Publish topic hints and editorials after participants choose to see them. Never expose solutions
  to ongoing contests through automated upsolving features.
- Announcements should use opt-in roles. Do not add automatic `@everyone` pings.
- Reward steady improvement and beginner participation alongside absolute rating.
- For duels, define ties, forfeits, problem reuse, prior solves, and restart recovery before launch.
- Agree on contest rules and anti-abuse procedures before awarding prizes based on bot points.
