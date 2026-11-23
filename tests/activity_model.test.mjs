import test from 'node:test';
import assert from 'node:assert/strict';
import {bracketColumns, matchLabel, timeLabel, safeProblemUrl} from '../just_bot/activity_web/model.js';

test('only known winners fill later rounds', () => {
  const columns = bracketColumns([{round_no: 1, position: 1, state: 'done', player_a: '1', player_b: '2', winner_id: '1'},
    {round_no: 1, position: 2, state: 'playing', player_a: '3', player_b: '4', winner_id: null}]);
  assert.equal(columns.length, 2);
  assert.equal(columns[1][0].player_a, '1');
  assert.equal(columns[1][0].player_b, null);
  assert.equal(columns[1][0].winner_id, null);
});
test('match state distinguishes ready checks, live play, and manual results', () => {
  assert.equal(matchLabel({state: 'playing'}, {state: 'pending'}), 'Ready check');
  assert.equal(matchLabel({state: 'playing'}, {state: 'active'}), 'Live');
  assert.equal(matchLabel({state: 'done', note: 'Manager decision by 123'}, {}), 'Manager decision');
});
test('deadlines clamp at zero and problem links stay on Codeforces', () => {
  assert.equal(timeLabel(-4), '00:00');
  assert.equal(timeLabel(121), '02:01');
  assert.equal(safeProblemUrl({contestId: 4, index: 'A'}), 'https://codeforces.com/problemset/problem/4/A');
  assert.equal(safeProblemUrl({contestId: 4, index: '../evil'}), null);
});
