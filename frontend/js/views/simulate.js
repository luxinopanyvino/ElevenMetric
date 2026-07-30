/* MATCH SIM — pick two sides, play the fixture, watch it back. */

import { api } from '../api.js';
import { seriesColor } from '../charts.js';
import { badge, el, empty, fmt, loading, mount, note, svg, table, tile } from '../ui.js';

const L = 105; const W = 68;
const toY = (y) => W - y;

/** Playback speeds, expressed as match-minutes per real second. */
const MODES = {
  instant: { label: 'Instant', rate: Infinity, hint: 'Straight to the result' },
  fast: { label: 'Fast · 30 s', rate: 3.0, hint: '90 minutes in half a minute' },
  realtime: { label: 'Real time · 4 min', rate: 0.375, hint: '90 minutes in four minutes' },
};

export async function render(root, state) {
  mount(root, loading('Loading the match engine'));
  const options = await api.get('/simulation/options');

  const playable = options.teams.filter((t) => t.can_play);
  if (!playable.length) {
    mount(root, el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'Nothing to play with yet' })),
      note('A side needs eleven available players before it can take the field. '
        + 'Import a squad from the Import data tab.', 'warn')));
    return;
  }

  const homeDefault = playable.find((t) => t.id === state.teamId) || playable[0];

  const ui = {
    homeTeam: homeDefault.id,
    awayTeam: '',                 // empty = generated opposition
    awayName: 'Opposition',
    awayStrength: 80,
    homeFormation: homeDefault.default_formation,
    awayFormation: '4-4-2',
    minutes: 90,
    seed: Math.floor(Math.random() * 1e6),
    autoSubs: true,
    mode: 'fast',
    result: null,
    busy: false,
    error: null,
    // Playback state
    clock: 0,
    playing: false,
    raf: null,
    lastTs: null,
  };

  const container = el('div', {});
  mount(root, container);

  /* --- Running ------------------------------------------------------------ */

  async function run() {
    stop();
    ui.busy = true; ui.error = null; ui.result = null; draw();
    try {
      ui.result = await api.post('/simulation/run', {
        home_team_id: ui.homeTeam,
        away_team_id: ui.awayTeam || null,
        away_name: ui.awayName,
        away_strength: Number(ui.awayStrength),
        home_formation: ui.homeFormation,
        away_formation: ui.awayFormation,
        minutes: Number(ui.minutes),
        seed: Number(ui.seed),
        auto_subs: ui.autoSubs,
        persist: true,
      });
      ui.clock = ui.mode === 'instant' ? ui.result.summary.minutes * 60 : 0;
      draw();
      if (ui.mode !== 'instant') play();
    } catch (err) {
      ui.error = err.detail || err.message;
      draw();
    } finally {
      ui.busy = false;
    }
  }

  /* --- Playback ----------------------------------------------------------- */

  function play() {
    if (!ui.result || ui.playing) return;
    const total = ui.result.summary.minutes * 60;
    if (ui.clock >= total) ui.clock = 0;
    ui.playing = true;
    ui.lastTs = null;
    updateControls();
    ui.raf = requestAnimationFrame(step);
  }

  function stop() {
    ui.playing = false;
    if (ui.raf) cancelAnimationFrame(ui.raf);
    ui.raf = null;
    updateControls();
  }

  function step(ts) {
    if (!ui.playing) return;
    if (ui.lastTs === null) ui.lastTs = ts;
    const elapsed = (ts - ui.lastTs) / 1000;
    ui.lastTs = ts;

    const rate = MODES[ui.mode].rate;
    ui.clock += elapsed * (Number.isFinite(rate) ? rate * 60 : 1e9);

    const total = ui.result.summary.minutes * 60;
    if (ui.clock >= total) {
      ui.clock = total;
      paint();
      stop();
      return;
    }
    paint();
    ui.raf = requestAnimationFrame(step);
  }

  /** Interpolate between the two playback frames bracketing the clock. */
  function positionsAt(seconds) {
    const pb = ui.result.playback;
    const frames = pb.frames;
    if (!frames.length) return null;

    const idx = Math.min(frames.length - 1,
      Math.max(0, Math.round(seconds * pb.hz)));
    const a = frames[idx];
    const b = frames[Math.min(frames.length - 1, idx + 1)];
    const span = (b[0] - a[0]) || 1;
    const t = Math.max(0, Math.min(1, (seconds - a[0]) / span));

    const scale = pb.scale || 1;
    const count = pb.roster.length;
    const out = { players: new Array(count), ball: null, possession: a[a.length - 1] };
    for (let i = 0; i < count; i += 1) {
      const ax = a[1 + i * 2]; const ay = a[2 + i * 2];
      const bx = b[1 + i * 2]; const by = b[2 + i * 2];
      out.players[i] = [(ax + (bx - ax) * t) / scale, (ay + (by - ay) * t) / scale];
    }
    const bi = 1 + count * 2;
    out.ball = [
      (a[bi] + (b[bi] - a[bi]) * t) / scale,
      (a[bi + 1] + (b[bi + 1] - a[bi + 1]) * t) / scale,
    ];
    return out;
  }

  /* --- Rendering ---------------------------------------------------------- */

  let tokens = [];       // SVG groups, index-aligned with the roster
  let tokenLabels = [];  // the <text> inside each home token, or null
  let ballNode = null;
  let clockNode = null;
  let scoreNode = null;
  let possNode = null;
  let conditionRows = new Map();
  let controlsNode = null;
  let feedNode = null;

  function buildPitch() {
    const line = (d) => svg('path', { class: 'pitch-line', d });
    const root_ = svg('svg', {
      class: 'pitch', viewBox: `-3 -3 ${L + 6} ${W + 6}`,
      role: 'img', 'aria-label': 'Live match view',
    },
    svg('rect', { class: 'pitch-bg', x: 0, y: 0, width: L, height: W, rx: 1 }),
    line(`M0,0 H${L} V${W} H0 Z`),
    line(`M${L / 2},0 V${W}`),
    svg('circle', { class: 'pitch-line', cx: L / 2, cy: W / 2, r: 9.15 }),
    line(`M0,${toY(54.16)} H16.5 V${toY(13.84)} H0`),
    line(`M${L},${toY(54.16)} H${L - 16.5} V${toY(13.84)} H${L}`),
    line(`M0,${toY(43.16)} H5.5 V${toY(24.84)} H0`),
    line(`M${L},${toY(43.16)} H${L - 5.5} V${toY(24.84)} H${L}`),
    line(`M0,${toY(37.66)} H-1.8 V${toY(30.34)} H0`),
    line(`M${L},${toY(37.66)} H${L + 1.8} V${toY(30.34)} H${L}`));

    const s = ui.result.summary;
    tokenLabels = [];
    tokens = ui.result.playback.roster.map((r) => {
      const colour = r.side === 'home' ? s.home.colour : s.away.colour;
      // Only the managed side is labelled: twenty-two names on a pitch this
      // size collide into noise, and the opposition's are placeholders anyway.
      const label = r.side === 'home'
        ? svg('text', { y: 3.9, 'text-anchor': 'middle', 'font-size': 2,
          'font-weight': 650, fill: 'var(--text-primary)',
          'paint-order': 'stroke', stroke: 'var(--pitch-fill)',
          'stroke-width': 0.6, text: shortName(r.name) })
        : null;
      tokenLabels.push(label);
      const g = svg('g', { class: 'sim-token' },
        svg('circle', { r: 1.9, fill: colour, stroke: 'rgba(255,255,255,.8)',
          'stroke-width': 0.3 }),
        label);
      root_.appendChild(g);
      return g;
    });

    ballNode = svg('circle', { r: 1.0, fill: '#fff', stroke: '#0b0b0b',
      'stroke-width': 0.25 });
    root_.appendChild(ballNode);
    return root_;
  }

  function shortName(name) {
    const parts = String(name).split(' ');
    return parts.length > 1 ? parts[parts.length - 1] : name;
  }

  function paint() {
    const frame = positionsAt(ui.clock);
    if (!frame) return;
    for (let i = 0; i < tokens.length; i += 1) {
      const [x, y] = frame.players[i];
      tokens[i].setAttribute('transform', `translate(${x.toFixed(2)},${toY(y).toFixed(2)})`);
    }
    if (ballNode && frame.ball) {
      ballNode.setAttribute('cx', frame.ball[0].toFixed(2));
      ballNode.setAttribute('cy', toY(frame.ball[1]).toFixed(2));
    }

    const minute = Math.floor(ui.clock / 60);
    if (clockNode) clockNode.textContent = `${minute}'`;

    const goals = ui.result.summary.goals.filter((g) => g.minute <= minute);
    const h = goals.filter((g) => g.is_own_team).length;
    const a = goals.length - h;
    if (scoreNode) scoreNode.textContent = `${h} — ${a}`;
    if (possNode) {
      possNode.style.width = `${frame.possession === 0 ? 62 : 38}%`;
    }

    paintLabels(minute);
    paintConditions(minute);
    paintFeed(minute);
    paintResultSlot(minute);
  }

  /** A token is a *slot*, not a player: after a substitution the same dot is a
   *  different footballer. Names are resolved from the roster plus every
   *  substitution that has already happened by this minute, so scrubbing
   *  backwards puts the original XI back on the pitch. */
  function paintLabels(minute) {
    const roster = ui.result.playback.roster;
    const subs = ui.result.summary.substitutions;
    const seen = { home: 0, away: 0 };
    for (let i = 0; i < tokenLabels.length; i += 1) {
      const side = roster[i].side;
      const slot = seen[side];             // slot indices restart per side
      seen[side] += 1;
      const label = tokenLabels[i];
      if (!label) continue;
      let name = roster[i].name;
      for (const sub of subs) {
        if (sub.side !== side || sub.slot_index !== slot) continue;
        if (sub.minute <= minute) name = sub.on.name;
      }
      const short = shortName(name);
      if (label.textContent !== short) label.textContent = short;
    }
  }

  /** The full result stays hidden until the match ends — showing it during
   *  playback gives away the score you are watching for. */
  function paintResultSlot(minute) {
    const slot = document.getElementById('sim-result-slot');
    if (!slot) return;
    const s = ui.result.summary;
    const finished = minute >= s.minutes;
    if (slot.dataset.state === (finished ? 'final' : 'live')) return;
    slot.dataset.state = finished ? 'final' : 'live';
    mount(slot, finished
      ? el('div', {}, statsCard(s), playerTableCard(s))
      : note('The full result and player report appear at full time. '
        + 'Use "Skip to end" if you would rather not wait.'));
  }

  function paintConditions(minute) {
    const timeline = ui.result.summary.condition_timeline;
    for (const [id, row] of conditionRows) {
      const on = row.player.came_on_at === null ? 0 : row.player.came_on_at;
      const off = row.player.came_off_at === null ? Infinity : row.player.came_off_at;
      const live = minute >= on && minute < off;
      row.node.style.display = live ? '' : 'none';
      if (!live) continue;

      const points = timeline[id];
      if (!points || !points.length) continue;
      let current = points[0];
      for (const p of points) if (p.minute <= minute) current = p;
      const pct = Math.round(current.condition * 100);
      row.bar.style.width = `${pct}%`;
      row.bar.style.background = pct >= 90 ? 'var(--good)'
        : pct >= 80 ? 'var(--warning)' : 'var(--critical)';
      row.value.textContent = `${pct}%`;
    }
  }

  function paintFeed(minute) {
    if (!feedNode) return;
    const s = ui.result.summary;
    const items = [
      ...s.goals.map((g) => ({ minute: g.minute, kind: 'goal',
        text: `Goal — ${g.is_own_team ? s.home.name : s.away.name}` })),
      ...s.substitutions.map((x) => ({ minute: x.minute, kind: 'sub',
        text: `${x.side === 'home' ? s.home.name : s.away.name}: `
          + `${x.on.name} for ${x.off.name} — ${x.reason}` })),
    ].filter((i) => i.minute <= minute).sort((a, b) => b.minute - a.minute);

    mount(feedNode, ...(items.length
      ? items.map((i) => el('div', { class: 'sim-feed-item' },
        el('span', { class: 'sim-feed-minute', text: `${i.minute}'` }),
        badge(i.kind === 'goal' ? 'Goal' : 'Sub', i.kind === 'goal' ? 'good' : null,
          i.kind === 'goal' ? '⚽' : '⇄'),
        el('span', { text: i.text })))
      : [empty('Nothing yet.')]));
  }

  function updateControls() {
    if (!controlsNode) return;
    mount(controlsNode,
      el('button', {
        class: 'primary', text: ui.playing ? 'Pause' : 'Play',
        onClick: () => (ui.playing ? stop() : play()),
      }),
      el('button', { text: 'Restart', onClick: () => { ui.clock = 0; paint(); play(); } }),
      el('button', { text: 'Skip to end', onClick: () => {
        stop();
        ui.clock = ui.result.summary.minutes * 60;
        paint();
      } }));
  }

  /* --- Layout ------------------------------------------------------------- */

  function draw() {
    conditionRows = new Map();
    mount(container,
      setupCard(),
      ui.error ? note(String(ui.error), 'critical') : null,
      ui.busy ? loading('Playing the fixture') : null,
      ui.result ? resultView() : (ui.busy ? null : empty('Set the fixture up and kick off.')),
    );
    if (ui.result) paint();
  }

  function setupCard() {
    const generated = !ui.awayTeam;
    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'Set up the fixture' }),
        el('span', { class: 'hint', text: options.note })),

      el('div', { class: 'toolbar' },
        el('div', { class: 'field', style: 'min-width:190px' },
          el('label', { text: 'Home' }),
          el('select', { onChange: (e) => { ui.homeTeam = e.target.value; } },
            ...playable.map((t) => el('option', {
              value: t.id, selected: t.id === ui.homeTeam,
              text: `${t.name} (${t.available_players} available)`,
            })))),
        el('div', { class: 'field', style: 'min-width:150px' },
          el('label', { text: 'Home shape' }),
          el('select', { onChange: (e) => { ui.homeFormation = e.target.value; } },
            ...options.formations.map((f) => el('option', {
              value: f, selected: f === ui.homeFormation, text: f,
            })))),

        el('div', { class: 'field', style: 'min-width:190px' },
          el('label', { text: 'Away' }),
          el('select', { onChange: (e) => { ui.awayTeam = e.target.value; draw(); } },
            el('option', { value: '', selected: generated, text: '— generated side —' }),
            ...playable.filter((t) => t.id !== ui.homeTeam).map((t) => el('option', {
              value: t.id, selected: t.id === ui.awayTeam, text: t.name,
            })))),
        el('div', { class: 'field', style: 'min-width:150px' },
          el('label', { text: 'Away shape' }),
          el('select', { onChange: (e) => { ui.awayFormation = e.target.value; } },
            ...options.formations.map((f) => el('option', {
              value: f, selected: f === ui.awayFormation, text: f,
            })))),

        generated ? el('div', { class: 'field', style: 'min-width:150px' },
          el('label', { text: 'Opposition name' }),
          el('input', { value: ui.awayName,
            onChange: (e) => { ui.awayName = e.target.value; } })) : null,
        generated ? el('div', { class: 'field', style: 'min-width:150px' },
          el('label', { text: `Opposition level — ${ui.awayStrength}` }),
          el('input', { type: 'range', min: 55, max: 95, value: ui.awayStrength,
            onInput: (e) => {
              ui.awayStrength = e.target.value;
              e.target.previousSibling.textContent = `Opposition level — ${e.target.value}`;
            } })) : null,

        el('div', { class: 'field', style: 'min-width:110px' },
          el('label', { text: 'Minutes' }),
          el('input', { type: 'number', min: 5, max: 120, value: ui.minutes,
            onChange: (e) => { ui.minutes = e.target.value; } })),
        el('div', { class: 'field', style: 'min-width:130px' },
          el('label', { text: 'Seed' }),
          el('input', { type: 'number', value: ui.seed,
            onChange: (e) => { ui.seed = e.target.value; } })),
        el('div', { class: 'field', style: 'min-width:160px' },
          el('label', { text: 'Playback' }),
          el('select', { onChange: (e) => { ui.mode = e.target.value; } },
            ...Object.entries(MODES).map(([k, m]) => el('option', {
              value: k, selected: k === ui.mode, text: m.label,
            })))),
        el('label', { style: 'display:flex;gap:.4rem;align-items:center;margin:0' },
          el('input', { type: 'checkbox', checked: ui.autoSubs,
            onChange: (e) => { ui.autoSubs = e.target.checked; } }),
          el('span', { text: 'Auto substitutions' })),

        el('button', { class: 'primary', text: ui.busy ? 'Playing…' : 'Kick off',
          disabled: ui.busy, onClick: run })),

      el('p', { style: 'margin:.2rem 0 0;font-size:.78rem;color:var(--text-muted)',
        text: `${MODES[ui.mode].hint}. The engine is deterministic — the same seed `
          + 'replays the same match.' }),
    );
  }

  function resultView() {
    const s = ui.result.summary;
    clockNode = el('span', { class: 'sim-clock', text: "0'" });
    scoreNode = el('span', { class: 'sim-score', text: '0 — 0' });
    possNode = el('div', { class: 'sim-poss-fill' });
    controlsNode = el('div', { class: 'toolbar', style: 'margin:0' });
    feedNode = el('div', { class: 'sim-feed' });
    updateControls();

    return el('div', {},
      ui.result.opponent_is_synthetic ? note(ui.result.note, 'warn') : note(ui.result.note),

      el('div', { class: 'grid two' },
        el('section', { class: 'card' },
          el('header', { style: 'align-items:center' },
            el('span', { class: 'sim-side', style: `--kit:${s.home.colour}` },
              el('span', { class: 'sim-dot' }), s.home.name),
            scoreNode,
            el('span', { class: 'sim-side', style: `--kit:${s.away.colour}` },
              el('span', { class: 'sim-dot' }), s.away.name),
            el('span', { style: 'flex:1' }),
            clockNode),
          el('div', { class: 'sim-poss' }, possNode),
          el('div', { class: 'pitch-wrap' }, buildPitch()),
          controlsNode,
          el('p', { style: 'margin:.5rem 0 0;font-size:.75rem;color:var(--text-muted)',
            text: `${s.home.name} attacking left → right.` })),

        el('div', {},
          el('section', { class: 'card', style: 'margin-bottom:1rem' },
            el('header', {}, el('h2', { text: 'Match feed' })),
            feedNode),
          conditionCard(s))),

      el('div', { id: 'sim-result-slot' }),
    );
  }

  /** Everyone who takes the field at some point gets a row; `paintConditions`
   *  shows each one only for the minutes they were actually on it. Filtering on
   *  the final `on_pitch` flag instead would show the closing XI from kick-off,
   *  which quietly gives away who is going to be substituted. */
  function conditionCard(s) {
    const played = s.players.home.filter(
      (p) => p.minutes_played > 0 || p.came_on_at !== null);
    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: `${s.home.name} — condition` }),
        el('span', { class: 'hint', text: 'Percentage of the player\'s own level' })),
      el('div', { class: 'bars' }, ...played.map((p) => {
        const bar = el('div', { class: 'bar-fill', style: 'width:100%' });
        const value = el('div', { class: 'bar-value', text: '100%' });
        const node = el('div', { class: 'bar-row' },
          el('div', { class: 'bar-label', text: `${p.name} · ${p.position}` }),
          el('div', { class: 'bar-track' }, bar),
          value);
        conditionRows.set(p.id, { bar, value, node, player: p });
        return node;
      })));
  }

  function statsCard(s) {
    return el('div', { class: 'tiles', style: 'margin-top:1rem' },
      tile('Final score', `${s.score[0]} — ${s.score[1]}`,
        { note: `${s.home.name} vs ${s.away.name}` }),
      tile('xG', `${s.xg[0]} — ${s.xg[1]}`, { note: 'Chance quality created' }),
      tile('Shots', `${s.shots[0]} — ${s.shots[1]}`),
      tile('Possession', fmt.num(s.possession_pct, 1), { unit: '%' }),
      tile('Substitutions', s.substitutions.length),
      tile('Seed', s.seed, { note: 'Replays the same match' }),
    );
  }

  function playerTableCard(s) {
    const rows = [...s.players.home].sort(
      (a, b) => b.minutes_played - a.minutes_played);
    return el('section', { class: 'card', style: 'margin-top:1rem' },
      el('header', {},
        el('h2', { text: `${s.home.name} — player report` }),
        ui.result.match_id
          ? el('button', {
            text: 'Analyse this match',
            onClick: () => {
              state.matchId = ui.result.match_id;
              window.location.hash = 'analysis';
            },
          })
          : null),
      table([
        { label: 'Player', get: (p) => p.name },
        { label: 'Pos', get: (p) => p.position },
        { label: 'Mins', num: true, get: (p) => Math.round(p.minutes_played) },
        { label: 'Condition', num: true, get: (p) => fmt.pct(p.condition * 100, 0) },
        { label: 'Fatigue', num: true, get: (p) => fmt.num(p.fatigue, 0) },
        { label: 'Distance', num: true, get: (p) => `${fmt.num(p.distance_km, 1)} km` },
        { label: 'Touches', num: true, get: (p) => p.touches },
        { label: 'Pass %', num: true, get: (p) => fmt.pct(p.pass_accuracy_pct, 0) },
        { label: 'Shots', num: true, get: (p) => p.shots },
        { label: 'Goals', num: true, get: (p) => p.goals },
        {
          label: '',
          get: (p) => (p.came_on_at !== null ? badge(`on ${p.came_on_at}'`, 'good', '↑')
            : p.came_off_at !== null ? badge(`off ${p.came_off_at}'`, 'warn', '↓') : ''),
        },
      ], rows));
  }

  draw();
}
