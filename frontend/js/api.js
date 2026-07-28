/* Thin API client. Keeps the token in sessionStorage so a refresh does not
 * log you out, and surfaces server error details rather than "request failed". */

const BASE = '/api/v1';
const TOKEN_KEY = 'elevenmetric.token';
const CLUB_KEY = 'elevenmetric.club';

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export const auth = {
  get token() { return sessionStorage.getItem(TOKEN_KEY); },
  set token(v) {
    if (v) sessionStorage.setItem(TOKEN_KEY, v);
    else sessionStorage.removeItem(TOKEN_KEY);
  },
  get club() {
    const raw = sessionStorage.getItem(CLUB_KEY);
    return raw ? JSON.parse(raw) : null;
  },
  set club(v) {
    if (v) sessionStorage.setItem(CLUB_KEY, JSON.stringify(v));
    else sessionStorage.removeItem(CLUB_KEY);
  },
  clear() { this.token = null; this.club = null; },
};

async function request(method, path, { body, params, headers } = {}) {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
    }
  }

  const opts = { method, headers: { ...(headers || {}) } };
  if (auth.token) opts.headers.Authorization = `Bearer ${auth.token}`;
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(url, opts);
  if (res.status === 204) return null;

  let payload = null;
  const text = await res.text();
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }

  if (!res.ok) {
    const detail = payload && payload.detail !== undefined ? payload.detail : payload;
    if (res.status === 401) auth.clear();
    throw new ApiError(res.status, detail || res.statusText);
  }
  return payload;
}

export const api = {
  get: (p, params) => request('GET', p, { params }),
  post: (p, body, params) => request('POST', p, { body, params }),
  patch: (p, body) => request('PATCH', p, { body }),
  del: (p) => request('DELETE', p),

  async login(email, password, tenantSlug) {
    const body = { email, password };
    if (tenantSlug) body.tenant_slug = tenantSlug;
    const res = await request('POST', '/auth/login', { body });
    auth.token = res.access_token;
    auth.club = res.tenant;
    return res;
  },

  logout() { auth.clear(); },

  /* Video upload needs multipart, so it bypasses the JSON helper. */
  async uploadVideo(file, fields) {
    const form = new FormData();
    form.append('file', file);
    for (const [k, v] of Object.entries(fields)) {
      if (v !== undefined && v !== null && v !== '') form.append(k, v);
    }
    const res = await fetch(BASE + '/video/analyze', {
      method: 'POST',
      headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
      body: form,
    });
    const payload = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, payload?.detail || res.statusText);
    return payload;
  },
};
