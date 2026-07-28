/* Chart primitives.
 *
 * Rules held throughout, per the project's visualisation guidelines:
 *   - one y-axis, never two;
 *   - at most three categorical series, assigned in fixed slot order and never
 *     recoloured when a filter changes the series count;
 *   - sequential (one hue, light→dark) for magnitude, diverging (blue↔red with
 *     a neutral midpoint) for signed values;
 *   - a legend whenever there is more than one series, plus selective direct
 *     labels, so colour never carries meaning alone;
 *   - every chart has a table-view twin.
 */

import { el, svg, fmt, tooltip, token } from './ui.js';

export const SERIES = ['--series-1', '--series-2', '--series-3'];
export const seriesColor = (i) => `var(${SERIES[i % SERIES.length]})`;

const SEQ_STEPS = ['--seq-100', '--seq-200', '--seq-300', '--seq-400',
  '--seq-500', '--seq-600', '--seq-700'];

/** Sequential blue ramp: t in [0,1] → hex. Light near zero, dark at the top. */
export function sequential(t) {
  const clamped = Math.max(0, Math.min(1, t));
  const idx = Math.min(SEQ_STEPS.length - 1, Math.floor(clamped * SEQ_STEPS.length));
  return token(SEQ_STEPS[idx]) || '#2a78d6';
}

/** Diverging ramp: v in [-1,1] → colour, neutral grey at zero. */
export function diverging(v) {
  const clamped = Math.max(-1, Math.min(1, v));
  const mid = token('--div-mid') || '#f0efec';
  if (Math.abs(clamped) < 0.04) return mid;
  const end = clamped > 0 ? (token('--div-pos') || '#d03b3b') : (token('--div-neg') || '#2a78d6');
  return mixHex(mid, end, Math.abs(clamped));
}

function mixHex(a, b, t) {
  const pa = hexToRgb(a); const pb = hexToRgb(b);
  if (!pa || !pb) return b;
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function hexToRgb(hex) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(String(hex).trim());
  if (m) return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
  const rgb = /rgba?\(([^)]+)\)/.exec(String(hex));
  if (rgb) return rgb[1].split(',').slice(0, 3).map((v) => parseInt(v, 10));
  return null;
}

/* --- Pitch --------------------------------------------------------------- */

const L = 105; const W = 68;
const toY = (y) => W - y;   // pitch y grows upward; SVG y grows downward

/** Pitch markings as an SVG group. Drawn once and reused by every overlay. */
function pitchMarkings() {
  const line = (attrs) => svg('path', { class: 'pitch-line', ...attrs });
  return svg('g', {},
    svg('rect', { class: 'pitch-bg', x: 0, y: 0, width: L, height: W, rx: 1 }),
    line({ d: `M0,0 H${L} V${W} H0 Z` }),
    line({ d: `M${L / 2},0 V${W}` }),
    svg('circle', { class: 'pitch-line', cx: L / 2, cy: W / 2, r: 9.15 }),
    svg('circle', { cx: L / 2, cy: W / 2, r: 0.5, fill: 'var(--pitch-line)' }),
    // Penalty areas (16.5 m deep, 40.32 m wide).
    line({ d: `M0,${toY(54.16)} H16.5 V${toY(13.84)} H0` }),
    line({ d: `M${L},${toY(54.16)} H${L - 16.5} V${toY(13.84)} H${L}` }),
    // Six-yard boxes.
    line({ d: `M0,${toY(43.16)} H5.5 V${toY(24.84)} H0` }),
    line({ d: `M${L},${toY(43.16)} H${L - 5.5} V${toY(24.84)} H${L}` }),
    svg('circle', { cx: 11, cy: W / 2, r: 0.4, fill: 'var(--pitch-line)' }),
    svg('circle', { cx: L - 11, cy: W / 2, r: 0.4, fill: 'var(--pitch-line)' }),
    // Goals.
    line({ d: `M0,${toY(37.66)} H-1.8 V${toY(30.34)} H0` }),
    line({ d: `M${L},${toY(37.66)} H${L + 1.8} V${toY(30.34)} H${L}` }),
  );
}

