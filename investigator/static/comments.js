'use strict';
/* Comment sections (collected with the logged-in API): search, detail, accounts,
   comment collection and schedules. Loaded after app.js and uses its helpers. */

/* ------------------------------------------------------------ comment cards */

function senderHTML(c, link = true) {
  const id = c.sender_id != null ? `<span class="mono faint small">${esc(c.sender_id)}</span>` : '';
  let who;
  if (c.sender_type === 'user') {
    const name = c.sender_name || (c.sender_deleted ? 'Deleted account' : 'Unknown');
    who = `<b dir="auto">${esc(name)}</b>${c.sender_username ? ` @${esc(c.sender_username)}` : ''}`;
    if (link) who = `<a href="#" data-action="open-user" data-id="${esc(c.sender_id)}">${who}</a>`;
  } else if (c.sender_type === 'channel') {
    who = `<b dir="auto">${esc(c.sender_name || 'Channel')}</b>${c.sender_username ? ` <a href="#/channel/${esc(c.sender_username)}">@${esc(c.sender_username)}</a>` : ''}
      <span class="pill verify" title="Posted under a channel's identity, not a personal account">as channel</span>`;
  } else if (c.sender_type === 'group') {
    who = `<b>Anonymous admin</b> <span class="pill verify" title="An admin posting as the group itself">anonymous</span>`;
  } else {
    who = '<span class="faint">unknown sender</span>';
  }
  const badges = [
    c.admin_role ? `<span class="pill seed" title="Admin of this comment group">${esc(c.admin_role)}${c.admin_rank ? ': ' + esc(c.admin_rank) : ''}</span>` : '',
    c.sender_bot ? '<span class="pill">bot</span>' : '',
    c.post_author ? `<span class="pill" title="Signature on the message">signed: ${esc(c.post_author)}</span>` : '',
  ].join('');
  return `${who} ${id} ${badges}`;
}
function commentFwd(c) {
  if (!c.fwd_type) return '';
  const who = c.fwd_username
    ? `<a href="#/channel/${esc(c.fwd_username)}"><b dir="auto">${esc(c.fwd_name || '')}</b> @${esc(c.fwd_username)}</a>`
    : `<b dir="auto">${esc(c.fwd_name || '')}</b> <span class="faint">${c.fwd_type === 'hidden' ? '(source hidden)' : `(${esc(c.fwd_type)} ${esc(c.fwd_id || '')})`}</span>`;
  return `<div class="fwd">↪ Forwarded from ${who}${c.fwd_post ? ` <span class="faint">post ${esc(c.fwd_post)}</span>` : ''}</div>`;
}
function postRef(c) {
  return c.post_msg_id
    ? `<a href="#/comments?${qs({ channels: c.channel, post: c.post_msg_id })}" title="All comments on this post">on post ${esc(c.post_msg_id)}</a>`
    : '';
}
function commentCard(c, opts = {}) {
  const asPost = { ...c, sensitive: false, video_durations: [], media_unavailable: [] };
  return `<article class="card comment${c.deleted_detected ? ' deleted' : ''}${opts.selected ? ' selected' : ''}" data-comment="${c.id}">
    <div class="card-head">
      <a class="chan" href="#/channel/${esc(c.channel)}">@${esc(c.channel)}</a> ${postRef(c)}
      <span>${fmtDate(c.date)}</span>
      ${c.edit_date || c.versions ? '<span class="pill">edited</span>' : ''}
      ${c.deleted_detected ? `<span class="pill failed" title="Found missing on ${esc(fmtDate(c.deleted_detected))}; last seen ${esc(fmtDate(c.last_seen))}">deleted</span>` : ''}
      ${c.is_mirror ? '<span class="pill">channel post copy</span>' : ''}
    </div>
    <div class="small" style="margin-top:3px">${senderHTML(c)}</div>
    ${commentFwd(c)}
    ${c.text ? `<div class="text${(c.text || '').length > 700 && !opts.full ? ' clamp' : ''}" dir="auto">${renderText(c.text, S.terms)}</div>` : ''}
    ${mediaTiles(asPost, opts.large)}
    ${opts.noFoot ? '' : `<div class="card-foot"><span class="spacer"></span>
      <button class="btn small" data-action="open-comment" data-id="${c.id}">Details</button></div>`}
  </article>`;
}

