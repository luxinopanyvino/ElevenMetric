/* TRANSFER MARKET — needs, scored targets, and an affordable signing plan. */

import { api } from '../api.js';
import { barChart, legend, seriesColor, withTableView } from '../charts.js';
import { badge, el, empty, fmt, loading, mount, note, table, tile } from '../ui.js';

export async function render(root, state) {
  mount(root, loading('Loading market'));

  const [needsResp, market, reports] = await Promise.all([
    api.get('/transfers/needs', { team_id: state.teamId }),
    api.get('/transfers/market', { limit: 500 }),
    api.get('/analysis/reports', { limit: 5 }).catch(() => []),
  ]);

  const ui = {
    budget: state.club?.transfer_budget_eur ?? 0,
    wageBudget: state.club?.wage_budget_eur_per_year ?? 0,
    maxSignings: 3,
    minSeverity: 0.18,
    maxAge: '',
    useReport: reports.length ? reports[0].id : '',
    scan: null,
    busy: false,
    error: null,
  };

  const container = el('div', {});
  mount(root, container);

  async function runScan() {
    ui.busy = true; ui.error = null; draw();
    try {
      ui.scan = await api.post('/transfers/scan', {
        team_id: state.teamId,
        budget_eur: Number(ui.budget),
        wage_budget_eur_per_year: Number(ui.wageBudget),
        max_signings: Number(ui.maxSignings),
        min_severity: Number(ui.minSeverity),
        max_age: ui.maxAge === '' ? null : Number(ui.maxAge),
        report_id: ui.useReport || null,
      });
    } catch (err) {
      ui.error = err.detail || err.message;
      ui.scan = null;
    } finally {
      ui.busy = false; draw();
    }
  }

  function draw() {
    mount(container,
      toolbar(),
      ui.error ? note(String(ui.error), 'critical') : null,
      needsCard(),
      ui.busy && !ui.scan ? loading('Scanning the market') : null,
      ui.scan ? el('div', { class: ui.busy ? 'stale' : '' },
        bundleCard(ui.scan), targetsCard(ui.scan)) : null,
      marketCard(),
    );
  }

  function toolbar() {
    return el('div', { class: 'toolbar' },
      el('div', { class: 'field' },
        el('label', { text: 'Transfer budget (€)' }),
        el('input', { type: 'number', min: 0, step: 1000000, value: ui.budget,
          onChange: (e) => { ui.budget = e.target.value; } })),
      el('div', { class: 'field' },
        el('label', { text: 'Wage budget (€/yr)' }),
        el('input', { type: 'number', min: 0, step: 500000, value: ui.wageBudget,
          onChange: (e) => { ui.wageBudget = e.target.value; } })),
      el('div', { class: 'field', style: 'min-width:100px' },
        el('label', { text: 'Max signings' }),
        el('input', { type: 'number', min: 1, max: 8, value: ui.maxSignings,
          onChange: (e) => { ui.maxSignings = e.target.value; } })),
      el('div', { class: 'field', style: 'min-width:100px' },
        el('label', { text: 'Max age' }),
        el('input', { type: 'number', min: 16, max: 40, value: ui.maxAge, placeholder: 'any',
          onChange: (e) => { ui.maxAge = e.target.value; } })),
      el('div', { class: 'field', style: 'min-width:120px' },
        el('label', { text: 'Need threshold' }),
        el('input', { type: 'number', min: 0, max: 1, step: 0.02, value: ui.minSeverity,
          onChange: (e) => { ui.minSeverity = e.target.value; } })),
      reports.length
        ? el('div', { class: 'field', style: 'min-width:200px' },
          el('label', { text: 'Feed in a match report' }),
          el('select', { onChange: (e) => { ui.useReport = e.target.value; } },
            el('option', { value: '', text: 'None' }),
            ...reports.map((r) => el('option', {
              value: r.id, selected: r.id === ui.useReport,
              text: `${r.summary.slice(0, 46)}…`,
            }))))
        : null,
      el('button', { class: 'primary', text: ui.busy ? 'Scanning…' : 'Scan market',
        disabled: ui.busy, onClick: runScan }),
    );
  }

  function needsCard() {
    const needs = ui.scan?.needs ?? needsResp.needs;
    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'Squad needs' }),
        el('span', { class: 'hint', text:
          `Scanned for ${needsResp.formation}: ${needsResp.scanned_positions.join(', ')}` })),
      needs.length
        ? el('div', {},
          barChart(needs.map((n) => ({
            label: `${n.position} — ${n.reason}`,
            value: n.severity * 100,
            display: fmt.pct(n.severity * 100, 0),
            color: n.severity >= 0.6 ? 'var(--critical)'
              : n.severity >= 0.3 ? 'var(--serious)' : seriesColor(0),
          })), { max: 100 }),
          legend([
            { label: 'Urgent (≥60)', color: 'var(--critical)' },
            { label: 'Notable (≥30)', color: 'var(--serious)' },
            { label: 'Marginal', color: seriesColor(0) },
          ]),
          el('div', { style: 'margin-top:.9rem' },
            ...needs.map((n) => el('details', {},
              el('summary', { text: `${n.position} · severity ${fmt.pct(n.severity * 100, 0)} · ${n.reason}` }),
              el('ul', { style: 'font-size:.8rem;color:var(--text-secondary)' },
                ...(n.drivers || []).map((d) => el('li', { text: d })))))))
        : note('No position crosses the need threshold for the shapes this club plays. '
          + 'Lower the threshold to surface marginal upgrades.'),
    );
  }

  function bundleCard(scan) {
    const b = scan.budget || {};
    if (!scan.bundle.length) {
      return el('section', { class: 'card', style: 'margin-bottom:1rem' },
        el('header', {}, el('h2', { text: 'Recommended signings' })),
        note(b.reason || 'No combination fits both budgets.', 'warn'));
    }
    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'Recommended signings' }),
        el('span', { class: 'hint', text: b.method })),
      el('div', { class: 'tiles', style: 'margin-bottom:.9rem' },
        tile('Signings', b.signings),
        tile('Total fee', fmt.money(b.total_fee_eur),
          { note: `of ${fmt.money(b.budget_eur)}` }),
        tile('Annual wages', fmt.money(b.total_wage_eur_per_year),
          { note: `of ${fmt.money(b.wage_budget_eur_per_year)}` }),
        tile('Fee remaining', fmt.money(b.budget_remaining_eur)),
      ),
      el('div', {}, ...scan.bundle.map((t) => el('div', { class: 'rec kind-substitution' },
        el('div', { class: 'rec-head' },
          badge(t.target_position),
          el('span', { class: 'rec-title', text: t.name }),
          el('span', { class: 'rec-meta', text:
            `${fmt.money(t.effective_cost_eur)} fee · ${fmt.money(t.wage_eur_per_year)}/yr · `
            + `score ${fmt.num(t.composite_score, 0)}` })),
        el('ul', {}, ...(t.rationale || []).map((line) => el('li', { text: line })))))),
    );
  }

  function targetsCard(scan) {
    const rows = scan.targets;
    if (!rows.length) {
      return el('section', { class: 'card' },
        el('header', {}, el('h2', { text: 'Scored targets' })),
        empty('No market player improves on the current options at these positions.'));
    }

    const top = rows.slice(0, 12);
    const chart = barChart(top.map((t) => ({
      label: `${t.name} (${t.target_position})`,
      value: t.composite_score,
      display: fmt.num(t.composite_score, 0),
      color: t.selected ? seriesColor(1) : seriesColor(0),
    })), { max: 100 });

    const tbl = table([
      { label: 'Player', get: (t) => t.name },
      { label: 'Club', get: (t) => t.current_club },
      { label: 'Nat. pos', get: (t) => t.primary_position },
      { label: 'For', get: (t) => t.target_position },
      { label: 'Age', num: true, get: (t) => fmt.num(t.age, 1) },
      { label: 'OVR', num: true, get: (t) => fmt.num(t.overall_rating, 0) },
      { label: 'Upgrade', num: true, get: (t) => fmt.signed(t.projected_upgrade, 1) },
      { label: 'Quality', num: true, get: (t) => fmt.num(t.quality_score, 0) },
      { label: 'Fit', num: true, get: (t) => fmt.num(t.fit_score, 0) },
      { label: 'Value', num: true, get: (t) => fmt.num(t.value_score, 0) },
      { label: 'Risk', num: true, get: (t) => fmt.num(t.risk_score, 0) },
      { label: 'Score', num: true, get: (t) => fmt.num(t.composite_score, 0) },
      { label: 'Fee', num: true, get: (t) => fmt.money(t.effective_cost_eur) },
      { label: 'Wage', num: true, get: (t) => fmt.money(t.wage_eur_per_year) },
      { label: '', get: (t) => (t.selected ? badge('In plan', 'good', '✓') : '') },
    ], rows);

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: `Scored targets (${rows.length})` }),
        el('span', { class: 'hint', text:
          'Composite = quality, positional fit and value, minus risk, scaled by need severity.' })),
      withTableView(chart, tbl, { defaultView: 'table' }),
      legend([
        { label: 'Recommended', color: seriesColor(1) },
        { label: 'Scored', color: seriesColor(0) },
      ]));
  }

  function marketCard() {
    return el('section', { class: 'card', style: 'margin-top:1rem' },
      el('header', {},
        el('h2', { text: `Market pool (${market.length})` }),
        el('span', { class: 'hint', text: 'Everything the club has scouted' })),
      market.length
        ? table([
          { label: 'Player', get: (m) => m.name },
          { label: 'Club', get: (m) => m.current_club },
          { label: 'League', get: (m) => `${m.league} (T${m.league_tier})` },
          { label: 'Pos', get: (m) => m.primary_position },
          { label: 'Age', num: true, get: (m) => fmt.num(m.age, 1) },
          { label: 'OVR', num: true, get: (m) => fmt.num(m.overall_rating, 0) },
          { label: 'POT', num: true, get: (m) => fmt.num(m.potential_rating, 0) },
          { label: 'Asking', num: true, get: (m) => fmt.money(m.asking_price_eur) },
          { label: 'Clause', num: true, get: (m) => fmt.money(m.release_clause_eur) },
          { label: 'Wage', num: true, get: (m) => fmt.money(m.wage_demand_eur_per_year) },
          { label: 'Deal odds', num: true, get: (m) => fmt.pct(m.availability * 100, 0) },
        ], [...market].sort((a, b) => b.overall_rating - a.overall_rating))
        : note('The market pool is empty. Import targets via POST /api/v1/transfers/market.'));
  }

  draw();
  await runScan();
}
