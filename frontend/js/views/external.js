/* EXTERNAL SOURCES — import a real team instead of typing one in.
 *
 * The panel's job is not just to import. It is to keep two very different
 * sources from blurring together in the user's head: SoFIFA supplies a video
 * game's ratings for real clubs, StatsBomb supplies real match data and no
 * ratings at all. Every screen here names which is which, and every imported
 * row carries where it came from and when it was read. */

import { api } from '../api.js';
import { badge, el, empty, fmt, loading, note, table } from '../ui.js';

const SOURCE_ORDER = ['sofifa', 'statsbomb'];

export function panel(ui, draw, state) {
  if (!ui.ext) {
    ui.ext = {
      sources: null,
      source: 'sofifa',
      query: '',
      clubs: null,
      file: null,
      clubName: '',
      kind: 'opponent',
      allowPartial: false,
      preview: null,
      result: null,
      busy: false,
      error: null,
      competitions: null,
      competition: null,
      matches: null,
      fixture: null,
    };
  }
  const x = ui.ext;

  if (x.sources === null) {
    x.sources = 'loading';
    api.get('/external/sources')
      .then((body) => { x.sources = body; draw(); })
      .catch((err) => { x.sources = { sources: [], note: '' }; x.error = err.detail || err.message; draw(); });
    return loading('Checking which sources are available');
  }
  if (x.sources === 'loading') return loading('Checking which sources are available');

  const byKey = Object.fromEntries((x.sources.sources || []).map((s) => [s.key, s]));
  const active = byKey[x.source];

  return el('div', {},
    sourcePicker(x, byKey, draw),
    x.error ? note(String(x.error), 'critical') : null,
    active ? sourceCard(active) : null,
    x.source === 'sofifa' ? sofifaPanel(x, draw, state, byKey.sofifa)
      : statsbombPanel(x, draw, byKey.statsbomb),
    x.result ? resultCard(x) : null,
  );
}

/* --- Source choice ------------------------------------------------------- */

function sourcePicker(x, byKey, draw) {
  return el('div', { class: 'view-toggle', style: 'margin-bottom:1rem' },
    ...SOURCE_ORDER.filter((k) => byKey[k]).map((key) => el('button', {
      text: byKey[key].label,
      'aria-pressed': String(x.source === key),
      onClick: () => {
        x.source = key; x.error = null; x.preview = null; x.result = null; draw();
      },
    })));
}

function sourceCard(source) {
  return el('section', { class: 'card', style: 'margin-bottom:1rem' },
    el('header', {},
      el('h2', { text: source.label }),
      badge(`Tier ${source.tier}`, 'good'),
      /* Three states, not two: usable, usable-but-not-live (the file route
       * still works), and unavailable. Collapsing the middle one would tell a
       * user the source is fine when half of it is switched off. */
      !source.available ? badge('Unavailable', 'critical', '✕')
        : source.reason ? badge('Files only', 'warn', '!')
          : badge('Available', 'good', '✓')),

    /* The single most important sentence on the screen: what this actually is. */
    el('p', { class: 'hint', style: 'margin:.2rem 0 .6rem', text: source.what_it_is }),

    source.reason
      ? note(`${source.reason}. ${source.remedy}`, 'warn')
      : null,

    el('div', { class: 'grid two' },
      el('div', {},
        el('h3', { text: 'Supplies', style: 'margin:.4rem 0' }),
        el('div', { class: 'pill-row' },
          ...source.supplies.map((s) => badge(s, 'good', '✓')))),
      el('div', {},
        el('h3', { text: 'Does not supply', style: 'margin:.4rem 0' }),
        el('div', { class: 'pill-row' },
          ...source.does_not_supply.map((s) => badge(s, 'warn', '✕'))))),

    source.attribution
      ? el('p', { class: 'hint', style: 'margin-top:.7rem', text: source.attribution })
      : null,
  );
}

/* --- SoFIFA -------------------------------------------------------------- */