/* ----------------------------------------------------------- comments page */

async function renderComments(_, params) {
  const P = { limit: 50, ...params };
  S.terms = [];
  const cg = await api('/api/comment_groups');
  main().innerHTML = `
    <div class="page-head"><div><h1>Comments</h1><p>Comment sections collected with the logged-in account. Deleted comments stay here, marked with when they were found missing.</p></div>
      <span class="spacer"></span><a class="btn primary" href="#/jobs">+ Collect comments</a></div>
    <form data-form="comments" class="panel">
      <div class="searchbar">
        <input type="search" name="q" value="${esc(P.q || '')}" placeholder="Search comment text, signatures and forward sources" dir="auto" autofocus>
        <button class="btn primary">Search</button>
      </div>
      <div class="filters">
        <label class="field">Channel<select name="channels">${opt('', 'All comment sections', P.channels)}${cg.map(g => opt(g.channel, '@' + g.channel, P.channels)).join('')}</select></label>
        <label class="field">From<input type="date" name="date_from" value="${esc(P.date_from || '')}"></label>
        <label class="field">To<input type="date" name="date_to" value="${esc(P.date_to || '')}"></label>
        <label class="field">Sender<select name="sender_type">${opt('', 'Anyone', P.sender_type)}${opt('user', 'Personal accounts', P.sender_type)}${opt('channel', 'Posted as a channel', P.sender_type)}${opt('group', 'Anonymous admins', P.sender_type)}</select></label>
        <label class="field">Sort<select name="sort">${opt('', 'Newest', P.sort)}${opt('oldest', 'Oldest', P.sort)}</select></label>
        <label class="check"><input type="checkbox" name="deleted" value="1"${P.deleted === '1' ? ' checked' : ''}> Deleted</label>
        <label class="check"><input type="checkbox" name="forwarded" value="1"${P.forwarded === '1' ? ' checked' : ''}> Forwards</label>
        <label class="check"><input type="checkbox" name="edited" value="1"${P.edited === '1' ? ' checked' : ''}> Edited</label>
        <label class="check"><input type="checkbox" name="media" value="1"${P.media === '1' ? ' checked' : ''}> Media</label>
        <label class="check"><input type="checkbox" name="admins" value="1"${P.admins === '1' ? ' checked' : ''}> By admins</label>
        <label class="check" title="The automatic copies of channel posts that comments hang off"><input type="checkbox" name="mirrors" value="1"${P.mirrors === '1' ? ' checked' : ''}> Include post copies</label>
        <input type="hidden" name="sender" value="${esc(P.sender || '')}">
        <input type="hidden" name="post" value="${esc(P.post || '')}">
        <a class="btn ghost" href="#/comments">Reset</a>
      </div>
    </form>
    <div id="results"><div class="empty">Loading…</div></div>`;
  const res = await api('/api/comments?' + qs(P));
  S.terms = res.terms;
  S.list = res.results.map(c => c.id);
  S.listKind = 'comment';
  const offset = Number(res.offset), limit = Number(P.limit);
  const exportHref = '/api/comments/export.csv?' + qs({ ...P, limit: '', offset: '' });
  $('#results').innerHTML = `
    <div class="result-meta"><b style="color:var(--text)">${num(res.total)}</b> comments
      ${P.sender ? `<span class="tag on" data-action="cm-clear" data-key="sender">sender ${esc(P.sender)} ✕</span>` : ''}
      ${P.post ? `<span class="tag on" data-action="cm-clear" data-key="post">post ${esc(P.post)} ✕</span>` : ''}
      <span class="spacer"></span>
      ${res.total ? `<a class="btn small" href="${esc(exportHref)}" title="Includes sender IDs, usernames and phone numbers where collected">Export CSV</a>` : ''}
    </div>
    ${res.timeline.length > 1 ? `<div class="panel" style="padding:8px 10px 2px;margin-bottom:10px">${bars(res.timeline, { h: 40, onClickAttr: 'cm-day' })}</div>` : ''}
    <div class="cards">${res.results.map(c => commentCard(c)).join('') ||
      `<div class="empty">${cg.length ? 'No comments match.' : 'No comment sections collected yet. Start one on the Collect page or from a channel page.'}</div>`}</div>
    ${res.total > limit ? `<div class="pager">
      <button class="btn" data-action="cm-page" data-offset="${Math.max(0, offset - limit)}" ${offset ? '' : 'disabled'}>← Previous</button>
      <span class="muted small">${num(offset + 1)}–${num(Math.min(offset + limit, res.total))} of ${num(res.total)}</span>
      <button class="btn" data-action="cm-page" data-offset="${offset + limit}" ${offset + limit < res.total ? '' : 'disabled'}>Next →</button></div>` : ''}`;
}
function commentParams() {
  const f = $('form[data-form="comments"]');
  const out = {};
  if (f) new FormData(f).forEach((v, k) => { if (v) out[k] = v; });
  return out;
}