/**
 * Render a pitch with player tokens.
 * `slots`: [{x, y (normalised 0-1), position, player, shirt_number,
 *            overall_rating, out_of_position, player_id}]
 */
export function pitchView(slots, {
  overlay = null, kitColor = 'var(--series-1)', onSelect = null,
  selectedId = null, showNames = true,
} = {}) {
  const root = svg('svg', {
    class: 'pitch', viewBox: `-3 -3 ${L + 6} ${W + 6}`,
    role: 'img', 'aria-label': 'Pitch with the selected lineup',
  }, pitchMarkings());

  if (overlay) root.appendChild(overlay);

  for (const s of slots) {
    const cx = (s.x ?? 0.5) * L;
    const cy = toY((s.y ?? 0.5) * W);
    const cls = ['token', s.out_of_position ? 'oop' : '', selectedId === s.player_id ? 'selected' : '']
      .filter(Boolean).join(' ');

    const g = svg('g', { class: cls, transform: `translate(${cx},${cy})` },
      svg('circle', { class: 'shirt', r: 3.1, fill: kitColor }),
      svg('text', { class: 'num', y: 0.9, text: s.shirt_number ?? (s.position || '') }),
      showNames ? svg('text', { class: 'name', y: 6.2, text: s.player || '—' }) : null,
      showNames && s.overall_rating
        ? svg('text', { class: 'rating', y: 8.9, text: `${Math.round(s.overall_rating)} · ${s.position}` })
        : null,
    );

    g.addEventListener('mouseenter', (ev) => {
      const rows = [['Position', s.position]];
      if (s.overall_rating) rows.push(['Rating', Math.round(s.overall_rating)]);
      if (s.position_fit !== undefined && s.position_fit !== null) {
        rows.push(['Positional fit', fmt.pct(s.position_fit * 100, 0)]);
      }
      if (s.effective_level) rows.push(['Effective level', fmt.num(s.effective_level, 1)]);
      if (s.natural_position && s.natural_position !== s.position) {
        rows.push(['Natural', s.natural_position]);
      }
      tooltip.show(ev.clientX, ev.clientY, s.player || 'Empty slot', rows);
    });
    g.addEventListener('mousemove', (ev) => {
      tooltip.show(ev.clientX, ev.clientY, s.player || 'Empty slot', []);
    });
    g.addEventListener('mouseleave', () => tooltip.hide());
    if (onSelect) g.addEventListener('click', () => onSelect(s));

    root.appendChild(g);
  }
  return root;
}

/** Heat overlay from a normalised [rows][cols] grid, using the sequential ramp. */
export function heatOverlay(grid, { opacity = 0.85 } = {}) {
  if (!grid || !grid.length) return null;
  const rows = grid.length; const cols = grid[0].length;
  const max = Math.max(...grid.flat());
  if (max <= 0) return null;

  const cellW = L / cols; const cellH = W / rows;
  const g = svg('g', { opacity });
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const v = grid[r][c] / max;
      if (v < 0.02) continue;
      g.appendChild(svg('rect', {
        // Grid row 0 is y≈0 (bottom of the pitch), so flip for SVG.
        x: c * cellW, y: W - (r + 1) * cellH,
        width: cellW + 0.02, height: cellH + 0.02,
        fill: sequential(v), opacity: 0.25 + 0.75 * v,
      }));
    }
  }
  return g;
}

/** Signed zone-control overlay: diverging ramp plus a printed value per cell. */
export function zoneOverlay(control, { labels = true } = {}) {
  if (!control || !control.length) return null;
  const rows = control.length; const cols = control[0].length;
  const cellW = L / cols; const cellH = W / rows;
  const g = svg('g', {});
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const v = control[r][c];
      const x = c * cellW; const y = W - (r + 1) * cellH;
      g.appendChild(svg('rect', {
        x: x + 0.25, y: y + 0.25,
        width: cellW - 0.5, height: cellH - 0.5,   // 2px-equivalent surface gap
        fill: diverging(v), opacity: 0.8, rx: 0.6,
      }));
      if (labels && Math.abs(v) >= 0.08) {
        g.appendChild(svg('text', {
          x: x + cellW / 2, y: y + cellH / 2 + 1.1,
          'text-anchor': 'middle', 'font-size': 2.6, 'font-weight': 650,
          fill: 'var(--text-primary)', text: `${v > 0 ? '+' : ''}${Math.round(v * 100)}`,
        }));
      }
    }
  }
  return g;
}

