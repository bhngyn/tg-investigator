<script>
  // Case dashboard (or whole-archive dashboard in "all" scope).
  import { untrack } from 'svelte';
  import { app, loadCases, toast, sinceForCase, currentCase } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, hrefIn } from '../lib/router.svelte.js';
  import { num, bytes, ago, dt, day, plural, pj } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import EvCaseFields from '../components/EvCaseFields.svelte';
  import EvBarChart from '../components/EvBarChart.svelte';
  import EvAlertItem from '../components/EvAlertItem.svelte';
  import SavedSearches from '../components/SavedSearches.svelte';

  let data = $state(null);
  let loading = $state(true);
  let error = $state('');
  let since = $state(null);     // { at, posts, comments, changes } for "since your last visit"
  let edit = $state(null);      // { name, description, color, error, busy } while editing

  const cid = $derived(caseId());
  const prev = $derived(cid != null ? app.since[cid] : null);
  const cur = $derived(currentCase());

  async function load() {
    const id = caseId();
    error = '';
    try {
      data = { ...(id != null ? await get(`/api/cases/${id}`) : await get('/api/overview')), __scope: id };
    } catch (e) { error = e.message; data = null; }
    loading = false;
  }
  $effect(() => {
    route.scope; app.jobsTick;
    untrack(() => { loading = data == null || data.__scope !== caseId(); load(); });
  });

  // "Since your last visit": counted from the previous visit's timestamp (the case row's own
  // counters restart when the visit is stamped, so we ask search/comments/changes directly).
  $effect(() => {
    const id = cid, at = prev;
    since = null;
    if (id == null || !at) return;
    app.jobsTick;
    Promise.all([
      get('/api/search', { case: id, since: at, limit: 1 }).catch(() => null),
      get('/api/comments', { case: id, since: at, limit: 1 }).catch(() => null),
      get('/api/changes', { case: id, since: at, limit: 1 }).catch(() => null),
    ]).then(([p, c, ch]) => {
      const cc = ch?.counts || {};
      since = { at, posts: p?.total ?? 0, comments: c?.total ?? 0, changes: Object.values(cc).reduce((a, b) => a + (b || 0), 0), counts: cc };
    });
  });

  function startEdit() { edit = { name: data.name, description: data.description || '', color: data.color || '#2458d6', error: '', busy: false }; }
  async function saveEdit() {
    const name = edit.name.trim();
    if (!name) { edit.error = 'A case needs a name.'; return; }
    edit.busy = true; edit.error = '';
    try {
      await post('/api/cases', { id: cid, name, description: edit.description.trim() || null, color: edit.color });
      await Promise.all([loadCases(), load()]);
      edit = null;
      toast('Case updated', 'ok');
    } catch (e) { edit.error = e.status === 409 || e.status === 500 ? 'Could not save. Another case probably already has that name.' : e.message; edit.busy = false; }
  }

  // ---- channel freshness ----
  function fresh(ch) {
    if (!ch.last_crawled) return { k: 'bad', t: 'never collected' };
    const hrs = (Date.now() - Date.parse(ch.last_crawled)) / 36e5;
    const lim = ch.watch && ch.watch.enabled ? Math.max(6, (ch.watch.interval_min || 360) / 60 * 3) : 24 * 7;
    return hrs > lim ? { k: 'warn', t: `stale (${ago(ch.last_crawled)})` } : { k: 'ok', t: ago(ch.last_crawled) };
  }
  const channels = $derived.by(() => {
    const rows = (data?.channels_detail || []).map((c) => ({ ...c, f: fresh(c) }));
    const order = { bad: 0, warn: 1, ok: 2 };
    return rows.sort((a, b) => order[a.f.k] - order[b.f.k] || (b.new_posts || 0) - (a.new_posts || 0));
  });
  const staleN = $derived(channels.filter((c) => c.f.k !== 'ok').length);
  const watchedN = $derived(channels.filter((c) => c.watch && c.watch.enabled).length);

  const ACTION_LABEL = {
    case_create: 'Case created', case_edit: 'Case edited', case_channels_add: 'Channels added', case_channels_remove: 'Channels removed',
    case_review: 'Reviewed', case_tag_add: 'Tagged', case_tag_remove: 'Tag removed', case_note: 'Note added',
    incident_create: 'Incident created', incident_edit: 'Incident edited', incident_item_add: 'Item linked to incident',
    evidence_export: 'Evidence package built',
  };
  const actLabel = (a) => ACTION_LABEL[a] || a.replaceAll('_', ' ');
  function detailText(h) {
    const d = pj(h.detail, null);
    if (!d || typeof d !== 'object') return '';
    if (d.channels) return d.channels.map((x) => '@' + x).join(', ');
    if (d.to) return `${d.from ?? ''} → ${d.to}`;
    if (d.name) return d.name;
    if (d.tag) return d.tag;
    return '';
  }
  const openInc = $derived((data?.incidents_list || []).filter((i) => i.status !== 'closed'));
