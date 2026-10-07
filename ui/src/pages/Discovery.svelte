<script>
  // Discover new channels.
  //  1. Candidates (no login): channels outside the case that the case's channels point at, ranked.
  //  2. Logged-in lookups: Telegram's similar channels, channel-name search, public-post search.
  //     These use the investigator's own Telegram account, so they only run after an explicit confirm.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, setQuery, caseId, href } from '../lib/router.svelte.js';
  import { app, currentCase, attempt, toast, loadCases } from '../lib/store.svelte.js';
  import { num, compact, dt, day, ago, tme } from '../lib/fmt.js';
  import { EDGE_LABEL } from '../lib/net-data.js';
  import { addToCase, dismiss, previewLive, METHODS, METHOD_LABEL } from '../lib/net-actions.js';
  import ChannelLink from '../components/ChannelLink.svelte';
  import Modal from '../components/Modal.svelte';
  import Empty from '../components/Empty.svelte';
  import Loading from '../components/Loading.svelte';

  const cid = caseId();
  const cur = $derived(currentCase());
  const Q = route.query;

  // ---- shared: where "add" goes, and with which role ------------------------------------
  let role = $state('watched');
  let pickCase = $state(null);
  const targetCase = $derived(cid ?? pickCase ?? app.cases[0]?.id ?? null);
  const caseName = (id) => app.cases.find((c) => c.id === id)?.name || `case ${id}`;

  // ---- candidates ------------------------------------------------------------------------
  let limit = $state([50, 100, 250, 500].includes(Number(Q.limit)) ? Number(Q.limit) : 100);
  let users = $state(Q.users === '1');
  let filter = $state(Q.q || '');
  let cands = $state.raw(null);
  let cErr = $state(null);
  let checked = $state({});            // username -> true
  let live = $state({});               // username -> {loading} | resolve result
  let busy = $state(false);
  let seq = 0;

  $effect(() => { const p = { limit: limit !== 100 ? limit : null, users: users ? '1' : null, q: filter || null }; untrack(() => setQuery(p)); });

  async function loadCands() {
    const my = ++seq;
    cErr = null;
    try {
      const r = await get('/api/discovery/candidates', { case: cid ?? undefined, limit, users: users ? 1 : undefined });
      if (my === seq) cands = r;
    } catch (e) { if (my === seq) cErr = e.message; }
  }
  $effect(() => { void limit; void users; void app.jobsTick; untrack(loadCands); });

  const maxScore = $derived(Math.max(1, ...(cands || []).map((c) => c.score)));
  const shown = $derived.by(() => {
    const f = filter.trim().toLowerCase().replace(/^@/, '');
    if (!cands) return [];
    return cands.map((c, i) => ({ ...c, rank: i + 1 }))
      .filter((c) => !f || c.username.includes(f) || (c.title || '').toLowerCase().includes(f) || c.sources.some((s) => s.includes(f)));
  });
  const selNames = $derived(Object.keys(checked).filter((k) => checked[k]));
  const allChecked = $derived(shown.length > 0 && shown.every((c) => checked[c.username]));
  function toggleAll() {
    const on = !allChecked;
    const next = { ...checked };
    for (const c of shown) next[c.username] = on;
    checked = next;
  }

  async function doAdd(names) {
    if (targetCase == null) { toast('Create a case first (All cases → New case).', 'error'); return; }
    busy = true;
    const r = await attempt(() => addToCase(targetCase, names, role));
    busy = false;
    if (!r) return;
    const bits = [];
    if (r.added?.length) bits.push(`added ${r.added.length} to ${caseName(targetCase)} as ${role}`);
    if (r.already?.length) bits.push(`${r.already.length} already there`);
    if (r.invalid?.length) bits.push(`${r.invalid.length} invalid`);
    toast(bits.join(' · ') || 'Nothing changed', r.invalid?.length ? 'error' : 'ok');
    checked = {};
    loadCases();
    if (targetCase === cid) { cands = (cands || []).filter((c) => !names.includes(c.username)); loadCands(); }
    loadResults();
  }
  let confirmDismiss = $state(null);   // list of names awaiting confirmation (bulk)
  async function doDismiss(names) {
    busy = true;
    const n = await attempt(() => dismiss(names));
    busy = false;
    confirmDismiss = null;
    if (n === undefined) { loadCands(); return; }
    toast(`Dismissed ${n} channel${n === 1 ? '' : 's'} (hidden from discovery in every case)`, 'ok');
    cands = (cands || []).filter((c) => !names.includes(c.username));
    const next = { ...checked }; for (const x of names) delete next[x]; checked = next;
    results = (results || []).map((r) => (names.includes(r.username) ? { ...r, status: 'dismissed' } : r));
  }
  async function doPreview(name) {
    live = { ...live, [name]: { loading: true } };
    try { live = { ...live, [name]: await previewLive(name) }; }
    catch (e) { live = { ...live, [name]: { live: { error: e.message } } }; }
  }

  const typeLabel = (t) => EDGE_LABEL[t] || t.replace(/_/g, ' ');

  // ---- logged-in lookups -------------------------------------------------------------------
  let method = $state('similar');
  let entries = $state('');
  let jobLimit = $state(50);
  let confirmJob = $state(false);
  const m = $derived(METHODS.find((x) => x.id === method));
  const entryList = $derived([...new Set(entries.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean))]);
  const loggedIn = $derived(!!app.telegram?.ok);
  let caseChannels = $state([]);
  $effect(() => {
    if (cid == null) return;
    get(`/api/cases/${cid}`).then((c) => (caseChannels = (c.channels_detail || []).map((x) => x.channel || x.username).filter(Boolean))).catch(() => {});
  });
  function addEntries(list) {
    const have = new Set(entryList);
    entries = [...entryList, ...list.filter((x) => !have.has(x))].join('\n');
  }
  async function runJob() {
    confirmJob = false;
    const body = { method, [m.key]: entryList, case: cid ?? undefined };
    if (method !== 'similar') body.limit = jobLimit;
    const r = await attempt(() => post('/api/discovery', body));
    if (r?.id) { toast(`Queued logged-in lookup (job #${r.id}). Results appear below when it finishes.`, 'ok'); entries = ''; }
  }

  // ---- past results --------------------------------------------------------------------------
  let rMethod = $state('');
  let rQuery = $state('');
  let results = $state.raw(null);
  let rErr = $state(null);
  let rseq = 0;
  async function loadResults() {
    const my = ++rseq;
    try {
      const r = await get('/api/discoveries', { method: rMethod || undefined, query: rQuery.trim() || undefined, case: cid ?? undefined });
      if (my === rseq) { results = r; rErr = null; }
    } catch (e) { if (my === rseq) rErr = e.message; }
  }
  $effect(() => { void rMethod; void app.jobsTick; untrack(loadResults); });
  const groups = $derived.by(() => {
    const g = new Map();
    for (const r of results || []) {
      const k = r.method + '\u0000' + r.query;
      if (!g.has(k)) g.set(k, { method: r.method, query: r.query, rows: [], last: r.found_at });
      const G = g.get(k);
      G.rows.push(r);
      if ((r.found_at || '') > (G.last || '')) G.last = r.found_at;
    }
    for (const G of g.values()) G.rows.sort((a, b) => (a.detail?.rank ?? 999) - (b.detail?.rank ?? 999));
    return [...g.values()].sort((a, b) => (b.last || '').localeCompare(a.last || ''));
  });
  let openGroups = $state({});
  const groupTitle = (G) => G.method === 'similar' ? `Similar to @${G.query}` : G.method === 'search_channels' ? `Channel names matching “${G.query}”` : `Posts matching “${G.query}”`;
  const isInScope = (r) => cid != null ? (r.cases || '').split(', ').includes(cur?.name) : r.status === 'seed' || r.status === 'watched';
