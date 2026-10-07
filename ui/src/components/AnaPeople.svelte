<script>
  // "Who shows up across comment sections?" Shared commenters, cross-section admins, channel identities, forwards.
  import { get, csvUrl } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { scoped } from '../lib/store.svelte.js';
  import { dt, day, num } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp } from './AnaUtil.js';

  let data = $state(null), error = $state(null), loading = $state(true);
  let seq = 0;
  $effect(() => {
    const params = scoped({});
    const my = ++seq;
    loading = true; error = null;
    get('/api/overlap', params).then((d) => { if (my === seq) data = d; })
      .catch((e) => { if (my === seq) { error = e.message; data = null; } })
      .finally(() => { if (my === seq) loading = false; });
  });

  const minSec = $derived(Number(route.query.min) || 2);
  const hideBots = $derived(route.query.bots !== '1');
  let text = $state('');
  const label = (a) => a.name || (a.username ? '@' + a.username : 'account ' + a.user_id);
  const match = (a) => !text.trim() || [a.name, a.username, a.phone, String(a.user_id)].some((x) => (x || '').toString().toLowerCase().includes(text.trim().toLowerCase()));
  const accounts = $derived((data?.accounts || []).filter((a) => a.sections.length >= minSec && (!hideBots || !a.bot) && match(a)));
  const admins = $derived((data?.cross_admins || []).filter((a) => match(a)));
  const empty = $derived(data && !data.accounts.length && !data.cross_admins.length && !data.channel_identities.length && !data.forward_sources.length);

  // shared-commenter matrix
  const chanOf = $derived(Object.fromEntries((data?.groups || []).map((g) => [g.group_id, g.channel])));
  const mx = $derived.by(() => {
    const m = data?.matrix || [];
    if (!m.length) return null;
    const tot = {};
    for (const r of m) { tot[r.g1] = (tot[r.g1] || 0) + r.shared; tot[r.g2] = (tot[r.g2] || 0) + r.shared; }
    const ids = Object.keys(tot).sort((a, b) => tot[b] - tot[a]).slice(0, 20).map(Number);
    const cell = {};
    let max = 1;
    for (const r of m) { cell[r.g1 + ':' + r.g2] = r.shared; cell[r.g2 + ':' + r.g1] = r.shared; max = Math.max(max, r.shared); }
    return { ids, cell, max, more: Object.keys(tot).length - ids.length };
  });
  const commenters = $derived(Object.fromEntries((data?.groups || []).map((g) => [g.group_id, g.commenters])));

  function exportCsv() {
    const rows = [];
    for (const a of accounts) for (const s of a.sections) rows.push({
      user_id: a.user_id, username: a.username, name: a.name, phone: a.phone, phone_country_guess: a.phone_country, bot: a.bot ? 1 : 0,
      sections: a.sections.length, channel: s.channel, comments: s.n, deleted: s.deleted, first: s.first, last: s.last,
      admin_of: a.admin_of.map((x) => `${x.channel} (${x.role})`).join('; '),
    });
    download(`shared-commenters-${stamp()}.csv`, toCsv(rows, ['user_id', 'username', 'name', 'phone', 'phone_country_guess', 'bot', 'sections', 'channel', 'comments', 'deleted', 'first', 'last', 'admin_of']), 'text/csv');
  }
</script>

<AnaHead question="Who shows up across comment sections?">
  Accounts, channel identities and forwarded sources seen in more than one collected comment section. Appearing in several sections is common for ordinary readers of one topic;
  it points to a lead only when combined with timing, content or an admin role. Phone country comes from the number’s calling code (<b>heuristic</b>) and says nothing certain about where the person is.
</AnaHead>

