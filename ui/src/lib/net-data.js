// Data builders for the Network page. Each builder returns a view model:
//   { nodes: [{id, label, handle?, kind, ...}], links: [{source, target, weight, byType:{type: w}}] }
// `id` is the channel username for channel nodes, or a prefixed id ("acct:123") for people.
import { get } from './api.js';

/** Edge types stored by the backend, with plain-English meaning. Order = legend order. */
export const EDGE_TYPES = [
  { id: 'forward', label: 'Forward', help: 'A post in A is a forward of a post from B' },
  { id: 'mention', label: 'Mention', help: 'A post in A mentions @B' },
  { id: 'link', label: 'Link', help: 'A post in A links to t.me/B' },
  { id: 'description', label: 'Description link', help: "A's channel description links to or mentions B" },
  { id: 'comment_forward', label: 'Comment forward', help: "Someone in A's comment section forwarded a post from B" },
  { id: 'comment_sender', label: 'Comment as channel', help: "Channel B posted in A's comment section under its own identity" },
  { id: 'similar', label: 'Similar (Telegram)', help: "Telegram lists B among A's similar channels (logged-in discovery)" },
];
export const EDGE_LABEL = Object.fromEntries(EDGE_TYPES.map((t) => [t.id, t.label]));

const graphParams = (o) => ({
  hide_dismissed: o.hideDismissed ? '1' : '0',
  channels_only: o.channelsOnly ? '1' : '0',
  case: o.caseId ?? undefined,
});

/**
 * /api/graph collapses types per pair, so fetch one request per selected type (in parallel)
 * and merge, keeping per-type weights. min_weight is applied here, on the merged sum.
 * Returns { nodes: graph node rows, links: [{source,target,weight,byType}] }.
 */
export async function fetchGraph({ types, caseId = null, hideDismissed = true, channelsOnly = true }) {
  if (!types.length) return { nodes: [], links: [] };
  const base = graphParams({ caseId, hideDismissed, channelsOnly });
  const parts = await Promise.all(types.map((t) => get('/api/graph', { ...base, types: t, min_weight: 1 }).then((r) => ({ t, r }))));
  const nodes = new Map();
  const links = new Map();
  for (const { t, r } of parts) {
    for (const n of r.nodes || []) if (!nodes.has(n.username)) nodes.set(n.username, n);
    for (const l of r.links || []) {
      const k = l.source + '\u0000' + l.target;
      let e = links.get(k);
      if (!e) links.set(k, (e = { source: l.source, target: l.target, weight: 0, byType: {} }));
      e.byType[t] = (e.byType[t] || 0) + l.weight;
      e.weight += l.weight;
    }
  }
  return { nodes: [...nodes.values()], links: [...links.values()] };
}

/** Which node counts as "core": in the case (case scope) or seed/watched (whole archive). */
export const isCore = (n, caseId) => (caseId != null ? !!n.in_case : n.status === 'seed' || n.status === 'watched');

function channelNode(n, caseId) {
  return {
    id: n.username, handle: n.username, label: n.title || '@' + n.username, title: n.title, kind: 'channel',
    type: n.type, status: n.status, subscribers: n.subscribers, posts: n.posts, crawled: !!n.crawled,
    relevant: n.relevant, in_case: n.in_case, core: isCore(n, caseId),
  };
}

/** Amplification view: channels and the forwards/mentions/links between them. */
export function buildAmplification(raw, { caseId, coreOnly, minWeight }) {
  let nodes = raw.nodes.map((n) => channelNode(n, caseId));
  let links = raw.links.filter((l) => l.weight >= minWeight);
  if (coreOnly) {
    const core = new Set(nodes.filter((n) => n.core).map((n) => n.id));
    nodes = nodes.filter((n) => core.has(n.id));
    links = links.filter((l) => core.has(l.source) && core.has(l.target));
  } else {
    // Neighbours only appear when an edge (above min weight) still reaches them.
    const touched = new Set(links.flatMap((l) => [l.source, l.target]));
    nodes = nodes.filter((n) => n.core || touched.has(n.id));
  }
  const ids = new Set(nodes.map((n) => n.id));
  links = links.filter((l) => ids.has(l.source) && ids.has(l.target)).map((l) => ({ ...l }));
  return { nodes, links };
}

