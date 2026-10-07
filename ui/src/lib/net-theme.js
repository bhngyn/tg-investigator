// Canvas colours come from the CSS tokens in app.css, so the graph follows light/dark mode.
const KEYS = ['bg', 'panel', 'panel-2', 'line', 'line-strong', 'text', 'muted', 'faint', 'accent', 'ok', 'warn', 'bad', 'info', 'font'];

/** Read the current theme tokens: {bg, panel, panel2, line, lineStrong, text, …, font}. */
export function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  const out = {};
  for (const k of KEYS) out[k.replace(/-(\w)/g, (_, c) => c.toUpperCase())] = cs.getPropertyValue('--' + k).trim();
  out.font = out.font || 'system-ui, sans-serif';
  out.dark = matchMedia('(prefers-color-scheme: dark)').matches;
  return out;
}

function rgb(hex) {
  let h = (hex || '#888').replace('#', '').trim();
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const n = parseInt(h.slice(0, 6), 16);
  return Number.isNaN(n) ? [136, 136, 136] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Blend two hex colours: t=0 → a, t=1 → b. */
export function mix(a, b, t) {
  const x = rgb(a), y = rgb(b);
  const c = x.map((v, i) => Math.round(v + (y[i] - v) * Math.max(0, Math.min(1, t))));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/** Hex colour with alpha. */
export function alpha(hex, a) {
  const [r, g, b] = rgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}

/** Edge-type → theme key (legend and canvas share this). */
export const EDGE_COLOR_KEY = {
  forward: 'accent', mention: 'info', link: 'ok', description: 'warn',
  comment_forward: 'bad', comment_sender: 'muted', similar: 'muted',
  commented: 'accent', 'posted as channel': 'ok', 'forwarded into': 'warn', admin: 'bad', shared_commenters: 'info',
};

/** Dominant type of a merged edge (largest per-type weight). */
export function dominantType(l) {
  let best = null, bw = -1;
  for (const [t, w] of Object.entries(l.byType || {})) if (w > bw) { best = t; bw = w; }
  return best;
}
