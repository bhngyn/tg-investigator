<script>
  // Search history: every search that was run (first page only) is logged with the exact query that was executed, so the
  // way items were found can be documented and repeated. Also hosts the saved searches. Scope = the case in the switcher, or the whole archive.
  //   GET /api/search_log?case=&q=&limit=&offset=   GET /api/search_log/{id}   POST /api/search_log/{id}/rerun
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, hrefIn, setQuery, go } from '../lib/router.svelte.js';
  import { app, scoped, attempt, toast } from '../lib/store.svelte.js';
  import { num, dt, ago, pj, plural } from '../lib/fmt.js';
  import { listOf, paramsToQuery, isCommentsRun, describeParams } from '../lib/searchparams.js';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import Drawer from '../components/Drawer.svelte';
  import SavedSearches from '../components/SavedSearches.svelte';
  import TriageDetailDrawer from '../components/TriageDetailDrawer.svelte';

  const Q = $derived(route.query);
  const tab = $derived(Q.tab === 'saved' ? 'saved' : 'history');
  const LIMIT = 50;
  const offset = $derived(Math.max(0, Number(Q.offset) || 0));

  let qInput = $state(route.query.q || '');
  $effect(() => { const v = Q.q || ''; untrack(() => { qInput = v; }); });

  let res = $state(null);
  let rows = $state([]);
  let error = $state('');
  let loading = $state(true);
  let seq = 0;
  const key = $derived(JSON.stringify([tab, Q.q || '', offset, route.scope]));
  async function load() {
    const my = ++seq;
    loading = true; error = '';
    try {
      const r = await get('/api/search_log', scoped({ q: Q.q || undefined, limit: LIMIT, offset }));
      if (my !== seq) return;
      res = r; rows = listOf(r, 'runs', 'log', 'entries');
    } catch (e) { if (my === seq) { error = e.message; rows = []; } }
    if (my === seq) loading = false;
  }
  $effect(() => { if (tab === 'history') { key; untrack(load); } });
  const total = $derived(res?.total ?? rows.length);
  const commit = (e) => { e?.preventDefault(); setQuery({ q: qInput.trim() || null, offset: null }); };
  const page = (d) => setQuery({ offset: Math.max(0, offset + d * LIMIT) || null });

  const ENDPOINT = { posts: 'posts', comments: 'comments', threads: 'comment threads', export: 'CSV export', saved: 'saved search' };
  const short = (h) => (h ? String(h).slice(0, 10) : '–');
  const who = (r) => r.actor || r.investigator || '–';
  const caseName = (id) => (id == null ? 'Whole archive' : app.cases.find((c) => c.id === id)?.name || `case ${id}`);

  // ---- detail drawer ----------------------------------------------------------------------------
  let sel = $state(null);          // full run row
  let selBusy = $state(false);
  let cmp = $state(null);          // rerun result
  let cmpBusy = $state(false);
  let cmpErr = $state('');
  let item = $state(null);         // post/comment drawer
  async function openRun(r) {
    sel = r; cmp = null; cmpErr = ''; selBusy = true;
    try { const full = await get(`/api/search_log/${r.id}`); if (sel?.id === r.id) sel = { ...r, ...(full?.run || full) }; } catch { /* the row already has the essentials */ }
    selBusy = false;
  }
  const selParams = $derived(pj(sel?.params, {}) || {});
  const isComments = (r) => !!r && isCommentsRun(r.endpoint, r.params);
  function openLink(r) {
    const q = { ...paramsToQuery(r.params), ...(r.q ? { q: r.q } : {}) };
    return hrefIn(r.case_id ?? 'all', isComments(r) ? 'comments' : 'search', (r.endpoint === 'threads' || r.params?._kind === 'threads') ? { ...q, view: 'threads' } : q);
  }
  function openSearch(r) { sel = null; go(openLink(r)); }
  async function rerun(r) {
    cmpBusy = true; cmpErr = ''; cmp = null;
    try { cmp = await post(`/api/search_log/${r.id}/rerun`); }
    catch (e) { cmpErr = e.message; }
    cmpBusy = false;
  }
  const idList = (v) => (Array.isArray(v) ? v : []).map((x) => (typeof x === 'object' ? x.id : x));
  const openItem = (r, id) => (item = { type: isComments(r) ? 'comment' : 'post', id });
  async function copy(t) { try { await navigator.clipboard.writeText(t); toast('Copied', 'ok', 1500); } catch { toast('Copy not available here', 'error'); } }
</script>

