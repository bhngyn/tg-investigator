// Search-term highlighting shared by post and comment cards (and media excerpts).
// Case-insensitive; Arabic diacritics/tatweel are not normalised. Returns null when there is nothing to mark.

// Cyrillic terms of 6+ letters lose one inflectable final letter (Одесса also lights up Одессе), like the search does.
const stem = (x) => (/^[Ѐ-ӿ]{6,}$/.test(x) && /[аяоеьйыиую]$/i.test(x) ? x.slice(0, -1) : x);

/** @returns {{seg: string, hit: boolean}[] | null} */
export function highlightParts(text, terms) {
  const t = [...new Set((terms || []).map((x) => stem(String(x).trim())).filter((x) => x.length > 1))].sort((a, b) => b.length - a.length);
  if (!t.length || !text) return null;
  const re = new RegExp('(' + t.map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'giu');
  return text.split(re).map((seg, i) => ({ seg, hit: i % 2 === 1 }));
}

/** Server excerpts (media_hits) are plain stored text with no markers: show them exactly as stored and highlight client-side from the terms. */
export function excerptParts(ex, terms) {
  const s = String(ex ?? '');
  return highlightParts(s, terms) || [{ seg: s, hit: false }];
}

/** 75 -> "1:15", 3725 -> "1:02:05" */
export function clock(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '';
  let s = Math.max(0, Math.round(Number(sec)));
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}
