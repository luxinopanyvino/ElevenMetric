/* SQUAD · FORMATIONS · ROLES — the teamsheet view. */

import { api } from '../api.js';
import { barChart, legend, pitchView, seriesColor, withTableView } from '../charts.js';
import { badge, el, empty, fmt, loading, mount, note, table, tile } from '../ui.js';

export async function render(root, state) {
  mount(root, loading('Loading squad'));

  const [players, compare] = await Promise.all([
    api.get('/players', { team_id: state.teamId, limit: 200 }),
    api.get('/lineups/formations/compare', { team_id: state.teamId, top_n: 6 }),
  ]);

  const ui = {
    formation: state.formation || compare.ranking[0]?.formation || '4-3-3',
    ignoreLoad: false,
    locked: {},
    selected: null,
  };

  const container = el('div', {});
  mount(root, container);

  async function draw() {
    const xi = await api.post('/lineups/best-xi', {
      team_id: state.teamId,
      formation: ui.formation,
      ignore_load: ui.ignoreLoad,
      locked: ui.locked,
      bench_size: 9,
    });

    const unavailable = players.filter((p) => !p.is_available);
    const kit = state.team?.primary_color || 'var(--series-1)';

    mount(container,
      toolbar(),
      unavailable.length ? note(
        `${unavailable.length} player(s) unavailable and excluded from selection: `
        + unavailable.map((p) => p.display_name).join(', '), 'warn') : null,

      el('div', { class: 'grid two' },
        el('section', { class: 'card' },
          el('header', {},
            el('h2', { text: `${ui.formation} — best available XI` }),
            el('span', { class: 'hint', text: ui.ignoreLoad
              ? 'Ranked on raw quality (load ignored)'
              : 'Ranked on today\'s condition — fatigue and fitness applied' })),
          el('div', { class: 'pitch-wrap' },
            pitchView(xi.slots, {
              kitColor: kit,
              selectedId: ui.selected,
              onSelect: (s) => { ui.selected = s.player_id; draw(); },
            })),
          xi.warnings.length
            ? el('div', { style: 'margin-top:.7rem' },
              ...xi.warnings.map((w) => note(w, 'warn')))
            : null,
        ),

        el('div', {},
          el('section', { class: 'card', style: 'margin-bottom:1rem' },
            el('header', {}, el('h2', { text: 'Selection quality' })),
            el('div', { class: 'tiles' },
              tile('Mean effective level', fmt.num(xi.mean_effective_level, 1),
                { note: 'Rating after positional fit, fatigue and fitness' }),
              tile('Out of position', xi.balance.out_of_position_count ?? 0,
                { note: 'Players below an 80% positional fit' }),
              tile('Avg pace', fmt.num(xi.balance.avg_pace, 0)),
              tile('Left / right footed',
                `${xi.balance.left_footed ?? 0} / ${xi.balance.right_footed ?? 0}`),
            )),

          el('section', { class: 'card' },
            el('header', {},
              el('h2', { text: 'Bench' }),
              el('span', { class: 'hint', text: 'Ordered by effective level' })),
            xi.bench.length
              ? el('div', { class: 'bench' }, ...xi.bench.map(benchItem))
              : empty('Everyone available is in the XI.')),
        )),

      el('section', { class: 'card', style: 'margin-top:1rem' },
        el('header', {},
          el('h2', { text: 'Which shape does this squad actually fill?' }),
          el('span', { class: 'hint', text:
            'The best formation is the one the roster fills, not the one that sounds most modern.' })),
        formationComparison(compare.ranking)),

      el('section', { class: 'card', style: 'margin-top:1rem' },
        el('header', {}, el('h2', { text: `Squad (${players.length})` })),
        squadTable(players)),
    );
  }

  function toolbar() {
    return el('div', { class: 'toolbar' },
      el('div', { class: 'field' },
        el('label', { text: 'Formation' }),
        el('select', {
          onChange: (ev) => { ui.formation = ev.target.value; draw(); },
        }, ...compare.known_formations.map((f) =>
          el('option', { value: f, selected: f === ui.formation, text: f })))),
      el('div', { class: 'field' },
        el('label', { text: 'Selection basis' }),
        el('select', {
          onChange: (ev) => { ui.ignoreLoad = ev.target.value === 'quality'; draw(); },
        },
        el('option', { value: 'today', selected: !ui.ignoreLoad, text: 'Today (load applied)' }),
        el('option', { value: 'quality', selected: ui.ignoreLoad, text: 'Raw quality' }))),
      Object.keys(ui.locked).length
        ? el('button', { text: 'Clear locked slots', onClick: () => { ui.locked = {}; draw(); } })
        : null,
    );
  }

  function benchItem(b) {
    return el('div', { class: 'bench-item' },
      el('span', { class: 'pos', text: b.position }),
      el('span', { class: 'who' },
        el('b', { text: b.player }),
        el('small', { text: `fitness ${fmt.num(b.fitness, 0)}% · eff ${fmt.num(b.effective_level, 1)}` })),
      el('span', { class: 'ovr', text: Math.round(b.overall_rating) }),
    );
  }

  function formationComparison(ranking) {
    const chart = barChart(ranking.map((r) => ({
      label: r.formation,
      value: r.mean_effective_level,
      display: fmt.num(r.mean_effective_level, 1),
      // Colour follows the entity: the currently selected shape is highlighted,
      // everything else is a single recessive slot.
      color: r.formation === ui.formation ? seriesColor(0) : 'var(--axis)',
    })), { max: Math.max(...ranking.map((r) => r.mean_effective_level)) * 1.05 });

    const tbl = table([
      { label: 'Formation', get: (r) => r.formation },
      { label: 'Mean effective level', num: true, get: (r) => fmt.num(r.mean_effective_level, 2) },
      { label: 'Total', num: true, get: (r) => fmt.num(r.total_effective_level, 1) },
      { label: 'Out of position', num: true, get: (r) => r.out_of_position_count },
    ], ranking);

    return el('div', {},
      withTableView(chart, tbl),
      legend([
        { label: `${ui.formation} (selected)`, color: seriesColor(0) },
        { label: 'Alternatives', color: 'var(--axis)' },
      ]));
  }

  function squadTable(list) {
    const rows = [...list].sort((a, b) => b.overall_rating - a.overall_rating);
    return table([
      { label: '#', num: true, get: (p) => p.shirt_number ?? '—' },
      { label: 'Player', get: (p) => p.display_name },
      { label: 'Pos', get: (p) => p.primary_position },
      { label: 'Age', num: true, get: (p) => fmt.num(p.age, 1) },
      { label: 'OVR', num: true, get: (p) => Math.round(p.overall_rating) },
      { label: 'POT', num: true, get: (p) => Math.round(p.potential_rating) },
      { label: 'Fitness', num: true, get: (p) => fmt.pct(p.fitness, 0) },
      { label: 'Fatigue', num: true, get: (p) => fmt.pct(p.fatigue, 0) },
      { label: "Mins (7d)", num: true, get: (p) => p.minutes_last_7d },
      { label: 'Value', num: true, get: (p) => fmt.money(p.market_value_eur) },
      {
        label: 'Status',
        get: (p) => (p.is_available
          ? badge('Available', 'good', '✓')
          : badge('Unavailable', 'critical', '✕')),
      },
    ], rows);
  }

  await draw();
}
