// Formatting helpers. All times from the API are ISO UTC strings.

export const num = (n) => (n === null || n === undefined ? '–' : Number(n).toLocaleString('en-US'));

export function compact(n) {
  if (n === null || n === undefined) return '–';
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k';
  return String(n);
}

export function bytes(b) {
  if (!b && b !== 0) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

/** "2026-09-27 15:11" in UTC (investigators compare against UTC sources). */
export function dt(iso) {
  if (!iso) return '–';
  return iso.replace('T', ' ').slice(0, 16) + ' UTC';
}
export const day = (iso) => (iso ? iso.slice(0, 10) : '–');

/** "3 h ago", "2 d ago", or the date if older than 30 days. */
export function ago(iso) {
  if (!iso) return 'never';
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 0) return 'just now';
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return day(iso);
}

export const plural = (n, one, many = one + 's') => `${num(n)} ${n === 1 ? one : many}`;

/** Parse a JSON-string column (jobs.params, jobs.summary, alerts.detail) safely. */
export function pj(v, fallback = null) {
  if (v === null || v === undefined) return fallback;
  if (typeof v !== 'string') return v;
  try { return JSON.parse(v); } catch { return fallback; }
}

/**
 * Is the FIRST strong letter Arabic/Hebrew-script? (Only for the rare place that needs a boolean, e.g. picking a font
 * class. Text blocks use dir="auto" + CSS unicode-bidi: plaintext instead, so a Russian post quoting one Arabic word
 * keeps its own direction, and each paragraph decides for itself.)
 */
export const isRtl = (t) => {
  const m = /\p{L}/u.exec(t || '');
  return !!m && /[\p{Script=Arabic}\p{Script=Hebrew}]/u.test(m[0]);
};

export const tme = (username, msgId) => `https://t.me/${username}${msgId ? '/' + msgId : ''}`;

/**
 * Only http(s) and tg: URLs that came from Telegram data may become links; anything else
 * (javascript:, data:, file:, ...) is rendered as plain text by the caller. Returns the URL or null.
 */
export function safeUrl(u) {
  const s = String(u ?? '').trim();
  return /^(https?:\/\/|tg:\/\/)[^\s]/i.test(s) ? s : null;
}
/** Is this URL on Telegram itself (t.me / telegram.me / tg://)? */
export function isTelegramUrl(u) {
  return /^(https?:\/\/(www\.)?(t|telegram)\.me(\/|$|\?)|tg:\/\/)/i.test(String(u ?? '').trim());
}
export const EXTERNAL_TITLE = 'Opens an external site in a new tab. The site owner can see your IP address.';