/**
 * Discovery view: core channels plus ranked candidates (outside the case / seed+watched),
 * with arrows from the core channels that point at each candidate.
 */
export function buildDiscovery(raw, candidates, { caseId, minWeight }) {
  const byName = new Map(raw.nodes.map((n) => [n.username, n]));
  const cand = new Map(candidates.map((c, i) => [c.username, { ...c, rank: i + 1 }]));
  const maxScore = Math.max(1, ...candidates.map((c) => c.score || 0));
  const nodes = [];
  for (const n of raw.nodes) if (isCore(n, caseId) && !cand.has(n.username)) nodes.push({ ...channelNode(n, caseId), core: true });
  const coreIds = new Set(nodes.map((n) => n.id));
  for (const c of cand.values()) {
    const g = byName.get(c.username) || {};
    nodes.push({
      id: c.username, handle: c.username, label: c.title || g.title || '@' + c.username, title: c.title || g.title, kind: 'candidate',
      type: c.type ?? g.type, status: c.status ?? g.status, subscribers: c.subscribers ?? g.subscribers, posts: c.posts ?? g.posts,
      crawled: !!(c.last_crawled || g.crawled), core: false, in_case: false,
      score: c.score, scoreN: (c.score || 0) / maxScore, rank: c.rank, reasons: c.reasons, sources: c.sources, cand_by_type: c.by_type,
    });
  }
  const ids = new Set(nodes.map((n) => n.id));
  const links = raw.links
    .filter((l) => l.weight >= minWeight && ids.has(l.source) && ids.has(l.target) && coreIds.has(l.source))
    .map((l) => ({ ...l, faint: !cand.has(l.target) }));
  return { nodes, links };
}

const splitList = (s) => (Array.isArray(s) ? s : String(s || '').split(',')).map((x) => x.trim()).filter(Boolean);

/**
 * Shared-people view from /api/overlap. Two layouts:
 *  - 'bipartite': comment sections (channels) + the people/identities/forward sources seen in 2+ of them.
 *  - 'projected': comment sections only, joined by the number of shared commenters (matrix).
 * `limit` caps the number of shared entities (strongest first).
 */
