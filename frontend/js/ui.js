/* DOM helpers and formatters. */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? '' : v);
  }
  append(node, children);
  return node;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') { node.textContent = v; continue; }
    if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2).toLowerCase(), v);
      continue;
    }
    node.setAttribute(k, v);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(4)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

export function mount(node, ...children) { clear(node); append(node, children); return node; }

/* --- Formatters ---------------------------------------------------------- */

export const fmt = {
  pct(v, digits = 1) {
    return v === null || v === undefined ? '—' : `${Number(v).toFixed(digits)}%`;
  },
  num(v, digits = 2) {
    return v === null || v === undefined ? '—' : Number(v).toFixed(digits);
  },
  int(v) { return v === null || v === undefined ? '—' : Math.round(v).toLocaleString(); },
  money(v) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (Math.abs(n) >= 1e6) return `€${(n / 1e6).toFixed(1)}M`;
    if (Math.abs(n) >= 1e3) return `€${(n / 1e3).toFixed(0)}k`;
    return `€${n.toFixed(0)}`;
  },
  months(v) {
    if (v === null || v === undefined) return 'not projected';
    if (v <= 0.5) return 'ready now';
    if (v < 12) return `${v.toFixed(0)} months`;
    return `${(v / 12).toFixed(1)} years`;
  },
  date(v) {
    if (!v) return '—';
    return new Date(v).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  },
  signed(v, digits = 3) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`;
  },
  title(s) {
    return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  },
};

/* --- Shared tooltip ------------------------------------------------------ */

let tipNode = null;

export const tooltip = {
  show(x, y, title, rows = []) {
    if (!tipNode) {
      tipNode = el('div', { class: 'tooltip', role: 'status' });
      document.body.appendChild(tipNode);
    }
    mount(
      tipNode,
      el('div', { class: 'tt-title', text: title }),
      ...rows.map(([k, v]) => el('div', { class: 'tt-row' },
        el('span', { text: k }), el('b', { text: String(v) }))),
    );
    tipNode.classList.add('show');
    const rect = tipNode.getBoundingClientRect();
    const left = Math.min(x + 14, window.innerWidth - rect.width - 8);
    const top = Math.max(8, Math.min(y + 14, window.innerHeight - rect.height - 8));
    tipNode.style.left = `${left}px`;
    tipNode.style.top = `${top}px`;
  },
  hide() { if (tipNode) tipNode.classList.remove('show'); },
};

/* --- Small components ---------------------------------------------------- */

export function tile(label, value, { unit, note, na } = {}) {
  return el('div', { class: 'tile' },
    el('div', { class: 'label', text: label }),
    el('div', { class: `value${na ? ' na' : ''}` },
      na ? 'not available' : String(value),
      unit && !na ? el('span', { class: 'unit', text: unit }) : null),
    note ? el('div', { class: 'note', text: note }) : null,
  );
}

export function badge(text, kind, icon) {
  return el('span', { class: `badge${kind ? ' ' + kind : ''}` },
    icon ? el('span', { class: 'ico', text: icon }) : null, text);
}

export function barRow(label, value, { max = 100, display, color } = {}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return el('div', { class: 'bar-row' },
    el('div', { class: 'bar-label', text: label }),
    el('div', { class: 'bar-track' },
      el('div', {
        class: 'bar-fill',
        style: `width:${pct}%${color ? `;background:${color}` : ''}`,
      })),
    el('div', { class: 'bar-value', text: display ?? fmt.num(value, 0) }),
  );
}

export function table(columns, rows, { onRowClick, selectedId } = {}) {
  return el('div', { class: 'table-scroll' },
    el('table', {},
      el('thead', {}, el('tr', {}, ...columns.map((c) =>
        el('th', { class: c.num ? 'num' : null, text: c.label })))),
      el('tbody', {}, ...rows.map((row) => {
        const tr = el('tr', {
          class: selectedId && row.__id === selectedId ? 'selected-row' : null,
        }, ...columns.map((c) => {
          const raw = c.get(row);
          return el('td', { class: c.num ? 'num' : null },
            raw && typeof raw === 'object' ? raw : String(raw ?? '—'));
        }));
        if (onRowClick) {
          tr.style.cursor = 'pointer';
          tr.addEventListener('click', () => onRowClick(row));
        }
        return tr;
      })),
    ));
}

export function note(text, kind) {
  return el('div', { class: `note${kind ? ' ' + kind : ''}`, text });
}

export function empty(text) { return el('div', { class: 'empty', text }); }

export function loading(text = 'Loading') { return el('div', { class: 'loading', text }); }

/** Read a CSS custom property from the document root. */
export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
