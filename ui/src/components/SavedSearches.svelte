<script>
  // Saved searches for the current scope: run one or all (with "new since last run"), open in Search, make an alert, edit, delete.
  //   GET /api/saved_searches?case=  POST /api/saved_searches {id, ...} | {id, delete:true}
  //   POST /api/saved_searches/{id}/run   POST /api/saved_searches/run_all?case=   POST /api/saved_searches/{id}/to_alert
  import { untrack } from 'svelte';
  import { get, post, api } from '../lib/api.js';
  import { caseId, href, hrefIn } from '../lib/router.svelte.js';
  import { app, scoped, attempt, toast } from '../lib/store.svelte.js';
  import { num, ago, dt, pj, plural } from '../lib/fmt.js';
  import { listOf, paramsToQuery, isCommentsRun, describeParams } from '../lib/searchparams.js';
  import Loading from './Loading.svelte';
  import Modal from './Modal.svelte';

  let { compact = false } = $props();

  let rows = $state(null);
  let error = $state('');
  let results = $state({});          // id -> last run summary
  let busyId = $state(null);
  let runningAll = $state(false);
  let ed = $state(null);             // {id, name, notes, freeze, busy}

  async function load() {
    try { rows = listOf(await get('/api/saved_searches', scoped({})), 'saved_searches', 'searches'); error = ''; }
    catch (e) { error = e.message; rows = rows || []; }
  }
  $effect(() => { caseId(); app.jobsTick; untrack(load); });

  const caseName = (id) => app.cases.find((c) => c.id === id)?.name;
  // run response: {total, total_before, first_run, new_since_last (null on the first run), changed (null on the first run), error?}
  const summary = (r) => ({ added: r?.added ?? null, removed: r?.removed ?? null, truncated: !!r?.truncated, total: r?.total ?? null, before: r?.total_before ?? null, first: !!r?.first_run, fresh: r?.new_since_last ?? null, changed: r?.changed ?? null, error: r?.error || null });
  const searchLink = (s) => hrefIn(s.case_id ?? 'all', isCommentsRun('saved', s.params) ? 'comments' : 'search', { ...paramsToQuery(pj(s.params, {})), q: s.q || undefined });

  async function run(s) {
    busyId = s.id;
    const r = await attempt(() => post(`/api/saved_searches/${s.id}/run`));
    busyId = null;
    if (r === undefined) return;
    results[s.id] = summary(r);
    load();
  }
  async function runAll() {
    runningAll = true;
    const r = await attempt(() => api('/api/saved_searches/run_all', { params: scoped({}), body: {} }));
    runningAll = false;
    if (r === undefined) return;
    const list = listOf(r, 'searches');
    for (const x of list) results[x.id] = summary(x);
    const bad = list.filter((x) => x.error).length;
    toast(`Ran ${plural(r.ran ?? list.length, 'saved search', 'saved searches')}: ${num(r.with_new || 0)} with new results, ${num(r.changed || 0)} changed${bad ? `, ${bad} failed` : ''}`, bad ? 'error' : 'ok', 6000);
    load();
  }
  async function makeAlert(s) {
    const own = describeParams(pj(s.params, {}), { skip: ['channels', 'expand', 'drop', 'skip'] }).map((d) => d.label);
    const warn = own.length ? `\n\nAn alert rule matches the query text only. These filters of the saved search will NOT apply, so the rule will alert on more posts than this search finds:\n- ${own.join('\n- ')}` : '';
    if (!confirm(`Create an alert rule from “${s.name}”?\n\nThe rule watches for new posts matching this query (with the spellings the dictionary adds now, frozen) and raises an alert each time collection finds some. You can change or delete it under Alerts.${warn}`)) return;
    try {
      let r;
      try { r = await post(`/api/saved_searches/${s.id}/to_alert`); }
      catch (e) {
        const dropped = e.status === 409 ? e.data?.dropped_filters : null;
        if (!dropped) throw e;
        // Human-readable filter names, built from the saved search's own parameters (never the raw API text or key names).
        const keys = new Set((Array.isArray(dropped) ? dropped : [dropped]).map((d) => String(typeof d === 'string' ? d : d?.key || '').replace(/^date_(from|to)$/, 'date').replace(/^near_label$/, 'near')));
        const labelled = describeParams(pj(s.params, {}), { skip: ['expand', 'drop', 'skip'] }).filter((d) => keys.has(d.k));
        const list = labelled.length ? labelled.map((d) => d.label) : ['some filters of this search'];
        if (!confirm(`An alert rule matches the words only, so these filters of “${s.name}” cannot be carried over:\n- ${list.join('\n- ')}\n\nCreate the alert without them? It will alert on everything matching the words alone.`)) return;
        r = await post(`/api/saved_searches/${s.id}/to_alert`, { drop_filters: true });
      }
      toast(r?.truncated ? 'Alert rule created, but its spelling list was cut off (some spellings are not covered). See Alerts.' : 'Alert rule created (see Alerts)', r?.truncated ? 'info' : 'ok', 6000);
    } catch (e) { toast(e.message, 'error', 7000); }
  }
  function startEdit(s) { ed = { id: s.id, name: s.name, notes: s.notes || '', freeze: !!s.freeze, busy: false }; }
  async function saveEdit() {
    ed.busy = true;
    const r = await attempt(() => post('/api/saved_searches', { id: ed.id, name: ed.name.trim(), notes: ed.notes.trim() || null, freeze: ed.freeze }), 'Saved');
    if (r !== undefined) { ed = null; load(); } else ed.busy = false;
  }
  async function del(s) {
    if (!confirm(`Delete the saved search “${s.name}”?\n\nThe search itself is forgotten; its earlier runs stay in Search history. The deletion is recorded in the audit log.`)) return;
    const r = await attempt(() => post('/api/saved_searches', { id: s.id, delete: true }), 'Deleted');
    if (r !== undefined) load();
  }
