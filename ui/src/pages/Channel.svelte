<script>
  import TriageDetailDrawer from '../components/TriageDetailDrawer.svelte';
  // One channel, everything in one place: posts, comments, links, collection settings, changes, notes.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, hrefIn, go, setQuery } from '../lib/router.svelte.js';
  import { app, attempt, toast, currentCase, loadCases, sinceForCase } from '../lib/store.svelte.js';
  import { addNote } from '../lib/triage.js';
  import { num, compact, ago, day, dt, plural, isRtl } from '../lib/fmt.js';
  import Empty from '../components/Empty.svelte';
  import Loading from '../components/Loading.svelte';
  import PostCard from '../components/PostCard.svelte';
  import ChannelLink from '../components/ChannelLink.svelte';
  import SrcFresh, { ACTIVE, KIND_LABEL, STATUS_CLASS, freshness, intervalLabel, jobChannels, jobDesc, jobSummary } from '../components/SrcFresh.svelte';
  import SrcStepChooser, { defaultSteps, stepsToBody, describeSteps, usesLogin } from '../components/SrcStepChooser.svelte';
  import SrcScheduleEditor from '../components/SrcScheduleEditor.svelte';
  import SrcJobMonitor from '../components/SrcJobMonitor.svelte';
  import SrcConfirm from '../components/SrcConfirm.svelte';

  const name = $derived(route.args[0] || '');
  const cid = $derived(caseId());
  const cur = $derived(currentCase());
  const tab = $derived(route.query.tab || 'posts');

  let c = $state(null);
  let notFound = $state(false);
  let error = $state('');
  let loading = $state(true);
  let memberOf = $state([]);       // /api/channels/{name}/cases
  let events = $state([]);
  let caseRow = $state(null);      // this channel's row in the current case (or null)
  let watch = $state(null);
  let jobs = $state([]);
  let deletedCount = $state(null);
  let allCases = $derived(app.cases);

  async function load() {
    const n = name, id = caseId();
    if (!n) return;
    try {
      const [ch, cs, ev, ws, js, cr, del] = await Promise.all([
        get(`/api/channels/${encodeURIComponent(n)}`),
        get(`/api/channels/${encodeURIComponent(n)}/cases`),
        get(`/api/channels/${encodeURIComponent(n)}/events`),
        get('/api/watches'),
        get('/api/jobs'),
        id != null ? get(`/api/cases/${id}`) : Promise.resolve(null),
        id == null ? get('/api/search', { channels: n, deleted: 1, limit: 1 }) : Promise.resolve(null),
      ]);
      if (n !== name) return;
      c = ch; memberOf = cs; events = ev;
      watch = ws.find((w) => w.channel === n) || null;
      jobs = js.filter((j) => jobChannels(j).includes(n));
      caseRow = cr ? cr.channels_detail.find((x) => x.channel === n) || null : null;
      deletedCount = cr ? (caseRow?.deleted_posts ?? 0) : del?.total ?? 0;
      notFound = false; error = '';
    } catch (e) {
      if (e.status === 404) notFound = true; else error = e.message;
    } finally { loading = false; }
  }
  $effect(() => {
    name; caseId(); app.jobsTick;
    untrack(load);
  });
  // keep job state live while something for this channel runs
  $effect(() => {
    const t = setInterval(() => { if (!document.hidden && jobs.some((j) => ACTIVE.includes(j.status))) load(); }, 4000);
    return () => clearInterval(t);
  });

  const inThisCase = $derived(cid == null || !!caseRow);
  const activeJobs = $derived(jobs.filter((j) => ACTIVE.includes(j.status)));
  const fresh = $derived(c ? freshness(c.last_crawled, watch) : null);
  const sched = $derived(c?.schedule || null);
  const otherCases = $derived(allCases.filter((k) => !memberOf.some((m) => m.id === k.id)));
  const isRTL = $derived(isRtl(c?.title));

  function setTab(t) { setQuery({ tab: t === 'posts' ? '' : t }); }

  // ---- header actions ------------------------------------------------------------------------
  let busy = $state(false);
  async function addToCase(id, role = 'watched') {
    const r = await attempt(() => post(`/api/cases/${id}/channels`, { add: [name], role }), `Added @${name} to the case`);
    if (r) { loadCases(); load(); }
  }
  let addPick = $state('');
  async function setRole(role) {
    const r = await attempt(() => post(`/api/cases/${cid}/channels`, { update: { channel: name, role } }), `Role set to ${role}`);
    if (r) { loadCases(); load(); }
  }
  async function setStatus(status, el) {
    if (status === 'dismissed' && !confirm(`Mark @${name} as Dismissed for ALL cases?\n\nDismissed channels are hidden from Discovery, the network and the channel list in every case, not just this one. Nothing is deleted, and you can change the status back later.`)) { if (el) el.value = c.status; return; }
    const r = await attempt(() => post(`/api/channels/${encodeURIComponent(name)}/status`, { status }), `Status set to ${status}`);
    if (r) load();
  }
  let removeAsk = $state(false);
  async function removeFromCase() {
    busy = true;
    const r = await attempt(() => post(`/api/cases/${cid}/channels`, { remove: [name] }), `Removed @${name} from this case`);
    busy = false;
    if (r) { removeAsk = false; loadCases(); load(); }
  }
  let descOpen = $state(false);

  // ---- posts tab --------------------------------------------------------------------------------
  let posts = $state([]);
  let postTotal = $state(0);
  let postsLoading = $state(false);
  let postsErr = $state('');
  let pFilter = $state('all');
  let pSort = $state('newest');
  const since = $derived(sinceForCase());

  async function loadPosts(reset) {
    if (!inThisCase && cid != null) { posts = []; return; }
    postsLoading = true; postsErr = '';
    const my = `${name}|${pFilter}|${pSort}`;
    try {
      const r = await get('/api/search', {
        channels: name, limit: 20, offset: reset ? 0 : posts.length, sort: pSort,
        ...(cid != null ? { case: cid } : {}),
        ...(pFilter === 'deleted' ? { deleted: 1 } : {}), ...(pFilter === 'edited' ? { edited: 1 } : {}),
        ...(pFilter === 'unreviewed' ? { review: 'unreviewed' } : {}),
      });
      if (my !== `${name}|${pFilter}|${pSort}`) return;
      posts = reset ? r.results : [...posts, ...r.results];
      postTotal = r.total;
    } catch (e) { postsErr = e.message; } finally { postsLoading = false; }
  }
  $effect(() => {
    if (tab !== 'posts' || !name) return;
    pFilter; pSort; cid; app.jobsTick; inThisCase;
    untrack(() => loadPosts(true));
  });

  // ---- collection tab -----------------------------------------------------------------------------
  let steps = $state(defaultSteps());
  let collectAsk = $state(false);
  const stepBody = $derived(stepsToBody(steps));
  const stepLines = $derived(describeSteps(steps).map((s) => ({ title: s.title, detail: s.detail, login: s.login })));
  const crawlBusy = $derived(activeJobs.some((j) => j.kind === 'crawl'));
  const commentBusy = $derived(activeJobs.some((j) => j.kind === 'comments'));
  async function doCollect() {
    busy = true;
    const r = await attempt(() => post(`/api/channels/${encodeURIComponent(name)}/collect`, stepBody));
    busy = false;
    if (!r) return;
    collectAsk = false;
    const ids = Object.values(r.jobs || {});
    toast(ids.length ? `Queued ${plural(ids.length, 'job')} (#${ids.join(', #')}). Progress shows below.` : 'Saved.', 'ok');
    selectedJob = ids[0] ?? null;
    load();
  }
  let selectedJob = $state(null);
  const shownJob = $derived(selectedJob ?? (activeJobs.find((j) => j.status !== 'queued') || activeJobs[0])?.id ?? null);
  const chBad = $derived(c && c.preview === 0);

  // ---- notes -------------------------------------------------------------------------------------
  let notes = $state([]);
  let noteText = $state('');
  async function loadNotes() {
    if (cid != null) {
      try { notes = await get(`/api/cases/${cid}/notes`, { channel: name }); } catch (e) { toast(e.message, 'error'); }
    } else notes = (c?.notes || []).filter((n) => !n.case_id);
  }
  $effect(() => { if (tab === 'notes' && c) { cid; untrack(loadNotes); } });
  async function saveNote() {
    const text = noteText.trim();
    if (!text) return;
    const r = await attempt(() => addNote('channel', null, text, name), 'Note saved');
    if (r) { noteText = ''; await load(); loadNotes(); }
  }

  // ---- changes -------------------------------------------------------------------------------------
  const EVENT = {
    renamed: { label: 'Renamed', cls: 'warn', text: (e) => `@${e.old} → @${e.new}` },
    title_changed: { label: 'Title changed', cls: 'info', text: (e) => `“${e.old}” → “${e.new}”` },
    description_changed: { label: 'Description changed', cls: 'info', text: (e) => `${trunc(e.old)} → ${trunc(e.new)}` },
    preview_disabled: { label: 'Public preview disabled', cls: 'bad', text: () => 'Posts can no longer be read from the public page.' },
    preview_restored: { label: 'Public preview restored', cls: 'ok', text: () => 'Posts can be read again.' },
    unavailable: { label: 'Unavailable', cls: 'bad', text: () => 'Not found or private when last checked.' },
    subscribers_jump: { label: 'Subscribers jump', cls: 'warn', text: (e) => `${num(Number(e.old))} → ${num(Number(e.new))} (${Number(e.new) >= Number(e.old) ? '+' : ''}${Math.round(100 * (Number(e.new) - Number(e.old)) / Number(e.old))}%)` },
  };
  const trunc = (s) => { s = s || ''; return s.length > 140 ? s.slice(0, 140) + '…' : s; };

  const maxDay = $derived(c ? Math.max(1, ...c.per_day.map((d) => d.n)) : 1);
  const tabs = $derived([
    ['posts', 'Posts', c ? num(c.posts) : ''],
    ['comments', 'Comments', c?.comment_group ? num(c.comment_group.comments) : ''],
    ['links', 'Links', c ? num((c.sources?.length || 0) + (c.targets?.length || 0)) : ''],
    ['collection', 'Collection', activeJobs.length ? '●' : ''],
    ['changes', 'Changes', events.length ? String(events.length) : ''],
    ['notes', 'Notes', c?.notes?.length ? String(c.notes.length) : ''],
  ]);
  let postDetail = $state(null);   // {type:'post', id, seed} while the details drawer is open
