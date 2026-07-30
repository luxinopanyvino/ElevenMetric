/* INGEST — the three ways data gets into the system: CSV, by hand, or video. */

import { api } from '../api.js';
import { badge, el, empty, fmt, loading, mount, note, table, tile } from '../ui.js';

const PANELS = [
  { id: 'csv', label: 'CSV import' },
  { id: 'manual', label: 'Manual entry' },
  { id: 'video', label: 'Video' },
];

export async function render(root, state) {
  mount(root, loading('Loading the import tools'));

  const [meta, teams, matches, cv, overview] = await Promise.all([
    api.get('/ingest/datasets'),
    api.get('/teams'),
    api.get('/matches', { team_id: state.teamId }).catch(() => []),
    api.get('/video/capabilities'),
    api.get('/meta/overview'),
  ]);

  const ui = {
    panel: 'csv',
    dataset: meta.datasets[0].key,
    // The chosen file and context live in state, not in the DOM: every redraw
    // recreates the <input type="file">, which silently drops the selection and
    // would make the Import button fail after a successful preview.
    file: null,
    ctx: {
      team_id: state.teamId,
      match_id: matches[0]?.id || '',
      provider: 'elevenmetric',
      target_hz: 5,
    },
    preview: null,
    busy: false,
    error: null,
    result: null,
    allowPartial: false,
    replaceExisting: false,
    editing: null,
    showDetail: false,
    job: null,
    poll: null,
  };

  const container = el('div', {});
  mount(root, container);

  const dataset = () => meta.datasets.find((d) => d.key === ui.dataset);

  function draw() {
    mount(container,
      el('div', { class: 'tiles', style: 'margin-bottom:1rem' },
        tile('Players', overview.players),
        tile('Academy', overview.academy_players),
        tile('Matches', overview.matches),
        tile('Events', fmt.int(overview.events)),
        tile('Tracking frames', fmt.int(overview.tracking_frames)),
      ),

      el('div', { class: 'view-toggle', style: 'margin-bottom:1rem' },
        ...PANELS.map((p) => el('button', {
          text: p.label, 'aria-pressed': String(ui.panel === p.id),
          onClick: () => { ui.panel = p.id; ui.error = null; draw(); },
        }))),

      ui.error ? note(String(ui.error), 'critical') : null,

      ui.panel === 'csv' ? csvPanel()
        : ui.panel === 'manual' ? manualPanel()
          : videoPanel(),
    );
  }

  /* --- CSV ---------------------------------------------------------------- */

  function csvPanel() {
    const ds = dataset();
    const needsTeam = ds.context.includes('team_id');
    const needsMatch = ds.context.includes('match_id');
    const needsProvider = ds.context.includes('provider');

    return el('div', {},
      el('section', { class: 'card', style: 'margin-bottom:1rem' },
        el('header', {},
          el('h2', { text: 'Import a CSV' }),
          el('span', { class: 'hint', text: meta.note })),

        el('div', { class: 'toolbar' },
          el('div', { class: 'field', style: 'min-width:220px' },
            el('label', { text: 'What are you importing?' }),
            el('select', {
              onChange: (e) => { ui.dataset = e.target.value; ui.preview = null; ui.result = null; draw(); },
            }, ...meta.datasets.map((d) => el('option', {
              value: d.key, selected: d.key === ui.dataset, text: d.label,
            })))),

          needsTeam ? el('div', { class: 'field', style: 'min-width:200px' },
            el('label', { text: 'Assign to team' }),
            el('select', { id: 'ing-team', value: ui.ctx.team_id,
              onChange: (e) => { ui.ctx.team_id = e.target.value; } },
              ...teams.map((t) => el('option', {
                value: t.id, selected: t.id === state.teamId, text: t.name,
              })))) : null,

          needsMatch ? el('div', { class: 'field', style: 'min-width:230px' },
            el('label', { text: 'Match' }),
            matches.length
              ? el('select', { id: 'ing-match',
                onChange: (e) => { ui.ctx.match_id = e.target.value; } },
                ...matches.map((m) => el('option', {
                selected: m.id === ui.ctx.match_id,
                value: m.id, text: `${m.competition} · vs ${m.opponent_name}`,
              })))
              : el('input', { id: 'ing-match', placeholder: 'No matches yet', disabled: true }))
            : null,

          needsProvider ? el('div', { class: 'field', style: 'min-width:170px' },
            el('label', { text: 'Coordinate frame' }),
            el('select', { id: 'ing-provider',
              onChange: (e) => { ui.ctx.provider = e.target.value; } },
              ...meta.providers.map((p) => el('option', {
                value: p, selected: p === ui.ctx.provider, text: p,
              })))) : null,

          ds.context.includes('target_hz') ? el('div', { class: 'field', style: 'min-width:120px' },
            el('label', { text: 'Store at (Hz)' }),
            el('input', { id: 'ing-hz', type: 'number', min: 1, max: 25,
              value: ui.ctx.target_hz,
              onChange: (e) => { ui.ctx.target_hz = e.target.value; } })) : null,
        ),

        el('p', { style: 'color:var(--text-secondary);font-size:.84rem', text: ds.description }),
        ds.note ? note(ds.note) : null,

        el('div', { class: 'toolbar' },
          el('div', { class: 'field' },
            el('label', { text: ui.file ? `File — ${ui.file.name}` : 'File' }),
            el('input', {
              type: 'file', id: 'ing-file', accept: '.csv,text/csv',
              onChange: (e) => {
                ui.file = e.target.files?.[0] || null;
                ui.preview = null;
                ui.result = null;
                draw();
              },
            })),
          el('button', {
            class: 'primary', text: ui.busy ? 'Checking…' : 'Check the file',
            disabled: ui.busy || !ui.file, onClick: runPreview,
          }),
          el('button', {
            text: 'Download a template',
            onClick: () => downloadTemplate(ds.key),
          })),

        columnsHelp(ds),
      ),

      ui.preview ? previewCard() : null,
      ui.result ? resultCard() : null,
    );
  }

  function columnsHelp(ds) {
    const required = ds.columns.filter((c) => c.required);
    const optional = ds.columns.filter((c) => !c.required && !c.attribute);
    const attrs = ds.columns.filter((c) => c.attribute);

    return el('details', { style: 'margin-top:.6rem' },
      el('summary', { style: 'cursor:pointer;font-size:.82rem;color:var(--text-secondary)',
        text: `Columns — ${required.length} required, ${optional.length} optional`
          + (attrs.length ? `, ${attrs.length} attribute` : '') }),
      el('div', { style: 'margin-top:.6rem' },
        table([
          { label: 'Column', get: (c) => el('code', { text: c.name }) },
          { label: 'Type', get: (c) => c.type },
          { label: '', get: (c) => (c.required ? badge('Required', 'critical', '●') : badge('Optional')) },
          { label: 'What it does', get: (c) => c.description },
          { label: 'Also accepts', get: (c) => (c.aliases.length ? c.aliases.join(', ') : '—') },
          { label: 'Example', get: (c) => (c.example ? el('code', { text: c.example }) : '—') },
        ], [...required, ...optional, ...attrs])),
    );
  }

  async function runPreview() {
    if (!ui.file) { ui.error = 'Choose a CSV file first.'; draw(); return; }

    ui.busy = true; ui.error = null; ui.result = null; draw();
    try {
      const form = new FormData();
      form.append('file', ui.file);
      form.append('dataset', ui.dataset);
      ui.preview = await postForm('/ingest/preview', form);
    } catch (err) {
      ui.error = err.detail || err.message;
      ui.preview = null;
    } finally {
      ui.busy = false; draw();
    }
  }

  function previewCard() {
    const p = ui.preview;
    const mapped = Object.entries(p.mapping).filter(([, v]) => v);
    const blocked = p.missing_required.length > 0;

    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'What this file would do' }),
        badge(`${p.valid_rows} of ${p.total_rows} rows valid`,
          p.ok ? 'good' : 'warn', p.ok ? '✓' : '!')),

      blocked
        ? note(`Missing required columns: ${p.missing_required.join(', ')}. `
          + 'Nothing can be imported until they are present.', 'critical')
        : null,

      p.unmapped_headers.length
        ? note(`Ignored columns, because they match nothing in this dataset: `
          + `${p.unmapped_headers.join(', ')}.`, 'warn')
        : null,

      el('h3', { text: 'Column mapping', style: 'margin:.6rem 0 .4rem' }),
      el('div', { class: 'pill-row' },
        ...mapped.map(([header, col]) =>
          badge(header === col ? header : `${header} → ${col}`, 'good', '→')),
        ...p.unmapped_headers.map((h) => badge(h, 'warn', '✕'))),

      p.errors.length
        ? el('div', { style: 'margin-top:1rem' },
          el('h3', { text: `Rows that failed (${p.error_count})`, style: 'margin-bottom:.4rem' }),
          table([
            { label: 'Row', num: true, get: (e) => e.row },
            { label: 'Column', get: (e) => el('code', { text: e.column }) },
            { label: 'Value', get: (e) => el('code', { text: e.value || '(empty)' }) },
            { label: 'Problem', get: (e) => e.message },
          ], p.errors))
        : null,

      p.preview.length
        ? el('div', { style: 'margin-top:1rem' },
          el('h3', { text: 'First rows as they would be stored', style: 'margin-bottom:.4rem' }),
          el('pre', { style: 'font-family:var(--mono);font-size:.7rem;background:var(--surface-2);'
            + 'border:1px solid var(--border);border-radius:var(--radius-sm);padding:.6rem;'
            + 'overflow-x:auto;max-height:280px',
            text: p.preview.map((r) => JSON.stringify(r)).join('\n') }))
        : null,

      el('div', { class: 'toolbar', style: 'margin-top:1rem;margin-bottom:0' },
        p.error_count
          ? el('label', { style: 'display:flex;gap:.4rem;align-items:center;margin:0' },
            el('input', {
              type: 'checkbox', checked: ui.allowPartial,
              // Must redraw: the Import button's disabled state is derived from
              // this, and without it the button stays greyed out for ever.
              onChange: (e) => { ui.allowPartial = e.target.checked; draw(); },
            }),
            el('span', { text: `Import the ${p.valid_rows} valid rows and skip the rest` }))
          : null,
        dataset().context.includes('match_id')
          ? el('label', { style: 'display:flex;gap:.4rem;align-items:center;margin:0' },
            el('input', {
              type: 'checkbox', checked: ui.replaceExisting,
              onChange: (e) => { ui.replaceExisting = e.target.checked; draw(); },
            }),
            el('span', { text: 'Replace what is already on this match' }))
          : null,
        el('button', {
          class: 'primary',
          text: ui.busy ? 'Importing…' : `Import ${p.error_count && !ui.allowPartial ? '' : p.valid_rows + ' rows'}`,
          disabled: ui.busy || blocked || (p.error_count > 0 && !ui.allowPartial),
          onClick: runCommit,
        })),
    );
  }

  async function runCommit() {
    if (!ui.file) { ui.error = 'Choose a CSV file first.'; draw(); return; }

    ui.busy = true; ui.error = null; draw();
    try {
      const ds = dataset();
      const form = new FormData();
      form.append('file', ui.file);
      form.append('dataset', ui.dataset);
      form.append('allow_partial', String(ui.allowPartial));
      form.append('replace_existing', String(ui.replaceExisting));
      if (ds.context.includes('team_id')) form.append('team_id', ui.ctx.team_id);
      if (ds.context.includes('match_id') && ui.ctx.match_id) {
        form.append('match_id', ui.ctx.match_id);
      }
      if (ds.context.includes('provider')) form.append('provider', ui.ctx.provider);
      if (ds.context.includes('target_hz')) form.append('target_hz', ui.ctx.target_hz);

      ui.result = await postForm('/ingest/commit', form);
      ui.preview = null;
      ui.file = null;
    } catch (err) {
      ui.error = err.detail || err.message;
    } finally {
      ui.busy = false; draw();
    }
  }

  function resultCard() {
    const r = ui.result;
    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'Imported' }),
        badge(`${r.rows_imported} rows`, 'good', '✓')),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Dataset' }), el('dd', { text: r.dataset }),
        el('dt', { text: 'Rows in file' }), el('dd', { text: r.rows_in_file }),
        el('dt', { text: 'Created' }), el('dd', { text: r.created }),
        el('dt', { text: 'Updated' }), el('dd', { text: r.updated }),
        el('dt', { text: 'Skipped' }), el('dd', { text: r.rows_skipped })),
      r.note ? note(r.note) : null,
      ...(r.warnings || []).map((w) => note(w, 'warn')),
      note('Reload the other tabs to see the new data.'),
    );
  }

  /* --- Manual ------------------------------------------------------------- */

  function manualPanel() {
    return el('div', { class: 'grid two' },
      playerFormCard(),
      el('div', {}, clubCard(), teamCard()));
  }

  function playerFormCard() {
    const p = ui.editing || {};
    const attrs = p.attributes || {};
    const isEdit = Boolean(p.id);

    const field = (label, id, attrsIn = {}) => el('div', { class: 'field' },
      el('label', { text: label }), el('input', { id, ...attrsIn }));

    const headline = ['pace', 'shooting', 'passing', 'dribbling', 'defending', 'physical'];
    const detail = [
      'acceleration', 'sprint_speed', 'finishing', 'shot_power', 'long_shots',
      'volleys', 'penalties', 'heading_accuracy', 'vision', 'crossing',
      'free_kick_accuracy', 'short_passing', 'long_passing', 'curve', 'agility',
      'balance', 'reactions', 'ball_control', 'composure', 'interceptions',
      'defensive_awareness', 'standing_tackle', 'sliding_tackle', 'jumping',
      'stamina', 'strength', 'aggression',
    ];
    const gk = ['gk_diving', 'gk_handling', 'gk_kicking', 'gk_reflexes',
      'gk_positioning', 'gk_speed'];

    const attrInput = (key) => el('div', { class: 'field', style: 'min-width:0' },
      el('label', { text: key.replace(/_/g, ' ') }),
      el('input', {
        id: `attr-${key}`, type: 'number', min: 0, max: 99,
        value: attrs[key] ?? '', placeholder: '—',
      }));

    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: isEdit ? `Edit ${p.display_name}` : 'Add a player' }),
        isEdit ? el('button', {
          text: 'New player instead',
          onClick: () => { ui.editing = null; draw(); },
        }) : null),

      el('div', { class: 'toolbar' },
        el('div', { class: 'field', style: 'min-width:220px' },
          el('label', { text: 'Load an existing player' }),
          el('select', {
            onChange: (e) => {
              ui.editing = e.target.value
                ? state.players.find((x) => x.id === e.target.value) : null;
              draw();
            },
          },
          el('option', { value: '', text: '— new player —' }),
          ...state.players.map((x) => el('option', {
            value: x.id, selected: x.id === p.id, text: x.display_name,
          }))))),

      el('div', { class: 'toolbar' },
        field('Name *', 'f-name', { value: p.name || '' }),
        field('Known as', 'f-known', { value: p.known_as || '' }),
        field('Shirt', 'f-shirt', { type: 'number', min: 1, max: 99, value: p.shirt_number ?? '' }),
        el('div', { class: 'field' },
          el('label', { text: 'Position *' }),
          el('select', { id: 'f-pos' }, ...POSITIONS.map((x) => el('option', {
            value: x, selected: x === (p.primary_position || 'CM'), text: x,
          })))),
        field('Other positions', 'f-secondary', {
          value: (p.secondary_positions || []).join(','), placeholder: 'DM,AM',
        }),
        field('Date of birth', 'f-dob', { type: 'date', value: p.birth_date || '' }),
        el('div', { class: 'field' },
          el('label', { text: 'Foot' }),
          el('select', { id: 'f-foot' }, ...['right', 'left', 'both'].map((x) =>
            el('option', { value: x, selected: x === (p.preferred_foot || 'right'), text: x })))),
        field('Overall *', 'f-ovr', { type: 'number', min: 0, max: 99, value: p.overall_rating ?? 70 }),
        field('Potential', 'f-pot', { type: 'number', min: 0, max: 99, value: p.potential_rating ?? 75 }),
        field('Fitness', 'f-fit', { type: 'number', min: 0, max: 100, value: p.fitness ?? 100 }),
        field('Fatigue', 'f-fatigue', { type: 'number', min: 0, max: 100, value: p.fatigue ?? 0 }),
        field('Minutes last 7d', 'f-min7', { type: 'number', min: 0, value: p.minutes_last_7d ?? 0 }),
        field('Market value €', 'f-value', { type: 'number', min: 0, value: p.market_value_eur ?? 0 }),
        field('Wage €/yr', 'f-wage', { type: 'number', min: 0, value: p.wage_eur_per_year ?? 0 }),
        field('Contract until', 'f-contract', { type: 'date', value: p.contract_until || '' }),
        el('label', { style: 'display:flex;gap:.4rem;align-items:center;margin:0' },
          el('input', { type: 'checkbox', id: 'f-available', checked: p.is_available !== false }),
          el('span', { text: 'Available' }))),

      el('h3', { text: 'Headline attributes', style: 'margin:.8rem 0 .4rem' }),
      el('div', { class: 'toolbar' }, ...headline.map(attrInput)),

      el('details', { open: ui.showDetail },
        el('summary', {
          style: 'cursor:pointer;font-size:.82rem;color:var(--text-secondary)',
          text: 'Detailed attributes — optional, each falls back to its headline face',
          onClick: () => { ui.showDetail = !ui.showDetail; },
        }),
        el('div', { class: 'toolbar', style: 'margin-top:.6rem' }, ...detail.map(attrInput)),
        el('h3', { text: 'Goalkeeping', style: 'margin:.4rem 0' }),
        el('div', { class: 'toolbar' }, ...gk.map(attrInput))),

      el('div', { class: 'toolbar', style: 'margin-bottom:0' },
        el('button', {
          class: 'primary', text: isEdit ? 'Save changes' : 'Add player',
          disabled: ui.busy, onClick: () => savePlayer(isEdit ? p.id : null),
        }),
        isEdit ? el('button', {
          text: 'Delete',
          onClick: () => deletePlayer(p.id, p.display_name),
        }) : null),
    );
  }

  function collectAttributes() {
    const out = {};
    document.querySelectorAll('[id^="attr-"]').forEach((input) => {
      const value = input.value.trim();
      if (value !== '') out[input.id.slice(5)] = Number(value);
    });
    return out;
  }

  const val = (id) => document.getElementById(id)?.value.trim() ?? '';
  const num = (id) => { const v = val(id); return v === '' ? null : Number(v); };

  async function savePlayer(playerId) {
    ui.busy = true; ui.error = null;
    try {
      const secondary = val('f-secondary')
        ? val('f-secondary').split(',').map((s) => s.trim()).filter(Boolean) : [];
      const body = {
        name: val('f-name'),
        known_as: val('f-known'),
        shirt_number: num('f-shirt'),
        primary_position: val('f-pos'),
        secondary_positions: secondary,
        birth_date: val('f-dob') || null,
        preferred_foot: val('f-foot'),
        overall_rating: num('f-ovr'),
        potential_rating: num('f-pot'),
        fitness: num('f-fit'),
        fatigue: num('f-fatigue'),
        minutes_last_7d: num('f-min7'),
        market_value_eur: num('f-value'),
        wage_eur_per_year: num('f-wage'),
        contract_until: val('f-contract') || null,
        is_available: document.getElementById('f-available')?.checked ?? true,
        attributes: collectAttributes(),
      };
      if (!body.name) throw new Error('A name is required.');

      if (playerId) {
        const patch = { ...body };
        delete patch.name;
        delete patch.birth_date;
        delete patch.preferred_foot;
        await api.patch(`/players/${playerId}`, patch);
      } else {
        await api.post('/players', { ...body, team_id: state.teamId });
      }
      state.players = await api.get('/players', { team_id: state.teamId, limit: 300 });
      ui.editing = null;
      ui.result = null;
      ui.error = null;
      overview.players = state.players.length;
    } catch (err) {
      ui.error = err.detail || err.message;
    } finally {
      ui.busy = false; draw();
    }
  }

  async function deletePlayer(playerId, name) {
    // eslint-disable-next-line no-alert
    if (!window.confirm(`Delete ${name}? This cannot be undone.`)) return;
    ui.busy = true;
    try {
      await api.del(`/players/${playerId}`);
      state.players = await api.get('/players', { team_id: state.teamId, limit: 300 });
      overview.players = state.players.length;
      ui.editing = null;
    } catch (err) {
      ui.error = err.detail || err.message;
    } finally {
      ui.busy = false; draw();
    }
  }

  function clubCard() {
    const club = state.club || {};
    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'Club finances' }),
        el('span', { class: 'hint', text: 'What the transfer optimiser spends against' })),
      el('div', { class: 'toolbar' },
        el('div', { class: 'field' },
          el('label', { text: 'Transfer budget €' }),
          el('input', { id: 'c-budget', type: 'number', min: 0, step: 1000000,
            value: club.transfer_budget_eur ?? 0 })),
        el('div', { class: 'field' },
          el('label', { text: 'Wage budget €/yr' }),
          el('input', { id: 'c-wage', type: 'number', min: 0, step: 500000,
            value: club.wage_budget_eur_per_year ?? 0 })),
        el('button', { class: 'primary', text: 'Save', onClick: saveClub })),
    );
  }

  async function saveClub() {
    ui.busy = true; ui.error = null;
    try {
      state.club = await api.patch('/tenants/current', {
        transfer_budget_eur: num('c-budget'),
        wage_budget_eur_per_year: num('c-wage'),
      });
    } catch (err) {
      ui.error = err.detail || err.message;
    } finally {
      ui.busy = false; draw();
    }
  }

  function teamCard() {
    return el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'Teams' })),
      table([
        { label: 'Team', get: (t) => t.name },
        { label: 'Kind', get: (t) => t.kind },
        { label: 'Competition', get: (t) => t.competition || '—' },
        { label: 'Formation', get: (t) => t.default_formation },
      ], teams),
      el('h3', { text: 'Add a team', style: 'margin:.9rem 0 .4rem' }),
      el('div', { class: 'toolbar', style: 'margin-bottom:0' },
        el('div', { class: 'field' },
          el('label', { text: 'Name' }), el('input', { id: 't-name' })),
        el('div', { class: 'field' },
          el('label', { text: 'Slug' }),
          el('input', { id: 't-slug', placeholder: 'demo-fc-b' })),
        el('div', { class: 'field' },
          el('label', { text: 'Kind' }),
          el('select', { id: 't-kind' },
            ...['first_team', 'academy', 'opponent'].map((k) =>
              el('option', { value: k, text: k })))),
        el('button', { class: 'primary', text: 'Create', onClick: createTeam })),
    );
  }

  async function createTeam() {
    ui.busy = true; ui.error = null;
    try {
      const created = await api.post('/teams', {
        name: val('t-name'), slug: val('t-slug'), kind: val('t-kind'),
      });
      teams.push(created);
    } catch (err) {
      ui.error = err.detail || err.message;
    } finally {
      ui.busy = false; draw();
    }
  }

  /* --- Video -------------------------------------------------------------- */

  function videoPanel() {
    const simulated = cv.engine === 'simulated';
    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Analyse video' }),
        badge(cv.engine, simulated ? 'warn' : 'good', simulated ? '!' : '✓')),
      note(cv.note, simulated ? 'warn' : null),
      note('A fixed wide (tactical) camera gives dramatically better calibration '
        + 'than broadcast footage, which shows roughly 60% of the pitch at any '
        + 'moment. Upload the file itself — URLs are not accepted.'),

      el('div', { class: 'toolbar' },
        el('div', { class: 'field', style: 'min-width:200px' },
          el('label', { text: 'Team' }),
          el('select', { id: 'v-team' }, ...teams.map((t) =>
            el('option', { value: t.id, selected: t.id === state.teamId, text: t.name })))),
        el('div', { class: 'field' },
          el('label', { text: 'Home kit colour' }),
          el('input', { type: 'color', id: 'v-kit',
            value: state.team?.primary_color || '#2a78d6' })),
        el('div', { class: 'field', style: 'min-width:110px' },
          el('label', { text: 'Sample rate (Hz)' }),
          el('input', { type: 'number', id: 'v-hz', min: 1, max: 25, value: 5 })),
        el('div', { class: 'field', style: 'min-width:160px' },
          el('label', { text: 'Camera' }),
          el('select', { id: 'v-camera' },
            el('option', { value: 'tactical', text: 'Tactical (wide, fixed)' }),
            el('option', { value: 'broadcast', text: 'Broadcast' }),
            el('option', { value: 'handheld', text: 'Handheld' }))),
        el('div', { class: 'field' },
          el('label', { text: 'Footage' }),
          el('input', { type: 'file', id: 'v-file', accept: 'video/*' })),
        el('button', {
          class: 'primary', text: ui.busy ? 'Uploading…' : 'Analyse',
          disabled: ui.busy, onClick: uploadVideo,
        })),

      ui.job ? jobStatus(ui.job) : null,
    );
  }

  async function uploadVideo() {
    const file = document.getElementById('v-file')?.files?.[0];
    if (!file) { ui.error = 'Choose a video file first.'; draw(); return; }
    ui.busy = true; ui.error = null; draw();
    try {
      ui.job = await api.uploadVideo(file, {
        team_id: document.getElementById('v-team').value,
        home_kit_hex: document.getElementById('v-kit').value,
        sample_hz: document.getElementById('v-hz').value,
        camera_type: document.getElementById('v-camera').value,
      });
      pollJob();
    } catch (err) {
      ui.error = err.detail || err.message;
    } finally {
      ui.busy = false; draw();
    }
  }

  function pollJob() {
    clearInterval(ui.poll);
    ui.poll = setInterval(async () => {
      if (!ui.job) return;
      try {
        ui.job = await api.get(`/analysis/jobs/${ui.job.id}`);
        draw();
        if (ui.job.status === 'succeeded' || ui.job.status === 'failed') clearInterval(ui.poll);
      } catch { clearInterval(ui.poll); }
    }, 1500);
  }

  function jobStatus(job) {
    const kind = job.status === 'failed' ? 'critical'
      : job.status === 'succeeded' ? 'good' : 'warn';
    return el('div', { style: 'margin-top:.8rem' },
      el('div', { class: 'pill-row', style: 'margin-bottom:.5rem' },
        badge(job.status, kind, job.status === 'succeeded' ? '✓'
          : job.status === 'failed' ? '✕' : '⋯'),
        badge(`engine ${job.engine || '—'}`),
        badge(`stage ${job.stage}`)),
      el('div', { class: 'bar-track', style: 'margin-bottom:.5rem' },
        el('div', { class: 'bar-fill',
          style: `width:${Math.round((job.progress || 0) * 100)}%` })),
      job.error ? note(job.error, 'critical') : null,
      job.status === 'succeeded'
        ? note('Tracking stored and the report is ready — open Analysis and pick this match.')
        : null);
  }

  /* --- Helpers ------------------------------------------------------------ */

  async function postForm(path, form) {
    const res = await fetch(`/api/v1${path}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${sessionStorage.getItem('elevenmetric.token')}` },
      body: form,
    });
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error(payload?.detail || res.statusText);
      err.detail = payload?.detail;
      throw err;
    }
    return payload;
  }

  async function downloadTemplate(key) {
    try {
      const text = await api.get(`/ingest/template/${key}`);
      const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = el('a', { href: url, download: `elevenmetric-${key}-template.csv` });
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      ui.error = err.detail || err.message;
      draw();
    }
  }

  draw();
}

const POSITIONS = ['GK', 'RB', 'RCB', 'CB', 'LCB', 'LB', 'RWB', 'LWB', 'DM', 'CM',
  'AM', 'RM', 'LM', 'RW', 'LW', 'CF', 'ST', 'SS'];
