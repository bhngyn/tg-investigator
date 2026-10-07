'use strict';
/* Accounts: who shows up in more than one comment section (overlap) and what a
   comment section's community looks like (phones, languages, activity times).
   Loaded after app.js and comments.js and uses their helpers. */

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function acctLabel(a) {
  const name = a.name || (a.deleted ? 'Deleted account' : '(no name)');
  return `<a href="#" data-action="open-user" data-id="${esc(a.user_id)}"><b dir="auto">${esc(name)}</b>${a.username ? ` @${esc(a.username)}` : ''}</a>
    <span class="mono faint small">${esc(a.user_id)}</span>${a.bot ? ' <span class="pill">bot</span>' : ''}`;
}
const phoneCell = a => a.phone ? `<span class="mono small">+${esc(a.phone)}</span>${a.phone_country ? `<div class="faint small">${esc(a.phone_country)}</div>` : ''}` : '<span class="faint">–</span>';
const chanLink = ch => `<a href="#/channel/${esc(ch)}">@${esc(ch)}</a>`;
const roleText = r => `${esc(r.role)}${r.rank ? ': <span dir="auto">' + esc(r.rank) + '</span>' : ''}${r.removed_detected ? ' (removed)' : ''}`;

function notReady(e) {
  // the page shipped before the server was restarted: static files are live, new API routes aren't
  const html = /not found/i.test(e.message)
    ? '<div class="callout warn"><b>Restart the investigator to enable this page.</b> Stop it with Ctrl-C in its terminal and start it again (<span class="mono">.venv/bin/python -m investigator</span>). Collected data isn\'t touched.</div>'
    : `<div class="callout bad">${esc(e.message)}</div>`;
  $('#acct-body').innerHTML = html;
}

async function renderAccounts(_, params) {
  const tab = params.tab === 'community' ? 'community' : 'overlap';
  main().innerHTML = `
    <div class="page-head"><div><h1>Accounts</h1><p>Who comments where, across the comment sections collected with the logged-in account. Built from data already collected: nothing here contacts Telegram.</p></div></div>
    <div class="row" style="margin-bottom:12px">
      <a class="btn${tab === 'overlap' ? ' on' : ''}" href="#/accounts?tab=overlap">Across comment sections</a>
      <a class="btn${tab === 'community' ? ' on' : ''}" href="#/accounts?${qs({ tab: 'community', channel: params.channel, tz: params.tz })}">Community overview</a>
    </div>
    <div id="acct-body"><div class="empty">Loading…</div></div>`;
  try {
    if (tab === 'overlap') await renderOverlap();
    else await renderCommunity(params);
  } catch (e) { notReady(e); }
}

/* ------------------------------------------------------------------ overlap */

