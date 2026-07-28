/* TACTICS · ANALYSIS — heatmaps, possession, tactical profile, proposals. */

import { api } from '../api.js';
import {
  barChart, heatOverlay, legend, lineChart, pitchView, scaleLegend,
  seriesColor, withTableView, zoneOverlay,
} from '../charts.js';
import { badge, el, empty, fmt, loading, mount, note, table, tile } from '../ui.js';

export async function render(root, state) {
  mount(root, loading('Loading matches'));

  const matches = await api.get('/matches', { team_id: state.teamId });
  if (!matches.length) {
    mount(root, el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'No matches yet' })),
      note('Analysis needs match data. Create a match, then ingest an event feed, '
        + 'tracking frames, or upload video. See the Data tab for the full input contract.')));
    return;
  }

  const ui = {
    matchId: state.matchId || matches[0].id,
    minute: 70,
    scoreDiff: 0,
    subsUsed: 0,
    overlay: 'heat',
    heatPlayer: 'team',
    report: null,
    busy: false,
  };

  const container = el('div', {});
  mount(root, container);

  async function run() {
    ui.busy = true;
    draw();
    try {
      ui.report = await api.post('/analysis/match', {
        match_id: ui.matchId,
        minute: ui.minute,
        score_difference: ui.scoreDiff,
        subs_used: ui.subsUsed,
      });
      ui.error = null;
    } catch (err) {
      ui.error = err.detail || err.message;
      ui.report = null;
    } finally {
      ui.busy = false;
      draw();
    }
  }

  function draw() {
    const r = ui.report;
    mount(container,
      toolbar(),
      ui.error ? note(String(ui.error), 'critical') : null,
      !r
        ? (ui.busy ? loading('Analysing') : empty('Run the analysis to see results.'))
        : el('div', { class: ui.busy ? 'stale' : '' },
          confidenceBanner(r),
          el('div', { class: 'tiles', style: 'margin-bottom:1rem' }, ...possessionTiles(r)),
          el('div', { class: 'grid two' }, pitchCard(r), tacticsCard(r)),
          el('div', { class: 'grid two', style: 'margin-top:1rem' },
            possessionTimelineCard(r), vulnerabilitiesCard(r)),
          recommendationsCard(r),
          playerMetricsCard(r),
        ),
    );
  }

  function toolbar() {
    return el('div', { class: 'toolbar' },
      el('div', { class: 'field', style: 'min-width:230px' },
        el('label', { text: 'Match' }),
        el('select', { onChange: (e) => { ui.matchId = e.target.value; ui.report = null; draw(); } },
          ...matches.map((m) => el('option', {
            value: m.id, selected: m.id === ui.matchId,
            text: `${m.competition} · vs ${m.opponent_name} (${m.goals_for}-${m.goals_against})`,
          })))),
      el('div', { class: 'field', style: 'min-width:90px' },
        el('label', { text: 'As at minute' }),
        el('input', {
          type: 'number', min: 1, max: 120, value: ui.minute,
          onChange: (e) => { ui.minute = Number(e.target.value); },
        })),
      el('div', { class: 'field', style: 'min-width:110px' },
        el('label', { text: 'Score difference' }),
        el('input', {
          type: 'number', min: -5, max: 5, value: ui.scoreDiff,
          onChange: (e) => { ui.scoreDiff = Number(e.target.value); },
        })),
      el('div', { class: 'field', style: 'min-width:90px' },
        el('label', { text: 'Subs used' }),
        el('input', {
          type: 'number', min: 0, max: 5, value: ui.subsUsed,
          onChange: (e) => { ui.subsUsed = Number(e.target.value); },
        })),
      el('button', { class: 'primary', text: ui.busy ? 'Analysing…' : 'Run analysis',
        disabled: ui.busy, onClick: run }),
    );
  }

  function confidenceBanner(r) {
    const missing = ['event_data', 'tracking'].filter((k) => !r.inputs_used.includes(k));
    return el('div', {},
      el('div', { class: 'pill-row', style: 'margin-bottom:.7rem' },
        badge(`Data completeness ${fmt.pct(r.data_completeness * 100, 0)}`,
          r.data_completeness >= 0.7 ? 'good' : 'warn', r.data_completeness >= 0.7 ? '✓' : '!'),
        badge(`Confidence ${fmt.pct(r.confidence * 100, 0)}`,
          r.confidence >= 0.7 ? 'good' : 'warn', r.confidence >= 0.7 ? '✓' : '!'),
        ...r.inputs_used.map((i) => badge(fmt.title(i))),
      ),
      missing.length
        ? note(`Not available for this match: ${missing.map(fmt.title).join(', ')}. `
          + 'Metrics that need them are reported as "not available" rather than estimated.', 'warn')
        : null,
      r.summary ? el('p', { style: 'color:var(--text-secondary)', text: r.summary }) : null,
    );
  }

  function possessionTiles(r) {
    const p = r.possession || {};
    return [
      tile('Time possession', fmt.num(p.time_possession_pct, 1), {
        unit: '%', na: p.time_possession_pct === null || p.time_possession_pct === undefined,
        note: 'Needs tracking or a continuous ball signal',
      }),
      tile('Pass possession', fmt.num(p.pass_possession_pct, 1), {
        unit: '%', na: p.pass_possession_pct === null || p.pass_possession_pct === undefined,
        note: 'Share of completed passes',
      }),
      tile('Field tilt', fmt.num(p.field_tilt_pct, 1), {
        unit: '%', na: p.field_tilt_pct === null || p.field_tilt_pct === undefined,
        note: 'Share of final-third touches — the best territory proxy',
      }),
      tile('PPDA', fmt.num(p.ppda, 1), {
        na: p.ppda === null || p.ppda === undefined,
        note: 'Opponent passes per defensive action. Lower = more aggressive',
      }),
      tile('xG created', fmt.num(r.tactics?.offensive?.xg_created, 2), {
        note: `${r.tactics?.offensive?.shots ?? 0} shots`,
      }),
      tile('xG conceded', fmt.num(r.tactics?.defensive?.xg_conceded, 2), {
        note: `${r.tactics?.defensive?.shots_conceded ?? 0} shots faced`,
      }),
    ];
  }

  function pitchCard(r) {
    const heat = r.heatmaps || {};
    const players = heat.players || {};
    const playerIds = Object.keys(players);

    let overlayNode = null;
    let legendNode = null;

    if (ui.overlay === 'heat') {
      const source = ui.heatPlayer === 'team' ? heat.team : players[ui.heatPlayer];
      overlayNode = source ? heatOverlay(source.grid) : null;
      legendNode = scaleLegend('Presence', 'low', 'high');
    } else {
      overlayNode = zoneOverlay(r.zones?.control);
      legendNode = scaleLegend('Zone control', 'opponent', 'us', { diverging: true });
    }

    const slots = ui.overlay === 'heat' && ui.heatPlayer !== 'team' && players[ui.heatPlayer]
      ? [{
        x: players[ui.heatPlayer].centroid[0] / 105,
        y: players[ui.heatPlayer].centroid[1] / 68,
        position: 'AVG', player: 'Average position',
        player_id: ui.heatPlayer,
      }]
      : [];

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: ui.overlay === 'heat' ? 'Heatmap' : 'Zone control' }),
        el('span', { class: 'hint', text: heat.note || `Source: ${heat.source || '—'}` }),
        el('span', { class: 'spacer', style: 'flex:1' }),
        el('div', { class: 'view-toggle' },
          el('button', {
            text: 'Heatmap', 'aria-pressed': String(ui.overlay === 'heat'),
            onClick: () => { ui.overlay = 'heat'; draw(); },
          }),
          el('button', {
            text: 'Zones', 'aria-pressed': String(ui.overlay === 'zones'),
            onClick: () => { ui.overlay = 'zones'; draw(); },
          }))),

      ui.overlay === 'heat' && playerIds.length
        ? el('div', { class: 'field', style: 'margin-bottom:.6rem;max-width:260px' },
          el('label', { text: 'Subject' }),
          el('select', { onChange: (e) => { ui.heatPlayer = e.target.value; draw(); } },
            el('option', { value: 'team', selected: ui.heatPlayer === 'team', text: 'Whole team' }),
            ...playerIds.map((id) => el('option', {
              value: id, selected: id === ui.heatPlayer,
              text: state.playerName(id) || id.slice(0, 8),
            }))))
        : null,

      el('div', { class: 'pitch-wrap' },
        pitchView(slots, { overlay: overlayNode, kitColor: 'var(--series-2)' })),
      legendNode,
      el('p', { style: 'font-size:.75rem;color:var(--text-muted);margin-top:.5rem',
        text: 'Attacking left → right.' }),
    );
  }

  function tacticsCard(r) {
    const t = r.tactics || {};
    const d = t.defensive || {}; const o = t.offensive || {};
    const items = [
      { label: 'Press height', value: d.press_intensity_index ?? 50 },
      { label: 'Defensive line', value: d.line_height_index ?? 50 },
      { label: 'Compactness', value: d.compactness_index ?? 50 },
      { label: 'Directness', value: o.directness_index ?? 50 },
      { label: 'Width', value: o.width_index ?? 50 },
      { label: 'Short build-up', value: o.build_up_index ?? 50 },
    ].map((i) => ({ ...i, display: fmt.num(i.value, 0), color: seriesColor(0) }));

    const tbl = table([
      { label: 'Index', get: (i) => i.label },
      { label: 'Value (0-100)', num: true, get: (i) => fmt.num(i.value, 1) },
    ], items);

    const shape = r.formation || {};
    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Tactical profile' }),
        el('span', { class: 'hint', text: t.identity || '—' })),

      el('div', { class: 'tiles', style: 'margin-bottom:.9rem' },
        tile('Played shape', shape.formation || '—', {
          note: shape.deviation?.declared
            ? `Declared ${shape.deviation.declared}` : 'No teamsheet to compare',
        }),
        tile('Vertical compactness', fmt.num(shape.vertical_compactness, 1), { unit: ' m' }),
        tile('Defensive line height', fmt.num(shape.defensive_line_height, 1), { unit: ' m' }),
        tile('Counterpress recovery', fmt.num(d.counterpress_recovery_pct, 1), {
          unit: '%', na: d.counterpress_recovery_pct === null
            || d.counterpress_recovery_pct === undefined,
          note: 'Losses recovered within 5 s',
        })),

      shape.deviation && shape.deviation.note
        ? note(shape.deviation.note, shape.deviation.matches ? null : 'warn')
        : null,

      withTableView(barChart(items), tbl),

      d.leak_zones?.length
        ? el('div', { style: 'margin-top:1rem' },
          el('h3', { text: 'Threat conceded by lane', style: 'margin-bottom:.5rem' }),
          barChart(d.leak_zones.map((z) => ({
            label: z.lane,
            value: z.share_pct,
            display: fmt.pct(z.share_pct, 0),
            color: z.share_pct >= 32 ? 'var(--critical)' : seriesColor(0),
          })), { max: 100 }),
          legend([
            { label: 'Within normal range', color: seriesColor(0) },
            { label: 'Concentration (≥32%)', color: 'var(--critical)' },
          ]))
        : null,
    );
  }

  function possessionTimelineCard(r) {
    const timeline = r.possession?.timeline || [];
    if (!timeline.length) {
      return el('section', { class: 'card' },
        el('header', {}, el('h2', { text: 'Possession over time' })),
        empty('Needs an event feed with timestamps.'));
    }

    const chart = lineChart([{
      label: 'Possession',
      points: timeline.map((b) => ({ x: b.from_minute, y: b.possession_pct })),
      color: seriesColor(0),
    }], {
      yMin: 0, yMax: 100, yFormat: (v) => `${v.toFixed(0)}%`,
      xFormat: (v) => `${Math.round(v)}'`,
      reference: { value: 50, label: 'even' },
    });

    const tbl = table([
      { label: 'From', num: true, get: (b) => `${b.from_minute}'` },
      { label: 'To', num: true, get: (b) => `${b.to_minute}'` },
      { label: 'Possession', num: true, get: (b) => fmt.pct(b.possession_pct, 1) },
      { label: 'Touches', num: true, get: (b) => b.touches },
    ], timeline);

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Possession over time' }),
        el('span', { class: 'hint', text: '5-minute buckets, by touch share' })),
      withTableView(chart, tbl));
  }

  function vulnerabilitiesCard(r) {
    const vulns = r.tactics?.vulnerabilities || [];
    const strengths = r.tactics?.strengths || [];
    return el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'Diagnosis' })),
      el('h3', { text: 'Exposures', style: 'margin-bottom:.4rem' }),
      vulns.length
        ? el('div', {}, ...vulns.map((v) => el('div', { class: 'rec kind-instruction_change' },
          el('div', { class: 'rec-head' },
            el('span', { class: 'rec-title', text: v.title }),
            el('span', { class: 'rec-meta', text: `severity ${Math.round(v.severity)}` })),
          el('p', { style: 'margin:.3rem 0 0;font-size:.8rem;color:var(--text-secondary)',
            text: v.detail }))))
        : empty('No exposure crossed its threshold in this sample.'),
      el('h3', { text: 'Strengths', style: 'margin:.9rem 0 .4rem' }),
      strengths.length
        ? el('ul', { style: 'margin:0;padding-left:1.1rem;font-size:.82rem;color:var(--text-secondary)' },
          ...strengths.map((s) => el('li', { text: `${s.title} (${Math.round(s.score)})` })))
        : empty('Nothing stood out as a strength in this sample.'),
    );
  }

  function recommendationsCard(r) {
    const recs = r.recommendations || [];
    return el('section', { class: 'card', style: 'margin-top:1rem' },
      el('header', {},
        el('h2', { text: `Proposals (${recs.length})` }),
        el('span', { class: 'hint', text: 'Ranked by priority. Every claim carries its numbers.' })),
      recs.length
        ? el('div', {}, ...recs.map(recCard))
        : empty('No proposal cleared the confidence threshold for this state.'));
  }

  function recCard(rec) {
    return el('div', { class: `rec kind-${rec.kind}` },
      el('div', { class: 'rec-head' },
        badge(fmt.title(rec.kind)),
        el('span', { class: 'rec-title', text: rec.title }),
        el('span', { class: 'rec-meta', text:
          `priority ${Math.round(rec.priority)} · confidence ${fmt.pct(rec.confidence * 100, 0)}`
          + (rec.minute_window ? ` · minute ${rec.minute_window}` : '')
          + (rec.expected_gain ? ` · ${fmt.signed(rec.expected_gain)} ${rec.expected_gain_unit}` : '') })),
      rec.drivers?.length
        ? el('ul', {}, ...rec.drivers.map((d) => el('li', { text: d })))
        : (rec.detail ? el('p', { style: 'margin:.3rem 0 0;font-size:.82rem', text: rec.detail }) : null),
      rec.evidence && Object.keys(rec.evidence).length
        ? el('details', {},
          el('summary', { text: 'Evidence' }),
          el('pre', { text: JSON.stringify(rec.evidence, null, 2) }))
        : null,
    );
  }

  function playerMetricsCard(r) {
    const metrics = r.player_metrics || {};
    const rows = Object.entries(metrics).map(([id, m]) => ({
      ...m, id, name: state.playerName(id) || id.slice(0, 8),
    })).sort((a, b) => b.xt - a.xt);

    if (!rows.length) {
      return el('section', { class: 'card', style: 'margin-top:1rem' },
        el('header', {}, el('h2', { text: 'Player contributions' })),
        empty('Per-player metrics need event data with player ids.'));
    }

    return el('section', { class: 'card', style: 'margin-top:1rem' },
      el('header', {},
        el('h2', { text: 'Player contributions' }),
        el('span', { class: 'hint', text: 'Sorted by threat created (xT)' })),
      table([
        { label: 'Player', get: (m) => m.name },
        { label: 'Touches', num: true, get: (m) => m.touches },
        { label: 'Passes', num: true, get: (m) => m.passes },
        { label: 'Pass %', num: true, get: (m) => fmt.pct(m.pass_accuracy_pct, 0) },
        { label: 'Prog. passes', num: true, get: (m) => m.progressive_passes },
        { label: 'Prog. carries', num: true, get: (m) => m.progressive_carries },
        { label: 'Shots', num: true, get: (m) => m.shots },
        { label: 'xG', num: true, get: (m) => fmt.num(m.xg, 2) },
        { label: 'xT', num: true, get: (m) => fmt.num(m.xt, 3) },
        { label: 'Def. actions', num: true, get: (m) => m.defensive_actions },
      ], rows));
  }

  await run();
}
