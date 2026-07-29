/* App shell: authentication, club context, tab routing. */

import { api, auth } from './api.js';
import { el, empty, mount, note } from './ui.js';

import * as squadView from './views/squad.js';
import * as analysisView from './views/analysis.js';
import * as transfersView from './views/transfers.js';
import * as academyView from './views/academy.js';
import * as dataView from './views/data.js';
import * as ingestView from './views/ingest.js';

const TABS = [
  { id: 'squad', label: 'Squad', view: squadView },
  { id: 'analysis', label: 'Analysis', view: analysisView },
  { id: 'transfers', label: 'Transfer market', view: transfersView },
  { id: 'academy', label: 'Academy', view: academyView },
  { id: 'ingest', label: 'Import data', view: ingestView },
  { id: 'data', label: 'Data contract', view: dataView },
];

const state = {
  club: null,
  teams: [],
  team: null,
  teamId: null,
  players: [],
  formation: null,
  matchId: null,
  playerName(id) {
    const p = this.players.find((x) => x.id === id);
    return p ? p.display_name : null;
  },
};

const app = document.getElementById('app');

/* --- Login --------------------------------------------------------------- */

function loginScreen(message) {
  const err = el('div', { class: 'err', text: message || '' });
  const email = el('input', { type: 'email', value: 'owner@demo.fc', autocomplete: 'username' });
  const password = el('input', { type: 'password', value: 'elevenmetric', autocomplete: 'current-password' });
  const slug = el('input', { type: 'text', placeholder: 'optional' });

  const submit = async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    try {
      await api.login(email.value, password.value, slug.value || null);
      await boot();
    } catch (e) {
      err.textContent = typeof e.detail === 'string' ? e.detail : 'Could not sign in';
    }
  };

  mount(app, el('form', { class: 'login', onSubmit: submit },
    el('h1', { text: 'ElevenMetric' }),
    el('p', { text: 'Multi-tenant football analysis. Sign in with your club account.' }),
    el('div', { class: 'field' }, el('label', { text: 'Email' }), email),
    el('div', { class: 'field' }, el('label', { text: 'Password' }), password),
    el('div', { class: 'field' },
      el('label', { text: 'Club slug (only if your email exists at several clubs)' }), slug),
    err,
    el('button', { class: 'primary', type: 'submit', text: 'Sign in',
      style: 'width:100%;margin-top:.5rem' }),
    el('p', { style: 'margin-top:1rem;font-size:.76rem;color:var(--text-muted)',
      text: 'Demo: owner@demo.fc / elevenmetric' }),
  ));
}

/* --- Shell --------------------------------------------------------------- */

let activeTab = location.hash.replace('#', '') || 'squad';

function shell() {
  const clubDot = el('span', {
    class: 'dot',
    style: `background:${state.team?.primary_color || 'var(--series-1)'}`,
  });

  const teamSelect = el('select', {
    onChange: async (ev) => {
      state.teamId = ev.target.value;
      state.team = state.teams.find((t) => t.id === state.teamId);
      await loadPlayers();
      renderTab();
    },
  }, ...state.teams.map((t) => el('option', {
    value: t.id, selected: t.id === state.teamId, text: t.name,
  })));

  const themeButton = el('button', {
    text: currentTheme() === 'dark' ? 'Light' : 'Dark',
    title: 'Toggle theme',
    onClick: () => {
      const next = currentTheme() === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('elevenmetric.theme', next);
      renderShell();
    },
  });

  const nav = el('nav', { class: 'tabs', role: 'tablist' }, ...TABS.map((t) =>
    el('button', {
      role: 'tab', text: t.label,
      'aria-selected': String(t.id === activeTab),
      onClick: () => {
        activeTab = t.id;
        location.hash = t.id;
        renderShell();
      },
    })));

  const main = el('main', { id: 'view' });

  mount(app, el('div', { class: 'app' },
    el('header', { class: 'topbar' },
      el('div', { class: 'brand' },
        el('span', { class: 'mark', text: 'ElevenMetric' }),
        el('span', { class: 'sub', text: 'tactical intelligence' })),
      el('span', { class: 'club-chip' }, clubDot,
        el('span', { text: state.club?.name || '—' }),
        el('span', { style: 'color:var(--text-muted)', text: state.club?.plan || '' })),
      el('div', { class: 'field', style: 'min-width:180px;margin:0' }, teamSelect),
      el('span', { class: 'spacer' }),
      themeButton,
      el('button', {
        text: 'Sign out',
        onClick: () => { api.logout(); loginScreen(); },
      })),
    nav, main));

  return main;
}

function currentTheme() {
  const explicit = document.documentElement.getAttribute('data-theme');
  if (explicit) return explicit;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function renderShell() {
  const main = shell();
  renderTab(main);
}

async function renderTab(target) {
  const main = target || document.getElementById('view');
  if (!main) return;
  const tab = TABS.find((t) => t.id === activeTab) || TABS[0];
  try {
    await tab.view.render(main, state);
  } catch (err) {
    if (err.status === 401) { loginScreen('Session expired — sign in again.'); return; }
    mount(main, el('section', { class: 'card' },
      el('header', {}, el('h2', { text: 'Something went wrong' })),
      note(typeof err.detail === 'string' ? err.detail : err.message, 'critical'),
      el('pre', { style: 'font-size:.7rem;color:var(--text-muted);overflow-x:auto',
        text: err.stack || '' })));
  }
}

async function loadPlayers() {
  state.players = await api.get('/players', { team_id: state.teamId, limit: 300 });
}

async function boot() {
  mount(app, el('div', { class: 'loading', text: 'Loading' }));
  try {
    state.club = await api.get('/tenants/current');
    auth.club = state.club;
    state.teams = await api.get('/teams');

    if (!state.teams.length) {
      mount(app, el('main', {}, el('section', { class: 'card' },
        el('header', {}, el('h2', { text: 'No teams yet' })),
        note('Create a team via POST /api/v1/teams, then import your squad. '
          + 'The Data tab documents the full input contract.'))));
      return;
    }

    const first = state.teams.find((t) => t.kind === 'first_team') || state.teams[0];
    state.teamId = first.id;
    state.team = first;
    state.formation = first.default_formation;
    await loadPlayers();
    renderShell();
  } catch (err) {
    if (err.status === 401) loginScreen();
    else mount(app, el('main', {}, note(String(err.detail || err.message), 'critical')));
  }
}

const savedTheme = localStorage.getItem('elevenmetric.theme');
if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);

window.addEventListener('hashchange', () => {
  const next = location.hash.replace('#', '');
  if (next && next !== activeTab && TABS.some((t) => t.id === next)) {
    activeTab = next;
    renderShell();
  }
});

if (auth.token) boot();
else loginScreen();
