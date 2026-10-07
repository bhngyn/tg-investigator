// Hash router. URL shape:
//   #/                              All cases (home)
//   #/settings                      global settings
//   #/<scope>/<page>/<args...>?k=v  scope = case id (number) or "all" (the whole pool)
// e.g. #/3/inbox, #/3/channel/example_channel, #/all/search?q=drone

const dec = (x) => { try { return decodeURIComponent(x); } catch { return x; } };   // a bad %-sequence must not blank the app

function parse() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const parts = pathPart.split('/').filter(Boolean).map(dec);
  let query = {};
  try { query = Object.fromEntries(new URLSearchParams(queryPart)); } catch { /* keep empty */ }
  if (!parts.length) return { scope: null, page: 'home', args: [], query };
  if (parts[0] === 'settings' || parts[0] === 'cases') return { scope: null, page: parts[0], args: parts.slice(1), query };
  const scope = parts[0] === 'all' ? 'all' : Number(parts[0]);
  return { scope: Number.isNaN(scope) ? 'all' : scope, page: parts[1] || 'overview', args: parts.slice(2), query };
}

export const route = $state(parse());

window.addEventListener('hashchange', () => {
  const r = parse();
  route.scope = r.scope; route.page = r.page; route.args = r.args; route.query = r.query;
});

/** Case id of the current scope, or null in "all" / global pages. */
export const caseId = () => (typeof route.scope === 'number' ? route.scope : null);

/** Scope segment to use for scoped links: current scope, else "all". */
export const scopeSeg = () => (route.scope === null ? 'all' : String(route.scope));

function build(path, query) {
  const q = query ? new URLSearchParams(Object.entries(query).filter(([, v]) => v !== null && v !== undefined && v !== '')).toString() : '';
  return '#/' + path + (q ? '?' + q : '');
}

/** Link inside the current scope: href('channel', name) -> "#/3/channel/name". */
export function href(page, ...args) {
  let query;
  if (args.length && typeof args[args.length - 1] === 'object') query = args.pop();
  return build([scopeSeg(), page, ...args.map((a) => encodeURIComponent(a))].join('/'), query);
}

/** Link into a specific scope ("all" or a case id). */
export function hrefIn(scope, page = 'overview', ...args) {
  let query;
  if (args.length && typeof args[args.length - 1] === 'object') query = args.pop();
  return build([String(scope), page, ...args.map((a) => encodeURIComponent(a))].join('/'), query);
}

export function go(url) {
  if (location.hash === url) window.dispatchEvent(new HashChangeEvent('hashchange'));
  else location.hash = url;
}

/** Replace the query of the current URL (keeps scope/page/args), without adding history. */
export function setQuery(patch) {
  const next = { ...route.query, ...patch };
  for (const k of Object.keys(next)) if (next[k] === null || next[k] === undefined || next[k] === '') delete next[k];
  const path = location.hash.replace(/^#\/?/, '').split('?')[0];
  history.replaceState(null, '', build(path, next));
  route.query = next;
}