</script>

<div class="page wide">
  <div class="page-head">
    <div>
      <h1>Discover new channels</h1>
      <p>Channels that {cid != null ? 'this case’s channels' : 'your seed and watched channels'} keep pointing at, ranked from what’s already collected (no login), plus lookups with your Telegram account.
        Add the useful ones to {cid != null ? 'the case' : 'a case'}; dismiss the rest so they stop coming back.</p>
    </div>
    <a class="btn" href={href('network', { mode: 'discovery' })}>See as a graph</a>
  </div>

  <!-- where adds go -->
  <div class="addbar row">
    <span class="small muted">Add to</span>
    {#if cid != null}
      <b class="small">{cur?.name || `case ${cid}`}</b>
    {:else if app.cases.length}
      <select bind:value={pickCase} aria-label="Case to add channels to">{#each app.cases as c (c.id)}<option value={c.id}>{c.name}</option>{/each}</select>
    {:else}
      <span class="small faint">no cases yet — <a href="#/">create one</a></span>
    {/if}
    <span class="small muted">as</span>
    <div class="seg" role="group" aria-label="Role">
      {#each [['seed', 'core channel of the case'], ['watched', 'follow it'], ['context', 'background only']] as [r, help]}
        <button class="sm" class:on={role === r} aria-pressed={role === r} onclick={() => (role = r)} title={help}>{r}</button>
      {/each}
    </div>
  </div>

  <!-- 1. candidates -->
  <section class="panel">
    <div class="panel-head">
      <h2>Candidates <span class="faint small">{cands ? num(cands.length) : ''}</span></h2>
      <input type="search" data-page-search placeholder="Filter by name or source…" bind:value={filter} aria-label="Filter candidates" />
      <label class="check small"><input type="checkbox" bind:checked={users} /> include users &amp; bots</label>
      <label class="check small">Top <select bind:value={limit}>{#each [50, 100, 250, 500] as n}<option value={n}>{n}</option>{/each}</select></label>
    </div>
    <div class="panel-body help">
      Score = 3 × the number of {cid != null ? 'case' : 'seed/watched'} channels pointing at it, plus a weighted count of forwards, mentions, links, description links,
      comment-section links and “similar” results, plus keyword-search hits and a bonus if seen in the last 30 days. It is a lead to check, not a finding.
    </div>

    {#if selNames.length}
      <div class="bulk row">
        <b class="small">{selNames.length} selected</b>
        <button class="sm primary" disabled={busy || targetCase == null} onclick={() => doAdd(selNames)}>Add {selNames.length} as {role}</button>
        <button class="sm danger" disabled={busy} onclick={() => (confirmDismiss = selNames)}>Dismiss {selNames.length}…</button>
        <a class="btn sm" href={href('add', { handles: selNames.join(',') })}>Collect {selNames.length}…</a>
        <button class="sm ghost" onclick={() => (checked = {})}>Clear</button>
      </div>
    {/if}

    {#if cErr}
      <div class="panel-body"><div class="callout bad" role="alert">{cErr} <button class="sm" onclick={loadCands}>Retry</button></div></div>
    {:else if !cands}
      <Loading />
    {:else if !cands.length}
      <Empty title="No candidates yet">
        Candidates come from forwards, mentions and links in collected posts. {cid != null && cur && !cur.channels ? 'This case has no channels yet.' : 'Collect posts from the channels first, or run a logged-in lookup below.'}
        {#snippet action()}<a class="btn" href={href(cid != null && cur && !cur.channels ? 'add' : 'collect')}>{cid != null && cur && !cur.channels ? 'Add channels' : 'Go to Collection'}</a>{/snippet}
      </Empty>
    {:else}
      <div class="table-wrap flat">
        <table class="t">
          <thead><tr>
            <th scope="col"><input type="checkbox" checked={allChecked} onchange={toggleAll} aria-label="Select all shown" /></th>
            <th scope="col" class="right">#</th><th scope="col">Channel</th><th scope="col" class="right">Subscribers</th><th scope="col">Score</th><th scope="col">Why</th><th scope="col">Pointed at by</th><th scope="col"></th>
          </tr></thead>
          <tbody>
            {#each shown as c (c.username)}
              {@const L = live[c.username]}
              <tr class:sel={checked[c.username]}>
                <td><input type="checkbox" checked={!!checked[c.username]} onchange={(e) => (checked = { ...checked, [c.username]: e.currentTarget.checked })} aria-label="Select @{c.username}" /></td>
                <td class="right faint num">{c.rank}</td>
                <td class="chan">
                  <ChannelLink name={c.username} title={c.title} />
                  <div class="row tight">
                    {#if c.type && c.type !== 'channel'}<span class="pill warn">{c.type}</span>{/if}
                    {#if c.status && c.status !== 'candidate'}<span class="pill accent" title="Status in the shared pool">{c.status}</span>{/if}
                    {#if c.posts}<span class="pill">{num(c.posts)} posts</span>{:else}<span class="tiny faint">not collected</span>{/if}
                    {#if c.preview === 0}<span class="pill warn" title="No public web preview: posts can't be read without login">no preview</span>{/if}
                  </div>
                  {#if L}
                    <div class="livebox small">
                      {#if L.loading}<span class="spinner"></span> checking t.me…
                      {:else if L.live?.error}<span class="bad">Live check failed: {L.live.error}</span>
                      {:else if L.live}
                        <b>Live on t.me:</b> {L.live.type}{L.live.preview ? ' · public preview' : ' · no preview'}
                        {#if L.live.title} · <span dir="auto">{L.live.title}</span>{/if}
                        {#if L.live.subscribers != null} · {compact(L.live.subscribers)} subs{/if}
                        {#if L.live.latest_post} · latest post {day(L.live.latest_post)}{/if}
                        {#if L.cases?.length}<div class="faint">In cases: {L.cases.join(', ')}</div>{/if}
                      {:else if L.error}<span class="bad">{L.error}</span>{/if}
                    </div>
                  {/if}
                </td>
                <td class="right num">{num(c.subscribers)}</td>
                <td class="score"><div class="num small">{c.score}</div><div class="bar"><i style:width="{(100 * c.score) / maxScore}%"></i></div></td>
                <td class="why small">
                  {c.reasons[0]}{#if c.reasons.length > 1}<div class="faint">{c.reasons.slice(1).join(' · ')}</div>{/if}
                  {#if c.last_seen}<div class="tiny faint">last pointed at {ago(c.last_seen)}</div>{/if}
                </td>
                <td class="src small">
                  {#each c.sources.slice(0, 4) as s}<div><ChannelLink name={s} /></div>{/each}
                  {#if c.sources.length > 4}<div class="faint tiny" title={c.sources.slice(4).join(', ')}>+{c.sources.length - 4} more</div>{/if}
                </td>
                <td class="acts">
                  <button class="sm primary" disabled={busy || targetCase == null} onclick={() => doAdd([c.username])} title="Add to {targetCase != null ? caseName(targetCase) : 'a case'} as {role}">Add</button>
                  <button class="sm" disabled={L?.loading} onclick={() => doPreview(c.username)} title="Fetch its public t.me page now (no login; visible to Telegram like any page visit)">Preview</button>
                  <a class="btn sm" href={href('add', { handles: c.username })} title="Choose what to collect">Collect…</a>
                  <button class="sm ghost danger" disabled={busy} onclick={() => doDismiss([c.username])} title="Hide from discovery in every case">Dismiss</button>
                </td>
              </tr>
            {:else}
              <tr><td colspan="8" class="faint">No candidate matches “{filter}”.</td></tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <!-- 2. logged-in lookups -->
  <section class="panel">
    <div class="panel-head"><h2>Look up with your Telegram account</h2>
      {#if app.telegram}<span class="pill" class:ok={loggedIn} class:bad={!loggedIn}>{loggedIn ? 'logged in' : 'not logged in'}</span>{/if}
    </div>
    <div class="panel-body stack">
      <div class="callout warn small">These lookups run as <b>your personal Telegram account</b> ({app.telegram?.message || 'status unknown'}). They are read-only, but Telegram sees the account making them. Nothing runs until you confirm.</div>
      <div class="methods">
        {#each METHODS as x}
          <label class="method" class:on={method === x.id}>
            <input type="radio" name="method" value={x.id} bind:group={method} />
            <span><b>{x.label}</b><span class="help">{x.one}</span></span>
          </label>
        {/each}
      </div>
      <div class="form-grid entry">
        <label class="field">{m.key === 'channels' ? 'Channels' : 'Keywords'} (max 50)
          <textarea bind:value={entries} placeholder={m.placeholder} rows="4"></textarea>
        </label>
        <div class="col">
          {#if method === 'similar' && caseChannels.length}
            <div class="small muted">Case channels:</div>
            <div class="row tight">{#each caseChannels as ch}<button class="chip" onclick={() => addEntries([ch])}><bdi dir="ltr">@{ch}</bdi></button>{/each}</div>
            <button class="sm" onclick={() => addEntries(caseChannels)}>Add all case channels</button>
          {/if}
          {#if method !== 'similar'}
            <label class="field">Results per keyword <input type="number" min="1" max="100" bind:value={jobLimit} style="width:90px" /></label>
          {/if}
          <div class="row">
            <button class="primary" disabled={!loggedIn || !entryList.length || entryList.length > 50} onclick={() => (confirmJob = true)}>Review &amp; run…</button>
            {#if entryList.length > 50}<span class="small bad">At most 50 per job.</span>
            {:else if !loggedIn}<span class="small faint">Log in to Telegram in Settings first.</span>{/if}
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- 3. past results -->
  <section class="panel">
    <div class="panel-head">
      <h2>What lookups found</h2>
      <select bind:value={rMethod} aria-label="Method"><option value="">All methods</option>{#each METHODS as x}<option value={x.id}>{x.label}</option>{/each}</select>
      <input type="search" placeholder="Exact query / channel…" bind:value={rQuery} onkeydown={(e) => e.key === 'Enter' && loadResults()} aria-label="Query" />
      <button class="sm" onclick={loadResults}>Filter</button>
    </div>
    {#if rErr}
      <div class="panel-body"><div class="callout bad" role="alert">{rErr}</div></div>
    {:else if !results}
      <Loading />
    {:else if !groups.length}
      <Empty title="No lookup results{cid != null ? ' for this case' : ''} yet">Run a lookup above. Results are grouped by the channel or keyword you looked up.</Empty>
    {:else}
      <div class="groups">
        {#each groups as G (G.method + G.query)}
          {@const k = G.method + '|' + G.query}
          {@const open = openGroups[k] ?? groups.length <= 3}
          <div class="grp">
            <button class="ghead" onclick={() => (openGroups = { ...openGroups, [k]: !open })} aria-expanded={open}>
              <span class="caret">{open ? '▾' : '▸'}</span>
              <span class="pill info">{METHOD_LABEL[G.method] || G.method}</span>
              <b dir="auto">{groupTitle(G)}</b>
              <span class="faint small">{G.rows.length} result{G.rows.length === 1 ? '' : 's'} · {dt(G.last)}</span>
            </button>
            {#if open}
              <div class="table-wrap flat">
                <table class="t">
                  <thead><tr><th scope="col" class="right">Rank</th><th scope="col">Channel</th><th scope="col" class="right">Subscribers</th><th scope="col">Status</th>{#if G.method === 'search_posts'}<th scope="col">Matching post</th>{/if}<th scope="col"></th></tr></thead>
                  <tbody>
                    {#each G.rows as r (r.id)}
                      <tr>
                        <td class="right faint num">{r.detail?.rank ?? '–'}</td>
                        <td class="chan">
                          {#if r.username}<ChannelLink name={r.username} title={r.title} />{:else}<span dir="auto">{r.title || 'Untitled'}</span> <span class="pill" title="No public username: recorded, but it can't be collected or added">private</span>{/if}
                          <div class="row tight">
                            {#if r.type && r.type !== 'channel'}<span class="pill warn">{r.type}</span>{/if}
                            {#if r.detail?.scam}<span class="pill bad">scam flag</span>{/if}{#if r.detail?.fake}<span class="pill bad">fake flag</span>{/if}{#if r.detail?.verified}<span class="pill ok">verified</span>{/if}
                          </div>
                        </td>
                        <td class="right num">{num(r.subscribers)}</td>
                        <td>{#if r.status}<span class="pill" class:accent={r.status === 'seed' || r.status === 'watched'} class:bad={r.status === 'dismissed'}>{r.status}</span>{/if}
                          {#if r.cases}<div class="tiny faint">in {r.cases}</div>{/if}</td>
                        {#if G.method === 'search_posts'}
                          <td class="post small">{#if r.detail?.post}<div class="text-body clamp" dir="auto">{r.detail.post.text || '(no text)'}</div>
                            <div class="tiny faint">{dt(r.detail.post.date)}{#if r.username} · <a href={tme(r.username, r.detail.post.msg_id)} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}</div>{/if}</td>
                        {/if}
                        <td class="acts">
                          {#if r.username}
                            {#if !isInScope(r)}<button class="sm primary" disabled={busy || targetCase == null} onclick={() => doAdd([r.username])}>Add</button>{/if}
                            <a class="btn sm" href={href('add', { handles: r.username })}>Collect…</a>
                            {#if r.status !== 'dismissed' && r.status !== 'seed' && r.status !== 'watched'}<button class="sm ghost danger" disabled={busy} onclick={() => doDismiss([r.username])}>Dismiss</button>{/if}
                          {/if}
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </section>
</div>

<Modal open={confirmJob} onclose={() => (confirmJob = false)} title="Run a logged-in lookup?">
  <p>This will use <b>your Telegram account</b> ({app.telegram?.message || 'logged-in account'}) to run:</p>
  <div class="callout small">
    <b>{m.label}</b> — {m.one}<br />
    {m.key === 'channels' ? 'Channels' : 'Keywords'} ({entryList.length}): <span dir="auto">{entryList.map((e) => (m.key === 'channels' ? e : `“${e}”`)).join(', ')}</span>
    {#if method !== 'similar'}<br />Up to {jobLimit} results per keyword.{/if}
    {#if cid != null}<br />Results are tagged to case “{cur?.name}”.{/if}
  </div>
  <p class="small muted" style="margin-top:8px">Read-only on Telegram. It joins the logged-in queue (one job at a time). New channels found become candidates; “similar” results also become links in the network.</p>
  {#snippet footer()}
    <button onclick={() => (confirmJob = false)}>Cancel</button>
    <button class="primary" onclick={runJob}>Run {entryList.length} lookup{entryList.length === 1 ? '' : 's'}</button>
  {/snippet}
</Modal>

<Modal open={!!confirmDismiss} onclose={() => (confirmDismiss = null)} title="Dismiss {confirmDismiss?.length} channels?">
  <p>They will be marked <b>dismissed</b> in the shared pool, which hides them from discovery and the network in <b>every case</b>. You can restore a channel from its page.</p>
  <p class="small muted">{confirmDismiss?.map((n) => '@' + n).join(', ')}</p>
  {#snippet footer()}
    <button onclick={() => (confirmDismiss = null)}>Cancel</button>
    <button class="danger" disabled={busy} onclick={() => doDismiss(confirmDismiss)}>Dismiss {confirmDismiss?.length}</button>
  {/snippet}
</Modal>

<style>
  section.panel { margin-bottom: 16px; }
  .addbar { margin-bottom: 12px; }
  .panel-head input[type=search] { width: min(240px, 100%); }
  .bulk { padding: 8px 14px; background: var(--accent-soft); border-bottom: 1px solid var(--line); }
  .table-wrap.flat { border: 0; border-radius: 0; border-top: 1px solid var(--line); }
  td.chan { min-width: 220px; max-width: 360px; }
  td.chan .row { margin-top: 3px; }
  td.score { width: 90px; }
  td.score .bar { margin-top: 3px; }
  td.why { min-width: 190px;  max-width: 260px; }
  td.src { max-width: 200px; }
  td.acts { white-space: nowrap; text-align: right; }
  td.acts > * { margin-left: 3px; }
  .livebox { margin-top: 6px; padding: 5px 8px; border-radius: var(--radius-sm); background: var(--panel-2); max-width: 360px; }
  .methods { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; }
  .method { display: flex; gap: 8px; align-items: flex-start; border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; cursor: pointer; }
  .method.on { border-color: var(--accent); background: var(--accent-soft); }
  .method span { display: flex; flex-direction: column; gap: 2px; }
  .entry { align-items: start; }
  .groups { display: flex; flex-direction: column; }
  .grp { border-top: 1px solid var(--line); }
  .ghead { display: flex; gap: 8px; align-items: center; width: 100%; border: 0; border-radius: 0; background: none; padding: 9px 14px; text-align: start; flex-wrap: wrap; white-space: normal; }
  .ghead:hover { background: var(--panel-2); }
  .caret { color: var(--faint); width: 12px; }
  td.post { max-width: 420px; }
  .clamp { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .bad { color: var(--bad); }
</style>
