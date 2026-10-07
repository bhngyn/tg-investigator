// Multilingual search expansion: URL <-> API param helpers shared by Search, Comments, Alerts, Settings.
// State shape used by ExpansionChips: { expand: boolean, drop: string[], skip: string[] }.

const list = (s) => (s ? String(s).split(',').map((x) => x.trim()).filter(Boolean) : []);

/** Read expand/drop/skip out of a route query. Expansion is ON unless expand=0. */
export function expansionFromQuery(Q) {
  return { expand: Q.expand !== '0', drop: list(Q.drop), skip: list(Q.skip) };
}

/** State -> URL query patch (defaults are omitted so URLs stay short). */
export function expansionToQuery(st) {
  return { expand: st.expand ? null : '0', drop: st.drop.length ? st.drop.join(',') : null, skip: st.skip.length ? st.skip.join(',') : null };
}

/** State -> API request params (only what differs from the defaults). */
export function expansionParams(st) {
  const p = {};
  if (!st.expand) p.expand = '0';
  if (st.drop.length) p.drop = st.drop.join(',');
  if (st.skip.length) p.skip = st.skip.join(',');
  return p;
}

/** Accept {groups:[…]}, a bare groups array (frozen rule JSON) or {expansion:{…}}. */
export function normaliseExpansion(x, extra = {}) {
  if (!x) return null;
  if (Array.isArray(x)) return { groups: x, ...extra };
  if (x.expansion && typeof x.expansion === 'object') return normaliseExpansion(x.expansion, extra);
  return { ...extra, ...x, groups: x.groups || [] };
}

/** Script badge for a term when the API gave no language. */
export function scriptOf(t) {
  if (/[؀-ۿ]/.test(t)) return 'ar';
  if (/[іїєґІЇЄҐ]/.test(t)) return 'uk';
  if (/[Ѐ-ӿ]/.test(t)) return 'ru';
  return 'en';
}