function sofifaPanel(x, draw, state, source) {
  const live = source && source.available && !source.reason;

  return el('div', {},
    el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {}, el('h2', { text: 'Import a club' })),

      live ? el('div', { class: 'toolbar' },
        el('div', { class: 'field', style: 'min-width:260px' },
          el('label', { text: 'Search sofifa.com by club name' }),
          el('input', {
            value: x.query, placeholder: 'Real Madrid',
            onInput: (e) => { x.query = e.target.value; },
          })),
        el('button', {
          class: 'primary', text: 'Search', disabled: x.busy,
          onClick: () => run(x, draw, async () => {
            const body = await api.get('/external/sofifa/clubs', { q: x.query });
            x.clubs = body.clubs;
          }),
        }),
      ) : null,

      el('div', { class: 'toolbar', style: 'margin-top:.6rem' },
        el('div', { class: 'field', style: 'min-width:280px' },
          el('label', { text: live ? 'Or import a saved page / export file'
            : 'Import a saved SoFIFA page (.html) or export (.csv)' }),
          el('input', {
            type: 'file', accept: '.csv,.html,.htm,.txt',
            onChange: (e) => { x.file = e.target.files[0] || null; },
          })),
        el('div', { class: 'field', style: 'min-width:180px' },
          el('label', { text: 'Club name (optional)' }),
          el('input', {
            value: x.clubName, placeholder: 'Read from the file if blank',
            onInput: (e) => { x.clubName = e.target.value; },
          })),
        el('div', { class: 'field', style: 'min-width:150px' },
          el('label', { text: 'Import as' }),
          el('select', { onChange: (e) => { x.kind = e.target.value; } },
            el('option', { value: 'opponent', selected: x.kind === 'opponent',
              text: 'Opponent (scouting)' }),
            el('option', { value: 'first_team', selected: x.kind === 'first_team',
              text: 'First team' }))),
        el('button', {
          text: 'Preview', disabled: x.busy,
          onClick: () => run(x, draw, async () => {
            x.preview = await postFile('/external/sofifa/preview-file', x.file,
              { club_name: x.clubName });
            x.result = null;
          }),
        })),

      el('p', { class: 'hint', style: 'margin-top:.5rem' },
        'Nothing is written until you press Import. A preview shows every row '
        + 'exactly as it would be stored, including the fields this source does '
        + 'not supply.'),
    ),

    x.clubs ? clubsCard(x, draw) : null,
    x.preview ? squadPreviewCard(x, draw) : null,
  );
}

function clubsCard(x, draw) {
  if (!x.clubs.length) return empty('No clubs matched that search.');
  return el('section', { class: 'card', style: 'margin-bottom:1rem' },
    el('header', {}, el('h2', { text: `${x.clubs.length} club(s) found` })),
    table([
      { label: 'Club', get: (c) => c.name },
      { label: 'SoFIFA id', get: (c) => el('code', { text: c.source_id }) },
      { label: '', get: (c) => el('button', {
        text: 'Preview squad',
        onClick: () => run(x, draw, async () => {
          x.preview = await api.get('/external/sofifa/preview', { club_id: c.source_id });
          x.result = null;
        }),
      }) },
    ], x.clubs));
}

function squadPreviewCard(x, draw) {
  const p = x.preview;
  const blocked = p.errors.length > 0 && !x.allowPartial;

  return el('section', { class: 'card', style: 'margin-bottom:1rem' },
    el('header', {},
      el('h2', { text: `${p.name} — what would be stored` }),
      badge(`${p.player_count} players`, p.errors.length ? 'warn' : 'good'),
      p.league ? badge(p.league) : null),

    provenanceNote(p.provenance),

    /* The constitution's fourth principle, on screen: a rating from a video
     * game is an opinion, and the product says so where it is used. */
    note('These attributes are ratings published by a video game, not '
      + 'measurements of these players. Every recommendation built on them '
      + 'inherits that.', 'warn'),

    p.not_supplied_by_this_source?.length
      ? note(`This source supplies no ${p.not_supplied_by_this_source.join(', ')}. `
        + 'Those stay absent rather than being given a plausible value.')
      : null,

    p.unrated_players?.length
      ? note(`${p.unrated_players.length} player(s) carry no rating and will be `
        + 'excluded from selection rather than given an invented one.', 'warn')
      : null,

    p.errors.length
      ? el('div', {},
        note(`${p.errors.length} row(s) could not be mapped:`, 'critical'),
        table([
          { label: 'Player', get: (e) => e.name },
          { label: 'Field', get: (e) => e.field },
          { label: 'Value', get: (e) => el('code', { text: String(e.value || '—') }) },
          { label: 'Why', get: (e) => e.error },
        ], p.errors))
      : null,

    el('h3', { text: 'Squad', style: 'margin:.8rem 0 .4rem' }),
    table([
      { label: '#', get: (r) => r.row.shirt_number ?? '—', num: true },
      { label: 'Player', get: (r) => r.row.name },
      { label: 'Pos', get: (r) => badge(r.row.primary_position, 'good') },
      { label: 'Also', get: (r) => (r.row.secondary_positions || []).join(', ') || '—' },
      { label: 'OVR', num: true,
        get: (r) => (r.row.overall_rating ?? el('span', { class: 'na', text: 'unrated' })) },
      { label: 'POT', num: true, get: (r) => r.row.potential_rating ?? '—' },
      { label: 'Attributes', num: true, get: (r) => Object.keys(r.attributes).length },
      { label: 'Value', num: true, get: (r) => fmt.money(r.row.market_value_eur) },
      { label: 'Notes', get: (r) => r.notes.length ? badge(`${r.notes.length}`, 'warn', '!') : '—' },
    ], p.players.slice(0, 40)),

    el('div', { class: 'toolbar', style: 'margin-top:.8rem' },
      p.errors.length ? el('label', {
        style: 'display:flex;gap:.4rem;align-items:center;margin:0' },
        el('input', {
          type: 'checkbox', checked: x.allowPartial,
          onChange: (e) => { x.allowPartial = e.target.checked; draw(); },
        }),
        ' Import the valid rows and skip the rest') : null,
      el('button', {
        class: 'primary', text: `Import ${p.player_count} players`,
        disabled: x.busy || blocked,
        onClick: () => run(x, draw, async () => {
          const path = x.file ? '/external/sofifa/commit-file' : '/external/sofifa/commit';
          const fields = {
            club_name: x.clubName, kind: x.kind,
            allow_partial: String(x.allowPartial),
          };
          x.result = x.file
            ? await postFile(path, x.file, fields)
            : await postForm(path, { club_id: p.source_id, ...fields });
          x.preview = null;
        }),
      }),
      blocked ? el('span', { class: 'hint',
        text: 'Rows failed to map. Fix them, or opt into a partial import.' }) : null,
    ),
  );
}