</script>

<div class="page">
  {#if loading}
    <Loading />
  {:else if notFound}
    <div class="panel"><Empty title="@{name} is not in your archive">
      It has not been collected or linked from anything you have collected yet.
      {#snippet action()}<a class="btn primary" href={href('add', { handles: name })}>Preview and add @{name}</a> <a class="btn" href={href('channels')}>All channels</a>{/snippet}
    </Empty></div>
  {:else if error || !c}
    <div class="callout bad" role="alert">{error || 'Could not load this channel.'} <button class="sm" onclick={load}>Retry</button></div>
  {:else}
    <!-- header -->
    <div class="head panel panel-pad">
      <div class="row top">
        <div class="grow">
          <div class="crumbs tiny faint"><a href={href('channels')}>← Channels</a></div>
          <h1><bdi dir="auto">{c.title || '@' + c.username}</bdi></h1>
          <div class="row small">
            <span class="mono"><bdi dir="ltr">@{c.username}</bdi></span>
            <span class="pill">{c.type || 'unchecked'}</span>
            {#if c.subscribers}<span class="muted">{num(c.subscribers)} subscribers</span>{/if}
            {#if c.tg_id}<span class="muted" title="Permanent Telegram id. It survives renames.">id {c.tg_id}</span>{/if}
            {#if chBad}<span class="pill warn" title="Telegram shows no public preview. Posts cannot be crawled without login.">no public preview</span>{/if}
            <a href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer noopener" title="Opens Telegram's public page (leaves the app)">t.me ↗</a>
          </div>
        </div>
        <div class="col right-col">
          {#if cid != null && caseRow}
            <label class="row tight small">Role in {cur?.name ? `“${cur.name}”` : 'this case'}
              <select value={caseRow.role} onchange={(e) => setRole(e.currentTarget.value)}>
                <option value="seed">Seed</option><option value="watched">Watched</option><option value="context">Context</option>
              </select>
            </label>
          {/if}
          <label class="row tight small">Status (all cases)
            <select value={c.status} onchange={(e) => setStatus(e.currentTarget.value, e.currentTarget)} title="Global label: seed, watched, candidate or dismissed">
              <option value="seed">Seed</option><option value="watched">Watched</option><option value="candidate">Candidate</option><option value="dismissed">Dismissed (all cases)</option>
            </select>
          </label>
        </div>
      </div>

      {#if c.description}
        <div class="desc small" class:clamp={!descOpen} dir="auto">{c.description}</div>
        {#if c.description.length > 220}<button class="ghost sm" onclick={() => (descOpen = !descOpen)}>{descOpen ? 'Show less' : 'Show all'}</button>{/if}
      {/if}

      <div class="row cases">
        <span class="small muted">In cases:</span>
        {#each memberOf as k (k.id)}
          <a class="pill" class:accent={k.id === cid} href={hrefIn(k.id, 'channel', c.username)} title="{k.role}">
            <span class="dot" style:background={k.color || 'var(--accent)'}></span>{k.name} · {k.role}
          </a>
        {:else}<span class="small faint">none yet</span>{/each}
        {#if cid != null && !caseRow}
          <button class="primary sm" onclick={() => addToCase(cid)}>Add to {cur?.name ? `“${cur.name}”` : 'this case'}</button>
        {:else if otherCases.length}
          <select bind:value={addPick} onchange={() => { if (addPick) { addToCase(Number(addPick)); addPick = ''; } }} aria-label="Add to a case" class="inl">
            <option value="">＋ Add to case…</option>
            {#each otherCases as k (k.id)}<option value={String(k.id)}>{k.name}</option>{/each}
          </select>
        {/if}
        {#if cid != null && caseRow}<button class="ghost sm danger" onclick={() => (removeAsk = true)}>Remove from this case</button>{/if}
      </div>
    </div>

    {#if cid != null && !caseRow}
      <div class="callout warn" style="margin-top:10px">This channel is not in "{cur?.name || 'this case'}", so case triage does not apply to its posts. Add it to the case, or <a href={hrefIn('all', 'channel', c.username)}>view it in the whole archive</a>.</div>
    {/if}

    <!-- key numbers -->
    <div class="stats" style="margin:12px 0">
      <div class="panel stat"><div class="v">{num(c.posts)}</div><div class="l">posts collected{#if caseRow?.new_posts} · <span style="color:var(--accent)">+{num(caseRow.new_posts)} new</span>{/if}</div></div>
      <div class="panel stat"><div class="v" style:color={deletedCount ? 'var(--bad)' : ''}>{deletedCount === null ? '–' : num(deletedCount)}</div><div class="l">found deleted on Telegram</div></div>
      <div class="panel stat"><div class="v" style="font-size:16px">{c.last_post ? day(c.last_post) : '–'}</div><div class="l">latest post{c.last_post ? ` · ${ago(c.last_post)}` : ''}</div></div>
      <div class="panel stat"><div class="v" style="font-size:16px">{#if fresh}<span class="pill {fresh.cls}">{fresh.label}{fresh.stale && !fresh.never ? ' · stale' : ''}</span>{/if}</div><div class="l">last crawled</div></div>
      <div class="panel stat"><div class="v" style="font-size:16px">{watch?.enabled ? intervalLabel(watch.interval_min) : watch ? 'paused' : 'no'}</div><div class="l">watch (auto re-crawl)</div></div>
      <div class="panel stat"><div class="v">{c.comment_group ? num(c.comment_group.comments) : '–'}</div><div class="l">comments collected</div></div>
    </div>

    {#if activeJobs.length}
      <div class="callout" style="margin-bottom:10px"><span class="spinner"></span>
        {#each activeJobs as j}<a href={href('channel', c.username, { tab: 'collection' })} onclick={() => (selectedJob = j.id)}>#{j.id} {(KIND_LABEL[j.kind] || j.kind).toLowerCase()} {j.status}</a>{' '}{/each}
      </div>
    {/if}

    <div class="tabs" role="group" aria-label="Channel sections">
      {#each tabs as [k, label, n]}
        <button aria-pressed={tab === k} class:on={tab === k} onclick={() => setTab(k)}>{label}{#if n} <span class="badge muted">{n}</span>{/if}</button>
      {/each}
    </div>

    <!-- POSTS -->
    {#if tab === 'posts'}
      <div class="row" style="margin-bottom:10px">
        <div class="seg" role="group" aria-label="Filter posts">
          {#each [['all', 'All'], ['unreviewed', 'Unreviewed'], ['deleted', 'Deleted'], ['edited', 'Edited']] as [k, l]}
            <button class:on={pFilter === k} aria-pressed={pFilter === k} onclick={() => (pFilter = k)}>{l}</button>
          {/each}
        </div>
        <select bind:value={pSort} aria-label="Sort"><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="views">Most views</option></select>
        <span class="spacer"></span>
        <span class="muted small">{num(postTotal)} match</span>
        <a class="btn sm" href={href('search', { channels: c.username })}>Open in Search →</a>
      </div>
      {#if c.per_day.length > 1}
        <div class="spark" title="Posts per day collected for this channel" aria-hidden="true">
          {#each c.per_day.slice(-120) as d}<i style:height="{Math.max(6, (100 * d.n) / maxDay)}%" title="{d.d}: {d.n}"></i>{/each}
        </div>
      {/if}
      {#if cid != null && !caseRow}
        <div class="panel"><Empty title="Not in this case">Add the channel to the case to review its posts here.</Empty></div>
      {:else if postsErr}
        <div class="callout bad" role="alert">{postsErr}</div>
      {:else if postsLoading && !posts.length}
        <Loading />
      {:else if !posts.length}
        <div class="panel"><Empty title={c.posts ? 'No posts match this filter' : 'No posts collected yet'}>
          {#if !c.posts}{chBad ? 'This channel has no public preview, so posts cannot be crawled without login.' : 'Run a crawl from the Collection tab to fetch its posts.'}{/if}
          {#snippet action()}{#if !c.posts && !chBad}<button class="primary" onclick={() => setTab('collection')}>Go to Collection</button>{/if}{/snippet}
        </Empty></div>
      {:else}
        <div class="col">
          {#each posts as p (p.id)}
            <PostCard post={p} showChannel={false} onopen={(x) => (postDetail = { type: 'post', id: x.id, seed: x })} isNew={!!since && p.first_captured > since} />
          {/each}
        </div>
        {#if posts.length < postTotal}
          <div class="row" style="justify-content:center;margin-top:12px"><button onclick={() => loadPosts(false)} disabled={postsLoading}>{#if postsLoading}<span class="spinner"></span>{/if}Load more ({num(postTotal - posts.length)} left)</button></div>
        {/if}
      {/if}

    <!-- COMMENTS -->
    {:else if tab === 'comments'}
      {#if c.comment_group}
        {@const g = c.comment_group}
        <div class="panel panel-pad" style="margin-bottom:12px">
          <div class="row"><h2>Comment section</h2>
            <span class="muted small" dir="auto">{g.title || ''}{g.username ? ` (@${g.username})` : ''}</span><span class="spacer"></span>
            <a class="btn sm" href={href('comments', { channels: c.username })}>Read comments →</a></div>
          <div class="stats" style="margin-top:10px">
            <div class="stat"><div class="v">{num(g.comments)}</div><div class="l">comments</div></div>
            <div class="stat"><div class="v">{num(g.commenters)}</div><div class="l">distinct commenters</div></div>
            <div class="stat"><div class="v" style:color={g.deleted ? 'var(--bad)' : ''}>{num(g.deleted)}</div><div class="l">found deleted</div></div>
            <div class="stat"><div class="v" style="font-size:16px">{g.first_date ? day(g.first_date) : '–'} → {g.last_date ? day(g.last_date) : '–'}</div><div class="l">date range</div></div>
            <div class="stat"><div class="v" style="font-size:16px">{g.participants != null ? num(g.participants) : '–'}</div><div class="l">members (reported){g.hidden_members ? ' · list hidden' : ''}</div></div>
          </div>
          <p class="small muted" style="margin:8px 0 0">Last checked {ago(g.last_checked)}. {#if sched}Schedule: {sched.enabled ? intervalLabel(sched.interval_min) : 'paused'}.{:else}No schedule.{/if}
            <button class="ghost sm" onclick={() => setTab('collection')}>Collect or schedule comments</button></p>
        </div>
        <div class="section-title">Admins of the comment section ({(c.admins || []).length})</div>
        {#if !(c.admins || []).length}
          <div class="panel"><Empty title="No admin list collected">Telegram may hide it, or it was not fetched.</Empty></div>
        {:else}
          <div class="table-wrap"><table class="t">
            <thead><tr><th scope="col">Account</th><th scope="col">Role</th><th scope="col">Since</th><th scope="col" class="right">Comments anywhere</th><th scope="col" class="right">Sections</th><th scope="col"></th></tr></thead>
            <tbody>
              {#each c.admins as a}
                <tr class:faint={a.removed_detected}>
                  <td dir="auto"><b>{[a.first_name, a.last_name].filter(Boolean).join(' ') || a.user_id}</b>{#if a.username} <span class="faint"><bdi dir="ltr">@{a.username}</bdi></span>{/if}
                    {#if a.bot}<span class="pill">bot</span>{/if}{#if a.deleted}<span class="pill bad">deleted account</span>{/if}</td>
                  <td class="small">{a.role || 'admin'}</td>
                  <td class="small">{a.since ? day(a.since) : '–'}</td>
                  <td class="right num">{num(a.comments_anywhere)}</td>
                  <td class="right num">{num(a.groups_anywhere)}</td>
                  <td class="small">{#if a.removed_detected}<span class="pill bad" title={dt(a.removed_detected)}>removed {ago(a.removed_detected)}</span>{/if}</td>
                </tr>
              {/each}
            </tbody></table></div>
        {/if}
      {:else}
        <div class="panel"><Empty title="Comments not collected">
          Comment sections (the linked discussion group) are read with your logged-in Telegram account, so they are only collected when you ask. Not every channel has one.
          {#snippet action()}<button class="primary" onclick={() => setTab('collection')}>Collect comments…</button>{/snippet}
        </Empty></div>
      {/if}

    <!-- LINKS -->
    {:else if tab === 'links'}
      <div class="grid two">
        <div>
          <div class="section-title" style="margin-top:0">Pointed at by ({c.sources.length}): who forwards, mentions or links to it</div>
          {#if !c.sources.length}<div class="panel"><Empty title="No incoming links seen"></Empty></div>
          {:else}
            <div class="table-wrap"><table class="t">
              <thead><tr><th scope="col">Channel</th><th scope="col">Type</th><th scope="col" class="right">Signals</th><th scope="col">Last seen</th></tr></thead>
              <tbody>{#each c.sources as s (s.username + s.type)}
                <tr><td><ChannelLink name={s.username} title={s.title} /></td><td class="small"><span class="pill">{s.type}</span></td>
                  <td class="right num">{num(s.weight)}</td><td class="small">{s.last_seen ? day(s.last_seen) : '–'}</td></tr>
              {/each}</tbody></table></div>
          {/if}
        </div>
        <div>
          <div class="section-title" style="margin-top:0">Points to ({c.targets.length}): what this channel forwards, mentions or links to</div>
          {#if !c.targets.length}<div class="panel"><Empty title="No outgoing links seen"></Empty></div>
          {:else}
            <div class="table-wrap"><table class="t">
              <thead><tr><th scope="col">Channel</th><th scope="col">Type</th><th scope="col" class="right">Signals</th><th scope="col">Status</th></tr></thead>
              <tbody>{#each c.targets as s (s.username + s.type)}
                <tr><td><ChannelLink name={s.username} title={s.title} />{#if s.ctype && s.ctype !== 'channel'}<span class="pill" style="margin-left:4px">{s.ctype}</span>{/if}</td>
                  <td class="small"><span class="pill">{s.type}</span></td><td class="right num">{num(s.weight)}</td>
                  <td class="small">{s.status || 'unknown'}</td></tr>
              {/each}</tbody></table></div>
          {/if}
        </div>
      </div>
      <p class="help" style="margin-top:8px">Signals count each forward, @mention or t.me link found in collected posts. They show who points at whom, not who agrees with whom. See the Network page for the graph.</p>

    <!-- COLLECTION -->
    {:else if tab === 'collection'}
      {#if shownJob}
        <div style="margin-bottom:14px"><SrcJobMonitor id={shownJob} onchange={load} /></div>
      {/if}
      <div class="grid two">
        <div class="panel">
          <div class="panel-head"><h2>Collect now</h2></div>
          <div class="panel-body">
            {#if crawlBusy}<div class="callout warn" style="margin-bottom:8px">A crawl of this channel is already queued or running. Choosing posts again reuses it instead of starting a second one.</div>{/if}
            {#if commentBusy}<div class="callout warn" style="margin-bottom:8px">Comments for this channel are already being collected.</div>{/if}
            <SrcStepChooser bind:steps only={['posts', 'comments', 'similar']} canPosts={!chBad} reason="This channel has no public preview, so posts cannot be crawled without logging in." idp="ch" />
            <div class="row" style="margin-top:10px">
              <button class="primary" disabled={!Object.keys(stepBody).length} onclick={() => (collectAsk = true)}>Collect…</button>
              <span class="help">You confirm exactly what runs before anything starts.</span>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="panel">
            <div class="panel-head"><h2>Watch: automatic post re-crawl</h2><span class="pill">public web, no login</span></div>
            <div class="panel-body"><SrcScheduleEditor kind="watch" channel={c.username} row={watch} onchange={load} /></div>
          </div>
          <div class="panel">
            <div class="panel-head"><h2>Comment schedule</h2><span class="pill warn">uses your Telegram account</span></div>
            <div class="panel-body"><SrcScheduleEditor kind="schedule" channel={c.username} row={sched} onchange={load} /></div>
          </div>
        </div>
      </div>

      <div class="section-title">Collection history for this channel</div>
      {#if !jobs.length}
        <div class="panel"><Empty title="Nothing collected by a job yet">Posts imported earlier may have come from a broader crawl or an imported file.</Empty></div>
      {:else}
        <div class="table-wrap"><table class="t">
          <thead><tr><th scope="col">#</th><th scope="col">Job</th><th scope="col">Result</th><th scope="col">When</th><th scope="col">Summary</th></tr></thead>
          <tbody>{#each jobs as j (j.id)}
            <tr class="click" class:sel={shownJob === j.id} onclick={() => (selectedJob = j.id)}>
              <td>{j.id}</td><td><button class="rowbtn" onclick={(e) => { e.stopPropagation(); selectedJob = j.id; }} title="Show this job's progress and log"><b>{KIND_LABEL[j.kind] || j.kind}</b></button><div class="small muted">{jobDesc(j)}</div></td>
              <td><span class="pill {STATUS_CLASS[j.status] || ''}">{j.status}</span></td>
              <td class="small nowrap" title={dt(j.started || j.created)}>{ago(j.started || j.created)}</td>
              <td class="small">{jobSummary(j)}</td></tr>
          {/each}</tbody></table></div>
        <p class="help" style="margin-top:6px">Click a row for its progress record and log. <a href={href('collect')}>All collection activity</a></p>
      {/if}

    <!-- CHANGES -->
    {:else if tab === 'changes'}
      <div class="row" style="margin-bottom:10px">
        <div class="small muted">Changes detected between crawls. Detection needs repeated crawls, so a watch gives the most complete record.</div>
        <span class="spacer"></span>
        <a class="btn sm" href={href('search', { channels: c.username, deleted: 1 })}>Deleted posts ({deletedCount === null ? '–' : num(deletedCount)}) →</a>
      </div>
      {#if !events.length}
        <div class="panel"><Empty title="No channel changes detected">Renames, title or description edits, preview changes and large subscriber jumps will be listed here.</Empty></div>
      {:else}
        <div class="table-wrap"><table class="t">
          <thead><tr><th scope="col">Detected</th><th scope="col">Change</th><th scope="col">Detail</th></tr></thead>
          <tbody>{#each events as e (e.id)}
            {@const m = EVENT[e.kind] || { label: e.kind, cls: '', text: (x) => `${x.old} → ${x.new}` }}
            <tr><td class="small nowrap" title={dt(e.detected_at)}>{day(e.detected_at)}<div class="tiny faint">{ago(e.detected_at)}</div></td>
              <td><span class="pill {m.cls}">{m.label}</span></td><td class="small text-body" dir="auto">{m.text(e)}</td></tr>
          {/each}</tbody></table></div>
      {/if}
      {#if c.history?.length}
        <div class="section-title">Activity log for this channel</div>
        <div class="table-wrap"><table class="t">
          <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th></tr></thead>
          <tbody>{#each c.history as h}<tr><td class="small nowrap">{dt(h.ts)}</td><td class="small">{h.actor}</td><td class="small">{h.action.replaceAll('_', ' ')}</td></tr>{/each}</tbody></table></div>
      {/if}

    <!-- NOTES -->
    {:else if tab === 'notes'}
      <div class="panel panel-pad" style="margin-bottom:12px">
        <label class="field">Add a note {cid != null ? `(saved in “${cur?.name || 'this case'}”)` : '(global, not tied to a case)'}
          <textarea bind:value={noteText} rows="3" dir="auto" aria-label="New note about this channel" placeholder="What did you find out about this channel? Who runs it, why it matters, what to verify…"></textarea>
        </label>
        <div class="row" style="margin-top:8px"><button class="primary" onclick={saveNote} disabled={!noteText.trim()}>Save note</button></div>
      </div>
      {#if !notes.length}
        <div class="panel"><Empty title="No notes yet"></Empty></div>
      {:else}
        <div class="col">
          {#each [...notes].reverse() as n (n.id)}
            <div class="panel panel-pad"><div class="text-body" dir="auto">{n.text}</div>
              <div class="tiny faint" style="margin-top:4px">{n.author || 'investigator'} · {dt(n.created_at)}</div></div>
          {/each}
        </div>
      {/if}
    {/if}
  {/if}
</div>

<SrcConfirm open={collectAsk} title="Collect @{name} now?" lines={stepLines} {busy}
            confirmLabel={usesLogin(steps) ? 'Confirm and use my account' : 'Confirm and start'}
            onclose={() => (collectAsk = false)} onconfirm={doCollect}
            intro="Exactly this will be queued:" />

<SrcConfirm open={removeAsk} title="Remove from this case?" danger confirmLabel="Remove from case" {busy}
            onclose={() => (removeAsk = false)} onconfirm={removeFromCase}
            intro={`@${name} will no longer be part of “${cur?.name || 'this case'}”. Its posts and comments stay in the archive, and any watch keeps running. Case triage and notes are kept and come back if you add it again.`} />


<TriageDetailDrawer bind:item={postDetail} />

<style>
  .head h1 { margin: 2px 0 4px; }
  .top { align-items: flex-start; flex-wrap: nowrap; }
  .grow { flex: 1; min-width: 0; }
  .right-col { align-items: flex-end; gap: 6px; }
  .desc { margin-top: 8px; color: var(--muted); max-width: 90ch; white-space: pre-wrap; }
  .desc.clamp { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .cases { margin-top: 10px; }
  .cases .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  select.inl { padding: 2px 4px; font-size: 12.5px; }
  .spark { display: flex; align-items: flex-end; gap: 1px; height: 36px; margin-bottom: 10px; padding: 2px 4px; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel); }
  .spark i { flex: 1; min-width: 1px; background: var(--accent); opacity: .7; border-radius: 1px; }
</style>
