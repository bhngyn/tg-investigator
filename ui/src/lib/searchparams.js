// Search parameters that live in the URL query and go straight to the API (posts search, exports, terms, map).
export const POST_FILTER_KEYS = ['q', 'channels', 'date_from', 'date_to', 'fwd', 'forwarded', 'media', 'review', 'tag', 'untagged', 'sensitive', 'min_views', 'has_notes', 'edited', 'deleted', 'new', 'sort', 'expand', 'drop', 'skip', 'in', 'entity', 'place', 'near', 'unambiguous'];

/** Help text for the "only unambiguous place names" option (`unambiguous=1`). */
export const UNAMBIGUOUS_HELP = 'Hide mentions whose name fits several places (e.g. several villages called Mykolaivka).';

/** Sources a post can match in (spec: `in=text,versions,ocr,speech`, default all). */
export const IN_SOURCES = [
  ['text', 'Post text'],
  ['versions', 'Earlier versions'],
  ['ocr', 'Text in images (OCR)'],
  ['speech', 'Speech in audio/video'],
  ['names', 'Channel & forward names'],
];
/** Sources on when `in=` is absent (channel and forward names are opt-in). */
export const DEFAULT_IN = ['text', 'versions', 'ocr', 'speech'];

/** Sources a comment can match in: its own words, OCR / speech of its attached media, sender & channel names (opt-in). */
export const COMMENT_IN_SOURCES = [
  ['text', 'Comment text'],
  ['ocr', 'Text in images (OCR)'],
  ['speech', 'Speech in audio/video'],
  ['names', 'Sender & channel names'],
];
export const DEFAULT_COMMENT_IN = ['text', 'ocr', 'speech'];

export const ENTITY_KINDS = {
  phone: 'Phone numbers', btc: 'Bitcoin addresses', eth: 'Ethereum addresses', trx: 'Tron addresses', card: 'Card numbers (masked)',
  unit: 'Military unit numbers', callsign: 'Call signs', plate: 'Vehicle plates',
};
export const kindLabel = (k) => ENTITY_KINDS[k] || k;
export const ENTITY_CAVEAT = 'Patterns found in text — claims to check, not verified identities.';
export const PLACES_CAVEAT = 'Places mentioned in text (not verified locations); ambiguous names are marked.';

/** "phone:+249123" -> {kind, value}; values may contain colons. */
export function parseEntity(s) {
  const i = String(s).indexOf(':');
  return i < 0 ? { kind: '', value: s } : { kind: s.slice(0, i), value: s.slice(i + 1) };
}

/** Tolerant list extraction: bare array, or {key: [...]} for the first matching key. */
export function listOf(r, ...keys) {
  if (Array.isArray(r)) return r;
  for (const k of [...keys, 'results', 'items', 'rows']) if (Array.isArray(r?.[k])) return r[k];
  return [];
}

/** Search API `params` (object or JSON string) -> UI query for the Search/Comments URL (no paging, no case). */
export function paramsToQuery(params) {
  let p = params;
  if (typeof p === 'string') { try { p = JSON.parse(p); } catch { p = {}; } }
  const out = {};
  for (const [k, v] of Object.entries(p || {})) {
    if (['case', 'limit', 'offset', 'scope', 'log'].includes(k) || k.startsWith('_') || v === null || v === undefined || v === '') continue;
    if (k === 'since') { out.since = v; continue; }
    out[k] = Array.isArray(v) ? v.join(',') : String(v);
  }
  return out;
}

/** Does a logged run / saved search belong to the comments search? (export and saved runs keep their kind in params._kind / scope) */
export function isCommentsRun(endpoint, params) {
  let p = params;
  if (typeof p === 'string') { try { p = JSON.parse(p); } catch { p = {}; } }
  return endpoint === 'comments' || endpoint === 'threads' || p?._kind === 'comments' || p?._kind === 'threads' || p?.scope === 'comments';
}

const MEDIA_LABEL = { photo: 'has photo', video: 'has video', document: 'has file', audio: 'has audio', downloaded: 'media stored', refused: 'media not stored yet', none: 'text only' };
const REVIEW_TXT = { unreviewed: 'unreviewed', relevant: 'relevant', verify: 'needs verification', irrelevant: 'not relevant' };
const csv = (v) => String(v ?? '').split(',').map((x) => x.trim()).filter(Boolean);

/**
 * Short human labels for the filters/options of a search (`params` object or JSON string), so a list row or a
 * dialog says what really ran: "exact spelling only", "-2 spellings", "+ phone: +2499…", "within 50 km of …".
 * Returns [{k, label, kind}] where kind is 'exp' (expansion), 'filter' or 'in'. The query text itself is not included.
 */
export function describeParams(params, { skip = [] } = {}) {
  let p = params;
  if (typeof p === 'string') { try { p = JSON.parse(p); } catch { p = {}; } }
  p = p || {};
  const out = [];
  const add = (k, label, kind = 'filter') => { if (!skip.includes(k)) out.push({ k, label, kind }); };
  if (String(p.expand) === '0') add('expand', 'exact spelling only', 'exp');
  const drop = csv(p.drop).filter((x) => !x.startsWith('+'));
  if (drop.length) add('drop', `${drop.length} spelling${drop.length > 1 ? 's' : ''} left out`, 'exp');
  const skipS = csv(p.skip);
  if (skipS.length) add('skip', `${skipS.length} word${skipS.length > 1 ? 's' : ''} not expanded`, 'exp');
  if (p.in) {
    const src = isCommentsRun(null, p) ? COMMENT_IN_SOURCES : IN_SOURCES;
    add('in', 'in: ' + csv(p.in).map((k) => src.find(([x]) => x === k)?.[1] || k).join(', '), 'in');
  }
  for (const e of csv(p.entity)) { const { kind, value } = parseEntity(e); add('entity', `identifier ${kindLabel(kind)}: ${value}`); }
  if (p.place) add('place', `${csv(p.place).length} place${csv(p.place).length > 1 ? 's' : ''} mentioned`);
  if (String(p.unambiguous) === '1') add('unambiguous', 'only unambiguous place names');
  if (p.near) {
    const [la, lo, km] = String(p.near).split(',');
    add('near', `within ${km || 25} km of ${p.near_label || `${la}, ${lo}`}`);
  }
  if (p.date_from || p.date_to) add('date', `dates ${p.date_from || '…'} → ${p.date_to || '…'}`);
  if (p.since) add('since', 'only items newer than a set time');
  if (p.channels) add('channels', csv(p.channels).map((c) => '@' + c).join(', '));
  if (p.review) add('review', REVIEW_TXT[p.review] || p.review);
  if (p.tag) add('tag', 'tagged');
  if (String(p.untagged) === '1') add('untagged', 'untagged');
  if (p.media) add('media', MEDIA_LABEL[p.media] || p.media);
  if (p.forwarded !== undefined && p.forwarded !== '') add('forwarded', String(p.forwarded) === '1' ? 'only forwards' : 'only original posts');
  if (p.fwd) add('fwd', `forwarded from @${p.fwd}`);
  if (p.min_views) add('min_views', `≥ ${p.min_views} views`);
  if (String(p.sensitive) === '1') add('sensitive', 'marked sensitive');
  if (String(p.has_notes) === '1') add('has_notes', 'has notes');
  if (String(p.edited) === '1') add('edited', 'edited');
  if (String(p.deleted) === '1') add('deleted', 'found deleted');
  if (p.sender) add('sender', 'one account');
  if (p.sender_type) add('sender_type', p.sender_type);
  return out;
}