/* --- StatsBomb ----------------------------------------------------------- */

function statsbombPanel(x, draw, source) {
  if (source && !source.available) {
    return el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'Not installed' })),
      note(`${source.reason}. ${source.remedy}`, 'warn'));
  }

  return el('div', {},
    el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {}, el('h2', { text: 'Import a real fixture' })),
      note('Real matches that actually happened — lineups and a full event feed, '
        + 'which is what the analysis pipeline needs for possession, PPDA, xG '
        + 'and xT. This source publishes no player ratings, so imported players '
        + 'arrive ungraded.'),

      el('div', { class: 'toolbar', style: 'margin-top:.6rem' },
        el('button', {
          text: 'Browse competitions', disabled: x.busy,
          onClick: () => run(x, draw, async () => {
            const body = await api.get('/external/statsbomb/competitions');
            x.competitions = body.competitions;
          }),
        }),
        x.competitions ? el('div', { class: 'field', style: 'min-width:320px' },
          el('label', { text: 'Competition and season' }),
          el('select', {
            onChange: (e) => {
              x.competition = x.competitions[Number(e.target.value)];
              run(x, draw, async () => {
                x.matches = (await api.get('/external/statsbomb/matches', {
                  competition_id: x.competition.competition_id,
                  season_id: x.competition.season_id,
                })).matches;
              });
            },
          },
          el('option', { text: 'Choose…', value: '' }),
          ...x.competitions.map((c, i) => el('option', {
            value: String(i),
            text: `${c.country} · ${c.competition} · ${c.season}`,
          })))) : null,
      )),

    x.matches ? matchesCard(x, draw) : null,
    x.fixture ? fixturePreviewCard(x, draw) : null,
  );
}

function matchesCard(x, draw) {
  if (!x.matches.length) return empty('No fixtures in that season.');
  return el('section', { class: 'card', style: 'margin-bottom:1rem' },
    el('header', {}, el('h2', { text: `${x.matches.length} fixture(s)` })),
    table([
      { label: 'Date', get: (m) => m.date },
      { label: 'Home', get: (m) => m.home },
      { label: 'Score', num: true, get: (m) => `${m.home_score}–${m.away_score}` },
      { label: 'Away', get: (m) => m.away },
      { label: '', get: (m) => el('button', {
        text: 'Preview',
        onClick: () => run(x, draw, async () => {
          x.fixture = await api.get('/external/statsbomb/preview', {
            match_id: m.match_id,
            competition_id: x.competition.competition_id,
            season_id: x.competition.season_id,
          });
          x.result = null;
        }),
      }) },
    ], x.matches.slice(0, 60)));
}

