/* DATA — the input contract, this club's readiness against it, video, and models. */

import { api } from '../api.js';
import { badge, el, empty, fmt, loading, mount, note, table, tile } from '../ui.js';

export async function render(root, state) {
  mount(root, loading('Loading the data contract'));

  const [contract, readiness, models, cv, overview, teams] = await Promise.all([
    api.get('/meta/data-requirements'),
    api.get('/meta/data-readiness'),
    api.get('/meta/models'),
    api.get('/video/capabilities'),
    api.get('/meta/overview'),
    api.get('/teams'),
  ]);

  const ui = { job: null, uploading: false, error: null, poll: null };
  const container = el('div', {});
  mount(root, container);

  const satisfied = new Map(readiness.tiers.map((t) => [t.key, t.satisfied]));

  function draw() {
    mount(container,
      el('div', { class: 'tiles', style: 'margin-bottom:1rem' },
        tile('Coverage', fmt.pct(readiness.coverage_score * 100, 0), {
          note: 'Input tiers satisfied',
        }),
        tile('Players', overview.players),
        tile('Matches', overview.matches),
        tile('Events', fmt.int(overview.events)),
        tile('Tracking frames', fmt.int(overview.tracking_frames)),
        tile('Academy', overview.academy_players),
      ),

      readiness.gaps.length
        ? el('section', { class: 'card', style: 'margin-bottom:1rem' },
          el('header', {}, el('h2', { text: 'What is missing' })),
          ...readiness.gaps.map((g) => note(g, 'warn')))
        : note(readiness.coverage_score >= 1
          ? 'Every input tier is satisfied — the full model set is available.'
          : `${readiness.tiers.filter((t) => !t.satisfied).map((t) => t.name).join(', ')} `
            + 'is not present, but nothing it unlocks is missing from another tier.'),

      cvSummaryCard(),

      el('section', { class: 'card', style: 'margin-bottom:1rem' },
        el('header', {},
          el('h2', { text: 'What data does ElevenMetric need?' }),
          el('span', { class: 'hint', text:
            'Tiers are cumulative. Each one adds capability; nothing is imputed across them.' })),
        ...contract.tiers.map(tierCard),
        el('h3', { text: 'Principles', style: 'margin:1rem 0 .4rem' }),
        el('ul', { style: 'margin:0;padding-left:1.15rem;font-size:.82rem;color:var(--text-secondary)' },
          ...contract.principles.map((p) => el('li', { text: p })))),

      modelsCard(),
    );
  }

  function tierCard(tier) {
    const ok = satisfied.get(tier.key);
    return el('div', { class: `tier ${ok ? 'satisfied' : 'missing'}` },
      el('h3', {},
        badge(ok ? 'Present' : 'Missing', ok ? 'good' : 'warn', ok ? '✓' : '!'),
        el('span', { text: tier.name })),
      el('p', { style: 'margin:.4rem 0 .5rem;color:var(--text-secondary);font-size:.84rem',
        text: tier.summary }),

      el('div', { class: 'grid two', style: 'gap:.8rem' },
        el('div', {},
          el('h4', { style: 'font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted)',
            text: 'Unlocks' }),
          el('ul', {}, ...tier.unlocks.map((u) => el('li', { text: u })))),
        tier.still_missing.length
          ? el('div', {},
            el('h4', { style: 'font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted)',
              text: 'Still not possible' }),
            el('ul', {}, ...tier.still_missing.map((u) => el('li', { text: u }))))
          : null),

      el('details', { style: 'margin-top:.6rem' },
        el('summary', { style: 'cursor:pointer;font-size:.8rem;color:var(--text-secondary)',
          text: `Fields (${tier.fields.length}) · ${tier.ingest_endpoint || 'n/a'}` }),
        el('div', { style: 'margin-top:.5rem' },
          table([
            { label: 'Field', get: (f) => el('code', { text: f.name }) },
            { label: 'Type', get: (f) => f.type },
            {
              label: 'Required',
              get: (f) => (f.required
                ? badge('Required', 'critical', '●')
                : badge('Optional')),
            },
            { label: 'Why it matters', get: (f) => f.description },
            { label: 'Example', get: (f) => (f.example ? el('code', { text: f.example }) : '—') },
          ], tier.fields)),
        tier.typical_sources.length
          ? el('p', { style: 'margin-top:.5rem;font-size:.78rem;color:var(--text-muted)',
            text: `Typical sources: ${tier.typical_sources.join(' · ')}` })
          : null),
    );
  }

  function cvSummaryCard() {
    const simulated = cv.engine === 'simulated';
    return el('section', { class: 'card', style: 'margin-bottom:1rem' },
      el('header', {},
        el('h2', { text: 'Computer-vision engine' }),
        badge(cv.engine, simulated ? 'warn' : 'good', simulated ? '!' : 'OK')),
      note(cv.note, simulated ? 'warn' : null),
      el('p', { style: 'margin:0;font-size:.82rem;color:var(--text-secondary)',
        text: 'Upload footage from the Import data tab.' }));
  }

  function modelsCard() {
    return el('section', { class: 'card' },
      el('header', {},
        el('h2', { text: 'Model registry' }),
        el('span', { class: 'hint', text:
          '"bootstrap" means fitted on a documented generative process, not on real matches — '
          + 'a calibrated prior to replace with club data.' })),
      table([
        { label: 'Model', get: (m) => m.name },
        { label: 'Version', get: (m) => el('code', { text: m.version }) },
        {
          label: 'Provenance',
          get: (m) => badge(m.provenance, m.provenance === 'club_data' ? 'good' : 'warn'),
        },
        { label: 'Target', get: (m) => m.metrics?.target || '—' },
        {
          label: 'Holdout',
          num: true,
          get: (m) => {
            const met = m.metrics || {};
            if (met.mae_months_holdout !== undefined) {
              return `MAE ${met.mae_months_holdout} months`;
            }
            if (met.mae_holdout !== undefined) return `MAE ${met.mae_holdout}`;
            return '—';
          },
        },
        { label: 'Features', num: true, get: (m) => m.features.length },
      ], models.models),

      el('h3', { text: 'Analytics models', style: 'margin:1rem 0 .4rem' }),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Expected goals' }),
        el('dd', { text: `${models.analytics.xg.version} — ${models.analytics.xg.features.join(', ')}` }),
        el('dt', { text: 'Expected threat' }),
        el('dd', { text: `${models.analytics.xt.version} — ${models.analytics.xt.note}` })),
    );
  }

  draw();
}
