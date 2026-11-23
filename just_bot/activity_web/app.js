import {bracketColumns, matchLabel, roundName, timeLabel, safeProblemUrl} from './model.js';

const $ = id => document.getElementById(id);
let model = null, selected = null, token = null, sdk = null, sdkEvents = null, participants = [];
let etag = '', busy = false, polling = false, connected = false, offset = 0, appliedAt = 0, subscribed = false;
let preview = false, toastTimer;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function avatar(player, cf = false) {
  const raw = player?.[cf ? 'cf_avatar' : 'discord_avatar'];
  // Server-generated PNG thumbnails only; never insert an arbitrary URL or SVG from a name.
  if (typeof raw === 'string' && raw.startsWith('data:image/png;base64,')) {
    const img = el('img', `avatar${cf ? ' cf' : ''}`);
    img.src = raw; img.alt = `${cf ? 'Codeforces' : 'Discord'} avatar for ${player.username}`;
    return img;
  }
  const node = el('span', `avatar${cf ? ' cf' : ''}`, (cf ? player?.handle : player?.username)?.slice(0, 2).toUpperCase() || '?');
  node.setAttribute('aria-hidden', 'true');
  return node;
}

function player(id) { return model?.players.find(p => p.id === id); }
function key(pair) { return `${pair.round_no}:${pair.position}`; }
function allNodes() { return model ? bracketColumns(model.pairs).flat() : []; }
function myPair() { return allNodes().filter(p => [p.player_a, p.player_b].includes(model?.viewer.id)).sort((a, b) => b.round_no - a.round_no)[0]; }

function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5500);
}

function connection(text, live) {
  $('connection').textContent = text; $('connection').classList.toggle('offline', !live);
}

async function request(path, {method = 'GET', body, headers = {}} = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), method === 'GET' ? 12000 : 60000);
  try {
    const response = await fetch(path, {method, cache: 'no-store', signal: controller.signal,
      headers: {...(token ? {Authorization: `Bearer ${token}`} : {}), ...(body ? {'Content-Type': 'application/json'} : {}), ...headers},
      body: body ? JSON.stringify(body) : undefined});
    const serverTime = Number(response.headers.get('X-Server-Time'));
    if (serverTime) offset = serverTime * 1000 - Date.now();
    if (response.status === 304) return {unchanged: true};
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401) { connected = false; $('reconnect').hidden = false; }
      throw new Error(data.error || 'The activity could not complete that request.');
    }
    return {data, serverTime, etag: response.headers.get('ETag')};
  } finally { clearTimeout(timeout); }
}

function apply(response) {
  if (response.unchanged) return;
  if (response.serverTime && response.serverTime < appliedAt) return;
  appliedAt = response.serverTime || Date.now() / 1000;
  model = response.data;
  if (response.etag) etag = response.etag;
  render();
}

async function poll() {
  if (!connected || polling || document.hidden) return;
  polling = true;
  try {
    const response = await request('/api/snapshot', {headers: etag ? {'If-None-Match': etag} : {}});
    apply(response); connection('Room synced', true); $('notice').hidden = true;
  } catch (error) {
    connection(connected ? 'Reconnecting…' : 'Disconnected', false);
    $('notice').textContent = error.name === 'AbortError' ? 'Connection is slow. Your bracket will update when it reconnects.' : error.message;
    $('notice').hidden = false;
  } finally { polling = false; }
}

async function act(action, extra = {}, tournamentId = model?.tournament?.id) {
  if (busy) return;
  busy = true; renderDesk();
  try {
    const response = await request('/api/action', {method: 'POST', body: {action, tournament_id: tournamentId, ...extra}});
    etag = ''; apply(response);
    if (action === 'check') toast(preview ? 'Sample solve check requested.' : 'Solve check queued. Results update automatically.');
    if (action === 'select') toast('The room is following this tournament.');
  } catch (error) {
    toast(error.name === 'AbortError' ? 'That action is taking longer than expected. Check the bracket before trying again.' : error.message);
  } finally { busy = false; renderDesk(); await poll(); }
}