</script>

<div class="saved">
  {#if error}<div class="callout bad small">{error} <button class="sm" onclick={load}>Retry</button></div>{/if}
  {#if rows === null}
    <Loading />
  {:else if !rows.length}
    <div class="muted small" class:pad={compact}>No saved searches yet. On the <a href={href('search')}>Search page</a>, run a search and choose <b>Save this search</b>.</div>
  {:else}
    <div class="row head" class:pad={compact}>
      <span class="faint small">{plural(rows.length, 'saved search', 'saved searches')}</span><span class="spacer"></span>
      <button class="sm" onclick={runAll} disabled={runningAll}>{runningAll ? 'Running…' : 'Run all'}</button>
    </div>
    <div class="table-wrap" style="border:0;border-radius:0">
      <table class="t">
        <thead><tr><th scope="col">Search</th>{#if !compact}<th scope="col">Case</th>{/if}<th scope="col" class="num">Results</th><th scope="col" class="num">New</th><th scope="col">Last run</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each rows as s (s.id)}
            {@const r = results[s.id]}
            <tr>
              <td><a href={searchLink(s)}><b dir="auto">{s.name}</b></a>
                {#if s.freeze}<span class="pill info" title="The exact query (with spellings) is frozen">frozen</span>{/if}
                <div class="tiny faint" dir="auto">{s.q || '(filters only)'}{s.notes ? ' · ' + s.notes : ''}</div>
                {#each describeParams(pj(s.params, {})) as d}<span class="pill {d.kind === 'exp' ? 'warn' : ''} chipx" dir="auto" title={d.kind === 'exp' ? 'Changes which spellings are searched' : 'Filter saved with this search'}>{d.label}</span>{/each}</td>
              {#if !compact}<td class="small">{s.case_id != null ? s.case_name || caseName(s.case_id) || '#' + s.case_id : 'Whole archive'}</td>{/if}
              <td class="num">{#if r?.error}<span class="pill bad" title={r.error}>failed</span>{:else}{num(r?.total ?? s.last_total)}{#if r && r.before != null && r.before !== r.total}<div class="tiny faint">was {num(r.before)}</div>{/if}{/if}
                {#if r?.added != null || r?.removed != null}<div class="tiny faint" title="Difference in the list of results compared with the previous run of this search (index or dictionary changes count, not only newly collected posts)">{r.added ? '+' + num(r.added) : '+0'} / {r.removed ? '−' + num(r.removed) : '−0'} vs last run</div>{/if}
                {#if r?.truncated}<div class="tiny" style="color:var(--warn)">spelling list cut off</div>{/if}</td>
              <td class="num">{#if r?.first}<span class="tiny faint" title="First run: nothing to compare with yet">first run</span>{:else if r?.fresh != null}<span class="pill {r.fresh ? 'accent' : ''}" title="Posts newly collected since the previous run">{num(r.fresh)}</span><div class="tiny faint">newly collected</div>{:else}<span class="faint">–</span>{/if}
                {#if r?.changed && !(r?.added != null)}<div class="tiny" style="color:var(--warn)" title="The list of results is different from the previous run">changed</div>{/if}
                {#if r && r.fresh === 0 && ((r.added || 0) > 0 || (r.removed || 0) > 0)}<div class="tiny" style="color:var(--warn)">{(r.added || 0) > 0 && !(r.removed || 0) ? 'list grew' : (r.removed || 0) > 0 && !(r.added || 0) ? 'list shrank' : 'list changed'} without newly collected posts: OCR/speech text, the search settings or the dictionary changed</div>{/if}</td>
              <td class="small nowrap" title={dt(s.last_run_at)}>{s.last_run_at ? ago(s.last_run_at) : 'never'}</td>
              <td class="right nowrap">
                <button class="sm" onclick={() => run(s)} disabled={busyId === s.id || runningAll}>{busyId === s.id ? '…' : 'Run'}</button>
                <a class="btn sm ghost" href={searchLink(s)}>Open</a>
                <button class="sm ghost" onclick={() => makeAlert(s)} title="Create an alert rule with this query">Make alert</button>
                <button class="sm ghost" onclick={() => startEdit(s)}>Edit</button>
                <button class="sm ghost" onclick={() => del(s)} aria-label="Delete {s.name}">✕</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<Modal open={!!ed} onclose={() => (ed = null)} title="Edit saved search" width="480px">
  {#if ed}
    <form id="edsaved" class="col" style="gap:12px" onsubmit={(e) => { e.preventDefault(); saveEdit(); }}>
      <label class="field">Name<input type="text" bind:value={ed.name} /></label>
      <label class="field">Notes<textarea rows="2" bind:value={ed.notes}></textarea></label>
      <label class="check"><input type="checkbox" bind:checked={ed.freeze} /> Freeze the exact query <span class="faint">(spellings stored now, not re-expanded on each run)</span></label>
    </form>
  {/if}
  {#snippet footer()}
    <button onclick={() => (ed = null)}>Cancel</button>
    <button class="primary" type="submit" form="edsaved" disabled={!ed || ed.busy || !ed.name.trim()}>Save</button>
  {/snippet}
</Modal>

<style>
  .head { padding: 4px 0 6px; }
  .pad { padding: 10px 14px; }
  .chipx { margin: 2px 4px 0 0; font-size: 10.5px; }
  .head.pad { padding-bottom: 0; }
</style>