/* --------------------------------------------------------- comment drawer */

async function openComment(id) {
  S.listIndex = S.list.indexOf(id);
  $('#drawer').hidden = false;
  $('#drawer-body').innerHTML = '<div class="empty">Loading…</div>';
  $$('.card.selected').forEach(c => c.classList.remove('selected'));
  const el = $(`.card[data-comment="${id}"]`);
  if (el) { el.classList.add('selected'); el.scrollIntoView({ block: 'nearest' }); }
  let c;
  try { c = await api(`/api/comments/${id}`); } catch (e) { $('#drawer-body').innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  const postUrl = c.post_msg_id ? `https://t.me/${c.channel}/${c.post_msg_id}` : null;
  const commentUrl = c.post_msg_id ? `${postUrl}?comment=${c.msg_id}` : null;
  $('#drawer-body').innerHTML = `
    ${c.parent ? `<div class="section" style="margin-top:0"><h3>In reply to</h3>${commentCard(c.parent, { noFoot: true })}
      <div class="small"><a href="#" data-action="open-comment" data-id="${c.parent.id}">Open that comment →</a></div></div>` : ''}
    ${commentCard(c, { full: true, large: true, noFoot: true })}
    ${c.deleted_detected ? `<div class="callout bad" style="margin-top:10px"><b>Deleted on Telegram.</b> Still there on ${fmtDate(c.last_seen)}; found missing on ${fmtDate(c.deleted_detected)}. It was deleted between those two checks. This copy is what was captured before.</div>` : ''}
    ${commentUrl ? `<div class="row" style="margin-top:8px"><span class="spacer"></span>
      <a class="btn small" href="${esc(commentUrl)}" target="_blank" rel="noreferrer noopener" title="Opens t.me in your browser. Telegram will see this visit.">Open in Telegram ↗</a></div>` : ''}

    ${c.post ? `<div class="section"><h3>The channel post</h3><div class="small"><a href="#" data-action="open-post" data-id="${c.post.id}">@${esc(c.channel)}/${esc(c.post_msg_id)} · ${fmtDate(c.post.date)}</a></div>
      <div class="small faint" dir="auto" style="white-space:pre-wrap;max-height:4.5em;overflow:hidden">${esc((c.post.text || '').slice(0, 300))}</div></div>`
      : postUrl ? `<div class="section"><h3>The channel post</h3><div class="small mono">${esc(postUrl)}</div><div class="faint small">Not in the collection yet (crawl the channel to add it).</div></div>` : ''}

    <div class="section"><h3>Comment data</h3><dl class="kv">
      <dt>Message ID</dt><dd class="mono">${esc(c.msg_id)} <span class="faint small">in comment group ${esc(c.group_id)}</span></dd>
      <dt>Sender</dt><dd>${senderHTML(c)}</dd>
      ${c.sender_phone ? `<dt>Phone</dt><dd class="mono">+${esc(c.sender_phone)}</dd>` : ''}
      <dt>Posted</dt><dd>${fmtDate(c.date)}${c.edit_date ? ` · edited ${fmtDate(c.edit_date)}` : ''}</dd>
      ${c.reactions_total ? `<dt>Reactions</dt><dd>${num(c.reactions_total)}</dd>` : ''}
      ${c.fwd_type ? `<dt>Forwarded from</dt><dd>${esc(c.fwd_type)} ${esc(c.fwd_id || '')} ${c.fwd_username ? '@' + esc(c.fwd_username) : ''} ${c.fwd_post ? 'post ' + esc(c.fwd_post) : ''} ${c.fwd_date ? '· originally ' + fmtDate(c.fwd_date) : ''}</dd>` : ''}
    </dl></div>

    <div class="section"><h3>Evidence trail</h3><dl class="kv">
      <dt>First captured</dt><dd>${fmtDate(c.first_captured)}</dd>
      <dt>Last captured</dt><dd>${fmtDate(c.last_captured)} · ${c.capture_count} capture${c.capture_count > 1 ? 's' : ''}</dd>
      <dt>Last seen on Telegram</dt><dd>${fmtDate(c.last_seen)}</dd>
      ${c.raw_capture ? `<dt>Source (API)</dt><dd><span class="mono">${esc(c.raw_capture.url)}</span><br>
        <span class="mono" title="SHA-256 of the raw API messages">${esc(c.raw_capture.sha256)}</span><br>
        <span class="faint small">${fmtDate(c.raw_capture.captured_at)} · ${esc(c.raw_capture.tool || '')} · account ${esc(c.raw_capture.account || '?')} ·
        <a href="/media/${esc(c.raw_capture.path)}" download>download raw API data (.jsonl.gz)</a></span></dd>` : ''}
    </dl>
    ${(c.media || []).length ? `<table class="data" style="margin-top:10px"><thead><tr><th>Item</th><th>SHA-256</th><th class="num">Size</th><th>Captured</th></tr></thead><tbody>
      ${c.media.map(m => `<tr><td class="nowrap">${esc(m.kind)} #${m.idx}</td>
        <td><span class="mono" title="${esc(m.sha256)}">${esc(m.sha256.slice(0, 16))}…</span> <button class="btn small ghost" data-action="copy" data-text="${esc(m.sha256)}">copy</button></td>
        <td class="num nowrap">${fmtBytes(m.bytes)}</td><td class="nowrap small">${fmtDate(m.captured_at)}</td></tr>`).join('')}</tbody></table>` : ''}
    ${(c.versions || []).length ? `<div style="margin-top:10px"><h3>Earlier versions</h3>${c.versions.map(v =>
      `<div class="note"><div class="by">Captured ${fmtDate(v.captured_at)}</div><div dir="auto" style="white-space:pre-wrap">${esc(v.text)}</div></div>`).join('')}</div>` : ''}
    </div>

    ${(c.replies || []).length ? `<div class="section"><h3>Replies (${c.replies.length})</h3>${c.replies.map(r =>
      `<div class="row small"><a href="#" data-action="open-comment" data-id="${r.id}">${esc(r.sender_name || r.sender_username || r.sender_type)}</a>
        <span class="faint">${fmtDate(r.date)}</span>${r.deleted_detected ? '<span class="pill failed">deleted</span>' : ''}</div>`).join('')}</div>` : ''}`;
}

/* ------------------------------------------------------------ account drawer */

async function openUser(uid) {
  $('#drawer').hidden = false;
  $('#drawer-body').innerHTML = '<div class="empty">Loading…</div>';
  let u;
  try { u = await api(`/api/tg_users/${uid}`); } catch (e) { $('#drawer-body').innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  const name = [u.first_name, u.last_name].filter(Boolean).join(' ') || (u.deleted ? 'Deleted account' : '(no name)');
  const flags = ['bot', 'premium', 'verified', 'scam', 'fake', 'deleted'].filter(k => u[k]).map(k => `<span class="pill">${k}</span>`).join(' ');
  $('#drawer-body').innerHTML = `
    <h2 dir="auto" style="margin-bottom:2px">${esc(name)}</h2>
    <div class="small muted">${u.username ? '@' + esc(u.username) + ' · ' : ''}Telegram ID <span class="mono">${esc(u.user_id)}</span>
      <button class="btn small ghost" data-action="copy" data-text="${esc(u.user_id)}">copy</button> ${flags}</div>
    <div class="section"><dl class="kv">
      <dt>Telegram ID</dt><dd class="mono">${esc(u.user_id)} <span class="faint small">(permanent; survives name and username changes)</span></dd>
      <dt>Phone</dt><dd>${u.phone ? `<span class="mono">+${esc(u.phone)}</span>` : '<span class="faint">hidden by their privacy settings</span>'}</dd>
      <dt>First seen</dt><dd>${fmtDate(u.first_seen)}</dd><dt>Last seen</dt><dd>${fmtDate(u.last_seen)}</dd>
    </dl></div>
    ${u.roles.length ? `<div class="section"><h3>Admin roles</h3><table class="data"><tbody>${u.roles.map(r => `<tr>
      <td><a href="#/channel/${esc(r.channel)}">@${esc(r.channel)}</a> <span class="faint small">comments</span></td>
      <td><span class="pill seed">${esc(r.role)}${r.rank ? ': ' + esc(r.rank) : ''}</span>${r.removed_detected ? ' <span class="pill failed">removed</span>' : ''}</td>
      <td class="small faint nowrap">${r.since ? 'since ' + fmtDate(r.since, false) : ''}</td></tr>`).join('')}</tbody></table></div>` : ''}
    <div class="section"><h3>Comment sections they post in</h3>${u.groups.length ? `<table class="data"><thead><tr><th>Channel</th><th class="num">Comments</th><th class="num">Deleted</th><th class="num">Forwards</th><th>Active</th></tr></thead><tbody>
      ${u.groups.map(g => `<tr class="click" data-href="#/comments?${qs({ channels: g.channel, sender: u.user_id })}"><td>@${esc(g.channel)}</td><td class="num">${num(g.n)}</td>
        <td class="num">${num(g.deleted)}</td><td class="num">${num(g.forwards)}</td><td class="small nowrap">${fmtDate(g.first, false)} – ${fmtDate(g.last, false)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="faint small">No comments collected from this account (seen in an admin list only).</div>'}</div>
    <div class="section"><h3>Name / username history</h3><table class="data"><thead><tr><th>Captured</th><th>Name</th><th>Username</th><th>Phone</th></tr></thead><tbody>
      ${u.snapshots.map(s => `<tr><td class="small nowrap">${fmtDate(s.captured_at)}</td><td dir="auto">${esc([s.first_name, s.last_name].filter(Boolean).join(' '))}</td>
        <td>${s.username ? '@' + esc(s.username) : '–'}</td><td class="mono small">${s.phone ? '+' + esc(s.phone) : '–'}</td></tr>`).join('')}</tbody></table></div>
    <div class="row" style="margin-top:12px"><a class="btn small primary" href="#/comments?${qs({ sender: u.user_id })}">All their comments</a></div>`;
}

/* ------------------------------------------------ channel page: comment section */

function commentSectionPanel(c) {
  const g = c.comment_group, s = c.schedule;
  const head = `<div class="row"><h2 style="margin:0">Comment section</h2><span class="spacer"></span>
    <button class="btn small primary" data-action="collect-comments" data-name="${esc(c.username)}">Collect comments now</button></div>`;
  const sched = `<div class="row small" style="margin-top:10px">
      <b>Keep collecting:</b>
      ${s ? `<span class="pill ${s.enabled ? 'running' : ''}">${s.enabled ? `every ${fmtEvery(s.interval_min)}` : 'paused'}</span>
        ${s.enabled ? `<span class="faint">next ${s.next_run ? fmtDate(s.next_run) : 'soon'} · media: ${esc(s.media)}</span>` : ''}
        <button class="btn small" data-action="schedule-toggle" data-id="${s.id}" data-enabled="${s.enabled ? 0 : 1}">${s.enabled ? 'Pause' : 'Resume'}</button>`
        : `<select id="sched-int">${opt(5, 'every 5 min')}${opt(10, 'every 10 min', 10)}${opt(30, 'every 30 min')}${opt(60, 'every hour')}${opt(360, 'every 6 hours')}</select>
           <select id="sched-media">${opt('none', 'text only', 'none')}${opt('photos', '+ photos')}${opt('all', '+ photos and videos')}</select>
           <button class="btn small" data-action="schedule-create" data-name="${esc(c.username)}">Start</button>`}
      <span class="faint">Each run fetches new comments and re-checks recent ones for deletions and edits.</span></div>`;
  if (!g) return `<div class="panel" style="margin-top:14px">${head}<div class="faint small" style="margin-top:6px">Not collected yet. Uses the logged-in account and reads without joining the group.</div>${sched}</div>`;
  const admins = c.admins || [];
  return `<div class="panel" style="margin-top:14px">${head}
    <div class="grid tiles compact" style="margin-top:10px">
      <a class="tile" href="#/comments?${qs({ channels: c.username })}" style="color:inherit;text-decoration:none"><div class="v">${num(g.comments)}</div><div class="k">comments collected</div></a>
      <a class="tile${g.deleted ? ' warn' : ''}" href="#/comments?${qs({ channels: c.username, deleted: 1 })}" style="color:inherit;text-decoration:none"><div class="v">${num(g.deleted)}</div><div class="k">found deleted</div></a>
      <div class="tile"><div class="v">${num(g.commenters)}</div><div class="k">accounts commenting</div></div>
      <div class="tile"><div class="v">${num(g.participants)}</div><div class="k">group members${g.hidden_members ? ' (list hidden)' : ''}</div></div>
      <div class="tile"><div class="v" style="font-size:15px">${ago(g.last_checked)}</div><div class="k">last collected</div></div>
    </div>
    <div class="small muted" style="margin-top:6px">Group <span class="dir-auto" dir="auto">${esc(g.title || '')}</span> · ID <span class="mono">${esc(g.group_id)}</span>${g.username ? ' · @' + esc(g.username) : ' · private (no username)'} · comments from ${fmtDate(g.first_date, false)} to ${fmtDate(g.last_date, false)} · <a href="#/accounts?${qs({ tab: 'community', channel: c.username })}">Community overview →</a></div>
    ${sched}
    <h3 style="margin-top:14px">Admins of the comment group (${admins.length})</h3>
    ${admins.length ? `<table class="data"><thead><tr><th>Account</th><th>Telegram ID</th><th>Role / title</th><th>Rights</th><th>Since</th><th class="num">Comments (all groups)</th></tr></thead><tbody>
      ${admins.map(a => `<tr class="click" data-action="open-user" data-id="${a.user_id}">
        <td dir="auto">${esc([a.first_name, a.last_name].filter(Boolean).join(' ') || '–')}${a.username ? ` <span class="faint">@${esc(a.username)}</span>` : ''}${a.bot ? ' <span class="pill">bot</span>' : ''}${a.removed_detected ? ' <span class="pill failed">removed</span>' : ''}</td>
        <td class="mono small">${esc(a.user_id)}</td>
        <td><span class="pill seed">${esc(a.role)}</span> ${a.rank ? `<span dir="auto">${esc(a.rank)}</span>` : ''}</td>
        <td class="small faint">${esc(JSON.parse(a.rights || '[]').join(', '))}</td>
        <td class="small nowrap">${a.since ? fmtDate(a.since, false) : '–'}</td>
        <td class="num">${num(a.comments_anywhere)}${a.groups_anywhere > 1 ? `<div class="small faint">in ${a.groups_anywhere} groups</div>` : ''}</td></tr>`).join('')}</tbody></table>
      <div class="faint small" style="margin-top:4px">The broadcast channel's own admins are only visible to its admins; these are the comment group's admins, which Telegram lets members see.</div>`
      : '<div class="faint small">The admin list could not be read.</div>'}
  </div>`;
}

/* ---------------------------------------------------------- collect page */

function commentsCollectPanel() {
  return `<div class="grid two" style="margin-top:14px">
    <form class="panel" data-form="comments-job"><h2>Collect comments (logged-in)</h2>
      <label class="field">Channel<input type="text" name="channel" placeholder="example_channel or t.me link"></label>
      <div class="filters">
        <label class="field">First time: newest<input type="number" name="limit" value="200" min="1" style="width:90px"></label>
        <label class="field">Media<select name="media">${opt('none', 'Text only', 'none')}${opt('photos', '+ photos')}${opt('all', '+ photos and videos')}</select></label>
        <label class="field">Skip files over (MB)<input type="number" name="max_mb" value="50" min="1" style="width:90px"></label>
      </div>
      <div class="hint">Reads the channel's comment group without joining it. The first run takes the newest N messages; later runs pick up everything new since the last one and re-check recent comments for deletions and edits. Records commenters' Telegram IDs, names, usernames${S.settings.collect_phones !== '0' ? ', phone numbers where visible' : ''} and the group's admin list.</div>
      <div class="row" style="margin-top:10px"><button class="btn primary">Collect</button></div>
    </form>
    <div class="panel"><h2>Scheduled comment collection</h2><div id="schedules"></div>
      <form data-form="schedule-add" class="row" style="margin-top:10px">
        <input type="text" name="channel" placeholder="channel" style="width:160px">
        <select name="interval_min">${opt(5, 'every 5 min')}${opt(10, 'every 10 min', 10)}${opt(30, 'every 30 min')}${opt(60, 'every hour')}${opt(360, 'every 6 hours')}${opt(1440, 'daily')}</select>
        <select name="media">${opt('none', 'text only', 'none')}${opt('photos', '+ photos')}${opt('all', '+ photos and videos')}</select>
        <button class="btn small">Add</button></form>
      <div class="hint">For comment sections that get wiped: a short interval captures comments before they are deleted. Runs use the logged-in account; comment and video jobs take turns, while crawls run alongside.</div>
    </div></div>`;
}
const fmtEvery = m => m % 1440 === 0 ? (m === 1440 ? 'daily' : m / 1440 + ' days') : m % 60 === 0 ? m / 60 + ' h' : m + ' min';
async function refreshSchedules() {
  const el = $('#schedules');
  if (!el) return;
  const rows = await api('/api/schedules');
  el.innerHTML = rows.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr><th>Channel</th><th>Every</th><th>Media</th><th>Next / last run</th><th></th></tr></thead><tbody>
    ${rows.map(s => `<tr><td><a href="#/channel/${esc(s.channel)}">@${esc(s.channel)}</a></td><td class="nowrap">${fmtEvery(s.interval_min)}</td><td class="small">${esc(s.media)}</td>
      <td class="small">${s.enabled ? `next ${s.next_run ? fmtDate(s.next_run) : 'now'}` : '<span class="pill">paused</span>'}<div class="faint">${s.last_run ? `last ${ago(s.last_run)}${s.last_status ? ' · ' + esc(s.last_status) : ''}` : 'never run'}</div></td>
      <td class="nowrap"><button class="btn small" data-action="schedule-run" data-id="${s.id}">Run now</button>
        <button class="btn small" data-action="schedule-toggle" data-id="${s.id}" data-enabled="${s.enabled ? 0 : 1}">${s.enabled ? 'Pause' : 'Resume'}</button>
        <button class="btn small ghost" data-action="schedule-delete" data-id="${s.id}">Remove</button></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="faint small">None. Add a channel below, or use "Keep collecting" on a channel page.</div>';
  if (rows[0]?.scheduler_error) el.insertAdjacentHTML('afterbegin', `<div class="callout bad" style="margin-bottom:8px">The scheduler hit an error and will keep retrying: <span class="mono small">${esc(rows[0].scheduler_error)}</span></div>`);
}

/* ------------------------------------------------------------- job monitor */

function commentsMonitor(pr) {
  const t = pr.totals || {};
  const tile = (v, k, cls = '') => `<div class="tile ${cls}"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  return `
    <div class="current"><div class="row"><b>Comments of <a href="#/channel/${esc(pr.channel)}">@${esc(pr.channel)}</a></b>
      ${pr.group ? `<span class="small muted" dir="auto">${esc(pr.group.title)} · ${num(pr.group.participants)} members</span>` : ''}
      <span class="spacer"></span><span class="small muted">${esc(pr.activity || pr.phase || '')}</span></div>
      ${pr.account ? `<div class="small faint">Account: ${esc(pr.account)}</div>` : ''}</div>
    <div class="grid tiles compact" style="margin-top:12px">
      ${tile(num(t.comments), 'new comments')}${tile(num(t.mirrors), 'post copies')}${tile(num(t.users), 'accounts seen')}
      ${tile(num(t.admins), 'admins listed')}${tile(num(t.rechecked), 'earlier comments re-checked')}
      ${tile(num(t.deleted), 'found deleted', t.deleted ? 'warn' : '')}${tile(num(t.files), `files · ${fmtBytes(t.bytes)}`)}
      ${tile(num(t.errors), 'errors', t.errors ? 'bad' : '')}
    </div>`;
}
function commentsJobDesc(p) {
  return `@${esc(p.channel)} · ${p.schedule_id ? 'scheduled' : `newest ${p.limit || 200} first time`} · media ${esc(p.media || 'none')}`;
}
function commentsJobSummary(s) {
  if (s.note) return `<span class="small faint">${esc(s.note)}</span>`;
  return `<span class="small">${num(s.comments_new)} new comments · ${num(s.deleted_detected)} deleted · ${num(s.comments_changed)} edited${s.media_new ? ` · ${num(s.media_new)} media` : ''}</span>`;
}

/* ----------------------------------------------------------------- wiring */

routes.comments = renderComments;

Object.assign(actions, {
  'open-comment': (el, e) => { e.preventDefault(); openComment(Number(el.dataset.id)); },
  'open-user': (el, e) => { e.preventDefault(); openUser(Number(el.dataset.id)); },
  'cm-page': el => { go('comments', { ...parseHash().params, offset: el.dataset.offset }); main().scrollTop = 0; },
  'cm-day': el => go('comments', { ...parseHash().params, date_from: el.dataset.day, date_to: el.dataset.day, offset: '' }),
  'cm-clear': el => go('comments', { ...parseHash().params, [el.dataset.key]: '', offset: '' }),
  'collect-comments': async el => {
    const r = await api('/api/jobs', { method: 'POST', body: { kind: 'comments', channel: el.dataset.name } });
    toast(`Comment collection #${r.id} queued. Watch it in the sidebar or on the Collect page.`);
    pollActive();
  },
  'schedule-create': async el => {
    await api('/api/schedules', { method: 'POST', body: { channel: el.dataset.name, interval_min: Number($('#sched-int').value), media: $('#sched-media').value } });
    toast('Scheduled. The first run starts within a minute.');
    route();
  },
  'schedule-toggle': async el => {
    await api('/api/schedules', { method: 'POST', body: { id: Number(el.dataset.id), enabled: el.dataset.enabled === '1' } });
    toast(el.dataset.enabled === '1' ? 'Resumed' : 'Paused');
    if ($('#schedules')) refreshSchedules(); else route();
  },
  'schedule-run': async el => {
    const r = await api(`/api/schedules/${el.dataset.id}/run`, { method: 'POST' });
    toast(r.queued.length ? `Job #${r.queued[0]} queued` : 'Already running');
    refreshSchedules(); refreshJobs(); pollActive();
  },
  'schedule-delete': async el => {
    if (!confirm('Stop and remove this schedule? Collected comments are kept.')) return;
    await api('/api/schedules', { method: 'POST', body: { id: Number(el.dataset.id), delete: true } });
    refreshSchedules();
  },
});

document.addEventListener('submit', async e => {
  const f = e.target.closest('form[data-form]');
  if (!f || !['comments', 'comments-job', 'schedule-add'].includes(f.dataset.form)) return;
  e.preventDefault();
  const data = Object.fromEntries(new FormData(f));
  try {
    if (f.dataset.form === 'comments') go('comments', commentParams());
    if (f.dataset.form === 'comments-job') {
      const r = await api('/api/jobs', { method: 'POST', body: { kind: 'comments', ...data } });
      toast(`Comment collection #${r.id} started. Follow it in the monitor above.`);
      f.reset();
      MON.selected = r.id; MON.sig = null;
      await refreshJobs(); await refreshMonitor(); pollActive();
      $('#monitor').scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
    if (f.dataset.form === 'schedule-add') {
      await api('/api/schedules', { method: 'POST', body: { ...data, interval_min: Number(data.interval_min) } });
      f.reset();
      toast('Scheduled. The first run starts within a minute.');
      refreshSchedules();
    }
  } catch (err) { toast('Error: ' + err.message, 5000); }
});

document.addEventListener('change', e => {
  const f = e.target.closest('form[data-form="comments"]');
  if (f && (e.target.tagName === 'SELECT' || e.target.type === 'checkbox')) go('comments', commentParams());
});