</script>

<div class="page">
  {#if loading}
    <Loading />
  {:else if error}
    <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
  {:else if cid == null && data}
    <!-- ================= whole archive ================= -->
    {@const c = data.counts}
    <div class="page-head">
      <div><h1>Whole archive</h1>
        <p>Everything ever collected, across all cases, with global (not per-case) triage. Pick a case in the top bar to work on one investigation.</p></div>
    </div>
    {#if !c.posts}
      <div class="callout" style="margin-bottom:12px">
        <b>Nothing has been collected yet.</b> Start with <a href={href('add')}>Add channels</a>: paste the public t.me channel names you want to follow (no login needed for posts).
        Nothing is collected automatically; you choose what to collect, and each collection asks first.
      </div>
    {/if}
    <div class="stats">
      <a class="panel stat" href={href('channels')}><div class="v">{num(c.channels)}</div><div class="l">channels ({num(c.watched)} watched, {num(c.candidates)} candidates)</div></a>
      <a class="panel stat" href={href('search')}><div class="v">{num(c.posts)}</div><div class="l">posts{#if c.first_post} · {day(c.first_post)} to {day(c.last_post)}{/if}</div></a>
      <a class="panel stat" href={href('inbox')}><div class="v">{num(c.unreviewed)}</div><div class="l">posts unreviewed (global triage)</div></a>
      <div class="panel stat"><div class="v">{num(c.relevant)}</div><div class="l">posts relevant · {num(c.verify)} to verify (global)</div></div>
      <a class="panel stat" href={href('comments')}><div class="v">{num(c.comments)}</div><div class="l">comments ({num(c.comments_deleted)} deleted) · {num(c.tg_users)} accounts</div></a>
      <div class="panel stat"><div class="v">{num(c.media)}</div><div class="l">media files · {bytes(c.media_bytes)}</div></div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h2>Posts per day</h2><span class="faint small">by post date, UTC</span></div>
      <div class="panel-body">
        {#if data.per_day?.length}<EvBarChart data={data.per_day} label="posts per day" />{:else}<span class="muted">No posts collected yet.</span>{/if}
      </div>
    </div>
    <section class="panel" style="margin-top:14px">
      <div class="panel-head"><h2>Saved searches</h2><a class="small" href={href('searchlog')}>Search history →</a></div>
      <SavedSearches />
    </section>
    <div class="row" style="margin-top:14px">
      <a class="btn" href={href('search')}>Search all posts</a>
      <a class="btn" href={href('channels')}>Browse channels</a>
      <a class="btn" href={href('add')}>Add channels</a>
      <a class="btn" href={href('alerts')}>Alerts</a>
      <a class="btn" href={href('exports')}>Evidence packages</a>
    </div>
  {:else if data}
    <!-- ================= case ================= -->
    <div class="page-head">
      {#if edit}
        <form class="panel panel-body editbox" onsubmit={(e) => { e.preventDefault(); saveEdit(); }}>
          <EvCaseFields bind:name={edit.name} bind:description={edit.description} bind:color={edit.color} error={edit.error} autofocus />
          <div class="row" style="margin-top:10px">
            <button class="primary" type="submit" disabled={edit.busy || !edit.name.trim()}>{edit.busy ? 'Saving…' : 'Save'}</button>
            <button type="button" onclick={() => (edit = null)}>Cancel</button>
          </div>
        </form>
      {:else}
        <div>
          <h1><span class="dot" style:background={data.color || 'var(--accent)'}></span>{data.name}
            <button class="ghost sm" onclick={startEdit} title="Edit name, description and colour">Edit</button></h1>
          {#if data.description}<p>{data.description}</p>{:else}<p class="faint">No description. <button class="ghost sm" onclick={startEdit}>Add one</button></p>{/if}
          <div class="faint tiny">Created {day(data.created)} by {data.created_by} · last collected {ago(data.last_collected)}</div>
        </div>
        <div class="row">
          <a class="btn primary" href={href('inbox')} title="Posts not yet triaged in this case">Review queue{#if data.unreviewed} <span class="badge">{num(data.unreviewed)}</span>{/if}</a>
          <a class="btn" href={href('add')}>＋ Add channels</a>
          <a class="btn" href={href('search')}>Search</a>
        </div>
      {/if}
    </div>

    {#if data.active_jobs?.length}
      <div class="callout" style="margin-bottom:12px">Collecting now: {plural(data.active_jobs.length, 'job')} running for this case. <a href={href('collect')}>Open collection</a></div>
    {/if}

    {#if !data.channels}
      <Empty title="This case has no channels yet">
        A case is a set of channels. Add the channels you are investigating; they are collected once and shared with your other cases.
        {#snippet action()}<a class="btn primary" href={href('add')}>＋ Add channels</a>{/snippet}
      </Empty>
    {:else}
      <div class="stats">
        <a class="panel stat" href={href('channels')}><div class="v">{num(data.channels)}</div><div class="l">channels · {watchedN} on watch</div></a>
        <div class="panel stat"><div class="v">{num(data.posts)}</div><div class="l">posts</div></div>
        <a class="panel stat" href={href('inbox')}><div class="v">{num(data.post_unreviewed ?? data.unreviewed)}</div><div class="l">posts unreviewed</div></a>
        <a class="panel stat" href={href('search', { review: 'relevant' })}><div class="v" style="color:var(--ok)">{num(data.post_relevant ?? data.relevant)}</div><div class="l">posts relevant</div></a>
        <a class="panel stat" href={href('search', { review: 'verify' })}><div class="v" style="color:var(--warn)">{num(data.post_verify ?? data.verify)}</div><div class="l">posts need verification</div></a>
        <a class="panel stat" href={href('comments')}><div class="v">{num(data.comments)}</div><div class="l">comments{#if data.comments} · {num(data.comment_unreviewed ?? 0)} unreviewed{/if}{#if data.comment_relevant || data.comment_verify} · {num(data.comment_relevant || 0)} relevant · {num(data.comment_verify || 0)} to verify{/if}</div></a>
        <a class="panel stat" href={href('alerts')}><div class="v" style:color={data.unread_alerts ? 'var(--bad)' : ''}>{num(data.unread_alerts)}</div><div class="l">unread alerts</div></a>
        <a class="panel stat" href={href('incidents')}><div class="v">{num(data.incidents)}</div><div class="l">incidents</div></a>
      </div>

      <div class="grid two" style="margin-top:14px">
        <section class="panel">
          <div class="panel-head"><h2>Since your last visit</h2>{#if prev}<span class="faint small" title={dt(prev)}>{ago(prev)}</span>{/if}</div>
          <div class="panel-body col">
            {#if !prev}
              <span class="muted">This is your first visit in this session, or the case had not been opened before, so there is no earlier visit to compare with.</span>
            {:else if !since}
              <span class="muted"><span class="spinner"></span> Checking…</span>
            {:else}
              <div class="row"><b class="num">{num(since.posts)}</b> new posts collected
                {#if since.posts}<a class="small" href={href('inbox', { since: since.at })}>Review them →</a>{/if}</div>
              <div class="row"><b class="num">{num(since.comments)}</b> new comments
                {#if since.comments}<a class="small" href={href('comments', { since: since.at })}>Open →</a>{/if}</div>
              <div class="row"><b class="num">{num(since.changes)}</b> changes on Telegram
                {#if since.changes}<span class="faint small">
                  ({[since.counts.post_deleted && `${since.counts.post_deleted} posts deleted`, since.counts.post_edited && `${since.counts.post_edited} edited`, since.counts.comment_deleted && `${since.counts.comment_deleted} comments deleted`, since.counts.channel && `${since.counts.channel} channel changes`].filter(Boolean).join(', ')})</span>
                  <a class="small" href={href('analysis', 'changes', { since: since.at })}>See changes →</a>{/if}</div>
              {#if !since.posts && !since.comments && !since.changes}<span class="muted small">Nothing new since then.</span>{/if}
            {/if}
            {#if data.deleted_posts}<div class="row"><span class="pill bad">{num(data.deleted_posts)} posts found deleted</span>
              <a class="small" href={href('search', { deleted: 1 })}>Show them →</a></div>{/if}
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Latest alerts</h2><a class="small" href={href('alerts')}>All alerts →</a></div>
          {#if data.alerts?.length}
            {#each data.alerts.slice(0, 5) as a (a.id)}<EvAlertItem alert={a} compact />{/each}
          {:else}<div class="panel-body muted small">No alerts yet. <a href={href('alerts')}>Set up alert rules</a> to be told about keywords, deletions and edits.</div>{/if}
        </section>
      </div>

      <section class="panel" style="margin-top:14px">
        <div class="panel-head"><h2>Saved searches</h2><a class="small" href={href('searchlog')}>Search history →</a></div>
        <SavedSearches compact />
      </section>

      <section class="panel" style="margin-top:14px">
        <div class="panel-head"><h2>Channels</h2>
          {#if staleN}<span class="pill warn">{staleN} stale or never collected</span>{/if}
          <a class="small" href={href('channels')}>Manage channels →</a></div>
        <div class="table-wrap" style="border:0;border-radius:0">
          <table class="t">
            <thead><tr><th scope="col">Channel</th><th scope="col" class="right">Posts</th><th scope="col" class="right">New</th><th scope="col" class="right">To review</th><th scope="col">Collected</th><th scope="col">Watch</th></tr></thead>
            <tbody>
              {#each channels.slice(0, 12) as ch (ch.channel)}
                <tr>
                  <td><a href={href('channel', ch.channel)}><b>{ch.title || '@' + ch.channel}</b></a> <span class="faint small"><bdi dir="ltr">@{ch.channel}</bdi></span>
                    {#if ch.active_jobs?.length}<span class="pill info">collecting</span>{/if}</td>
                  <td class="right num">{num(ch.posts)}{#if ch.deleted_posts} <span class="pill bad" title="Found deleted">{ch.deleted_posts}</span>{/if}</td>
                  <td class="right num">{ch.new_posts ? num(ch.new_posts) : '–'}</td>
                  <td class="right num">{num(ch.unreviewed)}</td>
                  <td><span class="pill {ch.f.k}">{ch.f.t}</span></td>
                  <td>{#if ch.watch && ch.watch.enabled}<span class="pill ok" title="Re-crawled every {ch.watch.interval_min} min">on</span>{:else}<span class="faint small">off</span>{/if}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if channels.length > 12}<div class="panel-body small muted">Showing 12 of {channels.length}, stalest first. <a href={href('channels')}>See all</a></div>{/if}
      </section>

      <div class="grid two" style="margin-top:14px">
        <section class="panel">
          <div class="panel-head"><h2>Incidents</h2><a class="small" href={href('incidents')}>All →</a></div>
          {#if data.incidents_list?.length}
            <ul class="plain">
              {#each data.incidents_list.slice(0, 6) as i (i.id)}
                <li><a href={href('incident', i.id)}>{i.title}</a>
                  <span class="pill {i.status === 'verified' ? 'ok' : i.status === 'disputed' ? 'warn' : i.status === 'closed' ? '' : 'accent'}">{i.status}</span>
                  {#if i.date_start}<span class="faint small">{day(i.date_start)}</span>{/if}</li>
              {/each}
            </ul>
          {:else}<div class="panel-body muted small">No incidents. Link posts that document one event (an attack, a killing, a detention) into an incident, then export the evidence. <a href={href('incidents')}>Create one</a></div>{/if}
        </section>
        <section class="panel">
          <div class="panel-head"><h2>Recent activity</h2><span class="faint small">audit log</span></div>
          {#if data.history?.length}
            <ul class="plain">
              {#each data.history.slice(0, 8) as h}
                <li><span>{actLabel(h.action)}</span> <span class="faint small">{detailText(h)}</span>
                  <div class="faint tiny">{h.actor} · {ago(h.ts)}</div></li>
              {/each}
            </ul>
          {:else}<div class="panel-body muted small">Nothing recorded yet.</div>{/if}
        </section>
      </div>
    {/if}
  {/if}
</div>

<style>
  h1 { display: flex; align-items: center; gap: 10px; }
  .dot { width: 14px; height: 14px; border-radius: 50%; display: inline-block; flex: none; }
  a.stat { color: inherit; text-decoration: none; }
  a.stat:hover { border-color: var(--accent); }
  .editbox { width: 100%; max-width: 560px; }
  ul.plain { list-style: none; margin: 0; padding: 0; }
  ul.plain li { padding: 8px 14px; border-bottom: 1px solid var(--line); }
  ul.plain li:last-child { border-bottom: 0; }
</style>
