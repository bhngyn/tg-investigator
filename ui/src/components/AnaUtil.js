// Small helpers shared by the Ana* analysis tabs (client-side downloads, CSV, loading guard).

/** Save text as a file, entirely in the browser (Blob + temporary link). */
export function download(filename, text, type = 'text/plain') {
  const blob = new Blob([text], { type: type + ';charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const cell = (v) => {
  if (v === null || v === undefined) return '';
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
  // Neutralise spreadsheet formula injection (text comes from Telegram) and quote as needed.
  // Only text is neutralised; real numbers (negative coordinates, deltas) stay numeric.
  const safe = typeof v === 'string' && /^[=+\-@\t\r]/.test(s) && !/^[+-]?\d+([.,]\d+)?$/.test(s) ? "'" + s : s;
  return /[",\n\r]/.test(safe) ? '"' + safe.replace(/"/g, '""') + '"' : safe;
};

/** rows: array of objects; cols: array of keys (header = key). Returns CSV text with a BOM for Excel. */
export function toCsv(rows, cols) {
  return '﻿' + [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\r\n') + '\r\n';
}

export const stamp = () => new Date().toISOString().replace(/[-:]/g, '').slice(0, 15);

/** Minutes -> "4 min", "3 h 20 min", "2 d 4 h". */
export function span(min) {
  if (min === null || min === undefined) return '–';
  if (min < 1) return 'under a minute';
  if (min < 60) return `${Math.round(min)} min`;
  if (min < 1440) { const h = Math.floor(min / 60), m = Math.round(min % 60); return m ? `${h} h ${m} min` : `${h} h`; }
  const d = Math.floor(min / 1440), h = Math.round((min % 1440) / 60);
  return h ? `${d} d ${h} h` : `${d} d`;
}

/** Parse "t.me/name/123", "https://t.me/name/123?single", or "https://t.me/s/name/123". */
export function parseTme(s) {
  const m = /(?:^|\/\/)(?:www\.)?t\.me\/(?:s\/)?([A-Za-z0-9_]{3,})\/(\d+)/i.exec((s || '').trim());
  return m ? { channel: m[1], msg_id: Number(m[2]) } : null;
}