function actionButton(label, action, extra = {}, options = {}) {
  const node = el('button', options.className || 'primary full', busy ? 'Working…' : label);
  node.disabled = busy || !!options.disabled;
  const tid = model?.tournament?.id;
  node.addEventListener('click', () => act(action, extra, tid));
  return node;
}

function render() {
  const tournament = model.tournament;
  $('room-name').textContent = model.room.name;
  $('tournament-name').textContent = tournament?.name || 'Your tournament, together.';
  $('tournament-state').textContent = ({registration: 'Registration open', running: 'In progress', finished: 'Finished', cancelled: 'Cancelled', cancelling: 'Stopping'})[tournament?.state] || 'Choose a tournament';
  $('tournament-meta').textContent = tournament ? `${model.players.length} players / ${tournament.capacity} places  ·  ${tournament.minutes ? `${tournament.minutes}-minute matches` : 'Unlimited time'}  ·  First solve advances` : 'The host chooses a tournament for everyone in this voice room.';
  const select = $('tournament-select');
  select.replaceChildren(new Option(tournament ? 'Choose tournament' : 'Waiting for the host', ''));
  for (const item of model.tournaments) select.add(new Option(item.name, item.id));
  select.value = tournament?.id || '';
  select.disabled = !model.room.can_select || busy || !model.tournaments.length;
  select.title = model.room.can_select ? 'Choose the bracket everyone in this activity sees' : 'The host chooses the shared bracket';
  if (!selected || !allNodes().some(p => key(p) === selected)) {
    const own = myPair();
    const live = allNodes().find(p => model.games[p.blitz_id]?.state === 'active');
    selected = own ? key(own) : live ? key(live) : allNodes()[0] ? key(allNodes()[0]) : null;
  }
  renderBoard(); renderDesk(); renderVoice();
}