export function buildShared(o, { layout = 'bipartite', limit = 150, include = { accounts: true, identities: true, forwards: true, admins: true } } = {}) {
  const groups = o?.groups || [];
  const chanOfGroup = new Map(groups.map((g) => [g.group_id, g.channel]));
  const sections = new Map();
  const section = (ch, extra = {}) => {
    if (!ch) return null;
    if (!sections.has(ch)) sections.set(ch, { id: ch, handle: ch, label: '@' + ch, kind: 'section', commenters: 0, ...extra });
    return sections.get(ch);
  };
  for (const g of groups) section(g.channel, { label: g.title || '@' + g.channel, title: g.title, commenters: g.commenters || 0 });

  const links = [];
  if (layout === 'projected') {
    for (const m of o?.matrix || []) {
      const a = chanOfGroup.get(m.g1), b = chanOfGroup.get(m.g2);
      if (!a || !b) continue;
      section(a); section(b);
      links.push({ source: a, target: b, weight: m.shared, byType: { shared_commenters: m.shared }, undirected: true });
    }
    return { nodes: [...sections.values()], links };
  }

  const people = [];
  const personLabel = (p) => p.name || (p.username ? '@' + p.username : `user ${p.user_id}`);
  if (include.accounts) {
    for (const a of o?.accounts || []) {
      people.push({
        id: 'acct:' + a.user_id, kind: 'account', label: personLabel(a), handle: a.username || null, user_id: a.user_id,
        phone_country: a.phone_country, bot: a.bot, total: a.total, first: a.first, last: a.last, admin_of: a.admin_of || [],
        strength: (a.sections?.length || 0) * 1000 + (a.total || 0),
        edges: (a.sections || []).map((s) => ({ ch: s.channel, w: s.n, t: 'commented' })),
      });
    }
  }
  if (include.admins) {
    for (const c of o?.cross_admins || []) {
      const id = 'acct:' + c.user_id;
      let p = people.find((x) => x.id === id);
      if (!p) {
        p = { id, kind: 'account', label: personLabel(c), handle: c.username || null, user_id: c.user_id, phone_country: c.phone_country,
          bot: c.bot, total: c.total_elsewhere, admin_of: c.admin_of || [], strength: 0, edges: [] };
        people.push(p);
        for (const s of c.comments_elsewhere || []) p.edges.push({ ch: s.channel, w: s.n, t: 'commented' });
      }
      p.cross_admin = true;
      p.strength += 1e6;   // the flagship signal: always keep
      for (const r of c.admin_of || []) if (!p.edges.some((e) => e.ch === r.channel && e.t === 'admin')) p.edges.push({ ch: r.channel, w: 1, t: 'admin', role: r.role });
    }
  }
  if (include.identities) {
    for (const c of o?.channel_identities || []) {
      const chs = splitList(c.channels);
      people.push({
        id: 'ident:' + c.chat_id, kind: 'identity', label: c.title || (c.username ? '@' + c.username : `chat ${c.chat_id}`), handle: c.username || null,
        total: c.n, first: c.first, last: c.last, strength: chs.length * 1000 + (c.n || 0),
        edges: chs.map((ch) => ({ ch, w: 1, t: 'posted as channel', approx: true })),   // per-section counts aren't in the API
      });
    }
  }
  if (include.forwards) {
    for (const f of o?.forward_sources || []) {
      const chs = splitList(f.channels);
      people.push({
        id: 'fwd:' + f.fwd_type + ':' + (f.fwd_id ?? f.name ?? f.username), kind: 'forward_source', label: f.name || (f.username ? '@' + f.username : 'hidden sender'),
        handle: f.username || null, fwd_type: f.fwd_type, total: f.n, forwarders: f.forwarders, first: f.first, last: f.last,
        strength: chs.length * 1000 + (f.n || 0),
        edges: chs.map((ch) => ({ ch, w: 1, t: 'forwarded into', approx: true })),
      });
    }
  }
  people.sort((a, b) => b.strength - a.strength);
  const kept = people.slice(0, limit);
  for (const p of kept) {
    // one link per (person, section): an admin who also comments there gets both relations on one link
    const per = new Map();
    for (const e of p.edges) {
      section(e.ch);
      let l = per.get(e.ch);
      if (!l) per.set(e.ch, (l = { source: p.id, target: e.ch, weight: 0, byType: {}, rel: e.t, approx: true, admin: false }));
      l.byType[e.t] = (l.byType[e.t] || 0) + e.w;
      if (e.t === 'admin') l.admin = true;
      else { l.weight += e.w; l.rel = e.t; if (!e.approx) l.approx = false; }
    }
    for (const l of per.values()) {
      if (!l.weight) { l.weight = 1; l.rel = 'admin'; l.approx = false; }   // admin only, no comments there
      links.push(l);
    }
    p.sections = [...per.keys()];
  }
  return { nodes: [...sections.values(), ...kept], links, truncated: people.length - kept.length };
}

/** Per-node in/out weights and degrees for the current view (mutates nodes). */
export function annotateDegrees(nodes, links) {
  const by = new Map(nodes.map((n) => [n.id, n]));
  for (const n of nodes) { n.inW = 0; n.outW = 0; n.inDeg = 0; n.outDeg = 0; }
  for (const l of links) {
    const s = by.get(typeof l.source === 'object' ? l.source.id : l.source);
    const t = by.get(typeof l.target === 'object' ? l.target.id : l.target);
    if (s) { s.outW += l.weight; s.outDeg++; }
    if (t) { t.inW += l.weight; t.inDeg++; }
  }
  return nodes;
}

/** CSV text for a list of rows and columns [[header, fn]]. */
export function toCsv(rows, cols) {
  const esc = (v) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    // Spreadsheet formula neutralisation (titles/handles come from Telegram): strings only, real numbers stay numeric.
    const safe = typeof v === 'string' && /^[=+\-@\t\r]/.test(s) && !/^[+-]?\d+([.,]\d+)?$/.test(s) ? "'" + s : s;
    return /[",\n\r]/.test(safe) ? '"' + safe.replace(/"/g, '""') + '"' : safe;
  };
  // BOM so Excel reads Arabic/Cyrillic titles as UTF-8.
  return '\ufeff' + [cols.map((c) => esc(c[0])).join(','), ...rows.map((r) => cols.map((c) => esc(c[1](r))).join(','))].join('\r\n') + '\r\n';
}

/** Offer a generated text file to the user (local Blob, nothing leaves the machine). */
export function saveText(filename, text, mime = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type: mime + ';charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
