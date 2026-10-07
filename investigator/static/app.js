'use strict';
/* Telegram Investigator — single-page UI. No external resources; all content escaped. */

const TOKEN = document.querySelector('meta[name="x-token"]').content;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const main = () => $('#main');

const S = {
  settings: {}, tags: [], tagById: {}, channelsCache: null,
  list: [], listIndex: -1,           // posts currently shown (for drawer j/k)
  terms: [],                          // search terms to highlight
  review: null, pollers: [],
};

/* ---------------------------------------------------------------- utilities */

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (init.method !== 'GET') init.headers['x-token'] = TOKEN;
  if (opts.body !== undefined) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, ms);
}
const num = n => (n === null || n === undefined || n === '') ? '–' : Number(n).toLocaleString('en-US');
function fmtDate(iso, withTime = true) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  const p = x => String(x).padStart(2, '0');
  const day = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
  return withTime ? `${day} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC` : day;
}
function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  return (b / 1024 ** i).toFixed(i ? 1 : 0) + ' ' + u[i];
}
function ago(iso) {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 129600) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' days ago';
}
const qs = obj => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== '' && v !== null && v !== undefined)).toString();
function copy(text) {
  navigator.clipboard.writeText(text).then(() => toast('Copied'), () => toast('Copy failed'));
}
function chanLabel(username, title) {
  return title ? `<span dir="auto">${esc(title)}</span> <span class="faint">@${esc(username)}</span>` : `@${esc(username)}`;
}

