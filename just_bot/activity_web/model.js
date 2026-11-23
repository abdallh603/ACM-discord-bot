export function bracketColumns(pairs) {
  const first = pairs.filter(p => p.round_no === 1);
  if (!first.length) return [];
  const size = first.length * 2;
  const actual = new Map(pairs.map(p => [`${p.round_no}:${p.position}`, p]));
  const columns = [];
  for (let round = 1; 2 ** round <= size; round++) {
    const nodes = [];
    for (let pos = 1; pos <= size / (2 ** round); pos++) {
      const previous = columns.at(-1);
      nodes.push(actual.get(`${round}:${pos}`) ?? {
        round_no: round, position: pos, state: 'future', blitz_id: null, note: '', winner_id: null,
        player_a: previous?.[(pos - 1) * 2]?.winner_id ?? null,
        player_b: previous?.[(pos - 1) * 2 + 1]?.winner_id ?? null,
      });
    }
    columns.push(nodes);
  }
  return columns;
}

export function matchLabel(pair, game) {
  if (pair.state === 'done') return pair.note?.startsWith('Manager decision') ? 'Manager decision' : pair.note === 'Bye' ? 'Bye · advanced' : 'Decided';
  if (pair.state === 'blocked') return 'Manager action';
  if (pair.state === 'future') return 'Upcoming';
  return ({active: 'Live', countdown: 'Countdown', starting: 'Preparing', settling: 'Verifying', pending: 'Ready check', cancelled: 'Stopped'})[game?.state] ?? 'Waiting';
}

export function roundName(count) {
  return ({2: 'Final', 4: 'Semi-finals', 8: 'Quarter-finals'})[count] ?? `Round of ${count}`;
}

export function timeLabel(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`;
}

export function safeProblemUrl(problem) {
  return Number.isInteger(problem?.contestId) && problem.contestId > 0 && /^[A-Z][0-9]?$/i.test(problem?.index ?? '')
    ? `https://codeforces.com/problemset/problem/${problem.contestId}/${problem.index}` : null;
}