async function renderOverlap() {
  const o = await api('/api/overlap');
  const G = Object.fromEntries(o.groups.map(g => [g.group_id, g]));
  const body = $('#acct-body');
  if (o.groups.length < 2) {
    body.innerHTML = `<div class="callout">Overlap needs at least two collected comment sections; ${o.groups.length ? `only ${chanLink(o.groups[0].channel)} has been collected so far` : 'none have been collected yet'}.
      Collect comments for another channel on the <a href="#/jobs">Collect</a> page or from a channel page, then come back here.</div>`;
    return;
  }
  const sectionList = rows => rows.map(r => `<div class="small nowrap">${chanLink(r.channel)} <b>${num(r.n)}</b>${r.deleted ? ` <span class="faint">(${num(r.deleted)} deleted)</span>` : ''}
    <a class="faint" href="#/comments?${qs({ channels: r.channel, sender: r.user_id })}" title="Their comments here">→</a></div>`).join('');

  const cross = o.cross_admins.length ? `<table class="data"><thead><tr><th>Account</th><th>Admin of</th><th>Comments in other sections</th><th>Phone</th></tr></thead><tbody>
    ${o.cross_admins.map(a => `<tr><td>${acctLabel(a)}</td>
      <td>${a.admin_of.map(r => `<div class="small">${chanLink(r.channel)} <span class="pill seed">${roleText(r)}</span></div>`).join('')}</td>
      <td>${sectionList(a.comments_elsewhere)}</td><td>${phoneCell(a)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="faint small">No admin of one collected comment group has commented in another.</div>';

  const accounts = o.accounts.length ? `<table class="data"><thead><tr><th>Account</th><th class="num">Sections</th><th>Comments per section</th><th>Admin of</th><th>Active</th><th>Phone</th></tr></thead><tbody>
    ${o.accounts.map(a => `<tr><td>${acctLabel(a)}</td><td class="num">${a.sections.length}</td><td>${sectionList(a.sections)}</td>
      <td>${a.admin_of.map(r => `<div class="small">${chanLink(r.channel)} <span class="pill seed">${roleText(r)}</span></div>`).join('') || '<span class="faint">–</span>'}</td>
      <td class="small nowrap">${fmtDate(a.first, false)} – ${fmtDate(a.last, false)}</td><td>${phoneCell(a)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="faint small">No personal account has commented in more than one collected section.</div>';

  const chans = o.channel_identities.length ? `<table class="data"><thead><tr><th>Channel identity</th><th>Posted as it in</th><th class="num">Messages</th><th>Active</th></tr></thead><tbody>
    ${o.channel_identities.map(c => `<tr><td>${c.username ? chanLink(c.username) : ''} <span dir="auto">${esc(c.title || '')}</span> <span class="mono faint small">${esc(c.chat_id)}</span></td>
      <td>${c.channels.split(',').map(chanLink).join(', ')}</td><td class="num">${num(c.n)}</td>
      <td class="small nowrap">${fmtDate(c.first, false)} – ${fmtDate(c.last, false)}</td></tr>`).join('')}</tbody></table>
      <div class="faint small" style="margin-top:4px">Someone with admin rights on that channel posted under its name. In its own comment section that is routine; in someone else's it links the two.</div>`
    : '<div class="faint small">No channel identity has posted in more than one section.</div>';

  const fwds = o.forward_sources.length ? `<table class="data"><thead><tr><th>Forwarded from</th><th>Into</th><th class="num">Forwards</th><th class="num">Accounts forwarding</th><th>Active</th></tr></thead><tbody>
    ${o.forward_sources.map(f => `<tr><td>${f.username ? chanLink(f.username) + ' ' : ''}<span dir="auto">${esc(f.name || '')}</span>
        <span class="faint small">${f.fwd_type === 'hidden' ? '(source hidden)' : `${esc(f.fwd_type)} <span class="mono">${esc(f.fwd_id || '')}</span>`}</span></td>
      <td>${f.channels.split(',').map(chanLink).join(', ')}</td><td class="num">${num(f.n)}</td><td class="num">${num(f.forwarders)}</td>
      <td class="small nowrap">${fmtDate(f.first, false)} – ${fmtDate(f.last, false)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="faint small">No source has been forwarded into more than one section.</div>';

  // pairwise shared commenters
  const shared = {};
  o.matrix.forEach(m => { shared[`${m.g1}|${m.g2}`] = shared[`${m.g2}|${m.g1}`] = m.shared; });
  const matrix = `<div style="overflow-x:auto"><table class="data"><thead><tr><th></th>${o.groups.map(g => `<th class="num">@${esc(g.channel)}</th>`).join('')}</tr></thead><tbody>
    ${o.groups.map(a => `<tr><th>@${esc(a.channel)}</th>${o.groups.map(b => a.group_id === b.group_id
      ? `<td class="num faint" title="Accounts commenting here">${num(a.commenters)}</td>`
      : `<td class="num">${shared[`${a.group_id}|${b.group_id}`] ? `<b>${num(shared[`${a.group_id}|${b.group_id}`])}</b>` : '<span class="faint">0</span>'}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div><div class="faint small" style="margin-top:4px">Accounts that commented in both sections. The diagonal (grey) is each section's total number of commenting accounts.</div>`;

  body.innerHTML = `
    <div class="grid tiles compact">
      <div class="tile"><div class="v">${num(o.groups.length)}</div><div class="k">comment sections</div></div>
      <div class="tile${o.cross_admins.length ? ' warn' : ''}"><div class="v">${num(o.cross_admins.length)}</div><div class="k">admins commenting elsewhere</div></div>
      <div class="tile"><div class="v">${num(o.accounts.length)}</div><div class="k">accounts in 2+ sections</div></div>
      <div class="tile"><div class="v">${num(o.channel_identities.length)}</div><div class="k">channel identities in 2+ sections</div></div>
      <div class="tile"><div class="v">${num(o.forward_sources.length)}</div><div class="k">sources forwarded into 2+</div></div>
    </div>
    <div class="panel" style="margin-top:14px"><h2>Admin in one section, commenting in another</h2>
      <p class="small muted" style="margin-top:0">Admins of a collected comment group who also comment in a different collected section. Admin lists only cover the comment groups; the broadcast channels' own admins aren't visible.</p>${cross}</div>
    <div class="panel" style="margin-top:14px"><div class="row"><h2 style="margin:0">Accounts in more than one section</h2><span class="spacer"></span>
      ${o.accounts.length ? '<a class="btn small" href="/api/overlap/export.csv" title="One row per account per section; includes IDs, usernames and phone numbers where collected">Export CSV</a>' : ''}</div>
      <p class="small muted">Matched by permanent Telegram ID, so name and username changes don't split an account.</p>${accounts}</div>
    <div class="grid two" style="margin-top:14px">
      <div class="panel"><h2>Posting as a channel in several sections</h2>${chans}</div>
      <div class="panel"><h2>Same source forwarded into several sections</h2>${fwds}</div>
    </div>
    <div class="panel" style="margin-top:14px"><h2>Shared commenters between sections</h2>${matrix}</div>`;
}

/* ---------------------------------------------------------------- community */

function hourBars(counts, tz) {
  const w = 700, h = 80, bw = w / 24, max = Math.max(...counts, 1);
  return `<svg class="bars" viewBox="0 0 ${w} ${h + 16}" preserveAspectRatio="none" style="width:100%;height:${h + 16}px">
    ${counts.map((n, i) => {
      const bh = n ? Math.max(1, (n / max) * h) : 0;
      return `<rect class="b" x="${(i * bw).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}"><title>${String(i).padStart(2, '0')}:00–${String(i).padStart(2, '0')}:59 ${tzLabel(tz)}: ${n}</title></rect>`;
    }).join('')}
    ${[0, 6, 12, 18].map(i => `<text x="${(i * bw).toFixed(1)}" y="${h + 13}">${String(i).padStart(2, '0')}:00</text>`).join('')}
    <text x="${w}" y="${h + 13}" text-anchor="end">24:00</text>
  </svg>`;
}
function heatmap(grid, tz) {
  const max = Math.max(1, ...grid.flat());
  return `<div class="heat" style="grid-template-columns:34px repeat(24,1fr)">
    <span></span>${Array.from({ length: 24 }, (_, i) => `<span class="heat-h">${i % 3 ? '' : i}</span>`).join('')}
    ${grid.map((row, d) => `<span class="heat-d">${WEEKDAYS[d]}</span>${row.map((n, i) =>
      `<span class="heat-c" style="--a:${n ? (0.15 + 0.85 * n / max).toFixed(2) : 0}" title="${WEEKDAYS[d]} ${String(i).padStart(2, '0')}:00 ${tzLabel(tz)}: ${n}"></span>`).join('')}`).join('')}
  </div>`;
}
const tzLabel = tz => tz ? `UTC${tz > 0 ? '+' : '−'}${Math.abs(tz)}` : 'UTC';
function shiftHours(c, tz) {
  const hours = Array(24).fill(0), grid = Array.from({ length: 7 }, () => Array(24).fill(0));
  c.weekday_hour_utc.forEach((row, d) => row.forEach((n, h) => {
    const t = h + tz, dd = (d + Math.floor(t / 24) + 7) % 7, hh = ((t % 24) + 24) % 24;
    grid[dd][hh] += n; hours[hh] += n;
  }));
  return { hours, grid };
}

async function renderCommunity(params) {
  const tz = Number(params.tz || 0);
  const [groups, c] = await Promise.all([api('/api/comment_groups'), api('/api/community?' + qs({ channel: params.channel }))]);
  const s = c.summary, body = $('#acct-body');
  const pct = (a, b) => b ? `${Math.round(100 * a / b)}%` : '–';
  const { hours, grid } = shiftHours(c, tz);
  const tzOpts = [0, 1, 2, 3, 4, 5, -5, -4, 6, 7, 8, -3, -8].map(o => opt(o, tzLabel(o) + ({ 2: ' (Khartoum)', 3: ' (Moscow, Riyadh)', 1: ' (West Africa)', 4: ' (Dubai)' }[o] || ''), tz)).join('');
  const controls = `<form class="panel filters" data-form="community">
    <label class="field">Comment section<select name="channel">${opt('', 'All collected sections', params.channel)}${groups.map(g => opt(g.channel, '@' + g.channel, params.channel)).join('')}</select></label>
    <label class="field">Show times in<select name="tz">${tzOpts}</select></label>
  </form>`;
  if (!s.comments) {
    body.innerHTML = controls + `<div class="empty">No comments collected${params.channel ? ' for this section' : ''} yet.</div>`;
    return;
  }
  const tile = (v, k, cls = '', title = '') => `<div class="tile ${cls}"${title ? ` title="${esc(title)}"` : ''}><div class="v">${v}</div><div class="k">${k}</div></div>`;
  const langTotal = c.languages.reduce((a, l) => a + l.comments, 0);
  body.innerHTML = controls + `
    <div class="grid tiles compact" style="margin-top:12px">
      ${tile(num(s.comments), 'comments')}
      ${tile(num(s.accounts), 'personal accounts commenting')}
      ${tile(num(s.with_phone), `phone visible · ${pct(s.with_phone, s.accounts)}`)}
      ${tile(num(s.with_username), `with a username · ${pct(s.with_username, s.accounts)}`)}
      ${tile(num(s.premium), 'Telegram Premium')}
      ${tile(num(s.in_other_sections), 'also comment in other sections', s.in_other_sections ? 'warn' : '', 'Accounts from here seen in another collected comment section')}
      ${tile(num(s.as_channel), 'posted as a channel')}
      ${tile(num(s.anonymous_admin), 'by anonymous admins')}
      ${tile(num(s.one_comment), 'accounts with a single comment')}
      ${tile(pct(s.top10_share * 100, 100), 'of comments by the top 10 accounts', '', 'High concentration can point to a few very active or coordinated accounts')}
      ${tile(num(s.bots), 'bots')}${tile(num(s.deleted), 'comments found deleted', s.deleted ? 'warn' : '')}
    </div>
    <div class="grid two" style="margin-top:14px">
      <div class="panel"><h2>Phone numbers by country</h2>
        <p class="small muted" style="margin-top:0">Only accounts whose privacy settings show their number (${num(s.with_phone)} of ${num(s.accounts)}), so this is a small, self-selected sample. The country comes from the calling code. That shows where the SIM was issued, not where the person is. <b>+888</b> numbers are anonymous numbers bought on Fragment.</p>
        ${c.countries.length ? `<table class="data"><thead><tr><th>Country</th><th>Code</th><th class="num">Accounts</th><th>Who</th></tr></thead><tbody>
          ${c.countries.map(k => `<tr><td>${esc(k.country)}</td><td class="mono small">+${esc(k.code)}</td><td class="num">${num(k.accounts.length)}</td>
            <td class="small">${k.accounts.slice(0, 8).map(a => `<a href="#" data-action="open-user" data-id="${esc(a.user_id)}" dir="auto">${esc(a.name || a.username || a.user_id)}</a>`).join(', ')}${k.accounts.length > 8 ? ` <span class="faint">+${k.accounts.length - 8} more</span>` : ''}</td></tr>`).join('')}</tbody></table>`
          : '<div class="faint small">No phone numbers visible.</div>'}</div>
      <div class="panel"><h2>Languages of comments</h2>
        <p class="small muted" style="margin-top:0">Worked out on this laptop from the alphabet used and a few telltale letters and common words (e.g. і/ї/є for Ukrainian, ы/э for Russian). It's a rough guide: short comments often land in "unsure", and mixed-language comments are counted under their main alphabet. Click a language for examples.</p>
        <table class="data"><thead><tr><th>Language</th><th class="num">Comments</th><th class="num">Share</th><th class="num">Accounts</th></tr></thead><tbody>
          ${c.languages.map((l, i) => `<tr class="${l.samples.length ? 'click' : ''}" data-action="${l.samples.length ? 'lang-samples' : ''}" data-i="${i}"><td>${esc(l.name)}</td><td class="num">${num(l.comments)}</td>
            <td class="num">${pct(l.comments, langTotal)}</td><td class="num">${num(l.accounts)}</td></tr>
            <tr class="lang-samples" data-i="${i}" hidden><td colspan="4">${l.samples.map(x => `<div class="small"><a href="#" data-action="open-comment" data-id="${x.id}" dir="auto">${esc(x.text || '(no text)')}</a></div>`).join('')}</td></tr>`).join('')}
        </tbody></table></div>
    </div>
    <div class="panel" style="margin-top:14px"><h2>When they comment (${tzLabel(tz)})</h2>
      <p class="small muted" style="margin-top:0">Telegram timestamps are UTC. A section whose activity follows one time zone's waking hours hints at where its commenters are; activity around the clock can point to several regions or to scheduled posting.</p>
      ${hourBars(hours, tz)}
      <h3 style="margin-top:12px">By weekday and hour</h3>${heatmap(grid, tz)}
      ${c.per_day.length > 1 ? `<h3 style="margin-top:12px">Comments per day</h3>${bars(c.per_day, { h: 50 })}` : ''}
    </div>
    <div class="panel" style="margin-top:14px"><h2>Most active accounts</h2>
      <table class="data"><thead><tr><th>Account</th><th class="num">Comments</th><th class="num">Sections</th><th>Phone</th><th></th></tr></thead><tbody>
      ${c.top.map(a => `<tr><td>${acctLabel(a)}${a.admin ? ' <span class="pill seed">admin</span>' : ''}${a.premium ? ' <span class="pill">premium</span>' : ''}</td>
        <td class="num">${num(a.comments)}</td><td class="num">${a.sections > 1 ? `<b>${a.sections}</b>` : a.sections}</td><td>${phoneCell(a)}</td>
        <td class="small"><a href="#/comments?${qs({ channels: c.channel || '', sender: a.user_id })}">comments →</a></td></tr>`).join('')}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ wiring */

routes.accounts = renderAccounts;

Object.assign(actions, {
  'lang-samples': el => { const r = $(`tr.lang-samples[data-i="${el.dataset.i}"]`); if (r) r.hidden = !r.hidden; },
});

document.addEventListener('change', e => {
  const f = e.target.closest('form[data-form="community"]');
  if (!f) return;
  const d = Object.fromEntries(new FormData(f));
  go('accounts', { tab: 'community', channel: d.channel, tz: d.tz === '0' ? '' : d.tz });
});
document.addEventListener('submit', e => {
  if (e.target.closest('form[data-form="community"]')) e.preventDefault();
});