/* -------------------------------------------------------- search highlighting */
// The index matches normalised text; the investigator sees the original. Build a
// regex that tolerates the same letter variants and optional Arabic diacritics.
const AR_MARKS = /[ؐ-ًؚ-ٰٟۖ-ۭـ]/g;
const VARIANTS = { 'ا': '[اأإآٱ]', 'ه': '[هة]', 'ي': '[يىئ]', 'و': '[وؤ]', 'е': '[её]' };
function normalize(s) {
  return s.normalize('NFKC').replace(AR_MARKS, '')
    .replace(/[أإآٱ]/g, 'ا').replace(/[ىئ]/g, 'ي').replace(/ؤ/g, 'و').replace(/ة/g, 'ه')
    .replace(/ё/gi, 'е').toLowerCase();
}
function highlighter(terms) {
  const alts = [];
  for (const raw of terms || []) {
    let t = normalize(raw).replace(/[^\p{L}\p{N}_]/gu, '');
    if (t.length < 2) continue;
    const forms = [t];
    if (/^(وال|بال|كال|فال|لل|ال)/.test(t)) forms.push(t.replace(/^(وال|بال|كال|فال|لل|ال)/, ''));
    for (const f of forms) {
      if (f.length < 2) continue;
      alts.push([...f].map(c => (VARIANTS[c] || c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('[\\u064B-\\u065F\\u0670\\u0640]*'));
    }
  }
  if (!alts.length) return null;
  try { return new RegExp('(' + alts.join('|') + ')', 'giu'); } catch (e) { return null; }
}
function renderText(text, terms) {
  const re = highlighter(terms);
  if (!re || !text) return esc(text);
  let out = '', last = 0;
  for (const m of text.matchAll(re)) {
    out += esc(text.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

/* ------------------------------------------------------------------ media */

function shouldBlur(p) {
  const mode = S.settings.blur || 'all';
  return mode === 'all' || (mode === 'sensitive' && p.sensitive);
}
function latest(rows) {        // several captures of the same item: show the newest
  const by = {};
  for (const r of rows) {
    const k = r.kind + ':' + r.idx;
    if (!by[k] || r.id > by[k].id) by[k] = r;
  }
  return Object.values(by).sort((a, b) => a.idx - b.idx);
}
function mediaTiles(p, large = false) {
  const media = latest(p.media || []);
  const photos = media.filter(m => m.kind === 'photo');
  const thumbs = media.filter(m => m.kind === 'thumb');
  const webVideos = media.filter(m => m.kind === 'video');
  const full = media.filter(m => m.kind === 'full');
  const fullVideos = full.filter(m => (m.mime || '').startsWith('video'));
  const fullOther = full.filter(m => !(m.mime || '').startsWith('video'));
  const blur = shouldBlur(p);
  const gray = S.settings.grayscale === '1' ? ' gray' : '';
  const durations = p.video_durations || [];
  const tiles = [];
  const veil = (what) => blur
    ? `<div class="veil" data-action="reveal"><div><b>Click to reveal</b>${esc(what)}${p.sensitive ? ' · flagged sensitive by Telegram' : ''}</div></div>`
    : '';

  const imgTile = (m, label) => `<div class="m${blur ? ' blurred' : ''}${gray}" data-kind="img">
      <img src="/media/${esc(m.path)}" alt="" loading="lazy">${veil(label)}</div>`;
  const vidTile = (src, poster, dur, label) => `<div class="m${blur ? ' blurred' : ''}${gray}" data-kind="video" data-src="/media/${esc(src)}">
      ${poster ? `<img src="/media/${esc(poster)}" alt="" loading="lazy">` : ''}
      ${veil(label)}${blur ? '' : `<div class="veil" data-action="play"><div><b>▶ Play</b></div></div>`}
      <span class="badge">▶ ${esc(dur || '')} ${esc(label)}</span></div>`;

  photos.forEach(m => tiles.push(imgTile(m, 'Photo')));
  fullOther.forEach(m => tiles.push(imgTile(m, 'Photo (original)')));
  if (fullVideos.length) {
    fullVideos.forEach((m, i) => tiles.push(vidTile(m.path, (thumbs[i] || thumbs[0] || {}).path, durations[i], 'full video')));
  } else if (webVideos.length) {
    webVideos.forEach((m, i) => tiles.push(vidTile(m.path, (thumbs[i] || thumbs[0] || {}).path, durations[i], 'video')));
  } else if (thumbs.length) {
    thumbs.forEach((m, i) => tiles.push(`<div class="m${blur ? ' blurred' : ''}${gray}" data-kind="img">
        <img src="/media/${esc(m.path)}" alt="" loading="lazy">${veil('Video thumbnail')}
        <span class="badge">Thumbnail only · ${esc(durations[i] || '')}${p.media_unavailable?.length ? ' · ' + esc(p.media_unavailable[0]) : ''}</span></div>`));
  }
  const types = p.media_types || [];
  if (!tiles.length && types.length) {
    tiles.push(`<div class="m missing">${esc(types.join(', '))}<br>not downloaded</div>`);
  }
  const wantsFull = !fullVideos.length && (types.includes('video') || (p.media_unavailable || []).length) && !webVideos.length;
  if (!tiles.length) return '';
  return `<div class="media-grid${large ? ' large' : ''}">${tiles.join('')}</div>` +
    (wantsFull ? `<div class="row small"><span class="faint">Full video not collected.</span>
      <button class="btn small" data-action="get-full" data-id="${p.id}">Download original (logged-in)</button></div>` : '');
}
function reveal(tile, play) {
  const wasBlurred = tile.classList.contains('blurred');
  tile.classList.remove('blurred');
  $$('.veil', tile).forEach(v => v.remove());
  if (wasBlurred) api('/api/exposure', { method: 'POST' }).then(updateExposure).catch(() => {});
  if (tile.dataset.kind === 'video' && (play || !wasBlurred)) {
    const v = document.createElement('video');
    v.src = tile.dataset.src;
    v.controls = true;
    v.preload = 'metadata';
    v.muted = S.settings.autoplay_muted !== '0';
    v.playsInline = true;
    const img = $('img', tile);
    if (img) img.replaceWith(v); else tile.prepend(v);
    $$('.badge', tile).forEach(b => b.remove());
    v.play().catch(() => {});
  } else if (tile.dataset.kind === 'video' && wasBlurred) {
    tile.insertAdjacentHTML('beforeend', `<div class="veil" data-action="play"><div><b>▶ Play</b></div></div>`);
  }
}
async function updateExposure() {
  try {
    const s = await api('/api/settings');
    S.settings.exposure_today = s.exposure_today;
    $('#exposure-meter').textContent = `Media revealed today: ${s.exposure_today}`;
  } catch (e) { /* ignore */ }
}

/* ------------------------------------------------------------------ cards */

function tagChips(p, interactive = false) {
  const on = new Set(p.tags || []);
  if (!interactive) {
    return [...on].map(id => S.tagById[id]).filter(Boolean)
      .map(t => `<span class="tag on" style="color:${esc(t.color)}"><i style="background:${esc(t.color)}"></i>${esc(t.name)}</span>`).join(' ');
  }
  let html = '', cat = null;
  for (const t of S.tags) {
    if (t.category !== cat) { cat = t.category; html += `<div class="faint small" style="margin:6px 0 2px">${esc(cat)}</div>`; }
    html += `<span class="tag${on.has(t.id) ? ' on' : ''}" style="${on.has(t.id) ? `color:${esc(t.color)}` : ''}" data-action="toggle-tag" data-id="${p.id}" data-tag="${t.id}" title="${esc(t.description)}">
      <i style="background:${esc(t.color)}"></i>${esc(t.name)}${t.key ? ` <kbd>${esc(t.key)}</kbd>` : ''}</span> `;
  }
  return html;
}
function reviewButtons(p) {
  const b = (st, label, cls) => `<button class="btn small ${cls}${p.review_status === st ? ' on' : ''}" data-action="review" data-id="${p.id}" data-status="${st}">${label}</button>`;
  return b('relevant', 'Relevant', 'good') + b('verify', 'Verify', 'warn') + b('irrelevant', 'Irrelevant', 'bad');
}
function fwdLine(p) {
  if (!p.fwd_username && !p.fwd_name) return '';
  const who = p.fwd_username
    ? `<a href="#/channel/${esc(p.fwd_username)}"><b dir="auto">${esc(p.fwd_name || '')}</b> @${esc(p.fwd_username)}</a>`
    : `<b dir="auto">${esc(p.fwd_name)}</b> <span class="faint">(source hidden)</span>`;
  return `<div class="fwd">↪ Forwarded from ${who}</div>`;
}
function cardHTML(p, opts = {}) {
  const long = (p.text || '').length > 700 && !opts.full;
  return `<article class="card${opts.selected ? ' selected' : ''}" data-post="${p.id}">
    <div class="card-head">
      <a class="chan" href="#/channel/${esc(p.channel)}">${chanLabel(p.channel, p.channel_title)}</a>
      <span>${fmtDate(p.date)}</span>
      ${p.views !== null ? `<span>${num(p.views)} views</span>` : ''}
      ${p.edited ? '<span class="pill">edited</span>' : ''}
      ${p.sensitive ? '<span class="pill verify">sensitive</span>' : ''}
      ${p.deleted_detected ? `<span class="pill failed" title="Found missing on ${esc(fmtDate(p.deleted_detected))}; last seen ${esc(fmtDate(p.last_seen))}">deleted</span>` : ''}
      <span class="spacer"></span>
      <span class="pill ${esc(p.review_status)}">${esc(p.review_status)}</span>
    </div>
    ${fwdLine(p)}
    ${p.text ? `<div class="text${long ? ' clamp' : ''}" dir="auto">${renderText(p.text, S.terms)}</div>` : '<div class="faint small" style="margin:8px 0">No text</div>'}
    ${mediaTiles(p, opts.large)}
    <div class="card-foot">
      ${tagChips(p)}
      ${p.notes ? `<span class="pill">✎ ${p.notes} note${p.notes > 1 ? 's' : ''}</span>` : ''}
      <span class="spacer"></span>
      ${opts.noActions ? '' : reviewButtons(p)}
      <button class="btn small" data-action="open-post" data-id="${p.id}">Details</button>
    </div>
  </article>`;
}
function bindList(posts) {
  S.list = posts.map(p => p.id);
  S.listKind = 'post';
}

/* ----------------------------------------------------------- post drawer */

async function openPost(id) {
  S.listIndex = S.list.indexOf(id);
  const drawer = $('#drawer');
  drawer.hidden = false;
  $('#drawer-body').innerHTML = '<div class="empty">Loading…</div>';
  $$('.card.selected').forEach(c => c.classList.remove('selected'));
  const c = $(`.card[data-post="${id}"]`);
  if (c) { c.classList.add('selected'); c.scrollIntoView({ block: 'nearest' }); }
  let p;
  try { p = await api(`/api/posts/${id}`); } catch (e) { $('#drawer-body').innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  drawer.dataset.id = id;
  const media = latest(p.media || []);
  const linkRow = (label, rows) => rows && rows.length ? `<div class="section"><h3>${label}</h3>${rows.map(r =>
    `<div class="row small"><a href="#" data-action="open-post" data-id="${r.id}">@${esc(r.channel)}</a><span class="faint">${fmtDate(r.date)}</span></div>`).join('')}</div>` : '';
  $('#drawer-body').innerHTML = `
    <div class="card-head" style="font-size:13px">
      <a class="chan" href="#/channel/${esc(p.channel)}">${chanLabel(p.channel, p.channel_title)}</a>
      <span>${fmtDate(p.date)}</span><span class="pill ${esc(p.review_status)}">${esc(p.review_status)}</span>
    </div>
    ${fwdLine(p)}
    ${p.original ? `<div class="small"><a href="#" data-action="open-post" data-id="${p.original.id}">Original post is in the collection →</a></div>` : ''}
    <div class="text" dir="auto">${renderText(p.text, S.terms) || '<span class="faint">No text</span>'}</div>
    ${mediaTiles(p, true)}
    <div class="row">${reviewButtons(p)}<span class="spacer"></span>
      <a class="btn small" href="${esc(p.url)}" target="_blank" rel="noreferrer noopener" title="Opens t.me in your browser. Telegram will see this visit.">Open in Telegram ↗</a></div>

    <div class="section"><h3>Tags</h3><div>${tagChips(p, true)}</div></div>

    <div class="section"><h3>Notes</h3>
      <div class="stack">${(p.notes || []).map(n => `<div class="note"><div dir="auto" style="white-space:pre-wrap">${esc(n.text)}</div><div class="by">${esc(n.author)} · ${fmtDate(n.created_at)}</div></div>`).join('')}</div>
      <form data-form="note" data-id="${p.id}" style="margin-top:8px"><textarea name="text" rows="2" dir="auto" placeholder="Add a note (observations, verification steps, leads)…"></textarea>
      <div class="row" style="margin-top:6px"><button class="btn small primary">Add note</button></div></form>
    </div>

    <div class="section"><h3>Post data</h3><dl class="kv">
      <dt>Telegram URL</dt><dd><span class="mono">${esc(p.url)}</span> <button class="btn small ghost" data-action="copy" data-text="${esc(p.url)}">copy</button></dd>
      <dt>Posted</dt><dd>${fmtDate(p.date)}${p.edited ? ' (edited)' : ''}</dd>
      <dt>Views / reactions</dt><dd>${num(p.views)} / ${num(p.reactions_total)} ${Object.entries(p.reactions || {}).map(([k, v]) => esc(k) + num(v)).join(' ')}</dd>
      ${p.fwd_url ? `<dt>Forwarded from</dt><dd class="mono">${esc(p.fwd_url)}</dd>` : ''}
      ${p.reply_to_url ? `<dt>Reply to</dt><dd class="mono">${esc(p.reply_to_url)}</dd>` : ''}
      ${p.author_signature ? `<dt>Signed by</dt><dd dir="auto">${esc(p.author_signature)}</dd>` : ''}
      ${p.hashtags?.length ? `<dt>Hashtags</dt><dd>${p.hashtags.map(h => `<a href="#/search?${qs({ q: h.replace('#', '') })}" dir="auto">${esc(h)}</a>`).join(' ')}</dd>` : ''}
      ${p.links?.length ? `<dt>Links in post</dt><dd>${p.links.map(l => `<div><span class="mono">${esc(l)}</span> <button class="btn small ghost" data-action="copy" data-text="${esc(l)}">copy</button></div>`).join('')}<div class="faint small">Not clickable on purpose: opening them reveals your IP to that site.</div></dd>` : ''}
    </dl></div>

    <div class="section"><h3>Evidence trail</h3><dl class="kv">
      <dt>First captured</dt><dd>${fmtDate(p.first_captured)}</dd>
      <dt>Last captured</dt><dd>${fmtDate(p.last_captured)} · ${p.capture_count} capture${p.capture_count > 1 ? 's' : ''}</dd>
      ${p.page_capture ? `<dt>Source page</dt><dd><span class="mono">${esc(p.page_capture.url)}</span><br>
        <span class="mono" title="SHA-256 of the raw HTML">${esc(p.page_capture.sha256)}</span><br>
        <span class="faint small">${fmtDate(p.page_capture.captured_at)} · ${esc(p.page_capture.tool || '')} ·
        <a href="/media/${esc(p.page_capture.path)}" download>download raw page (.html.gz)</a></span></dd>`
        : '<dt>Source page</dt><dd class="faint">Not linked (imported from a run made before page capture)</dd>'}
    </dl>
    ${media.length ? `<table class="data" style="margin-top:10px"><thead><tr><th>Item</th><th>SHA-256</th><th class="num">Size</th><th>Captured</th><th>Method</th></tr></thead><tbody>
      ${media.map(m => `<tr><td class="nowrap">${esc(m.kind)} #${m.idx}</td>
        <td><span class="mono" title="${esc(m.sha256)}">${esc(m.sha256.slice(0, 16))}…</span> <button class="btn small ghost" data-action="copy" data-text="${esc(m.sha256)}">copy</button></td>
        <td class="num nowrap">${fmtBytes(m.bytes)}</td><td class="nowrap small">${fmtDate(m.captured_at)}</td>
        <td class="small">${m.method === 'api' ? 'logged-in API' : 'public preview'}</td></tr>`).join('')}
    </tbody></table>` : ''}
    ${(p.versions || []).length ? `<div style="margin-top:10px"><h3>Earlier versions</h3>${p.versions.map(v =>
      `<div class="note"><div class="by">Captured ${fmtDate(v.captured_at)} · ${num(v.views)} views</div><div dir="auto" style="white-space:pre-wrap">${esc(v.text)}</div></div>`).join('')}</div>` : ''}
    </div>

    ${linkRow(p.fwd_url ? 'Same original forwarded by' : 'Forwarded by (in collection)', p.same_forward)}
    ${linkRow('Identical media also posted in', p.same_media)}

    ${(p.history || []).length ? `<div class="section"><h3>Review history</h3>${p.history.map(h =>
      `<div class="small"><span class="faint">${fmtDate(h.ts)}</span> ${esc(h.actor)} · ${esc(h.action)} ${esc(h.detail || '')}</div>`).join('')}</div>` : ''}
  `;
}
function closeDrawer() {
  $('#drawer').hidden = true;
  $$('.card.selected').forEach(c => c.classList.remove('selected'));
}
function drawerStep(d) {
  if (!S.list.length) return;
  const i = Math.max(0, Math.min(S.list.length - 1, S.listIndex + d));
  if (i !== S.listIndex) (S.listKind === 'comment' ? openComment : openPost)(S.list[i]);
}
function refreshCard(p) {
  const el = $(`#main .card[data-post="${p.id}"]`);
  if (el) el.outerHTML = cardHTML(p, { selected: el.classList.contains('selected') });
}

/* ------------------------------------------------------------------ router */

const routes = {
  '': renderOverview, search: renderSearch, review: renderReview, channels: renderChannels,
  channel: renderChannel, network: renderNetwork, jobs: renderJobs, settings: renderSettings,
};
function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [path, query] = h.split('?');
  const parts = path.split('/').map(decodeURIComponent);
  return { name: parts[0] || '', arg: parts[1], params: Object.fromEntries(new URLSearchParams(query || '')) };
}
async function route() {
  S.pollers.forEach(clearInterval);
  S.pollers = [];
  if (window.GRAPH) { window.GRAPH.stop(); window.GRAPH = null; }
  S.review = null;
  const r = parseHash();
  if (r.name !== route.last) { closeDrawer(); main().scrollTop = 0; }
  route.last = r.name;
  $$('#side a').forEach(a => a.classList.toggle('active',
    a.dataset.nav === (r.name || 'overview') || (r.name === 'channel' && a.dataset.nav === 'channels')));
  S.listKind = 'post';
  const fn = routes[r.name] || renderOverview;
  try {
    await fn(r.arg, r.params);
  } catch (e) {
    main().innerHTML = `<div class="callout bad">Something went wrong: ${esc(e.message)}</div>`;
    console.error(e);
  }
}
function go(name, params) {
  const h = '#/' + name + (params && qs(params) ? '?' + qs(params) : '');
  if (location.hash === h) route(); else location.hash = h;
}

/* --------------------------------------------------------------- overview */

function bars(data, { w = 700, h = 70, key = 'd', val = 'n', onClickAttr = '' } = {}) {
  if (!data || !data.length) return '<div class="faint small">No data yet</div>';
  const max = Math.max(...data.map(d => d[val]), 1);
  const bw = w / data.length;
  return `<svg class="bars" viewBox="0 0 ${w} ${h + 14}" preserveAspectRatio="none" style="width:100%;height:${h + 14}px">
    ${data.map((d, i) => {
      const bh = Math.max(1, (d[val] / max) * h);
      return `<rect class="b" x="${(i * bw).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${bh.toFixed(1)}" ${onClickAttr ? `data-action="${onClickAttr}" data-day="${esc(d[key])}" style="cursor:pointer"` : ''}><title>${esc(d[key])}: ${d[val]}</title></rect>`;
    }).join('')}
    <text x="0" y="${h + 12}">${esc(data[0][key])}</text>
    <text x="${w}" y="${h + 12}" text-anchor="end">${esc(data[data.length - 1][key])}</text>
  </svg>`;
}

async function renderOverview() {
  const o = await api('/api/overview');
  const c = o.counts;
  const tile = (v, k, href) => `<a class="tile" href="${href || '#'}" style="color:inherit;text-decoration:none"><div class="v">${v}</div><div class="k">${k}</div></a>`;
  main().innerHTML = `
    <div class="page-head"><div><h1>Overview</h1><p>${num(c.posts)} posts from ${num(c.crawled)} crawled channels · ${c.first_post ? fmtDate(c.first_post, false) + ' to ' + fmtDate(c.last_post, false) : 'nothing collected yet'}</p></div>
      <span class="spacer"></span><a class="btn primary" href="#/jobs">+ Collect</a></div>
    ${!c.posts ? `<div class="callout" style="margin-bottom:16px"><b>Getting started.</b> Go to <a href="#/jobs">Collect</a>, enter one or more channel usernames, and start a crawl. Posts, media and the channel network will appear here.</div>` : ''}
    <div class="grid tiles">
      ${tile(num(c.posts), 'posts collected', '#/search')}
      ${tile(num(c.unreviewed), 'awaiting review', '#/review')}
      ${tile(num(c.relevant), 'marked relevant', '#/search?review=relevant')}
      ${tile(num(c.verify), 'need verification', '#/search?review=verify')}
      ${tile(num(c.watched), 'seeds & watched', '#/channels?status=seed,watched')}
      ${tile(num(c.candidates), 'candidate channels', '#/channels?status=candidate')}
      ${tile(fmtBytes(c.media_bytes), `${num(c.media)} media files`, '#/search?media=downloaded')}
      ${tile(num(c.pages), 'raw pages preserved', '#/settings')}
      ${c.comments ? tile(num(c.comments), `comments · ${num(c.comments_deleted)} found deleted`, '#/comments') : ''}
    </div>
    <div class="grid two" style="margin-top:14px">
      <div class="panel"><h2>Posts per day (last 120 days)</h2>${bars(o.per_day, { onClickAttr: 'day-search' })}</div>
      <div class="panel"><h2>Most linked-to channels</h2>
        <table class="data"><tbody>${o.top.map(t => `<tr class="click" data-href="#/channel/${esc(t.username)}">
          <td>${chanLabel(t.username, t.title)} ${t.status !== 'candidate' ? `<span class="pill ${esc(t.status)}">${esc(t.status)}</span>` : ''}</td>
          <td class="num">${t.linked_from} channels</td><td class="num faint">${num(t.signals)} signals</td></tr>`).join('')}</tbody></table>
      </div>
      <div class="panel"><h2>Recent activity</h2>${o.activity.map(a => `<div class="small"><span class="faint">${fmtDate(a.ts)}</span> ${esc(a.actor)} · ${esc(a.action.replace(/_/g, ' '))} ${esc(a.target || '')}</div>`).join('') || '<div class="faint">None yet</div>'}</div>
      <div class="panel"><h2>Collection jobs</h2>${o.jobs.map(j => `<div class="row small"><a href="#/jobs?job=${j.id}">#${j.id} ${esc(j.kind)}</a><span class="pill ${esc(j.status)}">${esc(j.status)}</span><span class="faint">${ago(j.created)}</span></div>`).join('') || '<div class="faint">No jobs yet</div>'}
        <div id="tg-status" class="small" style="margin-top:10px"></div></div>
    </div>`;
  loadTelegramStatus($('#tg-status'));
}
async function loadTelegramStatus(el, force = false) {
  if (!el) return;
  el.innerHTML = '<span class="faint">Checking Telegram login…</span>';
  try {
    const t = await api('/api/telegram' + (force ? '?force=1' : ''));
    el.innerHTML = t.ok
      ? `<span class="pill done">Telegram</span> ${esc(t.message)}`
      : `<span class="pill failed">Telegram</span> ${esc(t.message)}<div class="faint">Full-size video downloads need a one-time login: run <span class="mono">.venv/bin/python tgmedia.py login</span> in a terminal.</div>`;
  } catch (e) { el.textContent = 'Could not check Telegram login.'; }
}

/* ------------------------------------------------------------------ search */

async function ensureChannels(force) {
  if (!S.channelsCache || force) S.channelsCache = await api('/api/channels?sort=posts');
  return S.channelsCache;
}
function tagOptions(sel) {
  return `<option value="">Any tag</option>` + S.tags.map(t => `<option value="${t.id}"${String(sel) === String(t.id) ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
}
function opt(v, label, cur) { return `<option value="${esc(v)}"${String(cur ?? '') === String(v) ? ' selected' : ''}>${esc(label)}</option>`; }

async function renderSearch(_, params) {
  const P = { limit: S.settings.page_size || 25, ...params };
  S.terms = [];
  main().innerHTML = `
    <div class="page-head"><div><h1>Search</h1><p>Search all collected posts. Arabic spelling variants and diacritics are handled automatically.</p></div></div>
    <form data-form="search" class="panel">
      <div class="searchbar">
        <input type="search" name="q" value="${esc(P.q || '')}" placeholder="e.g. الفاشر أسرى · &quot;El Fasher&quot; · штурм" dir="auto" autofocus>
        <button class="btn primary">Search</button>
      </div>
      <div class="hint">Words are combined with AND and prefix-matched. Use <span class="mono">"exact phrase"</span>, <span class="mono">OR</span>, and <span class="mono">-exclude</span>.</div>
      <div class="filters">
        <label class="field">From<input type="date" name="date_from" value="${esc(P.date_from || '')}"></label>
        <label class="field">To<input type="date" name="date_to" value="${esc(P.date_to || '')}"></label>
        <label class="field">Media<select name="media">${opt('', 'Any', P.media)}${opt('photo', 'Has photo', P.media)}${opt('video', 'Has video', P.media)}${opt('downloaded', 'Media downloaded', P.media)}${opt('refused', 'Video not yet collected', P.media)}${opt('none', 'Text only', P.media)}</select></label>
        <label class="field">Forwards<select name="forwarded">${opt('', 'All posts', P.forwarded)}${opt('1', 'Only forwards', P.forwarded)}${opt('0', 'Only original posts', P.forwarded)}</select></label>
        <label class="field">Review<select name="review">${opt('', 'Any status', P.review)}${opt('unreviewed', 'Unreviewed', P.review)}${opt('relevant', 'Relevant', P.review)}${opt('verify', 'Needs verification', P.review)}${opt('irrelevant', 'Irrelevant', P.review)}</select></label>
        <label class="field">Tag<select name="tag">${tagOptions(P.tag)}</select></label>
        <label class="field">Min views<input type="number" name="min_views" min="0" style="width:90px" value="${esc(P.min_views || '')}"></label>
        <label class="field">Sort<select name="sort">${opt('', 'Best match / newest', P.sort)}${opt('newest', 'Newest', P.sort)}${opt('oldest', 'Oldest', P.sort)}${opt('views', 'Most viewed', P.sort)}${opt('reactions', 'Most reactions', P.sort)}</select></label>
        <input type="hidden" name="channels" value="${esc(P.channels || '')}">
        <input type="hidden" name="fwd" value="${esc(P.fwd || '')}">
        <a class="btn ghost" href="#/search">Reset</a>
      </div>
    </form>
    <div id="results"><div class="empty">Searching…</div></div>`;
  const res = await api('/api/search?' + qs(P));
  S.terms = res.terms;
  bindList(res.results);
  const chans = (P.channels || '').split(',').filter(Boolean);
  const exportHref = '/api/export.csv?' + qs({ ...P, limit: '', offset: '' });
  const offset = Number(res.offset), limit = Number(P.limit);
  $('#results').innerHTML = `
    <div class="search-layout">
      <div>
        <div class="result-meta"><b style="color:var(--text)">${num(res.total)}</b> posts
          ${chans.map(c => `<span class="tag on" data-action="facet" data-channel="${esc(c)}">@${esc(c)} ✕</span>`).join('')}
          ${P.fwd ? `<span class="tag on" data-action="clear-fwd">forwarded from @${esc(P.fwd)} ✕</span>` : ''}
          <span class="spacer"></span>
          ${res.total ? `<a class="btn small" href="${esc(exportHref)}">Export CSV</a>` : ''}
        </div>
        ${res.timeline.length > 1 ? `<div class="panel" style="padding:8px 10px 2px;margin-bottom:10px">${bars(res.timeline, { h: 40, onClickAttr: 'day-filter' })}</div>` : ''}
        <div class="cards">${res.results.map(p => cardHTML(p)).join('') || '<div class="empty">No posts match. Try fewer words or remove filters.</div>'}</div>
        ${res.total > limit ? `<div class="pager">
          <button class="btn" data-action="page" data-offset="${Math.max(0, offset - limit)}" ${offset ? '' : 'disabled'}>← Previous</button>
          <span class="muted small">${num(offset + 1)}–${num(Math.min(offset + limit, res.total))} of ${num(res.total)}</span>
          <button class="btn" data-action="page" data-offset="${offset + limit}" ${offset + limit < res.total ? '' : 'disabled'}>Next →</button></div>` : ''}
      </div>
      <aside class="facets panel">
        <h3>Channels</h3>
        ${res.facets.map(f => `<div class="facet${chans.includes(f.channel) ? ' on' : ''}" data-action="facet" data-channel="${esc(f.channel)}"><span>@${esc(f.channel)}</span><span class="faint">${num(f.n)}</span></div>`).join('') || '<div class="faint small">–</div>'}
      </aside>
    </div>`;
}
function searchParams() {
  const f = $('form[data-form="search"]');
  const out = {};
  if (f) new FormData(f).forEach((v, k) => { if (v) out[k] = v; });
  return out;
}

/* ------------------------------------------------------------------ review */

async function renderReview(_, params) {
  await ensureChannels();
  const P = { review: 'unreviewed', ...params };
  S.review = { P, batch: [], i: 0, done: 0, total: 0 };
  S.terms = [];
  const chans = S.channelsCache.filter(c => c.posts > 0);
  main().innerHTML = `
    <div class="page-head"><div><h1>Review</h1><p>Work through collected posts one at a time. Media stays blurred until you reveal it.</p></div></div>
    <form data-form="review" class="filters" style="margin-top:0;margin-bottom:12px">
      <label class="field">Queue<select name="review">${opt('unreviewed', 'Unreviewed', P.review)}${opt('verify', 'Needs verification', P.review)}${opt('relevant', 'Relevant (re-check)', P.review)}</select></label>
      <label class="field">Channel<select name="channels"><option value="">All channels</option>${chans.map(c => opt(c.username, `@${c.username} (${c.posts})`, P.channels)).join('')}</select></label>
      <label class="field">Media<select name="media">${opt('', 'Any', P.media)}${opt('photo', 'Has photo', P.media)}${opt('video', 'Has video', P.media)}${opt('none', 'Text only', P.media)}</select></label>
      <label class="field">Search within<input type="search" name="q" value="${esc(P.q || '')}" dir="auto" placeholder="optional keywords"></label>
      <button class="btn">Apply</button>
    </form>
    <div class="review-wrap"><div class="progress"><div id="rv-progress" style="width:0"></div></div>
    <div class="row small muted" style="margin:6px 0 10px"><span id="rv-count"></span><span class="spacer"></span>
      <span class="keys"><kbd>r</kbd> relevant <kbd>v</kbd> verify <kbd>i</kbd> irrelevant <kbd>j</kbd>/<kbd>k</kbd> skip/back <kbd>space</kbd> reveal media <kbd>n</kbd> note <kbd>o</kbd> details · tag keys shown on tags</span></div>
    <div id="rv-body"><div class="empty">Loading…</div></div></div>`;
  await reviewLoad();
}
async function reviewLoad() {
  const R = S.review;
  const res = await api('/api/search?' + qs({ ...R.P, sort: 'oldest', limit: 25, offset: 0 }));
  R.batch = res.results;
  R.i = 0;
  R.total = res.total;
  S.terms = res.terms;
  bindList(R.batch);
  reviewShow();
}
function reviewShow() {
  const R = S.review;
  if (!R) return;
  const p = R.batch[R.i];
  $('#rv-count').textContent = `${num(R.done)} reviewed this session · ${num(Math.max(0, R.total))} in queue`;
  $('#rv-progress').style.width = R.total + R.done ? (100 * R.done / (R.total + R.done)).toFixed(1) + '%' : '0';
  if (!p) {
    $('#rv-body').innerHTML = `<div class="empty"><b>Queue empty.</b><br>Nothing left matching these filters. <a href="#/search">Search</a> or collect more on the <a href="#/jobs">Collect</a> page.</div>`;
    return;
  }
  $('#rv-body').innerHTML = `<div class="review-card">${cardHTML(p, { large: true, full: true, noActions: true })}</div>
    <div class="panel" style="margin-top:10px">
      <div class="row">${reviewButtons(p)}<span class="spacer"></span><button class="btn small" data-action="rv-skip">Skip →</button></div>
      <div style="margin-top:10px">${tagChips(p, true)}</div>
      <form data-form="note" data-id="${p.id}" style="margin-top:10px"><textarea name="text" rows="2" dir="auto" placeholder="Note (n)…"></textarea>
        <div class="row" style="margin-top:6px"><button class="btn small">Add note</button><span id="rv-notes" class="faint small">${p.notes ? p.notes + ' note(s) on this post' : ''}</span></div></form>
    </div>`;
}
async function reviewVerdict(status) {
  const R = S.review;
  const p = R && R.batch[R.i];
  if (!p) return;
  await api(`/api/posts/${p.id}/review`, { method: 'POST', body: { status } });
  R.done++;
  R.total--;
  R.batch.splice(R.i, 1);
  bindList(R.batch);
  if (R.i >= R.batch.length) {
    if (R.batch.length === 0) { await reviewLoad(); return; }
    R.i = R.batch.length - 1;
  }
  reviewShow();
  refreshNavCounts();
}
function reviewMove(d) {
  const R = S.review;
  if (!R) return;
  R.i += d;
  if (R.i >= R.batch.length) { toast('End of this batch: skipped posts stay in the queue'); R.i = R.batch.length - 1; }
  if (R.i < 0) R.i = 0;
  reviewShow();
}

/* ---------------------------------------------------------------- channels */

async function renderChannels(_, params) {
  const P = { status: '', type: 'channel,unchecked', sort: 'linked', ...params };
  const tab = (st, label) => `<button class="btn small${(P.status || '') === st ? ' primary' : ''}" data-action="chan-tab" data-status="${st}">${label}</button>`;
  main().innerHTML = `
    <div class="page-head"><div><h1>Channels</h1><p>Every username found while crawling. Watch the ones that matter, dismiss the noise, and crawl candidates to spider further.</p></div></div>
    <div class="row" style="margin-bottom:10px">${tab('', 'All')}${tab('seed,watched', 'Seeds & watched')}${tab('candidate', 'Candidates')}${tab('dismissed', 'Dismissed')}
      <span class="spacer"></span>
      <select data-change="chan-type">${opt('channel,unchecked', 'Channels (incl. not yet checked)', P.type)}${opt('channel', 'Confirmed channels', P.type)}${opt('group', 'Groups', P.type)}${opt('user,bot', 'Users & bots', P.type)}${opt('', 'Everything', P.type)}</select>
      <input type="search" data-change="chan-q" placeholder="Filter by name…" value="${esc(P.q || '')}" dir="auto">
    </div>
    <div class="row" style="margin-bottom:10px"><button class="btn small" data-action="crawl-selected" disabled id="crawl-sel">Crawl selected</button>
      <span class="faint small" id="sel-count"></span></div>
    <div class="panel" style="padding:0;overflow-x:auto"><div id="chan-table"><div class="empty">Loading…</div></div></div>`;
  const rows = await api('/api/channels?' + qs(P));
  const th = (k, label, cls = '') => `<th class="${cls}" data-sort="${k}" data-action="chan-sort">${label}${P.sort === k ? ' ▾' : ''}</th>`;
  $('#chan-table').innerHTML = rows.length ? `<table class="data"><thead><tr><th></th>
      ${th('name', 'Channel')}<th>Status</th>${th('subscribers', 'Subscribers', 'num')}${th('posts', 'Posts', 'num')}
      ${th('relevant', 'Relevant', 'num')}${th('linked', 'Linked from', 'num')}<th class="num">Links to</th>${th('recent', 'Last post')}<th>Last crawled</th><th></th></tr></thead>
    <tbody>${rows.map(c => `<tr class="click" data-href="#/channel/${esc(c.username)}">
      <td><input type="checkbox" data-select="${esc(c.username)}"></td>
      <td>${chanLabel(c.username, c.title)}${c.type && c.type !== 'channel' ? ` <span class="pill">${esc(c.type)}</span>` : ''}${!c.type ? ' <span class="pill">unchecked</span>' : ''}</td>
      <td><span class="pill ${esc(c.status)}">${esc(c.status)}</span></td>
      <td class="num">${num(c.subscribers)}</td><td class="num">${num(c.posts)}</td><td class="num">${num(c.relevant)}</td>
      <td class="num">${c.linked_from ? `${c.linked_from} <span class="faint">(${num(c.signals_in)})</span>` : '–'}</td>
      <td class="num">${c.links_to || '–'}</td>
      <td class="small nowrap">${c.last_post ? fmtDate(c.last_post, false) : '–'}</td>
      <td class="small nowrap">${c.last_crawled ? ago(c.last_crawled) : '<span class="faint">never</span>'}</td>
      <td class="nowrap">${c.status === 'candidate' ? `<button class="btn small" data-action="chan-status" data-name="${esc(c.username)}" data-status="watched">Watch</button>
        <button class="btn small ghost" data-action="chan-status" data-name="${esc(c.username)}" data-status="dismissed">Dismiss</button>` : ''}</td>
    </tr>`).join('')}</tbody></table>` : '<div class="empty">No channels here yet.</div>';
}

async function renderChannel(name) {
  const c = await api('/api/channels/' + encodeURIComponent(name));
  const res = await api('/api/search?' + qs({ channels: name, sort: 'newest', limit: 20 }));
  S.terms = [];
  bindList(res.results);
  const statusBtn = (st, label) => `<button class="btn small${c.status === st ? ' primary' : ''}" data-action="chan-status" data-name="${esc(c.username)}" data-status="${st}">${label}</button>`;
  const edgeList = (rows, dir) => rows.length ? `<table class="data"><tbody>${rows.map(e => `<tr class="click" data-href="#/channel/${esc(e.username)}">
      <td>${chanLabel(e.username, e.title)}${e.ctype && e.ctype !== 'channel' ? ` <span class="pill">${esc(e.ctype)}</span>` : ''}</td>
      <td class="small">${esc(e.type)}</td><td class="num">${num(e.weight)}</td>
      <td class="small faint nowrap">${e.last_seen ? fmtDate(e.last_seen, false) : ''}</td></tr>`).join('')}</tbody></table>`
    : `<div class="faint small">${dir === 'in' ? 'No crawled channel links here yet.' : c.last_crawled ? 'This channel doesn’t forward, link or mention other channels in what was collected.' : 'Not crawled yet.'}</div>`;
  main().innerHTML = `
    <div class="page-head">
      <div><h1 dir="auto">${esc(c.title || '@' + c.username)}</h1>
        <p>@${esc(c.username)} · <span class="pill ${esc(c.status)}">${esc(c.status)}</span> ${c.type ? `<span class="pill">${esc(c.type)}</span>` : '<span class="pill">unchecked</span>'}
        ${c.status_reason ? `<span class="faint">“${esc(c.status_reason)}”</span>` : ''}</p></div>
      <span class="spacer"></span>
      ${statusBtn('watched', 'Watch')}${statusBtn('candidate', 'Candidate')}${statusBtn('dismissed', 'Dismiss')}
    </div>
    <div class="row" style="margin-bottom:14px">
      <button class="btn primary" data-action="crawl-now" data-name="${esc(c.username)}" data-depth="1">Crawl this channel</button>
      <button class="btn" data-action="crawl-now" data-name="${esc(c.username)}" data-depth="2">Spider out (2 hops)</button>
      <button class="btn" data-action="media-channel" data-name="${esc(c.username)}">Download full videos (logged-in)</button>
      <a class="btn" href="#/search?${qs({ channels: c.username })}">Search in channel</a>
      <a class="btn" href="#/review?${qs({ channels: c.username })}">Review channel</a>
      <span class="spacer"></span>
      <a class="btn ghost" href="https://t.me/${esc(c.username)}" target="_blank" rel="noreferrer noopener" title="Opens t.me in your browser. Telegram will see this visit.">Open in Telegram ↗</a>
    </div>
    <div class="grid tiles">
      <div class="tile"><div class="v">${num(c.subscribers)}</div><div class="k">subscribers</div></div>
      <div class="tile"><div class="v">${num(c.posts)}</div><div class="k">posts collected</div></div>
      <div class="tile"><div class="v">${num(c.relevant)}</div><div class="k">marked relevant</div></div>
      <div class="tile"><div class="v">${num(c.linked_from)}</div><div class="k">channels link here</div></div>
      <div class="tile"><div class="v">${num(c.links_to)}</div><div class="k">channels it links to</div></div>
      <div class="tile"><div class="v" style="font-size:15px">${c.last_crawled ? ago(c.last_crawled) : 'never'}</div><div class="k">last crawled</div></div>
    </div>
    ${c.description ? `<div class="panel" style="margin-top:14px"><h3>Description</h3><div dir="auto" style="white-space:pre-wrap">${esc(c.description)}</div></div>` : ''}
    <div class="grid two" style="margin-top:14px">
      <div class="panel"><h2>Posting activity (collected posts)</h2>${bars(c.per_day)}</div>
      <div class="panel"><h2>Identity</h2><dl class="kv">
        <dt>Username</dt><dd>@${esc(c.username)}</dd>
        <dt>Telegram ID</dt><dd class="mono">${c.tg_id ? esc(c.tg_id) : '–'} <span class="faint small">(permanent; survives renames)</span></dd>
        <dt>First seen</dt><dd>${fmtDate(c.first_seen)}</dd>
        <dt>Public preview</dt><dd>${c.preview ? 'yes' : c.type === 'channel' && c.last_crawled ? 'disabled' : '–'}</dd>
        ${c.tags.length ? `<dt>Tags on its posts</dt><dd>${c.tags.map(t => `<a class="tag" style="color:${esc(t.color)}" href="#/search?${qs({ channels: c.username, tag: t.id })}"><i style="background:${esc(t.color)}"></i>${esc(t.name)} ${t.n}</a>`).join(' ')}</dd>` : ''}
      </dl></div>
      <div class="panel"><h2>Channels pointing here</h2>${edgeList(c.sources, 'in')}</div>
      <div class="panel"><h2>This channel points to</h2>${edgeList(c.targets, 'out')}</div>
    </div>
    ${commentSectionPanel(c)}
    <div class="panel" style="margin-top:14px"><h2>Channel notes</h2>
      ${c.notes.map(n => `<div class="note" style="margin-bottom:6px"><div dir="auto" style="white-space:pre-wrap">${esc(n.text)}</div><div class="by">${esc(n.author)} · ${fmtDate(n.created_at)}</div></div>`).join('')}
      <form data-form="channel-note" data-name="${esc(c.username)}"><textarea name="text" rows="2" dir="auto" placeholder="Affiliation, reliability, what it posts, leads…"></textarea>
      <div class="row" style="margin-top:6px"><button class="btn small">Add note</button></div></form>
    </div>
    <div class="row" style="margin:20px 0 8px"><h2 style="margin:0">Latest posts</h2><span class="spacer"></span><a href="#/search?${qs({ channels: c.username })}">All ${num(c.posts)} posts →</a></div>
    <div class="cards">${res.results.map(p => cardHTML(p)).join('') || '<div class="empty">No posts collected from this channel yet.</div>'}</div>
    ${c.history.length ? `<div class="panel" style="margin-top:14px"><h3>History</h3>${c.history.map(h => `<div class="small"><span class="faint">${fmtDate(h.ts)}</span> ${esc(h.actor)} · ${esc(h.action)} ${esc(h.detail || '')}</div>`).join('')}</div>` : ''}`;
}

/* ----------------------------------------------------------------- network */

async function renderNetwork(_, params) {
  const P = { min_weight: 1, types: 'forward,link,mention,description,comment_forward,comment_sender', channels_only: '1', ...params };
  const types = P.types.split(',');
  const cb = t => `<label class="check"><input type="checkbox" data-edge-type="${t}"${types.includes(t) ? ' checked' : ''}>${t}</label>`;
  main().innerHTML = `
    <div class="page-head"><div><h1>Network</h1><p>Who forwards, links to or mentions whom. Arrows point from the channel that shared to the channel it points at. Drag to move, scroll to zoom, click a channel for details.</p></div></div>
    <div class="row" style="margin-bottom:10px">${cb('forward')}${cb('link')}${cb('mention')}${cb('description')}${cb('comment_forward')}${cb('comment_sender')}
      <label class="check">Min. signals <input type="number" min="1" id="net-min" value="${esc(P.min_weight)}" style="width:64px"></label>
      <label class="check"><input type="checkbox" id="net-users"${P.channels_only === '0' ? ' checked' : ''}>include users/bots</label>
      <button class="btn small" data-action="net-apply">Apply</button><span class="spacer"></span>
      <button class="btn small" data-action="net-fit">Fit</button></div>
    <div class="net-layout"><canvas id="net"></canvas>
      <div class="panel" style="overflow:auto">
        <div class="legend">
          <div><i style="background:#2b59c3"></i>Seed / watched</div>
          <div><i style="background:#0f9d8a"></i>Crawled candidate</div>
          <div><i style="background:#9aa3b2"></i>Not crawled yet</div>
          <div><i style="background:#8b5cf6"></i>Group</div>
          <div class="faint small" style="margin-top:4px">Size = subscribers. Line thickness = number of forwards/links/mentions. Red ring = has posts marked relevant.</div>
        </div>
        <div id="net-info" style="margin-top:14px" class="faint small">Click a channel.</div>
      </div></div>`;
  const data = await api('/api/graph?' + qs({ min_weight: P.min_weight, types: P.types, channels_only: P.channels_only }));
  if (!data.nodes.length) { $('#net-info').textContent = 'No links collected yet. Crawl some channels first.'; return; }
  window.GRAPH = new Graph($('#net'), data, { onSelect: showNetInfo, onOpen: n => go('channel/' + encodeURIComponent(n.username)) });
}
function showNetInfo(n) {
  const el = $('#net-info');
  if (!n) { el.innerHTML = '<span class="faint small">Click a channel.</span>'; return; }
  el.classList.remove('faint', 'small');
  el.innerHTML = `<h2 dir="auto" style="margin-bottom:2px">${esc(n.title || '@' + n.username)}</h2>
    <div class="small muted">@${esc(n.username)} · <span class="pill ${esc(n.status)}">${esc(n.status)}</span></div>
    <dl class="kv" style="grid-template-columns:110px 1fr;margin-top:8px">
      <dt>Subscribers</dt><dd>${num(n.subscribers)}</dd><dt>Posts collected</dt><dd>${num(n.posts)}</dd>
      <dt>Relevant</dt><dd>${num(n.relevant)}</dd><dt>Crawled</dt><dd>${n.crawled ? 'yes' : 'no'}</dd>
      <dt>Links in / out</dt><dd>${n.inW} / ${n.outW}</dd></dl>
    <div class="row" style="margin-top:10px"><a class="btn small primary" href="#/channel/${esc(n.username)}">Open</a>
      ${n.crawled ? '' : `<button class="btn small" data-action="crawl-now" data-name="${esc(n.username)}" data-depth="1">Crawl</button>`}
      ${n.status === 'candidate' ? `<button class="btn small" data-action="chan-status" data-name="${esc(n.username)}" data-status="watched">Watch</button>
      <button class="btn small ghost" data-action="chan-status" data-name="${esc(n.username)}" data-status="dismissed">Dismiss</button>` : ''}</div>`;
}

/* -------------------------------------------------------------------- jobs */

async function renderJobs(_, params) {
  await ensureChannels(true);
  const chans = S.channelsCache.filter(c => c.posts > 0);
  main().innerHTML = `
    <div class="page-head"><div><h1>Collect</h1><p>Crawl channels from their public preview (no login), and download full-size videos and collect comment sections with the logged-in account. Jobs start automatically and are imported into the workspace when they finish. Crawls run one at a time; logged-in jobs run one at a time alongside them.</p></div></div>
    <div id="monitor"></div>
    <div class="grid two" style="margin-top:14px">
      <form class="panel" data-form="crawl"><h2>Crawl channels</h2>
        <label class="field">Channels (username, @name or t.me link; one per line)
          <textarea name="seeds" rows="4" placeholder="example_channel&#10;@another_channel">${esc((params.seeds || '').split(',').join('\n'))}</textarea></label>
        <div class="filters">
          <label class="field">Spider depth<select name="depth">${opt(1, '1: these channels only', 1)}${opt(2, '2: plus what they link to')}${opt(3, '3: two hops out')}</select></label>
          <label class="field">Pages per channel<input type="number" name="pages" value="10" min="1" max="500" style="width:90px"></label>
          <label class="field">Max channels<input type="number" name="max_channels" value="30" min="1" style="width:90px"></label>
          <label class="field">Collect<select name="collect" data-change="collect-mode">${opt('content', 'Posts + media', 'content')}${opt('network', 'Network only (no content)')}</select></label>
          <label class="field" id="media-field">Media<select name="media">${opt('photos', 'Photos + thumbnails', 'photos')}${opt('all', 'Photos + small videos')}${opt('none', 'None')}</select></label>
        </div>
        <div class="hint" id="network-hint" hidden><b>Network only:</b> maps who forwards, links to and mentions whom, and records each channel's details. No post text, media or page copies are kept. It's faster and lighter, and good for mapping a large network first.</div>
        <div class="hint">One page ≈ 20 posts, newest first. Depth 2+ crawls every channel the seeds forward, link or mention (up to Max channels). Requests are spaced 1.5 s apart.</div>
        <div class="row" style="margin-top:10px"><button class="btn primary">Start crawl</button></div>
      </form>
      <form class="panel" data-form="media"><h2>Download full videos (logged-in)</h2>
        <div id="tg-status2" class="small" style="margin-bottom:10px"></div>
        <label class="field">Channel<select name="channel"><option value="">All channels</option>${chans.map(c => opt(c.username, '@' + c.username)).join('')}</select></label>
        <div class="filters">
          <label class="field">Max posts<input type="number" name="limit" value="20" min="1" style="width:90px"></label>
          <label class="field">Skip files over (MB)<input type="number" name="max_mb" value="200" min="1" style="width:100px"></label>
        </div>
        <div class="hint">Downloads the originals of videos the public preview refused ("Media is too big"), newest first. Expect roughly 0.3–0.5 MB/s.</div>
        <div class="row" style="margin-top:10px"><button class="btn primary">Start download</button></div>
      </form>
    </div>
    ${commentsCollectPanel()}
    <div class="panel" style="margin-top:14px"><div class="row"><h2 style="margin:0">All jobs</h2><span class="spacer"></span>
      <form data-form="import" class="row"><input type="text" name="path" placeholder="Import an existing tgspider folder (path)" style="width:320px"><button class="btn small">Import</button></form></div>
      <div id="jobs-table" style="margin-top:8px"></div></div>`;
  loadTelegramStatus($('#tg-status2'));
  MON.selected = params.job ? Number(params.job) : null;
  MON.logOffset = 0; MON.logJob = null;
  await refreshJobs();
  await refreshMonitor();
  refreshSchedules();
  S.pollers.push(setInterval(refreshMonitor, 1000));
  S.pollers.push(setInterval(refreshJobs, 4000));
  S.pollers.push(setInterval(refreshSchedules, 15000));
}

const fmtDur = sec => {
  if (sec === null || sec === undefined || isNaN(sec)) return '–';
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ' + String(sec % 60).padStart(2, '0') + 's';
  return Math.floor(sec / 3600) + 'h ' + String(Math.floor(sec % 3600 / 60)).padStart(2, '0') + 'm';
};
const pct = (a, b) => b ? Math.min(100, Math.max(0, 100 * a / b)) : 0;
const bar = (a, b, cls = '') => `<div class="mbar ${cls}"><div style="width:${pct(a, b).toFixed(1)}%"></div></div>`;
const hms = iso => iso ? new Date(iso).toISOString().slice(11, 19) : '';
const ACTIVE = ['queued', 'running', 'importing'];

function jobDesc(j) {
  const p = JSON.parse(j.params || '{}');
  if (j.kind === 'comments') return commentsJobDesc(p);
  if (j.kind === 'discover') return `${esc((p.method || '').replace('_', ' '))}: ${esc((p.channels || p.queries || []).join(', '))}`;
  if (j.kind === 'mediascan') return `perceptual hashes + metadata${p.channel ? ' · @' + esc(p.channel) : ''}`;
  if (p.recheck_days) return `watchlist re-crawl of ${(p.seeds || []).map(s => '@' + esc(s)).join(', ')} · re-checks ${p.recheck_days} days`;
  return j.kind === 'crawl'
    ? `${(p.seeds || []).map(s => '@' + esc(s)).join(', ')} · depth ${p.depth} · ${p.pages} pages · max ${p.max_channels} ch · ${p.network_only ? '<b>network only</b>' : 'media ' + esc(p.media)}`
    : `${p.channel ? '@' + esc(p.channel) : p.post_ids?.length ? p.post_ids.length + ' selected post(s)' : 'all channels'} · ≤${p.max_mb} MB · up to ${p.limit} posts`;
}
function jobSummary(j) {
  const s = JSON.parse(j.summary || '{}');
  if (j.kind === 'comments' && j.summary && !s.error) return commentsJobSummary(s);
  if (s.error) return `<span class="small" style="color:var(--bad)">${esc(s.error)}</span>`;
  if (s.note) return `<span class="small faint">${esc(s.note)}</span>`;
  if (j.kind === 'discover' && j.summary) return `<span class="small">${num(s.found)} found · ${num(s.discoveries_new)} new</span>`;
  if (j.kind === 'mediascan' && j.summary) return `<span class="small">${num(s.scanned)} files scanned · ${num(s.with_gps)} with GPS</span>`;
  if (s.posts_deleted || s.posts_changed) return `<span class="small">${num(s.posts_new)} new posts · ${num(s.posts_changed)} changed · ${num(s.posts_deleted)} found deleted</span>`;
  if (s.network_only) return `<span class="small">${num(s.edges_new)} links · ${num(s.channels_new)} new channels</span>`;
  return j.summary ? `<span class="small">${num(s.posts_new)} new posts · ${num(s.media_new)} media · ${num(s.channels_new)} new channels</span>` : '';
}

async function refreshJobs() {
  const el = $('#jobs-table');
  if (!el) return;
  const jobs = await api('/api/jobs');
  MON.jobs = jobs;
  el.innerHTML = jobs.length ? `<table class="data"><thead><tr><th>#</th><th>Job</th><th>Status</th><th>Started</th><th>Result</th></tr></thead><tbody>
    ${jobs.map(j => `<tr class="click${MON.shown === j.id ? ' sel' : ''}" data-action="select-job" data-id="${j.id}"><td>${j.id}</td><td><b>${esc(j.kind)}</b> <span class="small muted">${jobDesc(j)}</span></td>
      <td><span class="pill ${esc(j.status)}">${esc(j.status)}</span></td><td class="small nowrap">${ago(j.started || j.created)}</td><td>${jobSummary(j)}</td></tr>`).join('')}
    </tbody></table>` : '<div class="faint">No jobs yet. Start a crawl above.</div>';
}

async function refreshMonitor() {
  const el = $('#monitor');
  if (!el) return;
  let id = MON.selected;
  if (!id) {                                        // default: the active job, else the latest
    const jobs = MON.jobs || await api('/api/jobs');
    const act = jobs.filter(j => ACTIVE.includes(j.status)).sort((a, b) => a.id - b.id);
    id = (act.find(j => j.status !== 'queued') || act[0] || jobs[0] || {}).id;
  }
  if (!id) { el.innerHTML = ''; return; }
  if (MON.shown !== id) { MON.logOffset = 0; MON.logText = ''; }
  MON.shown = id;
  const { job, progress, stop_requested } = await api(`/api/jobs/${id}/progress`);
  MON.stopRequested = stop_requested;
  const live = ACTIVE.includes(job.status);
  // only redraw finished jobs once
  const sig = job.status + (progress ? progress.updated_at : '') + stop_requested;
  if (!live && MON.sig === sig && el.innerHTML) return;
  MON.sig = sig;
  const open = $('#mon-log') ? $('#mon-log').open : false;
  const evScroll = $('#mon-events') ? $('#mon-events').scrollTop : 0;
  el.innerHTML = monitorHTML(job, progress, live);
  if (open) $('#mon-log').open = true;
  if ($('#mon-events')) $('#mon-events').scrollTop = evScroll;
  await pollLog(job, live);
  $$('#jobs-table tr[data-id]').forEach(tr => tr.classList.toggle('sel', Number(tr.dataset.id) === id));
}

const unitOf = job => ({ crawl: 'channel', comments: 'message', discover: 'lookup', mediascan: 'file' }[job.kind] || 'video');
function monitorHTML(job, pr, live) {
  const t = pr?.totals || {};
  const elapsed = pr?.elapsed_seconds ?? (job.started ? (Date.now() - new Date(job.started)) / 1000 : 0);
  let head = `<div class="row"><h2 style="margin:0">${{ crawl: 'Crawl', comments: 'Comment collection', discover: 'Channel discovery', mediascan: 'Media scan' }[job.kind] || 'Video download'} #${job.id}</h2>
      <span class="pill ${esc(job.status)}">${esc(job.status)}${live && job.status === 'running' ? ' <span class="pulse"></span>' : ''}</span>
      <span class="muted small">${jobDesc(job)}</span><span class="spacer"></span>
      ${job.status === 'running' ? `<span class="small">elapsed <b>${fmtDur(elapsed)}</b>${pr?.eta_seconds != null ? ` · about <b>${fmtDur(pr.eta_seconds)}</b> left${pr.tool === 'tgspider' && pr.settings?.depth > 1 ? ' <span class="faint" title="Deeper hops add channels as they are discovered, so this can grow.">(for channels found so far)</span>' : ''}` : ''}</span>` : ''}
      ${job.status !== 'running' && job.finished ? `<span class="small muted">finished ${ago(job.finished)}${pr?.elapsed_seconds ? ` · took ${fmtDur(pr.elapsed_seconds)}` : ''}</span>` : ''}
      ${job.status === 'queued' ? `<button class="btn small bad" data-action="cancel-job" data-id="${job.id}">Remove from queue</button>` : ''}
      ${job.status === 'running' ? (MON.stopRequested
        ? `<span class="pill queued">stopping after this ${unitOf(job)}…</span>`
        : `<button class="btn small warn" data-action="stop-job" data-id="${job.id}" title="Finish the ${unitOf(job)} in progress, then stop. Everything collected is imported.">Stop after this ${unitOf(job)}</button>`)
        + ` <button class="btn small bad" data-action="cancel-job" data-id="${job.id}" title="Stop immediately. What was collected so far is still imported.">Stop now</button>` : ''}
    </div>`;
  let body = '';
  if (job.status === 'queued') {
    body = `<div class="callout" style="margin-top:12px">Queued: it will start automatically when the ${job.kind === 'crawl' ? 'crawl' : 'logged-in job'} ahead of it finishes.</div>`;
  } else if (!pr) {
    body = `<div class="callout" style="margin-top:12px">${live ? 'Starting…' : 'No live progress was recorded for this job (it ran before the monitor existed). The raw log is below.'}</div>`;
  } else {
    if (job.status === 'importing') body += `<div class="callout" style="margin-top:12px">Collection finished. Importing into the workspace (hashing and copying files)…</div>`;
    if (pr.waiting && live) {
      const left = Math.max(0, pr.waiting.seconds - (Date.now() - new Date(pr.waiting.since)) / 1000);
      body += `<div class="callout warn" style="margin-top:12px"><b>Paused:</b> ${esc(pr.waiting.reason)} · resuming in ${fmtDur(left)}</div>`;
    }
    const netOnly = pr.settings?.network_only || JSON.parse(job.summary || '{}').network_only;
    const simple = pr.tool === 'tgdiscover' || pr.tool === 'mediascan';
    body += pr.tool === 'tgmedia' ? mediaMonitor(pr, live) : pr.tool === 'tgcomments' ? commentsMonitor(pr)
      : simple ? `<div class="grid tiles compact" style="margin-top:12px">${Object.entries(pr.totals || {}).map(([k, v]) =>
          `<div class="tile"><div class="v">${num(v)}</div><div class="k">${esc(k)}</div></div>`).join('')}</div>` : crawlMonitor(pr, live, netOnly);
    body += `<div class="grid two" style="margin-top:14px">
      <div><h3>Activity</h3><div class="events" id="mon-events">${[...(pr.events || [])].reverse().map(e =>
        `<div class="ev ${esc(e.level)}"><span class="mono faint">${hms(e.t)}</span> ${esc(e.msg)}</div>`).join('')}</div></div>
      <div>${pr.tool === 'tgmedia' ? postsTable(pr) : pr.tool === 'tgcomments' || simple ? '' : channelsTable(pr)}</div></div>`;
  }
  body += `<details id="mon-log" style="margin-top:12px"><summary class="small muted">Raw log</summary><pre class="log" id="job-log" style="margin-top:6px"></pre></details>`;
  return `<div class="panel monitor${live ? ' live' : ''}">${head}${body}</div>`;
}

function crawlMonitor(pr, live, netOnly) {
  const t = pr.totals, c = pr.current, q = pr.queue || {}, st = pr.settings || {};
  const planned = t.channels_crawled + (c && c.mode === 'crawl' ? 1 : 0) + (q.planned_crawl || 0);
  const tile = (v, k, cls = '') => `<div class="tile ${cls}"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  let cur = '';
  if (c) {
    const title = c.title ? ` <span dir="auto">${esc(c.title)}</span>` : '';
    cur = `<div class="current">
      <div class="row"><b>${c.mode === 'crawl' ? 'Crawling' : 'Checking'} <a href="#/channel/${esc(c.channel)}">@${esc(c.channel)}</a></b>${title}
        <span class="pill">hop ${c.depth}</span>${c.subscribers ? `<span class="small muted">${num(c.subscribers)} subscribers</span>` : ''}
        <span class="spacer"></span><span class="small muted">${esc(c.activity || '')} · ${fmtDur((Date.now() - new Date(c.started_at)) / 1000)} on this channel</span></div>
      ${c.mode === 'crawl' ? `
      <div class="meter"><span class="lbl">Pages</span>${bar(c.page, c.pages_max)}<span class="val">${c.page} / ${c.pages_max}</span></div>
      <div class="meter"><span class="lbl">Posts</span><span class="val wide">${num(c.posts)} read · ${num(c.posts_saved)} new saved${c.oldest ? ` · reached back to <b>${fmtDate(c.oldest, false)}</b>` : ''}${c.newest ? ` (newest ${fmtDate(c.newest, false)})` : ''}</span></div>
      ${c.media_total ? `<div class="meter"><span class="lbl">Media</span>${bar(c.media_done, c.media_total, 'alt')}<span class="val">${c.media_done} / ${c.media_total} · ${fmtBytes(c.media_bytes)}</span></div>` : ''}
      <div class="meter"><span class="lbl">Signals</span><span class="val wide">${num(c.signals)} forwards / links / mentions of other channels so far</span></div>
      ${c.request && live ? `<div class="small faint mono" style="margin-top:4px">last request: ${esc(c.request)}</div>` : ''}` : ''}
    </div>`;
  } else if (live && pr.phase !== 'done') {
    cur = `<div class="current small muted">${esc(pr.phase)}…</div>`;
  }
  return `
    <div class="meter big" style="margin-top:14px"><span class="lbl">Channels</span>${bar(t.channels_crawled, planned || 1)}
      <span class="val">${t.channels_crawled} of ${planned || '?'} crawled</span></div>
    <div class="small muted" style="margin:2px 0 10px 96px">${netOnly ? '<b>Network only</b> (no content kept) · ' : ''}${q.classify ? `${q.classify} more username(s) waiting to be checked · ` : ''}${num(t.discovered)} usernames discovered · depth ${st.depth}, max ${st.max_channels} channels, ${st.pages} pages each</div>
    ${cur}
    <div class="grid tiles compact" style="margin-top:12px">
      ${tile(num(t.pages), 'pages read')}${tile(num(t.posts_seen), 'posts read')}${netOnly ? tile('–', 'posts not kept (network only)') : tile(num(t.posts_saved), 'new posts saved')}
      ${tile(num(t.media_files), `media files · ${fmtBytes(t.media_bytes)}`)}${tile(num(t.signals), 'channel signals')}
      ${tile(num(t.names_classified), 'usernames checked')}${tile(num(t.requests), 'requests to Telegram')}
      ${tile(num(t.retries), 'retries / rate limits', t.retries ? 'warn' : '')}${tile(num(t.errors + t.media_failed), 'errors', t.errors + t.media_failed ? 'bad' : '')}
    </div>`;
}

function channelsTable(pr) {
  const rows = [...(pr.channels || [])].reverse();
  if (!rows.length) return '<h3>Channels processed</h3><div class="faint small">None finished yet.</div>';
  return `<h3>Channels processed (${rows.length})</h3><div class="scroll-box"><table class="data"><thead><tr><th>Channel</th><th>Result</th><th class="num">Posts</th><th class="num">Pages</th><th>Back to</th><th class="num">Signals</th><th class="num">Media</th><th class="num">Time</th></tr></thead><tbody>
    ${rows.map(r => `<tr class="click" data-href="#/channel/${esc(r.username)}"><td>@${esc(r.username)}${r.title ? `<div class="small faint" dir="auto">${esc(r.title)}</div>` : ''}</td>
      <td class="small">${r.error ? `<span style="color:var(--bad)" title="${esc(r.error)}">error</span>` : r.status === 'crawled' ? 'crawled' : `${esc(r.type || '?')}${r.mode === 'crawl' ? ' <span class="faint">(not a channel; skipped)</span>' : ''}`}</td>
      <td class="num">${r.status === 'crawled' ? `${num(r.posts)}<div class="small faint">${num(r.posts_saved)} new</div>` : '–'}</td>
      <td class="num">${r.status === 'crawled' ? r.pages : '–'}</td><td class="small nowrap">${r.oldest ? fmtDate(r.oldest, false) : '–'}</td>
      <td class="num">${r.signals ? `${r.signals}<div class="small faint">${r.new_names} new names</div>` : '–'}</td>
      <td class="num">${r.media ? `${r.media}<div class="small faint">${fmtBytes(r.media_bytes)}</div>` : '–'}</td>
      <td class="num nowrap">${fmtDur(r.seconds)}</td></tr>`).join('')}</tbody></table></div>`;
}

function mediaMonitor(pr, live) {
  const t = pr.totals, c = pr.current;
  const tile = (v, k, cls = '') => `<div class="tile ${cls}"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  let cur = '';
  if (c) {
    const speed = c.speed ? `${(c.speed / 1048576).toFixed(2)} MB/s` : '';
    const fileLeft = c.speed && c.file_bytes_total ? fmtDur((c.file_bytes_total - c.file_bytes_done) / c.speed) : '';
    cur = `<div class="current"><div class="row"><b>Post ${c.index} of ${t.posts_total}: <a href="#" data-action="open-post-url" data-channel="${esc(c.channel)}" data-msg="${c.post}">@${esc(c.channel)}/${c.post}</a></b>
        ${(c.durations || []).length ? `<span class="pill">${esc(c.durations.join(', '))}</span>` : ''}<span class="spacer"></span><span class="small muted">${esc(c.activity || '')}</span></div>
      ${c.file_bytes_total ? `<div class="meter"><span class="lbl">${esc(c.file || 'File')}</span>${bar(c.file_bytes_done, c.file_bytes_total, 'alt')}
        <span class="val">${fmtBytes(c.file_bytes_done)} / ${fmtBytes(c.file_bytes_total)}${speed ? ` · ${speed}` : ''}${fileLeft ? ` · ${fileLeft} left` : ''}</span></div>` : ''}
      ${pr.account ? `<div class="small faint">Account: ${esc(pr.account)}</div>` : ''}</div>`;
  }
  return `
    <div class="meter big" style="margin-top:14px"><span class="lbl">Posts</span>${bar(t.posts_done, t.posts_total)}<span class="val">${t.posts_done} of ${t.posts_total}</span></div>
    ${cur}
    <div class="grid tiles compact" style="margin-top:12px">
      ${tile(num(t.files), 'files downloaded')}${tile(fmtBytes(t.bytes), 'downloaded')}
      ${tile(num(t.skipped), 'skipped (too big)', t.skipped ? 'warn' : '')}${tile(num(t.deleted), 'deleted on Telegram', t.deleted ? 'warn' : '')}
      ${tile(num(t.errors), 'errors', t.errors ? 'bad' : '')}
    </div>`;
}

function postsTable(pr) {
  const rows = [...(pr.posts || [])].reverse();
  if (!rows.length) return '<h3>Posts processed</h3><div class="faint small">None finished yet.</div>';
  return `<h3>Posts processed (${rows.length})</h3><div class="scroll-box"><table class="data"><thead><tr><th>Post</th><th>Result</th><th class="num">Files</th><th class="num">Size</th><th class="num">Speed</th><th class="num">Time</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td class="nowrap">@${esc(r.channel)}/${r.post}</td><td class="small">${esc(r.status)}${r.note ? `<div class="faint">${esc(r.note)}</div>` : ''}</td>
      <td class="num">${r.files}</td><td class="num nowrap">${r.bytes ? fmtBytes(r.bytes) : '–'}</td>
      <td class="num nowrap">${r.speed ? (r.speed / 1048576).toFixed(2) + ' MB/s' : '–'}</td><td class="num nowrap">${fmtDur(r.seconds)}</td></tr>`).join('')}</tbody></table></div>`;
}

async function pollLog(job, live) {
  const pre = $('#job-log');
  if (!pre) return;
  if (MON.logJob !== job.id) { MON.logJob = job.id; MON.logOffset = 0; MON.logText = ''; }
  if (live || !MON.logText) {
    const r = await api(`/api/jobs/${job.id}/log?offset=${MON.logOffset}`);
    MON.logText += r.text;
    MON.logOffset = r.offset;
  }
  pre.textContent = MON.logText;
  pre.scrollTop = pre.scrollHeight;
}

/* ----------------------------------------------------- sidebar live monitor */

const MON = { selected: null, shown: null, jobs: null, lastDone: undefined };
async function pollActive() {
  let a;
  try { a = await api('/api/jobs/active'); } catch (e) { return; }
  const el = $('#side-monitor');
  const j = a.job, pr = a.progress;
  $('#nav-jobs').textContent = (a.running || 0) + a.queued || '';
  if (!j) {
    el.hidden = !a.queued;
    el.innerHTML = a.queued ? `<div class="sm-line">${a.queued} job(s) queued</div>` : '';
  } else {
    let line = '', sub = '', a1 = 0, b1 = 1;
    if (j.status === 'importing') { line = `Importing job #${j.id}…`; sub = 'hashing and copying files'; a1 = 1; }
    else if (!pr) { line = `Starting job #${j.id}…`; }
    else if (pr.tool === 'tgcomments') {
      line = `Comments of @${esc(pr.channel)}`;
      sub = `${num(pr.totals.comments)} new · ${num(pr.totals.deleted)} deleted · ${esc(pr.activity || pr.phase)}`;
      a1 = pr.phase === 'done' ? 1 : 0.5;
    }
    else if (pr.tool === 'tgdiscover' || pr.tool === 'mediascan') {
      line = pr.tool === 'mediascan' ? 'Scanning media' : 'Discovering channels';
      sub = Object.entries(pr.totals || {}).map(([k, v]) => `${num(v)} ${k}`).join(' · ');
      a1 = pr.totals?.done || pr.totals?.lookups || 0; b1 = pr.totals?.files || a1 + 1;
    }
    else if (pr.tool === 'tgmedia') {
      const c = pr.current;
      line = c ? `Downloading @${esc(c.channel)}/${c.post}` : 'Downloading videos';
      sub = c && c.file_bytes_total ? `${fmtBytes(c.file_bytes_done)} / ${fmtBytes(c.file_bytes_total)}${c.speed ? ` · ${(c.speed / 1048576).toFixed(2)} MB/s` : ''}` : `post ${pr.totals.posts_done + 1} of ${pr.totals.posts_total}`;
      a1 = pr.totals.posts_done; b1 = pr.totals.posts_total;
    } else {
      const c = pr.current, t = pr.totals, q = pr.queue || {};
      line = c ? `${c.mode === 'crawl' ? 'Crawling' : 'Checking'} @${esc(c.channel)}` : esc(pr.phase);
      sub = c && c.mode === 'crawl' ? `page ${c.page}/${c.pages_max} · ${num(c.posts)} posts${c.media_total ? ` · media ${c.media_done}/${c.media_total}` : ''}` : `${t.channels_crawled} channels · ${num(t.posts_saved)} new posts`;
      a1 = t.channels_crawled + (c && c.mode === 'crawl' ? pct(c.page, c.pages_max) / 100 : 0);
      b1 = t.channels_crawled + (c && c.mode === 'crawl' ? 1 : 0) + (q.planned_crawl || 0) || 1;
    }
    if (pr?.waiting) sub = `⏸ ${esc(pr.waiting.reason)}`;
    if (a.stop_requested && j.status === 'running') sub = 'stopping after the current item… · ' + sub;
    el.hidden = false;
    el.innerHTML = `<a href="#/jobs?job=${j.id}"><div class="sm-line"><span class="pulse"></span>${line}</div>
      ${bar(a1, b1)}<div class="sm-sub">${sub}${pr?.eta_seconds != null && j.status === 'running' ? ` · ~${fmtDur(pr.eta_seconds)} left` : ''}${a.queued ? ` · ${a.queued} queued` : ''}</div></a>
      ${(a.others || []).map(o => `<a href="#/jobs?job=${o.id}"><div class="sm-sub"><span class="pulse"></span> also running: ${o.kind === 'comments' ? 'comments of @' + esc(JSON.parse(o.params || '{}').channel) : esc(o.kind)} #${o.id}</div></a>`).join('')}`;
  }
  // tell the investigator when a job finishes, wherever they are
  if (a.last) {
    if (MON.lastDone !== undefined && a.last.id !== MON.lastDone) {
      const s = JSON.parse(a.last.summary || '{}');
      const what = s.comments_new !== undefined ? `: ${num(s.comments_new)} new comments, ${num(s.deleted_detected)} deleted`
        : s.posts_new !== undefined ? `: ${num(s.posts_new)} new posts, ${num(s.media_new)} media` : '';
      if (!(a.last.kind === 'comments' && !s.comments_new && !s.deleted_detected && a.last.status === 'done'))
        toast(`Job #${a.last.id} ${a.last.status}${what}`, 6000);   // quiet scheduled polls that found nothing
      S.channelsCache = null;
      refreshNavCounts();
    }
    MON.lastDone = a.last.id;
  } else if (MON.lastDone === undefined) MON.lastDone = null;
}

/* ---------------------------------------------------------------- settings */

async function renderSettings() {
  const s = await api('/api/settings');
  await loadTags();
  const tags = S.tags;
  main().innerHTML = `
    <div class="page-head"><div><h1>Settings</h1><p>Workspace: <span class="mono">${esc(s.workspace)}</span></p></div></div>
    <div class="grid two">
      <form class="panel stack" data-form="settings"><h2>You</h2>
        <label class="field">Investigator name (recorded in the audit log)<input type="text" name="investigator" value="${esc(s.investigator)}"></label>
        <h2 style="margin-top:18px">Wellbeing</h2>
        <div class="callout">Repeated exposure to violent imagery causes vicarious trauma. Keep media blurred by default, read the text first, and take breaks. You've revealed <b>${num(s.exposure_today)}</b> media items today.</div>
        <label class="field">Blur media
          <select name="blur">${opt('all', 'Blur all images and videos (recommended)', s.blur)}${opt('sensitive', 'Only posts Telegram flags as sensitive', s.blur)}${opt('none', 'Never blur', s.blur)}</select></label>
        <label class="check"><input type="checkbox" name="grayscale" value="1"${s.grayscale === '1' ? ' checked' : ''}> Show revealed media in grayscale</label>
        <label class="check"><input type="checkbox" name="autoplay_muted" value="1"${s.autoplay_muted !== '0' ? ' checked' : ''}> Start videos muted</label>
        <h2 style="margin-top:18px">Comment collection</h2>
        <label class="check"><input type="checkbox" name="collect_phones" value="1"${s.collect_phones !== '0' ? ' checked' : ''}> Record commenters' phone numbers where Telegram shows them</label>
        <label class="field">Re-check comments from the last N days for deletions and edits<input type="number" name="recheck_days" min="1" max="365" value="${esc(s.recheck_days)}" style="width:90px"></label>
        <div><button class="btn primary">Save</button></div>
      </form>
      <div class="panel stack"><h2>Evidence integrity</h2>
        <p class="muted small" style="margin:0">Re-hashes every stored media file and raw page and compares with the SHA-256 recorded at capture. Stored files are read-only.</p>
        <div><button class="btn" data-action="integrity">Verify all files</button></div><div id="integrity-result"></div>
      </div>
    </div>
    <div class="panel" style="margin-top:14px"><div class="row"><h2 style="margin:0">Codebook (tags)</h2><span class="spacer"></span><button class="btn small" data-action="tag-add">+ Add tag</button></div>
      <p class="muted small">Tags describe what a post documents. The key is a one-character shortcut on the Review page.</p>
      <table class="data" id="tag-table"><thead><tr><th>Name</th><th>Category</th><th>Colour</th><th>Key</th><th>Description</th><th class="num">Used</th><th></th></tr></thead><tbody>
      ${tags.map(t => tagRow(t)).join('')}</tbody></table></div>
    <div class="panel" style="margin-top:14px"><h2>Audit log</h2><div id="audit"></div></div>`;
  const audit = await api('/api/audit?limit=300');
  $('#audit').innerHTML = `<table class="data"><thead><tr><th>When (UTC)</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>
    ${audit.map(a => `<tr><td class="nowrap small">${fmtDate(a.ts)}</td><td class="small">${esc(a.actor)}</td><td class="small">${esc(a.action)}</td><td class="small">${esc(a.target || '')}</td><td class="small mono" dir="auto" style="word-break:break-all">${esc((a.detail || '').slice(0, 300))}</td></tr>`).join('')}</tbody></table>`;
}
function tagRow(t) {
  return `<tr data-tag-row="${t.id || ''}">
    <td><input type="text" name="name" value="${esc(t.name || '')}" style="width:100%"></td>
    <td><input type="text" name="category" value="${esc(t.category || '')}" style="width:110px"></td>
    <td><input type="color" name="color" value="${esc(t.color || '#64748b')}"></td>
    <td><input type="text" name="key" value="${esc(t.key || '')}" maxlength="1" style="width:36px"></td>
    <td><input type="text" name="description" value="${esc(t.description || '')}" style="width:100%"></td>
    <td class="num">${num(t.uses || 0)}</td>
    <td class="nowrap"><button class="btn small" data-action="tag-save">Save</button>${t.id ? ` <button class="btn small ghost" data-action="tag-delete">Delete</button>` : ''}</td></tr>`;
}

/* --------------------------------------------------------------- actions */

const actions = {
  'open-post': (el, e) => { e.preventDefault(); openPost(Number(el.dataset.id)); },
  'close-drawer': () => closeDrawer(),
  'drawer-prev': () => drawerStep(-1),
  'drawer-next': () => drawerStep(1),
  reveal: el => reveal(el.closest('.m'), false),
  play: el => reveal(el.closest('.m'), true),
  copy: el => copy(el.dataset.text),
  review: async el => {
    const id = Number(el.dataset.id), status = el.dataset.status;
    if (S.review && S.review.batch[S.review.i]?.id === id && !el.closest('#drawer')) return reviewVerdict(status);
    await api(`/api/posts/${id}/review`, { method: 'POST', body: { status } });
    toast(`Marked ${status}`);
    const p = await api(`/api/posts/${id}`);
    refreshCard(p);
    if (!$('#drawer').hidden && Number($('#drawer').dataset.id) === id) openPost(id);
    refreshNavCounts();
  },
  'toggle-tag': async el => {
    const id = Number(el.dataset.id), tag = Number(el.dataset.tag);
    const on = !el.classList.contains('on');
    const r = await api(`/api/posts/${id}/tags`, { method: 'POST', body: { tag_id: tag, on } });
    const t = S.tagById[tag];
    $$(`[data-action="toggle-tag"][data-id="${id}"][data-tag="${tag}"]`).forEach(x => {
      x.classList.toggle('on', on);
      x.style.color = on ? t.color : '';
    });
    if (S.review) { const p = S.review.batch.find(p => p.id === id); if (p) p.tags = r.tags; }
    const card = $(`#main .card[data-post="${id}"] .card-foot`);
    if (card && !S.review) { const p = await api(`/api/posts/${id}`); refreshCard(p); }
  },
  'get-full': async el => {
    const r = await api('/api/jobs', { method: 'POST', body: { kind: 'media', post_ids: [Number(el.dataset.id)], only_refused: false, limit: 1 } });
    toast(`Download #${r.id} queued. Watch it in the sidebar.`);
    pollActive();
  },
  facet: el => {
    const P = parseHash().params;
    const cur = (P.channels || '').split(',').filter(Boolean);
    const c = el.dataset.channel;
    const next = cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c];
    go('search', { ...P, channels: next.join(','), offset: '' });
  },
  'clear-fwd': () => go('search', { ...parseHash().params, fwd: '', offset: '' }),
  page: el => { go('search', { ...parseHash().params, offset: el.dataset.offset }); main().scrollTop = 0; },
  'day-filter': el => go('search', { ...parseHash().params, date_from: el.dataset.day, date_to: el.dataset.day, offset: '' }),
  'day-search': el => go('search', { date_from: el.dataset.day, date_to: el.dataset.day }),
  'rv-skip': () => reviewMove(1),
  'chan-sort': el => go('channels', { ...parseHash().params, sort: el.dataset.sort }),
  'chan-tab': el => go('channels', { ...parseHash().params, status: el.dataset.status }),
  'chan-status': async (el, e) => {
    e.stopPropagation();
    let reason = null;
    if (el.dataset.status === 'dismissed') {
      reason = prompt('Why dismiss this channel? (recorded in the audit log)', '');
      if (reason === null) return;
    }
    await api(`/api/channels/${encodeURIComponent(el.dataset.name)}/status`, { method: 'POST', body: { status: el.dataset.status, reason } });
    toast(`@${el.dataset.name} → ${el.dataset.status}`);
    S.channelsCache = null;
    if (parseHash().name === 'network' && window.GRAPH) {
      window.GRAPH.updateNode(el.dataset.name, { status: el.dataset.status });
      showNetInfo(window.GRAPH.selected);
    } else route();
  },
  'crawl-now': async el => {
    const depth = Number(el.dataset.depth || 1);
    const r = await api('/api/jobs', { method: 'POST', body: { kind: 'crawl', seeds: el.dataset.name, depth, pages: 10, max_channels: depth > 1 ? 30 : 1, media: 'photos', mark_seeds: false } });
    toast(`Crawl #${r.id} queued. Watch it on the Collect page or in the sidebar.`);
    pollActive();
  },
  'media-channel': async el => {
    const r = await api('/api/jobs', { method: 'POST', body: { kind: 'media', channel: el.dataset.name, limit: 20, max_mb: 200 } });
    toast(`Download #${r.id} queued. Watch it on the Collect page or in the sidebar.`);
    pollActive();
  },
  'crawl-selected': () => {
    const names = $$('[data-select]:checked').map(x => x.dataset.select);
    go('jobs', { seeds: names.join(',') });
  },
  'select-job': el => { MON.selected = Number(el.dataset.id); MON.sig = null; refreshMonitor(); $('#monitor').scrollIntoView({ block: 'nearest' }); },
  'stop-job': async el => {
    await api(`/api/jobs/${el.dataset.id}/stop`, { method: 'POST' });
    toast('Will stop after the current item finishes, then import everything');
    MON.sig = null; refreshMonitor(); pollActive();
  },
  'cancel-job': async el => {
    if (!confirm('Stop this job right now? Everything collected so far is kept and imported.')) return;
    await api(`/api/jobs/${el.dataset.id}/cancel`, { method: 'POST' });
    toast('Cancelling… collected data will still be imported');
    refreshJobs(); refreshMonitor(); pollActive();
  },
  'open-post-url': async (el, e) => {
    e.preventDefault();
    const r = await api('/api/search?' + qs({ channels: el.dataset.channel, limit: 200 }));
    const p = r.results.find(x => String(x.msg_id) === el.dataset.msg);
    if (p) openPost(p.id); else toast('Post not in the workspace yet');
  },
  'net-apply': () => {
    const types = $$('[data-edge-type]:checked').map(x => x.dataset.edgeType).join(',');
    go('network', { types, min_weight: $('#net-min').value, channels_only: $('#net-users').checked ? '0' : '1' });
  },
  'net-fit': () => window.GRAPH && window.GRAPH.fit(),
  integrity: async el => {
    el.disabled = true;
    $('#integrity-result').innerHTML = '<span class="faint">Hashing…</span>';
    const r = await api('/api/integrity', { method: 'POST' });
    el.disabled = false;
    $('#integrity-result').innerHTML = r.ok
      ? `<div class="callout good">All ${num(r.checked_media)} media files and ${num(r.checked_pages)} raw pages match their recorded SHA-256 (${fmtDate(r.at)}).</div>`
      : `<div class="callout bad">${r.mismatched.length} mismatched, ${r.missing.length} missing.<div class="mono small">${[...r.mismatched, ...r.missing].map(esc).join('<br>')}</div></div>`;
  },
  'tag-add': () => { $('#tag-table tbody').insertAdjacentHTML('beforeend', tagRow({ category: 'Other' })); },
  'tag-save': async el => {
    const tr = el.closest('tr');
    const body = Object.fromEntries($$('input', tr).map(i => [i.name, i.value]));
    if (!body.name.trim()) return toast('Name is required');
    if (tr.dataset.tagRow) body.id = Number(tr.dataset.tagRow);
    await api('/api/tags', { method: 'POST', body });
    await loadTags();
    toast('Tag saved');
    route();
  },
  'tag-delete': async el => {
    const tr = el.closest('tr');
    if (!confirm('Delete this tag? It will be removed from all posts.')) return;
    await api('/api/tags', { method: 'POST', body: { id: Number(tr.dataset.tagRow), delete: true } });
    await loadTags();
    route();
  },
};

document.addEventListener('click', e => {
  const a = e.target.closest('[data-action]');
  if (a) {
    const fn = actions[a.dataset.action];
    if (fn) {
      Promise.resolve(fn(a, e)).catch(err => toast('Error: ' + err.message, 5000));
      return;
    }
  }
  if (e.target.closest('input, button, a, select, textarea, label')) return;
  const row = e.target.closest('[data-href]');
  if (row) location.hash = row.dataset.href;
});

document.addEventListener('submit', async e => {
  const f = e.target.closest('form[data-form]');
  if (!f) return;
  e.preventDefault();
  const data = Object.fromEntries(new FormData(f));
  try {
    switch (f.dataset.form) {
      case 'search': go('search', searchParams()); break;
      case 'review': go('review', data); break;
      case 'note': {
        if (!data.text.trim()) return;
        await api(`/api/posts/${f.dataset.id}/notes`, { method: 'POST', body: { text: data.text } });
        toast('Note added');
        f.reset();
        if (S.review) {
          const p = S.review.batch[S.review.i];
          if (p) { p.notes = (p.notes || 0) + 1; const n = $('#rv-notes'); if (n) n.textContent = `${p.notes} note(s) on this post`; }
        }
        if (!$('#drawer').hidden && f.closest('#drawer')) openPost(Number(f.dataset.id));
        break;
      }
      case 'channel-note':
        await api(`/api/channels/${encodeURIComponent(f.dataset.name)}/notes`, { method: 'POST', body: { text: data.text } });
        route();
        break;
      case 'crawl': {
        const r = await api('/api/jobs', { method: 'POST', body: { kind: 'crawl', ...data } });
        toast(`Crawl #${r.id} started. Follow it in the monitor above.`);
        f.reset();
        MON.selected = r.id; MON.sig = null;
        await refreshJobs(); await refreshMonitor(); pollActive();
        $('#monitor').scrollIntoView({ block: 'start', behavior: 'smooth' });
        break;
      }
      case 'media': {
        const r = await api('/api/jobs', { method: 'POST', body: { kind: 'media', ...data } });
        toast(`Download #${r.id} started. Follow it in the monitor above.`);
        MON.selected = r.id; MON.sig = null;
        await refreshJobs(); await refreshMonitor(); pollActive();
        $('#monitor').scrollIntoView({ block: 'start', behavior: 'smooth' });
        break;
      }
      case 'import': {
        const r = await api('/api/import', { method: 'POST', body: data });
        toast(`Imported: ${r.posts_new} new posts, ${r.media_new} media`);
        refreshJobs();
        break;
      }
      case 'settings': {
        const body = { investigator: data.investigator, blur: data.blur, grayscale: data.grayscale ? '1' : '0', autoplay_muted: data.autoplay_muted ? '1' : '0',
          collect_phones: data.collect_phones ? '1' : '0', recheck_days: String(Math.max(1, Number(data.recheck_days) || 7)) };
        S.settings = await api('/api/settings', { method: 'POST', body });
        $('#whoami').textContent = 'Signed as ' + S.settings.investigator;
        toast('Saved');
        break;
      }
    }
  } catch (err) { toast('Error: ' + err.message, 5000); }
});

document.addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.change === 'collect-mode') {
    const net = el.value === 'network';
    $('#media-field').hidden = net;
    $('#network-hint').hidden = !net;
  }
  if (el.dataset.change === 'chan-type') go('channels', { ...parseHash().params, type: el.value });
  if (el.dataset.change === 'chan-q') go('channels', { ...parseHash().params, q: el.value });
  if (el.dataset.select !== undefined) {
    const n = $$('[data-select]:checked').length;
    $('#crawl-sel').disabled = !n;
    $('#sel-count').textContent = n ? `${n} selected` : '';
  }
  if (el.closest('form[data-form="search"]') && el.tagName === 'SELECT') go('search', searchParams());
});

