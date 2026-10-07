<script>
  // Channels in scope: one table that makes collection state obvious (how fresh, watched?, running?).
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, hrefIn, go, setQuery } from '../lib/router.svelte.js';
  import { app, attempt, toast, currentCase, loadCases } from '../lib/store.svelte.js';
  import { num, compact, ago, day, dt, plural } from '../lib/fmt.js';
  import Empty from '../components/Empty.svelte';
  import Loading from '../components/Loading.svelte';
  import SrcFresh, { freshness, ACTIVE, intervalLabel, jobChannels, KIND_LABEL, until } from '../components/SrcFresh.svelte';
  import SrcConfirm from '../components/SrcConfirm.svelte';

  let rows = $state([]);
  let loading = $state(true);
  let error = $state('');
  let selected = $state(new Set());

  const cid = $derived(caseId());
  const inCase = $derived(cid != null);
  const q = $derived((route.query.q || '').trim());
  const fType = $derived(route.query.type || '');
  const fStatus = $derived(route.query.status || '');
  const fRole = $derived(route.query.role || '');
  const fQuick = $derived(route.query.f || '');
  const sortKey = $derived(route.query.sort || (inCase ? 'role' : 'last_crawled'));
  const sortDir = $derived(route.query.dir || 'asc');

  async function load() {
    const id = caseId();
    error = '';
    try {
      const [main, watches, scheds, jobs] = await Promise.all([
        id != null ? get(`/api/cases/${id}`) : get('/api/channels'),
        get('/api/watches'), get('/api/schedules'), get('/api/jobs'),
      ]);
      if (caseId() !== id) return;   // scope changed while loading
      const wm = new Map(watches.map((w) => [w.channel, w]));
      const sm = new Map(scheds.map((s) => [s.channel, s]));
      const running = new Map();
      for (const j of jobs) {
        if (!ACTIVE.includes(j.status)) continue;
        for (const ch of jobChannels(j)) {
          if (!running.has(ch)) running.set(ch, []);
          running.get(ch).push({ id: j.id, kind: j.kind, status: j.status });
        }
      }
      const src = id != null ? main.channels_detail : main;
      rows = src.map((c) => {
        const name = c.channel || c.username;
        return {
          name, title: c.title, type: c.type || 'unchecked', subscribers: c.subscribers,
          status: c.channel_status || c.status, role: c.role || null,
          posts: c.posts ?? 0, new_posts: c.new_posts ?? 0, deleted_posts: c.deleted_posts ?? null,
          unreviewed: c.unreviewed ?? null, last_post: c.last_post, last_crawled: c.last_crawled,
          comments: c.comments ?? null, comments_checked: c.comments_checked ?? null, preview: c.preview,
          linked_from: c.linked_from ?? 0, other_cases: c.other_cases || [],
          watch: wm.get(name) || null, sched: sm.get(name) || null, running: running.get(name) || [],
        };
      });
      // drop selections that no longer exist
      selected = new Set([...selected].filter((n) => rows.some((r) => r.name === n)));
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  // A primitive: only a job starting or finishing (not every poll) reloads the page.
  const jobsBusy = $derived((app.jobs.running || 0) + (app.jobs.queued || 0));
  $effect(() => {
    caseId(); app.jobsTick; jobsBusy;
    untrack(load);
  });

  // ---- derived table ---------------------------------------------------------------
  const types = $derived([...new Set(rows.map((r) => r.type))].sort());
  const fresh = (r) => freshness(r.last_crawled, r.watch);

  const filtered = $derived.by(() => {
    const needle = q.toLowerCase();
    let out = rows.filter((r) => {
      if (needle && !(r.name.toLowerCase().includes(needle) || (r.title || '').toLowerCase().includes(needle))) return false;
      if (fType && r.type !== fType) return false;
      if (!inCase) {
        if (fStatus) { if (r.status !== fStatus) return false; }
        else if (r.status === 'dismissed') return false;
      } else if (fRole && r.role !== fRole) return false;
      if (fQuick === 'stale' && !fresh(r).stale) return false;
      if (fQuick === 'running' && !r.running.length) return false;
      if (fQuick === 'nowatch' && r.watch?.enabled) return false;
      if (fQuick === 'deleted' && !r.deleted_posts) return false;
      if (fQuick === 'nocomments' && (r.comments || r.sched)) return false;
      return true;
    });
    const dir = sortDir === 'desc' ? -1 : 1;
    const val = {
      name: (r) => r.name, subscribers: (r) => r.subscribers ?? -1, posts: (r) => r.posts, last_post: (r) => r.last_post || '',
      last_crawled: (r) => r.last_crawled || '', role: (r) => ({ seed: 0, watched: 1, context: 2 }[r.role] ?? 3),
      status: (r) => ({ seed: 0, watched: 1, candidate: 2, dismissed: 3 }[r.status] ?? 4),
      watch: (r) => (r.watch?.enabled ? r.watch.interval_min : 1e9), deleted: (r) => r.deleted_posts ?? -1,
    }[sortKey] || ((r) => r.name);
    out = [...out].sort((a, b) => {
      const x = val(a), y = val(b);
      return (x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name)) * dir;
    });
    return out;
  });

  const stats = $derived({
    total: rows.length,
    running: rows.filter((r) => r.running.length).length,
    stale: rows.filter((r) => fresh(r).stale && !fresh(r).never).length,
    never: rows.filter((r) => !r.last_crawled).length,
    watched: rows.filter((r) => r.watch?.enabled).length,
    sched: rows.filter((r) => r.sched?.enabled).length,
  });

  function sortBy(k) {
    if (sortKey === k) setQuery({ sort: k, dir: sortDir === 'asc' ? 'desc' : 'asc' });
    else setQuery({ sort: k, dir: ['posts', 'subscribers', 'last_post', 'last_crawled', 'deleted'].includes(k) ? 'desc' : 'asc' });
  }
  const arrow = (k) => (sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '');
  const ariaSort = (k) => (sortKey === k ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none');

  // ---- row actions -------------------------------------------------------------------
  async function setRole(r, role) {
    const ok = await attempt(() => post(`/api/cases/${cid}/channels`, { update: { channel: r.name, role } }), `@${r.name} is now ${role}`);
    if (ok) { r.role = role; loadCases(); }
  }
  async function setStatus(r, status, el) {
    if (status === 'dismissed' && !confirm(`Mark @${r.name} as Dismissed for ALL cases?\n\nDismissed channels are hidden from Discovery, the network and the channel list in every case, not just this one. Nothing is deleted, and you can change the status back later.`)) { if (el) el.value = r.status; return; }
    const ok = await attempt(() => post(`/api/channels/${encodeURIComponent(r.name)}/status`, { status }), `@${r.name} marked ${status}`);
    if (ok) r.status = status;
  }
  let removing = $state(null);
  let busy = $state(false);
  async function doRemove() {
    busy = true;
    const r = removing;
    const ok = await attempt(() => post(`/api/cases/${cid}/channels`, { remove: [r.name] }), `Removed @${r.name} from this case`);
    busy = false;
    if (ok) { removing = null; rows = rows.filter((x) => x.name !== r.name); loadCases(); }
  }

  // ---- selection + bulk crawl -----------------------------------------------------------
  const allChecked = $derived(filtered.length > 0 && filtered.every((r) => selected.has(r.name)));
  function toggle(name, on) {
    const s = new Set(selected);
    if (on) s.add(name); else s.delete(name);
    selected = s;
  }
  function toggleAll(on) { selected = on ? new Set(filtered.map((r) => r.name)) : new Set(); }
  function selectStale() { selected = new Set(filtered.filter((r) => fresh(r).stale).map((r) => r.name)); }

  let bulkOpen = $state(false);
  let bulkPages = $state(10);
  let bulkMedia = $state('photos');
  const bulkNames = $derived([...selected].filter((n) => !rows.find((r) => r.name === n)?.running.some((j) => j.kind === 'crawl')));
  async function doBulk() {
    busy = true;
    let ok = 0, fail = 0;
    for (const n of bulkNames) {
      try {
        await post(`/api/channels/${encodeURIComponent(n)}/collect`, { posts: { pages: Number(bulkPages) || 10, media: bulkMedia, depth: 1, max_channels: 1 } });
        ok++;
      } catch (e) { fail++; toast(`@${n}: ${e.message}`, 'error', 7000); }
    }
    busy = false; bulkOpen = false; selected = new Set();
    if (ok) toast(`Queued crawls for ${plural(ok, 'channel')}. Watch them on the Collection page.`, 'ok');
    load();
  }
  const cur = $derived(currentCase());
</script>

<div class="page wide">
  <div class="page-head">
    <div>
      <h1>Channels</h1>
      <p>
        {#if inCase}{cur ? `Channels in "${cur.name}".` : 'Channels in this case.'}{:else}Every channel in the archive, across all cases.{/if}
        Click a channel for everything about it: posts, comments, links, collection settings and changes.
      </p>
    </div>
    <div class="row">
      <a class="btn" href={href('collect')}>Collection status</a>
      <a class="btn primary" href={href('add')}>＋ Add channels</a>
    </div>
  </div>

  {#if loading}
    <Loading />
  {:else if error}
    <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
  {:else}
    <div class="stats" style="margin-bottom:12px">
      <div class="panel stat"><div class="v">{num(stats.total)}</div><div class="l">channels</div></div>
      <button class="panel stat statbtn" class:on={fQuick === 'running'} aria-pressed={fQuick === 'running'} onclick={() => setQuery({ f: fQuick === 'running' ? '' : 'running' })}>
        <div class="v">{num(stats.running)}</div><div class="l">collecting now</div></button>
      <button class="panel stat statbtn" class:on={fQuick === 'stale'} aria-pressed={fQuick === 'stale'} onclick={() => setQuery({ f: fQuick === 'stale' ? '' : 'stale' })}>
        <div class="v" class:warnv={stats.stale > 0}>{num(stats.stale)}</div><div class="l">stale (needs a re-crawl)</div></button>
      <div class="panel stat"><div class="v" class:warnv={stats.never > 0}>{num(stats.never)}</div><div class="l">never crawled</div></div>
      <div class="panel stat"><div class="v">{num(stats.watched)}</div><div class="l">on a watch</div></div>
      <div class="panel stat"><div class="v">{num(stats.sched)}</div><div class="l">comment schedules</div></div>
    </div>

    <div class="toolbar row">
      <input type="search" data-page-search placeholder="Filter by name or title ( / )" value={q}
             oninput={(e) => setQuery({ q: e.currentTarget.value })} style="min-width:min(100%,220px)" aria-label="Filter channels" />
      <select value={fType} onchange={(e) => setQuery({ type: e.currentTarget.value })} aria-label="Type">
        <option value="">All types</option>
        {#each types as t}<option value={t}>{t}</option>{/each}
      </select>
      {#if inCase}
        <select value={fRole} onchange={(e) => setQuery({ role: e.currentTarget.value })} aria-label="Role in case">
          <option value="">All roles</option><option value="seed">Seed</option><option value="watched">Watched</option><option value="context">Context</option>
        </select>
      {:else}
        <select value={fStatus} onchange={(e) => setQuery({ status: e.currentTarget.value })} aria-label="Status">
          <option value="">Hide dismissed</option>
          <option value="seed">Seed</option><option value="watched">Watched</option><option value="candidate">Candidate</option><option value="dismissed">Dismissed</option>
        </select>
      {/if}
      <select value={fQuick} onchange={(e) => setQuery({ f: e.currentTarget.value })} aria-label="Quick filter">
        <option value="">Everything</option>
        <option value="stale">Stale only</option>
        <option value="running">Collecting now</option>
        <option value="nowatch">Not on a watch</option>
        {#if inCase}<option value="deleted">With deleted posts</option><option value="nocomments">Comments not collected</option>{/if}
      </select>
      <span class="spacer"></span>
      <span class="muted small">{filtered.length === rows.length ? plural(rows.length, 'channel') : `${filtered.length} of ${rows.length}`}</span>
      <button class="sm" onclick={selectStale} disabled={!filtered.some((r) => fresh(r).stale)}>Select stale</button>
    </div>

    {#if selected.size}
      <div class="bulk row panel">
        <b>{plural(selected.size, 'channel')} selected</b>
        <button class="primary sm" onclick={() => (bulkOpen = true)}>Crawl selected now…</button>
        <button class="ghost sm" onclick={() => toggleAll(false)}>Clear</button>
      </div>
    {/if}

    {#if !rows.length}
      <div class="panel">
        <Empty title={inCase ? 'No channels in this case yet' : 'No channels yet'}>
          Paste one or more handles, preview them, and choose what to collect for each.
          {#snippet action()}<a class="btn primary" href={href('add')}>＋ Add channels</a>{/snippet}
        </Empty>
      </div>
    {:else if !filtered.length}
      <div class="panel"><Empty title="No channels match these filters">
        {#snippet action()}<button onclick={() => setQuery({ q: '', type: '', status: '', role: '', f: '' })}>Clear filters</button>{/snippet}
      </Empty></div>
    {:else}
      <div class="table-wrap">
        <table class="t">
          <thead>
            <tr>
              <th scope="col" style="width:28px"><input type="checkbox" checked={allChecked} onchange={(e) => toggleAll(e.currentTarget.checked)} aria-label="Select all shown" /></th>
              <th scope="col" aria-sort={ariaSort('name')}><button class="th" onclick={() => sortBy('name')}>Channel{arrow('name')}</button></th>
              <th scope="col" aria-sort={ariaSort(inCase ? 'role' : 'status')}><button class="th" onclick={() => sortBy(inCase ? 'role' : 'status')}>{inCase ? 'Role' : 'Status'}{arrow(inCase ? 'role' : 'status')}</button></th>
              <th scope="col" class="right" aria-sort={ariaSort('subscribers')}><button class="th" onclick={() => sortBy('subscribers')}>Subscribers{arrow('subscribers')}</button></th>
              <th scope="col" class="right" aria-sort={ariaSort('posts')}><button class="th" onclick={() => sortBy('posts')}>Posts{arrow('posts')}</button></th>
              <th scope="col" aria-sort={ariaSort('last_post')}><button class="th" onclick={() => sortBy('last_post')}>Latest post{arrow('last_post')}</button></th>
              <th scope="col" aria-sort={ariaSort('last_crawled')}><button class="th" onclick={() => sortBy('last_crawled')}>Last crawled{arrow('last_crawled')}</button></th>
              <th scope="col" aria-sort={ariaSort('watch')}><button class="th" onclick={() => sortBy('watch')} title="Automatic re-crawl of posts">Watch{arrow('watch')}</button></th>
              <th scope="col">Comments</th>
              <th scope="col">Now</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            {#each filtered as r (r.name)}
              {@const f = fresh(r)}
              <tr class="click" class:sel={selected.has(r.name)} onclick={(e) => { if (!e.target.closest('button, a, select, input, label')) go(href('channel', r.name)); }}>
                <td><input type="checkbox" checked={selected.has(r.name)} onchange={(e) => toggle(r.name, e.currentTarget.checked)} aria-label="Select @{r.name}" /></td>
                <td class="namecell">
                  <a href={href('channel', r.name)} class="nm"><span class="t" dir="auto">{r.title || '@' + r.name}</span></a>
                  <div class="tiny faint"><bdi dir="ltr">@{r.name}</bdi> · <span class="pill">{r.type}</span>
                    {#if r.preview === 0}<span class="pill warn" title="Telegram shows no public preview of this channel, so posts cannot be crawled without login">no preview</span>{/if}
                    {#if inCase && r.other_cases.length}<span class="pill info" title={'Also in: ' + r.other_cases.join(', ')}>+{r.other_cases.length} case{r.other_cases.length > 1 ? 's' : ''}</span>{/if}
                  </div>
                </td>
                <td>
                  {#if inCase}
                    <select class="inl" value={r.role} onchange={(e) => setRole(r, e.currentTarget.value)} aria-label="Role of @{r.name}">
                      <option value="seed">Seed</option><option value="watched">Watched</option><option value="context">Context</option>
                    </select>
                  {:else}
                    <select class="inl" value={r.status} onchange={(e) => setStatus(r, e.currentTarget.value, e.currentTarget)} aria-label="Status of @{r.name}">
                      <option value="seed">Seed</option><option value="watched">Watched</option><option value="candidate">Candidate</option><option value="dismissed">Dismissed (all cases)</option>
                    </select>
                  {/if}
                </td>
                <td class="right num">{compact(r.subscribers)}</td>
                <td class="right num">
                  {num(r.posts)}
                  {#if r.new_posts}<div class="tiny" style="color:var(--accent)" title="Collected since you last opened this case">+{num(r.new_posts)} new</div>{/if}
                  {#if r.deleted_posts}<div class="tiny" style="color:var(--bad)">{num(r.deleted_posts)} deleted</div>{/if}
                </td>
                <td class="nowrap small" title={dt(r.last_post)}>{r.last_post ? day(r.last_post) : '–'}<div class="tiny faint">{r.last_post ? ago(r.last_post) : ''}</div></td>
                <td class="nowrap"><SrcFresh iso={r.last_crawled} watch={r.watch} />
                  {#if r.watch?.enabled && r.watch.next_run}<div class="tiny faint">next {until(r.watch.next_run)}</div>{/if}</td>
                <td class="nowrap small">
                  {#if r.watch?.enabled}<span class="pill ok" title="Re-check window {r.watch.recheck_days} days">{intervalLabel(r.watch.interval_min)}</span>
                  {:else if r.watch}<span class="pill" title="Watch is paused">paused</span>
                  {:else}<span class="faint">not watched</span>{/if}
                </td>
                <td class="nowrap small">
                  {#if r.comments}{num(r.comments)}{:else}<span class="faint">{r.sched ? '0' : 'not collected'}</span>{/if}
                  {#if r.sched}<div class="tiny"><span class="pill" class:ok={r.sched.enabled} title="Uses your Telegram account">{r.sched.enabled ? intervalLabel(r.sched.interval_min) : 'paused'}</span></div>{/if}
                </td>
                <td class="small">
                  {#each r.running as j}
                    <a class="pill info" href={href('collect')} title="Job #{j.id}, {j.status}">{j.status === 'queued' ? 'queued' : 'running'} · {(KIND_LABEL[j.kind] || j.kind).toLowerCase()}</a>
                  {/each}
                </td>
                <td class="nowrap right">
                  <a class="btn sm" href={href('channel', r.name, { tab: 'collection' })} title="Collect, watch and schedule this channel">Collect…</a>
                  {#if inCase}<button class="ghost sm danger" onclick={() => (removing = r)} title="Remove from this case (data is kept)">Remove</button>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="help" style="margin-top:8px">
        Freshness: <span class="pill ok">green</span> up to date · <span class="pill warn">amber</span> stale (no watch: older than 2 weeks; with a watch: missed a few runs) · <span class="pill bad">red</span> never crawled or badly overdue.
        {#if !inCase}Channel status here is global; per-case roles are set inside a case.{/if}
      </p>
    {/if}
  {/if}
</div>

<SrcConfirm open={!!removing} title="Remove from this case?" danger confirmLabel="Remove from case" {busy}
            onclose={() => (removing = null)} onconfirm={doRemove}
            intro={removing ? `@${removing.name} will no longer be part of "${cur?.name || 'this case'}". Its posts and comments stay in the archive, and any watch keeps running. Case triage and notes made for it are kept and come back if you add it again.` : ''} />

<SrcConfirm open={bulkOpen} title="Crawl {plural(bulkNames.length, 'channel')} now?" confirmLabel="Queue crawls" {busy}
            onclose={() => (bulkOpen = false)} onconfirm={doBulk}
            lines={[{ title: 'Public post crawl for each selected channel', detail: `${bulkPages} pages each, ${bulkMedia === 'none' ? 'no media' : bulkMedia === 'photos' ? 'photos' : 'photos and videos'}. Reads the public t.me preview, no login. Crawls run one at a time.` }]}>
  {#if selected.size > bulkNames.length}<p class="small muted">{selected.size - bulkNames.length} selected channel(s) already have a crawl queued or running and will be skipped.</p>{/if}
  <div class="form-grid">
    <label class="field">Pages each<input type="number" min="1" max="500" bind:value={bulkPages} /></label>
    <label class="field">Media<select bind:value={bulkMedia}><option value="none">None</option><option value="photos">Photos</option><option value="all">Photos and videos</option></select></label>
  </div>
</SrcConfirm>

<style>
  .toolbar { margin-bottom: 10px; }
  .bulk { padding: 8px 12px; margin-bottom: 10px; background: var(--accent-soft); }
  .statbtn { text-align: left; font-weight: inherit; display: block; cursor: pointer; }
  .statbtn.on { border-color: var(--accent); background: var(--accent-soft); }
  .warnv { color: var(--warn); }
  button.th { background: none; border: 0; padding: 0; font: inherit; font-weight: 600; color: inherit; cursor: pointer; }
  button.th:hover { color: var(--text); background: none; }
  .namecell { max-width: 320px; }
  .nm .t { font-weight: 650; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  select.inl { padding: 2px 4px; font-size: 12.5px; }
</style>