function renderBoard() {
  const board = $('board'), tournament = model.tournament, scroll = $('board-scroll');
  const scrollX = scroll.scrollLeft, scrollY = scroll.scrollTop;
  const focused = document.activeElement?.dataset.pair;
  board.replaceChildren();
  if (!tournament) {
    const empty = el('div', 'empty-board');
    empty.append(el('span', 'empty-symbol', '⌁'), el('h3', '', model.tournaments.length ? 'Which cup are we following?' : 'A room for your next tournament.'),
      el('p', '', model.tournaments.length ? 'The host can choose a shared tournament above. Everyone in this activity will see the same bracket.' : 'Create a tournament with /tournament create in Discord, then open the activity again.'));
    board.append(empty); $('bracket-summary').textContent = 'No tournament selected'; return;
  }
  if (!model.pairs.length) {
    const grid = el('div', 'registration-grid');
    for (const item of model.players) {
      const row = el('div', 'registration-player'), name = el('div');
      name.append(el('strong', '', item.username), el('small', '', `~ ${item.handle}`));
      row.append(avatar(item), name, avatar(item, true)); grid.append(row);
    }
    if (model.players.length < tournament.capacity) grid.append(el('div', 'registration-player open', `${tournament.capacity - model.players.length} open places`));
    board.append(grid, el('p', 'waiting-copy', 'The bracket is seeded when the host starts. Join below, get comfortable, and be ready.'));
    $('bracket-summary').textContent = `${model.players.length} players registered`; return;
  }
  const columns = bracketColumns(model.pairs), grid = el('div', 'bracket-grid');
  const count = columns[0].length, step = 140, laneHeight = count * step;
  const rounds = [...columns, []];
  const roundGap = 28;
  const columnWidth = Math.max(200, Math.min(260, (board.clientWidth - 48 - (rounds.length - 1) * roundGap) / rounds.length));
  grid.style.setProperty('--column-width', `${columnWidth}px`);
  grid.style.setProperty('--round-gap', `${roundGap}px`);
  rounds.forEach((nodes, round) => {
    const col = el('div', 'round-column');
    const heading = el('h3', 'round-title', nodes.length ? roundName(count * 2 / (2 ** round)) : 'Champion');
    if (nodes.length) heading.append(el('span', '', `${nodes.length} ${nodes.length === 1 ? 'match' : 'matches'}`));
    col.append(heading);
    const lane = el('div', 'round-matches'); lane.style.height = `${laneHeight}px`;
    if (!nodes.length) {
      const winner = player(tournament.champion_id), card = el('div', 'champion-card');
      card.style.top = `${laneHeight / 2 - 71}px`;
      card.append(el('span', 'trophy', '🏆'), el('h3', '', winner ? 'Cup forged.' : 'One place. One winner.'));
      if (winner) card.append(avatar(winner), el('p', 'champion-name', winner.username), el('p', '', `~ ${winner.handle}`));
      else card.append(el('p', '', 'The final decides who takes it home.'));
      lane.append(card);
    }
    for (const pair of nodes) {
      const game = model.games[pair.blitz_id], card = el('button', `match-card${pair.state === 'future' ? ' future' : ''}${pair.winner_id ? ' advanced' : ''}${key(pair) === selected ? ' selected' : ''}${round === columns.length - 1 ? ' last' : ''}`);
      const center = (pair.position - .5) * (2 ** round) * step;
      card.style.top = `${center - 56}px`; card.dataset.pair = key(pair);
      card.setAttribute('aria-label', `${roundName(count * 2 / (2 ** round))}, match ${pair.position}: ${matchLabel(pair, game)}`);
      card.setAttribute('aria-pressed', String(key(pair) === selected));
      const top = el('div', 'match-topline');
      top.append(el('span', '', `Match ${pair.position}`), el('span', `match-status${pair.state === 'done' ? ' done' : game?.state === 'active' || game?.state === 'countdown' ? ' live' : ''}`, matchLabel(pair, game)));
      card.append(top);
      ['player_a', 'player_b'].forEach((field, side) => {
        const id = pair[field], item = player(id);
        const row = el('div', `match-player${id && id === pair.winner_id ? ' winner' : ''}${id && pair.winner_id && id !== pair.winner_id ? ' loser' : ''}`);
        if (item) {
          const label = el('span', 'player-label');
          label.append(el('strong', '', item.username), el('small', '', `~ ${item.handle}`));
          row.append(avatar(item), label, avatar(item, true));
        } else row.append(el('span', 'placeholder', pair.note === 'Bye' ? 'Bye' : round ? `Winner of R${round}, match ${(pair.position - 1) * 2 + side + 1}` : 'To be decided'));
        card.append(row);
      });
      card.addEventListener('click', () => { selected = key(pair); renderBoard(); renderDesk(); });
      lane.append(card);
    }
    col.append(lane); grid.append(col);
  });
  board.append(grid);
  requestAnimationFrame(() => {
    const colWidth = grid.querySelector('.round-column').getBoundingClientRect().width, gap = roundGap;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('connectors'); svg.style.top = '36px';
    svg.setAttribute('width', String(rounds.length * (colWidth + gap) - gap)); svg.setAttribute('height', String(laneHeight));
    columns.forEach((nodes, round) => {
      if (round === columns.length - 1) return;
      for (const pair of nodes) {
        const startX = round * (colWidth + gap) + colWidth, endX = startX + gap;
        const y = (pair.position - .5) * (2 ** round) * step;
        const targetY = (Math.ceil(pair.position / 2) - .5) * (2 ** (round + 1)) * step;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M${startX} ${y} H${startX + gap / 2} V${targetY} H${endX}`); svg.append(path);
      }
    });
    const finalPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const finalEnd = (columns.length - 1) * (colWidth + gap) + colWidth;
    finalPath.setAttribute('d', `M${finalEnd} ${laneHeight / 2} H${finalEnd + gap}`); svg.append(finalPath);
    grid.prepend(svg); scroll.scrollLeft = scrollX; scroll.scrollTop = scrollY;
    if (focused) board.querySelector(`[data-pair="${focused}"]`)?.focus({preventScroll: true});
  });
  const done = model.pairs.filter(p => p.state === 'done' && p.note !== 'Bye').length;
  $('bracket-summary').textContent = `${done} ${done === 1 ? 'match' : 'matches'} decided${tournament.champion_id ? ' · Champion confirmed' : ` · Round ${tournament.current_round}`}`;
}

function deskPerson(id) {
  const person = el('div', 'desk-person'), item = player(id);
  person.append(avatar(item), el('strong', '', item?.username || 'To be decided'), el('small', '', item ? `~ ${item.handle}` : 'Waiting for a winner'));
  return person;
}

function rules(parent, tournament) {
  const grid = el('div', 'desk-rules');
  for (const [label, value] of [['Time limit', tournament.minutes ? `${tournament.minutes} minutes` : 'Unlimited'],
    ['Voice mode', ({none: 'No effects', mute: 'Mute', deafen: 'Deafen', both: 'Mute + deafen'})[tournament.voice_mode]],
    ['Problem rating', tournament.rating || 'Automatic'], ['Topics', tournament.topics.length ? tournament.topics.join(', ') : tournament.random_topic ? 'Random topic' : 'Any topic']]) {
    const cell = el('div'); cell.append(el('span', 'rule-label', label), el('span', 'rule-value', value)); grid.append(cell);
  }
  parent.append(grid);
}

function renderDesk() {
  if (!model) return;
  const pane = $('match-content'), tournament = model.tournament;
  pane.replaceChildren(); pane.append(el('p', 'desk-context', 'Match desk'));
  if (!tournament) {
    pane.append(el('div', 'empty-desk-icon', '⌁'), el('h2', '', 'Together in the room.'),
      el('p', 'desk-copy', 'Once the host chooses a tournament, select a match to see its players, ready check, countdown, and problem.'));
    return;
  }
  if (!model.pairs.length) {
    pane.append(el('h2', '', 'A place in the bracket.'), el('p', 'desk-copy', 'Join with your verified Codeforces account. The host seeds the bracket when everyone is here.'));
    const joined = model.players.some(p => p.id === model.viewer.id);
    if (tournament.state === 'registration') {
      pane.append(actionButton(joined ? 'Leave registration' : 'Join tournament', joined ? 'leave' : 'join', {}, {disabled: !joined && model.players.length >= tournament.capacity}));
      if (tournament.can_manage) pane.append(actionButton('Start tournament', 'start', {}, {className: 'full', disabled: model.players.length < 2}));
    }
    rules(pane, tournament); managerControls(pane, tournament); return;
  }
  const pair = allNodes().find(p => key(p) === selected);
  if (!pair) { pane.append(el('h2', '', 'Pick a match.')); return; }
  const game = model.games[pair.blitz_id], columns = bracketColumns(model.pairs);
  const heading = el('div', 'desk-round');
  heading.append(el('h2', '', roundName(columns[0].length * 2 / (2 ** (pair.round_no - 1)))), el('span', 'desk-state', matchLabel(pair, game))); pane.append(heading);
  const versus = el('div', 'desk-versus'); versus.append(deskPerson(pair.player_a), el('span', 'versus-label', 'vs'), deskPerson(pair.player_b)); pane.append(versus);
  if (['cancelled', 'cancelling'].includes(tournament.state)) {
    pane.append(el('p', 'desk-copy', 'This tournament has stopped. RoundForge restores the voice changes it owns; disconnected players are retried when they rejoin.'));
    rules(pane, tournament); return;
  }
  const own = game?.players.some(p => p.id === model.viewer.id), manageable = own || tournament.can_manage;
  if (game?.state === 'pending') {
    pane.append(el('h3', 'desk-section-title', own ? 'Ready when you are.' : 'Waiting for the players.'));
    pane.append(el('p', 'desk-copy', 'Both players accept these settings before a 10-second countdown. The problem stays hidden until it ends.'));
    for (const entry of game.players) {
      const row = el('div', 'ready-row'); row.append(el('span', '', player(entry.id)?.username || 'Player'), el('span', entry.ready ? 'yes' : 'no', entry.ready ? '✓ Ready' : 'Not ready yet')); pane.append(row);
    }
    const ready = game.players.find(p => p.id === model.viewer.id)?.ready;
    if (own) {
      pane.append(actionButton(ready ? 'You’re ready' : 'Accept & get ready', 'ready', {match_id: game.id, revision: game.revision}, {disabled: ready}));
      pane.append(actionButton('Decline this match', 'decline', {match_id: game.id}, {className: 'secondary-action'}));
    }
  } else if (game?.state === 'countdown' || game?.state === 'starting') {
    pane.append(el('div', 'countdown-number', game.state === 'countdown' ? '10' : '…'));
    const value = pane.querySelector('.countdown-number'); value.dataset.countdown = game.countdown_ends_at || '';
    pane.append(el('p', 'timer-caption', game.state === 'countdown' ? 'Get ready. The problem appears at GO.' : 'Choosing a fair problem for both players.'));
  } else if (game?.state === 'active' || game?.state === 'settling') {
    const timer = el('div', 'timer', game.ends_at ? timeLabel(game.ends_at - now()) : '∞');
    if (game.ends_at) timer.dataset.deadline = game.ends_at;
    pane.append(timer, el('p', 'timer-caption', game.ends_at ? 'remaining in this match' : 'Unlimited time · first verified solve wins'));
    if (game.problem) {
      const box = el('div', 'problem-box');
      box.append(el('small', '', preview ? 'Sample problem · Codeforces' : 'Codeforces problem'), el('h3', '', `${game.problem.contestId}${game.problem.index} · ${game.problem.name}`),
        el('p', '', game.problem.tags?.join(', ') || 'Individual practice submission'));
      const url = safeProblemUrl(game.problem);
      if (url) { const open = el('button', 'primary', 'Open problem'); open.onclick = () => openLink(url); box.append(open); }
      pane.append(box);
    }
    if (manageable) pane.append(actionButton('Check my solve', 'check', {match_id: game.id}, {className: 'full'}));
  } else if (pair.winner_id) {
    pane.append(el('h3', 'desk-section-title', tournament.champion_id === pair.winner_id && pair.round_no === columns.length
      ? `${player(pair.winner_id)?.username || 'The winner'} is the champion.` : `${player(pair.winner_id)?.username || 'The winner'} advances.`));
    pane.append(el('p', 'desk-copy', pair.note === 'Bye' ? 'This player advances with a bye.' : pair.note?.startsWith('Manager decision') ? pair.note : 'This pairing has a confirmed winner. Follow the connecting line to their next round.'));
  } else {
    pane.append(el('p', 'desk-copy', pair.state === 'blocked' ? pair.note || 'The manager needs to retry or resolve this match in Discord.' : 'The next pairing becomes playable once the previous round is decided.'));
  }
  rules(pane, tournament);
  pane.append(el('p', 'desk-notice', 'Only voice changes applied by RoundForge are restored. Players keep control of their own microphone settings.'));
  managerControls(pane, tournament); updateTimers();
}

function managerControls(parent, tournament) {
  if (tournament.can_manage && ['registration', 'running'].includes(tournament.state)) {
    const cancel = el('button', 'secondary-action', 'Cancel tournament'); cancel.disabled = busy;
    cancel.onclick = () => {
      const dialog = $('confirm-dialog'); dialog.showModal();
      $('confirm-yes').onclick = () => { dialog.close(); act('cancel_tournament', {}, tournament.id); };
    };
    parent.append(cancel);
  }
}

function renderVoice() {
  if (!model) return;
  const people = preview ? model.viewers || [] : participants;
  $('voice-people').replaceChildren();
  for (const item of people.slice(0, 6)) { const face = avatar(player(item.id) || item); face.title = item.username || 'Room member'; $('voice-people').append(face); }
  $('voice-count').textContent = `${people.length} ${people.length === 1 ? 'person' : 'people'}`;
}

function now() { return (Date.now() + offset) / 1000; }
function updateTimers() {
  for (const node of document.querySelectorAll('[data-deadline]')) node.textContent = timeLabel(Number(node.dataset.deadline) - now());
  for (const node of document.querySelectorAll('[data-countdown]')) if (node.dataset.countdown) node.textContent = Math.max(0, Math.ceil(Number(node.dataset.countdown) - now())) || '…';
}

async function openLink(url) {
  try { if (sdk) await sdk.commands.openExternalLink({url}); else window.open(url, '_blank', 'noopener,noreferrer'); }
  catch { toast('Discord could not open that link. Open the problem from its bot message.'); }
}

async function connect() {
  $('reconnect').hidden = true; $('notice').hidden = true; connection('Connecting', false);
  try {
    const {data: config} = await request('/api/config');
    preview = config.preview; offset = config.server_time * 1000 - Date.now();
    $('preview-badge').hidden = !preview; $('preview-tools').hidden = !preview;
    if (preview) {
      const response = await request('/api/session', {method: 'POST', body: {}}); token = response.data.session_token;
    } else {
      if (!new URLSearchParams(location.search).has('frame_id')) throw new Error('Open this activity from a Discord voice channel. For local review, run the preview server described in ACTIVITY.md.');
      if (!sdk) {
        const library = await import('/vendor/discord-sdk.js');
        sdk = new library.DiscordSDK(config.client_id); sdkEvents = library.Events;
      }
      await Promise.race([sdk.ready(), new Promise((_, reject) => setTimeout(() => reject(new Error('Discord did not connect. Reopen the activity in your voice room.')), 15000))]);
      const {code} = await sdk.commands.authorize({client_id: config.client_id, response_type: 'code', state: '', prompt: 'none', scope: ['identify']});
      const {data} = await request('/api/session', {method: 'POST', body: {code, instance_id: sdk.instanceId}});
      await sdk.commands.authenticate({access_token: data.access_token}); token = data.session_token;
      try {
        const result = await sdk.commands.getActivityInstanceConnectedParticipants(); participants = Array.isArray(result.participants) ? result.participants : [];
        if (!subscribed) { sdk.subscribe(sdkEvents.ACTIVITY_INSTANCE_PARTICIPANTS_UPDATE, value => { participants = Array.isArray(value.participants) ? value.participants : []; renderVoice(); }); subscribed = true; }
      } catch { participants = []; }
    }
    etag = ''; appliedAt = 0; connected = true; await poll();
  } catch (error) {
    connected = false; connection('Not connected', false); $('reconnect').hidden = false;
    $('notice').textContent = error.message || 'Could not connect. Reopen the activity from Discord.'; $('notice').hidden = false;
  }
}

$('tournament-select').onchange = event => { if (event.target.value) act('select', {}, event.target.value); };
$('my-match').onclick = () => { const pair = myPair(); if (pair) { selected = key(pair); renderBoard(); renderDesk(); if (innerWidth < 740) $('match-content').scrollIntoView({behavior: 'smooth', block: 'start'}); } else toast('You do not have a match in this bracket yet.'); };
$('bracket-tab').onclick = () => $('bracket-title').scrollIntoView({behavior: 'smooth', block: 'start'});
$('reconnect').onclick = connect; $('confirm-no').onclick = () => $('confirm-dialog').close();
for (const button of document.querySelectorAll('[data-scenario]')) button.onclick = async () => {
  if (!preview) return; selected = null;
  await act('demo_scenario', {scenario: button.dataset.scenario});
  for (const item of document.querySelectorAll('[data-scenario]')) item.classList.toggle('selected', item === button);
};
setInterval(poll, 2500); setInterval(updateTimers, 250);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
let resizeTimer; window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (model) renderBoard(); }, 120); });
connect();