document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = e.target.closest('input, textarea, select, [contenteditable]');
  if (e.key === 'Escape') {
    if (typing) { e.target.blur(); return; }
    if (!$('#drawer').hidden) { closeDrawer(); return; }
  }
  if (typing) return;
  const k = e.key;
  if (S.review && $('#drawer').hidden) {
    const p = S.review.batch[S.review.i];
    const tag = S.tags.find(t => t.key && t.key.toLowerCase() === k.toLowerCase());
    if (k === 'r') return void reviewVerdict('relevant');
    if (k === 'v') return void reviewVerdict('verify');
    if (k === 'i') return void reviewVerdict('irrelevant');
    if (k === 'j' || k === 'ArrowRight') return reviewMove(1);
    if (k === 'k' || k === 'ArrowLeft') return reviewMove(-1);
    if (k === 'n') { e.preventDefault(); const t = $('#rv-body textarea'); if (t) t.focus(); return; }
    if (k === 'o' && p) return void openPost(p.id);
    if (k === ' ') { e.preventDefault(); const m = $('#rv-body .m.blurred, #rv-body .m [data-action="play"]'); if (m) reveal(m.closest('.m'), true); return; }
    if (tag && p) { const chip = $(`#rv-body [data-action="toggle-tag"][data-tag="${tag.id}"]`); if (chip) chip.click(); return; }
    return;
  }
  if (!$('#drawer').hidden) {
    if (k === 'j' || k === 'ArrowDown') { e.preventDefault(); drawerStep(1); }
    if (k === 'k' || k === 'ArrowUp') { e.preventDefault(); drawerStep(-1); }
  }
  if (k === '/' && parseHash().name === 'search') { e.preventDefault(); const q = $('input[name="q"]'); if (q) q.focus(); }
});

/* --------------------------------------------------------------------- boot */

async function loadTags() {
  const tags = await api('/api/tags');
  const catOrder = {};                     // categories in the order they were created
  for (const t of tags) catOrder[t.category] = Math.min(catOrder[t.category] ?? Infinity, t.id);
  S.tags = tags.sort((a, b) => catOrder[a.category] - catOrder[b.category] || a.id - b.id);
  S.tagById = Object.fromEntries(S.tags.map(t => [t.id, t]));
}
async function refreshNavCounts() {
  try {
    const o = await api('/api/search?' + qs({ review: 'unreviewed', limit: 1 }));
    $('#nav-unreviewed').textContent = o.total ? num(o.total) : '';
  } catch (e) { /* ignore */ }
}
(async function boot() {
  S.settings = await api('/api/settings');
  $('#whoami').textContent = 'Signed as ' + S.settings.investigator;
  $('#exposure-meter').textContent = `Media revealed today: ${S.settings.exposure_today}`;
  await loadTags();
  window.addEventListener('hashchange', route);
  await route();
  refreshNavCounts();
  setInterval(refreshNavCounts, 20000);
  pollActive();
  setInterval(pollActive, 1500);
})();
