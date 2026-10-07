// Thin client for the local investigator API (see ../../API.md).
// Every non-GET request carries the per-launch token from <meta name="x-token">.

const TOKEN = document.querySelector('meta[name="x-token"]')?.content || '';

export class ApiError extends Error {
  constructor(status, message, data = null) {
    super(message);
    this.status = status;
    this.data = data;   // the parsed JSON body of the error response, when there was one
  }
}

/** Build "?a=1&b=2", skipping null/undefined/'' values and joining arrays with commas. */
export function qs(params = {}) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '' || v === false) continue;
    p.set(k, Array.isArray(v) ? v.join(',') : v === true ? '1' : String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/** True once a request was refused because the page token is stale (server restarted). */
export let staleToken = false;
const tokenListeners = new Set();
export const onStaleToken = (f) => { tokenListeners.add(f); return () => tokenListeners.delete(f); };

/** FastAPI 422 bodies carry an array of {loc, msg}; turn any detail into a readable sentence. */
function detailText(d, status) {
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    const parts = d.map((e) => (e && e.msg ? `${(e.loc || []).filter((x) => x !== 'body' && x !== 'query' && x !== 'path').join('.') || 'input'}: ${e.msg}` : String(e)));
    return 'Invalid input (' + parts.join('; ') + ')';
  }
  return d && typeof d === 'object' ? JSON.stringify(d) : `Request failed (${status})`;
}

/**
 * api('/api/search', { params: {q: 'x'} })
 * api('/api/cases', { body: {name: 'X'} })            -> POST
 * api('/api/foo', { method: 'DELETE' })
 */
export async function api(path, { params, body, method } = {}) {
  const m = method || (body !== undefined ? 'POST' : 'GET');
  const headers = { Accept: 'application/json' };
  if (m !== 'GET' && m !== 'HEAD') headers['x-token'] = TOKEN;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path + qs(params), {
      method: m,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'The local server is not reachable. Is it still running? Reload the page once it is back.');
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || `${res.status} ${res.statusText}`;
    if (res.status === 403 && /token/i.test(String(msg))) {
      staleToken = true;
      for (const f of tokenListeners) f();
      throw new ApiError(403, 'The server restarted. Reload the page to continue.');
    }
    throw new ApiError(res.status, detailText(msg, res.status), data && typeof data === 'object' ? data : null);
  }
  return data;
}

export const get = (path, params) => api(path, { params });
export const post = (path, body = {}) => api(path, { body });

/** URL for a stored media file (media row `path`). */
export const mediaUrl = (path) => `/media/${path}`;

/** Download URL helper for CSV endpoints (plain links, GET only). */
export const csvUrl = (path, params) => path + qs(params);