{#if loading}
  <Loading />
{:else if error}
  <div class="callout bad">Could not load overlap: {error}</div>
{:else if empty}
  <Empty title="No shared people yet">
    {#if !data.groups.length}
      No comment section has been collected. Comments are read through the logged-in Telegram account and only when you start it, one channel at a time. Start from <a href={href('channels')}>Channels</a>.
    {:else}
      {num(data.groups.length)} comment section{data.groups.length === 1 ? '' : 's'} collected, but no account, channel identity or forward source appears in more than one of them.
    {/if}
  </Empty>
{:else}
  <div class="row controls">
    <div class="stats grow">
      <div class="stat panel"><div class="v">{num(data.accounts.length)}</div><div class="l">accounts in 2+ sections</div></div>
      <div class="stat panel"><div class="v">{num(data.cross_admins.length)}</div><div class="l">admins commenting elsewhere</div></div>
      <div class="stat panel"><div class="v">{num(data.channel_identities.length)}</div><div class="l">channels commenting as themselves</div></div>
      <div class="stat panel"><div class="v">{num(data.forward_sources.length)}</div><div class="l">forwarded sources</div></div>
    </div>
  </div>
  <div class="row controls">
    <label class="field">Search people<input type="search" placeholder="name, @username, phone, id" bind:value={text} data-page-search /></label>
    <label class="field">In at least
      <select value={minSec} onchange={(e) => setQuery({ min: e.currentTarget.value })}>{#each [2, 3, 4, 5] as n}<option value={n}>{n} sections</option>{/each}</select>
    </label>
    <label class="row small" style="gap:6px"><input type="checkbox" checked={!hideBots} onchange={(e) => setQuery({ bots: e.currentTarget.checked ? '1' : null })} /> Include bots</label>
    <span class="spacer"></span>
    <button class="sm" disabled={!accounts.length} onclick={exportCsv} title="The accounts shown, built in your browser (respects the case scope)">Export shown as CSV</button>
    <a class="btn sm" href={csvUrl('/api/overlap/export.csv', scoped())} download title="Server export; it currently covers all collected sections, not only this case">Server CSV (all sections)</a>
  </div>

  <h3 class="section-title">Admins of one section who comment in another ({num(admins.length)})</h3>
  {#if !admins.length}<p class="muted small">None. An account that moderates one collected section and writes in a different one would appear here.</p>
  {:else}
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Account</th><th scope="col">Admin of</th><th scope="col">Comments elsewhere</th><th scope="col"></th></tr></thead>
      <tbody>{#each admins as a (a.user_id)}
        <tr>
          <td><b>{label(a)}</b>{#if a.username && a.name}<div class="faint small"><bdi dir="ltr">@{a.username}</bdi></div>{/if}{#if a.bot}<span class="pill">bot</span>{/if}</td>
          <td class="small">{#each a.admin_of as r}<div><ChannelLink name={r.channel} showHandle={false} /> <span class="faint">{r.role}{r.rank ? ': ' + r.rank : ''}{r.removed_detected ? ' (removed ' + day(r.removed_detected) + ')' : ''}</span></div>{/each}</td>
          <td class="small">{#each a.comments_elsewhere as r}<div><ChannelLink name={r.channel} showHandle={false} /> <b class="num">{r.n}</b></div>{/each}</td>
          <td><a class="btn sm" href={href('comments', { sender: a.user_id })}>Comments</a></td>
        </tr>{/each}</tbody>
    </table></div>
  {/if}

  <h3 class="section-title">Accounts in several sections ({num(accounts.length)}{accounts.length !== data.accounts.length ? ' of ' + num(data.accounts.length) : ''})</h3>
  {#if !accounts.length}<p class="muted small">No accounts match.</p>
  {:else}
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Account</th><th scope="col">Phone country <span class="faint">(guess)</span></th><th scope="col">Sections</th><th scope="col" class="right">Comments</th><th scope="col">Active</th><th scope="col"></th></tr></thead>
      <tbody>{#each accounts.slice(0, 300) as a (a.user_id)}
        <tr>
          <td><a href={href('comments', { sender: a.user_id })}><b>{label(a)}</b></a>{#if a.username && a.name}<div class="faint small"><bdi dir="ltr">@{a.username}</bdi></div>{/if}
            {#if a.bot}<span class="pill">bot</span>{/if}{#if a.deleted}<span class="pill bad">deleted account</span>{/if}
            {#if a.admin_of.length}<span class="pill warn" title="Admin of a collected section">admin</span>{/if}</td>
          <td class="small">{a.phone_country || '–'}</td>
          <td class="small">{#each a.sections as s}<div><ChannelLink name={s.channel} showHandle={false} /> <b class="num">{s.n}</b>{#if s.deleted} <span class="faint">({s.deleted} deleted)</span>{/if}</div>{/each}</td>
          <td class="right num"><b>{a.total}</b></td>
          <td class="small nowrap">{day(a.first)} → {day(a.last)}</td>
          <td><a class="btn sm" href={href('comments', { sender: a.user_id })}>Comments</a></td>
        </tr>{/each}</tbody>
    </table></div>
    {#if accounts.length > 300}<p class="faint small">Showing 300; export for all.</p>{/if}
  {/if}

  {#if data.channel_identities.length}
    <h3 class="section-title">Channels commenting as themselves in several sections ({num(data.channel_identities.length)})</h3>
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Channel identity</th><th scope="col">Sections</th><th scope="col" class="right">Comments</th><th scope="col">Active</th></tr></thead>
      <tbody>{#each data.channel_identities as c (c.chat_id)}
        <tr><td>{#if c.username}<ChannelLink name={c.username} title={c.title} />{:else}<b>{c.title || 'chat ' + c.chat_id}</b>{/if}</td>
          <td class="small">{c.sections}: {(c.channels || '').split(',').map((x) => '@' + x).join(', ')}</td>
          <td class="right num">{c.n}</td><td class="small nowrap">{day(c.first)} → {day(c.last)}</td></tr>{/each}</tbody>
    </table></div>
  {/if}

  {#if data.forward_sources.length}
    <h3 class="section-title">Sources forwarded into several sections ({num(data.forward_sources.length)})</h3>
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Forwarded from</th><th scope="col">Sections</th><th scope="col" class="right">Times</th><th scope="col" class="right">Accounts forwarding</th><th scope="col">Active</th></tr></thead>
      <tbody>{#each data.forward_sources as f}
        <tr><td>{#if f.username}<ChannelLink name={f.username} title={f.name} />{:else}<b>{f.name || f.fwd_type + ' ' + (f.fwd_id ?? '')}</b>{/if}<span class="faint small"> · {f.fwd_type}</span></td>
          <td class="small">{f.sections}: {(f.channels || '').split(',').map((x) => '@' + x).join(', ')}</td>
          <td class="right num">{f.n}</td><td class="right num">{f.forwarders}</td><td class="small nowrap">{day(f.first)} → {day(f.last)}</td></tr>{/each}</tbody>
    </table></div>
  {/if}

  {#if mx}
    <h3 class="section-title">Commenters shared between sections</h3>
    <p class="muted small" style="max-width:80ch">Each cell counts accounts that commented in both sections. Larger sections share more people by chance, so read this alongside the section sizes (diagonal).</p>
    <div class="table-wrap"><table class="t mx">
      <thead><tr><th scope="col"></th>{#each mx.ids as id}<th scope="col" class="rot"><span><bdi dir="ltr">@{chanOf[id] || id}</bdi></span></th>{/each}</tr></thead>
      <tbody>{#each mx.ids as r}
        <tr><th scope="col" class="rowh"><bdi dir="ltr">@{chanOf[r] || r}</bdi></th>
          {#each mx.ids as c}
            {#if r === c}<td class="diag num" title="Distinct commenting accounts in this section">{commenters[r] ?? ''}</td>
            {:else}{@const v = mx.cell[r + ':' + c] || 0}
              <td class="num cellv" style:--a={v ? 0.12 + 0.75 * (v / mx.max) : 0} title="{v} shared between @{chanOf[r]} and @{chanOf[c]}">{v || ''}</td>{/if}
          {/each}</tr>{/each}</tbody>
    </table></div>
    {#if mx.more > 0}<p class="faint small">Top 20 sections by sharing shown; {mx.more} more not shown.</p>{/if}
  {/if}
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  .grow { flex: 1; }
  .stats { max-width: 760px; }
  .mx td, .mx th { text-align: center; padding: 4px 6px; font-size: 12px; }
  .mx th.rowh { text-align: right; position: static; white-space: nowrap; font-weight: 600; }
  .mx th.rot { height: 110px; vertical-align: bottom; padding: 4px 2px; }
  .mx th.rot span { writing-mode: vertical-rl; transform: rotate(180deg); white-space: nowrap; }
  .cellv { background: color-mix(in srgb, var(--accent) calc(var(--a) * 100%), transparent); min-width: 28px; }
  .diag { color: var(--faint); background: var(--panel-2); }
</style>
