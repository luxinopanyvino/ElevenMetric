/* ACADEMY — development tracking and time-to-first-team projections. */

import { api } from '../api.js';
import { barChart, legend, lineChart, seriesColor, withTableView } from '../charts.js';
import { badge, el, empty, fmt, loading, mount, note, table, tile } from '../ui.js';

const PATHWAY_STYLE = {
  promote_now: ['good', '↑'],
  train_with_first_team: ['good', '→'],
  loan_out: ['warn', '↗'],
  continue_academy: [null, '·'],
  review: ['serious', '?'],
  release: ['critical', '↓'],
};

export async function render(root, state) {
  mount(root, loading('Projecting the academy'));

  let review;
  try {
    review = await api.post('/academy/review', { team_id: state.teamId, persist: true });
  } catch (err) {
    mount(root, el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'Academy' })),
      note(String(err.detail || err.message), 'warn'),
      el('p', { text: 'Add academy players and at least three assessments each, six months '
        + 'apart, for a measured growth rate rather than an age prior.' })));
    return;
  }

  const pipeline = await api.get('/academy/pipeline', { team_id: state.teamId, horizon_months: 36 });
  const ui = { selected: review.projections[0]?.academy_player_id || null };

  const container = el('div', {});
  mount(root, container);

  function draw() {
    const s = review.summary;
    const selected = review.projections.find((p) => p.academy_player_id === ui.selected);

    mount(container,
      el('div', { class: 'tiles', style: 'margin-bottom:1rem' },
        tile('Prospects tracked', s.count),
        tile('First-team bar', fmt.num(s.first_team_bar, 1), {
          note: '25th percentile of the senior squad; each prospect is judged '
            + 'against the bar for their own position',
        }),
        tile('Ready within a year',
          (s.ready_windows['0-6m'] || 0) + (s.ready_windows['6-12m'] || 0)),
        tile('No projected path', s.ready_windows['not projected'] || 0, {
          note: 'Ceiling below the first-team bar',
        }),
      ),

      el('div', { class: 'grid two' },
        arrivalsCard(),
        pathwayCard(s)),

      el('div', { class: 'grid two', style: 'margin-top:1rem' },
        trajectoryCard(selected),
        detailCard(selected)),

      projectionsTableCard(),

      s.uncovered_positions?.length
        ? el('section', { class: 'card', style: 'margin-top:1rem' },
          el('header', {},
            el('h2', { text: 'Positions the academy does not cover' }),
            el('span', { class: 'hint', text: 'Nobody projected inside 24 months — the handoff to the transfer desk' })),
          el('div', { class: 'pill-row' },
            ...s.uncovered_positions.map((p) => badge(p, 'warn', '!'))))
        : null,
    );
  }

  function arrival(a) {
    const [kind, icon] = PATHWAY_STYLE[a.pathway] || [null, '·'];
    const active = a.name === selectedName();
    return el('div', {
      class: `bench-item${active ? ' active' : ''}`,
      onClick: () => {
        const match = review.projections.find((p) => p.name === a.name);
        if (match) { ui.selected = match.academy_player_id; draw(); }
      },
    },
    el('span', { class: 'pos', text: a.position }),
    el('span', { class: 'who' },
      el('b', { text: a.name }),
      el('small', { text: fmt.months(a.months) })),
    badge(fmt.title(a.pathway), kind, icon));
  }

  function arrivalWindow(w) {
    return el('div', { style: 'margin-bottom:.8rem' },
      el('h3', { text: w.window, style: 'margin-bottom:.35rem' }),
      el('div', { class: 'bench' }, ...w.arrivals.map(arrival)));
  }

  function arrivalsCard() {
    const windows = pipeline.windows || [];
    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Arrival calendar' }),
        el('span', { class: 'hint', text: 'Next 36 months' })),
      windows.length
        ? el('div', {}, ...windows.map(arrivalWindow))
        : empty('Nobody is projected to reach the first team inside the horizon.'));
  }

  function selectedName() {
    return review.projections.find((p) => p.academy_player_id === ui.selected)?.name;
  }

  function pathwayCard(summary) {
    const entries = Object.entries(summary.by_pathway || {});
    const items = entries.map(([k, v]) => ({
      label: fmt.title(k),
      value: v,
      display: String(v),
      color: k === 'promote_now' || k === 'train_with_first_team'
        ? 'var(--good)' : k === 'release' ? 'var(--critical)' : seriesColor(0),
    }));
    const max = Math.max(...items.map((i) => i.value), 1);

    const tbl = table([
      { label: 'Pathway', get: (i) => i.label },
      { label: 'Players', num: true, get: (i) => i.value },
    ], items);

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Recommended pathways' }),
        el('span', { class: 'hint', text: 'What to do with each prospect now' })),
      withTableView(barChart(items, { max }), tbl),
      legend([
        { label: 'Towards the first team', color: 'var(--good)' },
        { label: 'Continue / review', color: seriesColor(0) },
        { label: 'Release', color: 'var(--critical)' },
      ]));
  }

  function trajectoryCard(p) {
    if (!p) return el('section', { class: 'card' }, empty('Select a prospect.'));

    const chart = lineChart([{
      label: p.name,
      points: p.trajectory.map((t) => ({ x: t.months_ahead, y: t.ability })),
      color: seriesColor(0),
    }], {
      yMin: Math.min(p.adjusted_ability - 5, p.first_team_bar - 8),
      yMax: Math.max(p.potential_ability + 3, p.first_team_bar + 3),
      yFormat: (v) => v.toFixed(0),
      xFormat: (v) => `${v}m`,
      reference: { value: p.first_team_bar, label: `bar at ${p.position}` },
    });

    const tbl = table([
      { label: 'Months ahead', num: true, get: (t) => t.months_ahead },
      { label: 'Projected ability', num: true, get: (t) => fmt.num(t.ability, 1) },
    ], p.trajectory);

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: `${p.name} — development curve` }),
        el('span', { class: 'hint', text: `${p.position} · age ${fmt.num(p.age, 1)}` })),
      withTableView(chart, tbl));
  }

  function detailCard(p) {
    if (!p) return el('section', { class: 'card' }, empty('Select a prospect.'));
    const [kind, icon] = PATHWAY_STYLE[p.pathway] || [null, '·'];

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Assessment' }),
        badge(fmt.title(p.pathway), kind, icon)),

      el('div', { class: 'tiles', style: 'margin-bottom:.9rem' },
        tile('Time to first team', fmt.months(p.months_to_first_team), {
          note: p.projected_ready_on ? `around ${fmt.date(p.projected_ready_on)}` : null,
        }),
        tile('Readiness', fmt.num(p.readiness_score, 0), {
          unit: '/100', note: `bar at ${p.position}: ${fmt.num(p.first_team_bar, 1)}`,
        }),
        tile('Current → ceiling',
          `${fmt.num(p.adjusted_ability, 0)} → ${fmt.num(p.potential_ability, 0)}`, {
          note: `${fmt.pct(p.ceiling_reached_pct, 0)} of ceiling reached`,
        }),
        tile('Growth', fmt.signed(p.growth_rate_per_year, 1), {
          unit: ' pts/yr', note: `confidence ${fmt.pct(p.confidence * 100, 0)}`,
        }),
      ),

      p.adjusted_ability !== p.current_ability
        ? note(`Raw assessment ${fmt.num(p.current_ability, 1)}, adjusted to `
          + `${fmt.num(p.adjusted_ability, 1)} for biological age and level of competition.`)
        : null,

      p.drivers?.length
        ? el('div', {},
          el('h3', { text: 'Drivers', style: 'margin-bottom:.35rem' }),
          el('ul', { style: 'margin:0;padding-left:1.1rem;font-size:.82rem;color:var(--text-secondary)' },
            ...p.drivers.map((d) => el('li', { text: d }))))
        : null,

      p.warnings?.length
        ? el('div', { style: 'margin-top:.7rem' },
          ...p.warnings.map((w) => note(w, 'warn')))
        : null,
    );
  }

  function projectionsTableCard() {
    return el('section', { class: 'card', style: 'margin-top:1rem' },
      el('header', {},
        el('h2', { text: 'All prospects' }),
        el('span', { class: 'hint', text: 'Ready soonest first' })),
      table([
        { label: 'Player', get: (p) => p.name },
        { label: 'Pos', get: (p) => p.position },
        { label: 'Age', num: true, get: (p) => fmt.num(p.age, 1) },
        { label: 'Ability', num: true, get: (p) => fmt.num(p.adjusted_ability, 1) },
        { label: 'Ceiling', num: true, get: (p) => fmt.num(p.potential_ability, 0) },
        { label: 'Growth/yr', num: true, get: (p) => fmt.signed(p.growth_rate_per_year, 1) },
        { label: 'Readiness', num: true, get: (p) => fmt.num(p.readiness_score, 0) },
        { label: 'Time to XI', get: (p) => fmt.months(p.months_to_first_team) },
        { label: 'Ready on', get: (p) => fmt.date(p.projected_ready_on) },
        {
          label: 'Pathway',
          get: (p) => {
            const [kind, icon] = PATHWAY_STYLE[p.pathway] || [null, '·'];
            return badge(fmt.title(p.pathway), kind, icon);
          },
        },
        { label: 'Confidence', num: true, get: (p) => fmt.pct(p.confidence * 100, 0) },
      ], review.projections, {
        onRowClick: (p) => { ui.selected = p.academy_player_id; draw(); },
      }));
  }

  draw();
}