<div class="page wide">
  <div class="page-head">
    <div>
      <h1>Search history</h1>
      <p>Every search is recorded with the exact query that was executed (including the spellings the dictionary added), when, and by whom, so you can show how items were found and run the same search again. {caseId() != null ? 'Showing this case’s searches.' : 'Showing the whole archive.'}</p>
    </div>
  </div>

  <div class="tabs">
    <a href={href('searchlog', { tab: null })} class:on={tab === 'history'} aria-current={tab === 'history' ? 'page' : undefined}>History{#if res && tab === 'history'} <span class="badge muted">{num(total)}</span>{/if}</a>
    <a href={href('searchlog', { tab: 'saved' })} class:on={tab === 'saved'} aria-current={tab === 'saved' ? 'page' : undefined}>Saved searches</a>
  </div>

  {#if tab === 'saved'}
    <section class="panel"><SavedSearches /></section>
  {:else}
    <form class="row filt" onsubmit={commit}>
      <input type="search" data-page-search dir="auto" bind:value={qInput} placeholder="Filter by search words" aria-label="Filter history" style="width:min(100%,280px)" />
      <button type="submit">Filter</button>
      {#if Q.q}<button type="button" class="ghost" onclick={() => { qInput = ''; setQuery({ q: null, offset: null }); }}>Clear</button>{/if}
      <span class="spacer"></span>
      <span class="tiny faint">Only the first page of each search is logged. Dictionary look-ups are not.</span>
    </form>

    {#if error}
      <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
    {:else if loading && !rows.length}
      <Loading />
    {:else if !rows.length}
      <Empty title="No searches logged {Q.q ? 'for these words' : 'yet'}">Searches you run on the Search and Comments pages, CSV exports and saved-search runs appear here.</Empty>
    {:else}
      <div class="table-wrap panel" class:dim={loading}>
        <table class="t">
          <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Kind</th><th scope="col">Search</th><th scope="col" class="num">Results</th><th scope="col" class="num" title="Identical runs within 10 minutes are counted, not repeated">Runs</th><th scope="col">Dictionary</th><th scope="col" title="First 10 characters of the SHA-256 over the ordered result ids">Result hash</th></tr></thead>
          <tbody>
            {#each rows as r (r.id)}
              <tr class="click" onclick={() => openRun(r)}>
                <td class="nowrap small" title={dt(r.ts)}><button class="rowbtn" onclick={(e) => { e.stopPropagation(); openRun(r); }} aria-label="Open search run from {ago(r.ts)}">{ago(r.ts)}</button><div class="tiny faint">{dt(r.ts)}</div></td>
                <td class="small">{who(r)}</td>
                <td class="small">{ENDPOINT[r.endpoint] || r.endpoint}{#if caseId() == null && r.case_id != null}<div class="tiny faint">{caseName(r.case_id)}</div>{/if}</td>
                <td class="qcell"><span dir="auto">{r.q || '(filters only)'}</span>
                  {#each describeParams(r.params) as d}<span class="pill {d.kind === 'exp' ? 'warn' : ''} chipx" dir="auto" title={d.kind === 'exp' ? 'Changes which spellings were searched' : 'Filter that was applied'}>{d.label}</span>{/each}</td>
                <td class="num">{num(r.total)}</td>
                <td class="num">{r.repeat > 1 ? '×' + r.repeat : '1'}</td>
                <td class="small mono">{r.dict_version ? String(r.dict_version).slice(0, 12) : '–'}</td>
                <td class="small mono">{short(r.result_sha256)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if total > LIMIT}
        <div class="pager row">
          <button onclick={() => page(-1)} disabled={offset === 0}>← Newer</button>
          <span class="muted small">{num(offset + 1)}–{num(Math.min(total, offset + rows.length))} of {num(total)}</span>
          <button onclick={() => page(1)} disabled={offset + LIMIT >= total}>Older →</button>
        </div>
      {/if}
    {/if}
  {/if}
</div>

<Drawer open={!!sel} onclose={() => (sel = null)} title="Search run #{sel?.id ?? ''}" width="640px">
  {#if sel}
    <div class="col" style="gap:12px">
      <div class="row wrap">
        <span class="pill">{ENDPOINT[sel.endpoint] || sel.endpoint}</span>
        <span class="small">{dt(sel.ts)}</span><span class="small muted">by {who(sel)}</span>
        <span class="small faint">{caseName(sel.case_id)}</span>
        {#if sel.repeat > 1}<span class="pill info">run {sel.repeat}× within 10 min</span>{/if}
        {#if selBusy}<span class="spinner"></span>{/if}
      </div>
      <div class="stats">
        <div class="stat"><div class="v">{num(sel.total)}</div><div class="l">results when run</div></div>
        <div class="stat"><div class="v mono sm">{short(sel.result_sha256)}</div><div class="l">result hash (ordered ids)</div></div>
      </div>
      <dl class="kv">
        {#if sel.rerun_of}<dt>Re-run of</dt><dd><button class="linkish" onclick={() => openRun({ id: sel.rerun_of })}>run #{sel.rerun_of}</button></dd>{/if}
        {#if sel.last_ts && sel.repeat > 1}<dt>Last repeated</dt><dd>{dt(sel.last_ts)}</dd>{/if}
        <dt>Words typed</dt><dd dir="auto"><b>{sel.q || '(none)'}</b></dd>
        <dt>Dictionary</dt><dd class="mono">{sel.dict_version || 'none'}{sel.key_version != null ? ` · key v${sel.key_version}` : ''}</dd>
        {#if sel.saved_search_id}<dt>Saved search</dt><dd>#{sel.saved_search_id}</dd>{/if}
        {#if sel.first_id != null}<dt>First result id</dt><dd class="mono">{sel.first_id}</dd>{/if}
        {#if sel.result_sha256}<dt>SHA-256</dt><dd class="mono hash">{sel.result_sha256} <button class="sm ghost" onclick={() => copy(sel.result_sha256)}>Copy</button></dd>{/if}
      </dl>
      <div>
        <div class="section-title" style="margin-top:0">Exact query that was executed</div>
        <code class="fts" dir="ltr">{#each String(sel.fts || '(no text query)').split(/("[^"]*")/).filter(Boolean) as part}{#if part.startsWith('"')}<bdi>{part}</bdi>{:else}{part}{/if}{/each}</code>
        <div class="tiny faint">Frozen as recorded. Re-running uses this exact expression, not today’s dictionary.</div>
      </div>
      <div>
        <div class="section-title">Filters and options</div>
        {#if Object.keys(selParams).length}
          <dl class="kv">{#each Object.entries(selParams).filter(([k]) => k !== '_kind') as [k, v]}<dt>{k}</dt><dd class="mono" dir="auto">{typeof v === 'object' ? JSON.stringify(v) : v}</dd>{/each}</dl>
        {:else}<div class="tiny faint">None.</div>{/if}
      </div>

      <div class="row wrap">
        <button class="primary" onclick={() => openSearch(sel)} title="Restores these words and filters on the search page (it runs with today’s dictionary)">Open this search</button>
        <button onclick={() => rerun(sel)} disabled={cmpBusy} title="Runs the recorded query again and compares the result list with then">{cmpBusy ? 'Running…' : 'Re-run and compare'}</button>
      </div>
      {#if cmpErr}<div class="callout bad" role="alert">{cmpErr}</div>{/if}
      {#if cmp}
        <div class="callout {cmp.same ? 'ok' : 'warn'}">
          <b>{cmp.same ? 'Same results as then.' : 'The results are different now.'}</b>
          <div class="small">Then {num(cmp.total_then)} · now {num(cmp.total_now)}{cmp.total_now !== cmp.total_then ? ` (${cmp.total_now - cmp.total_then > 0 ? '+' : ''}${num(cmp.total_now - cmp.total_then)})` : ''}. {cmp.added != null ? ` ${num(cmp.added)} added, ${num(cmp.removed)} removed.` : ''} {#if cmp.dict_version_then === cmp.dict_version_now}The dictionary version is unchanged ({cmp.dict_version_now ? String(cmp.dict_version_now).slice(0, 12) : 'none'}).{:else}The dictionary changed since then: <span class="mono">{String(cmp.dict_version_then || 'none').slice(0, 12)}</span> → <span class="mono">{String(cmp.dict_version_now || 'none').slice(0, 12)}</span>.{/if} The re-run used the recorded query, not today’s dictionary. This re-run is itself recorded in the history{#if cmp.run_id} (run #{cmp.run_id}){/if}.</div>
        </div>
        {#each [['added_ids', 'Now in the results, not then', 'ok'], ['removed_ids', 'In the results then, not now', 'bad']] as [k, label, tone]}
          {@const ids = idList(cmp[k])}
          {#if ids.length}
            <div><div class="section-title">{label} <span class="faint">{plural(cmp[k === 'added_ids' ? 'added' : 'removed'] ?? ids.length, 'item')}{ids.length >= 200 ? ' (first 200 shown)' : ''}</span></div>
              <div class="idlist">{#each ids as id}<button class="chip {tone}" onclick={() => openItem(sel, id)} title="Open this {isComments(sel) ? 'comment' : 'post'}">#{id}</button>{/each}</div>
              {#if k === 'removed_ids'}<div class="tiny faint">Items can leave a search because a post was edited or deleted, a review status or filter changed, or the dictionary differs.</div>{/if}</div>
          {/if}
        {/each}
      {/if}
    </div>
  {/if}
</Drawer>

<TriageDetailDrawer bind:item />

<style>
  .filt { margin-bottom: 10px; gap: 8px; }
  .wrap { flex-wrap: wrap; }
  .dim { opacity: .6; }
  .click { cursor: pointer; }
  .qcell { max-width: 380px; overflow-wrap: anywhere; }
  .chipx { margin: 2px 4px 0 0; font-size: 10.5px; }
  .pager { justify-content: center; margin: 14px 0; gap: 12px; }
  .mono { font-family: var(--mono); }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 400; cursor: pointer; }
  .stat .v.sm { font-size: 15px; }
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: 3px 14px; margin: 0; font-size: 12.5px; }
  .kv dt { color: var(--muted); } .kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  .hash { font-size: 11px; }
  .fts { unicode-bidi: isolate; display: block; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 6px 8px; max-height: 200px; overflow: auto; font-size: 12px; }
  .idlist { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; max-height: 180px; overflow: auto; }
  .chip.ok { border-color: var(--ok); color: var(--ok); }
  .chip.bad { border-color: var(--bad); color: var(--bad); }
</style>