function fixturePreviewCard(x, draw) {
  const f = x.fixture;
  return el('section', { class: 'card', style: 'margin-bottom:1rem' },
    el('header', {},
      el('h2', { text: `${f.home} ${f.score[0]}–${f.score[1]} ${f.away}` }),
      badge(`${f.event_count} events`, f.event_count ? 'good' : 'critical'),
      f.competition ? badge(f.competition) : null),

    provenanceNote(f.provenance),

    note('Players imported from this fixture carry no rating — the source '
      + 'publishes none. They are excluded from selection and simulation '
      + 'rather than given an invented one.', 'warn'),

    !f.event_count
      ? note('This fixture is metadata-only in the open-data release. Importing '
        + 'it would produce a match that cannot be analysed.', 'critical')
      : null,

    el('h3', { text: 'Event types', style: 'margin:.8rem 0 .4rem' }),
    el('div', { class: 'pill-row' },
      ...Object.entries(f.event_types || {}).map(([type, n]) =>
        badge(`${type} ${n}`, 'good'))),

    el('h3', { text: 'Lineups', style: 'margin:.8rem 0 .4rem' }),
    el('div', { class: 'grid two' },
      ...Object.entries(f.lineups || {}).map(([team, players]) => el('div', {},
        el('h4', { text: `${team} — ${players.length}` }),
        table([
          { label: '#', num: true, get: (p) => p.row.shirt_number ?? '—' },
          { label: 'Player', get: (p) => p.row.name },
          { label: 'Pos', get: (p) => badge(p.row.primary_position) },
          { label: 'Rating', get: () => el('span', { class: 'na', text: 'unrated' }) },
        ], players.slice(0, 18))))),

    el('div', { class: 'toolbar', style: 'margin-top:.8rem' },
      el('button', {
        class: 'primary', text: 'Import this fixture',
        disabled: x.busy || !f.event_count,
        onClick: () => run(x, draw, async () => {
          x.result = await postForm('/external/statsbomb/commit', {
            match_id: f.source_id,
            competition_id: x.competition?.competition_id,
            season_id: x.competition?.season_id,
          });
          x.fixture = null;
        }),
      })),
  );
}

/* --- Shared -------------------------------------------------------------- */

function provenanceNote(p) {
  if (!p) return null;
  const bits = [`Source: ${p.source}`];
  if (p.edition) bits.push(p.edition);
  if (p.retrieved_at) bits.push(`read ${fmt.date(p.retrieved_at)}`);
  if (p.retrieved) bits.push(p.retrieved === 'file' ? 'from a file' : 'by fetch');
  return el('div', {},
    el('div', { class: 'pill-row', style: 'margin-bottom:.4rem' },
      ...bits.map((b) => badge(b))),
    p.note ? el('p', { class: 'hint', text: p.note }) : null,
  );
}

function resultCard(x) {
  const r = x.result;
  const isSquad = r.team_id !== undefined;
  return el('section', { class: 'card' },
    el('header', {},
      el('h2', { text: 'Imported' }),
      badge('Done', 'good', '✓')),

    isSquad
      ? el('div', { class: 'pill-row' },
        badge(`${r.team}`, 'good'),
        badge(`${r.created} created`, 'good'),
        badge(`${r.updated} updated`),
        badge(`kind: ${r.kind}`))
      : el('div', { class: 'pill-row' },
        badge(`${r.home} ${r.score[0]}–${r.score[1]} ${r.away}`, 'good'),
        badge(`${r.events_imported} events`, 'good'),
        badge(`${r.players_created} players`),
        badge(`tier: ${r.source}`)),

    r.departed?.length
      ? note(`${r.departed.length} player(s) are no longer in the source squad. `
        + 'They are kept on file, not deleted.', 'warn')
      : null,

    r.unrated_players?.length
      ? note(`${r.unrated_players.length} player(s) were stored unrated.`, 'warn')
      : null,

    r.note ? note(r.note) : null,
    provenanceNote(r.provenance),
  );
}

async function run(x, draw, work) {
  x.busy = true; x.error = null; draw();
  try {
    await work();
  } catch (err) {
    const d = err.detail;
    x.error = typeof d === 'string' ? d
      : d?.remedy ? `${d.source}: ${d.reason}. ${d.remedy}`
        : d?.expected ? `${d.source}: could not read ${d.url} — expected ${d.expected}. ${d.detail || ''}`
          : d?.message || err.message;
  } finally {
    x.busy = false; draw();
  }
}

function token() { return sessionStorage.getItem('elevenmetric.token'); }

async function send(path, form) {
  const res = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token()}` },
    body: form,
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(typeof payload?.detail === 'string' ? payload.detail : res.statusText);
    err.detail = payload?.detail;
    throw err;
  }
  return payload;
}

async function postFile(path, file, fields) {
  if (!file) throw Object.assign(new Error('Choose a file first.'),
    { detail: 'Choose a file first.' });
  const form = new FormData();
  form.append('file', file);
  appendAll(form, fields);
  return send(path, form);
}

async function postForm(path, fields) {
  const form = new FormData();
  appendAll(form, fields);
  return send(path, form);
}

function appendAll(form, fields) {
  for (const [k, v] of Object.entries(fields || {})) {
    if (v !== undefined && v !== null && v !== '') form.append(k, v);
  }
}