export function scaleLegend(label, from, to, { diverging: isDiv = false } = {}) {
  const ramp = isDiv
    ? `linear-gradient(90deg, ${token('--div-neg')}, ${token('--div-mid')}, ${token('--div-pos')})`
    : `linear-gradient(90deg, ${token('--seq-100')}, ${token('--seq-400')}, ${token('--seq-700')})`;
  return el('div', { class: 'scale-legend' },
    el('span', { text: label }),
    el('span', { text: from }),
    el('span', { class: 'ramp', style: `background:${ramp}` }),
    el('span', { text: to }),
  );
}

export function legend(items) {
  return el('div', { class: 'legend' }, ...items.map(({ label, color }) =>
    el('span', { class: 'item' },
      el('span', { class: 'swatch', style: `background:${color}` }),
      el('span', { text: label }))));
}

/* --- Line chart ---------------------------------------------------------- */

/**
 * Single-axis line chart with a crosshair tooltip.
 * `series`: [{ label, points: [{x, y}], color }]
 */
export function lineChart(series, {
  height = 190, yLabel = '', yMin = null, yMax = null,
  xFormat = (v) => v, yFormat = (v) => fmt.num(v, 0), reference = null,
} = {}) {
  const width = 640;
  // Right padding leaves room for the direct end-labels; without it the series
  // name is clipped by the viewBox.
  const labelRoom = series.length
    ? Math.max(...series.map((s) => s.label.length)) * 5.6 + 12 : 16;
  const pad = { top: 12, right: Math.min(120, Math.max(16, labelRoom)), bottom: 26, left: 42 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const all = series.flatMap((s) => s.points);
  if (!all.length) return el('div', { class: 'empty', text: 'No data for this chart' });

  const xs = all.map((p) => p.x); const ys = all.map((p) => p.y);
  const x0 = Math.min(...xs); const x1 = Math.max(...xs);
  const lo = yMin ?? Math.min(...ys); const hi = yMax ?? Math.max(...ys);
  const span = hi - lo || 1;

  const sx = (x) => pad.left + ((x - x0) / ((x1 - x0) || 1)) * plotW;
  const sy = (y) => pad.top + plotH - ((y - lo) / span) * plotH;

  const ticks = 4;
  const gridLines = [];
  for (let i = 0; i <= ticks; i += 1) {
    const v = lo + (span * i) / ticks;
    gridLines.push(svg('line', {
      class: 'grid-line', x1: pad.left, x2: pad.left + plotW, y1: sy(v), y2: sy(v),
    }));
    gridLines.push(svg('text', {
      class: 'axis-label', x: pad.left - 6, y: sy(v) + 3, 'text-anchor': 'end',
      text: yFormat(v),
    }));
  }

  const paths = series.map((s, i) => {
    const d = s.points
      .map((p, idx) => `${idx === 0 ? 'M' : 'L'}${sx(p.x).toFixed(2)},${sy(p.y).toFixed(2)}`)
      .join(' ');
    return svg('path', {
      d, fill: 'none', stroke: s.color || seriesColor(i),
      'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    });
  });

  // Direct end-labels: identity without relying on colour alone.
  const endLabels = series.map((s, i) => {
    const last = s.points[s.points.length - 1];
    if (!last) return null;
    return svg('text', {
      class: 'series-label', x: sx(last.x) + 4, y: sy(last.y) + 3,
      fill: s.color || seriesColor(i), text: s.label,
    });
  });

  const crosshair = svg('line', {
    class: 'axis-line', y1: pad.top, y2: pad.top + plotH, opacity: 0,
    'stroke-dasharray': 'none',
  });
  const marker = svg('circle', { r: 3.5, fill: 'var(--surface-1)', 'stroke-width': 2, opacity: 0 });

  const refEls = reference
    ? [svg('line', {
      class: 'axis-line', x1: pad.left, x2: pad.left + plotW,
      y1: sy(reference.value), y2: sy(reference.value), stroke: 'var(--axis)',
    }), svg('text', {
      class: 'axis-label', x: pad.left + plotW, y: sy(reference.value) - 4,
      'text-anchor': 'end', text: reference.label,
    })]
    : [];

  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `${yLabel || 'Line chart'} — ${series.map((s) => s.label).join(', ')}`,
  },
  ...gridLines, ...refEls,
  svg('line', { class: 'axis-line', x1: pad.left, x2: pad.left + plotW, y1: pad.top + plotH, y2: pad.top + plotH }),
  svg('text', { class: 'axis-label', x: pad.left, y: height - 8, text: xFormat(x0) }),
  svg('text', { class: 'axis-label', x: pad.left + plotW, y: height - 8, 'text-anchor': 'end', text: xFormat(x1) }),
  ...paths, ...endLabels, crosshair, marker,
  svg('rect', {
    x: pad.left, y: pad.top, width: plotW, height: plotH, fill: 'transparent',
  }),
  );

  const hit = root.lastChild;
  hit.addEventListener('mousemove', (ev) => {
    const box = root.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * width;
    const xv = x0 + ((px - pad.left) / plotW) * (x1 - x0);
    const primary = series[0];
    let nearest = primary.points[0];
    for (const p of primary.points) {
      if (Math.abs(p.x - xv) < Math.abs(nearest.x - xv)) nearest = p;
    }
    crosshair.setAttribute('x1', sx(nearest.x));
    crosshair.setAttribute('x2', sx(nearest.x));
    crosshair.setAttribute('opacity', 0.6);
    marker.setAttribute('cx', sx(nearest.x));
    marker.setAttribute('cy', sy(nearest.y));
    marker.setAttribute('stroke', primary.color || seriesColor(0));
    marker.setAttribute('opacity', 1);
    tooltip.show(ev.clientX, ev.clientY, xFormat(nearest.x),
      series.map((s, i) => {
        const match = s.points.find((p) => p.x === nearest.x);
        return [s.label, match ? yFormat(match.y) : '—'];
      }));
  });
  hit.addEventListener('mouseleave', () => {
    crosshair.setAttribute('opacity', 0);
    marker.setAttribute('opacity', 0);
    tooltip.hide();
  });

  return el('div', { class: 'chart' }, root);
}

/* --- Horizontal bar chart ------------------------------------------------ */

/** `items`: [{label, value, display, color}] — one colour per entity, never by rank. */
export function barChart(items, { max = 100 } = {}) {
  return el('div', { class: 'bars' }, ...items.map((it) => {
    const pct = Math.max(0, Math.min(100, (it.value / max) * 100));
    return el('div', { class: 'bar-row' },
      el('div', { class: 'bar-label', text: it.label }),
      el('div', { class: 'bar-track' },
        el('div', {
          class: 'bar-fill',
          style: `width:${pct}%;background:${it.color || seriesColor(0)}`,
        })),
      el('div', { class: 'bar-value', text: it.display ?? fmt.num(it.value, 0) }));
  }));
}

/* --- Table-view twin ----------------------------------------------------- */

/** Wrap a chart with a chart/table toggle, so every value is reachable. */
export function withTableView(chartNode, tableNode, { defaultView = 'chart' } = {}) {
  const body = el('div', {});
  const toggle = el('div', { class: 'view-toggle' });
  let view = defaultView;

  const render = () => {
    body.replaceChildren(view === 'chart' ? chartNode : tableNode);
    for (const b of toggle.children) {
      b.setAttribute('aria-pressed', String(b.dataset.view === view));
    }
  };
  for (const v of ['chart', 'table']) {
    toggle.appendChild(el('button', {
      dataset: { view: v }, text: v === 'chart' ? 'Chart' : 'Table',
      onClick: () => { view = v; render(); },
    }));
  }
  render();
  return el('div', {}, el('div', { style: 'display:flex;justify-content:flex-end;margin-bottom:.5rem' }, toggle), body);
}
