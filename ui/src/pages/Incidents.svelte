<script>
  // Incident list + create form.
  import { untrack } from 'svelte';
  import { app, toast } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, go, setQuery } from '../lib/router.svelte.js';
  import { num, day } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';

  const STATUS_TONE = { open: 'accent', verified: 'ok', disputed: 'warn', closed: '' };
  const STATUSES = ['open', 'verified', 'disputed', 'closed'];
  const HELP = {
    open: 'still being worked on', verified: 'date, place and events corroborated', disputed: 'sources conflict', closed: 'work finished (incidents are closed, never deleted)',
  };
  const cid = $derived(caseId());
  const status = $derived(route.query.status || '');

  let rows = $state([]);
  let loading = $state(true);
  let error = $state('');
  async function load() {
    error = '';
    try { rows = await get('/api/incidents', { case: cid, status }); } catch (e) { error = e.message; }
    loading = false;
  }
  $effect(() => { cid; status; untrack(load); });

  let creating = $state(false);
  let f = $state({ title: '', description: '', date_start: '', location: '', case_id: '' });
  let busy = $state(false);
  let err = $state('');
  function open() { f = { title: '', description: '', date_start: '', location: '', case_id: cid ?? '' }; err = ''; creating = true; }
  async function create() {
    busy = true; err = '';
    try {
      const i = await post('/api/incidents', {
        title: f.title.trim(), description: f.description.trim() || null, date_start: f.date_start.trim() || null,
        location: f.location.trim() || null, case_id: f.case_id || null, status: 'open',
      });
      toast('Incident created. Now link the posts and files that document it.', 'ok');
      go(href('incident', i.id));
    } catch (e) { err = e.message; }
    busy = false;
  }
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>Incidents</h1>
      <p>An incident is one event (an attack, a killing, a detention) with the date, the place, how each was established, and every post, file, channel or account that documents it. They feed evidence packages.</p>
    </div>
    <button class="primary" onclick={open}>＋ New incident</button>
  </div>

  {#if creating}
    <form class="panel panel-body" style="margin-bottom:14px" onsubmit={(e) => { e.preventDefault(); create(); }}>
      <div class="form-grid">
        <label class="field">Title<input type="text" bind:value={f.title} placeholder="e.g. Shelling of the market, El Fasher" /></label>
        <label class="field">Date <span class="faint">(UTC, as far as known)</span><input type="text" bind:value={f.date_start} placeholder="2026-09-20 or 2026-09-20T14:00" /></label>
        <label class="field">Location<input type="text" bind:value={f.location} placeholder="Place name" /></label>
        <label class="field">Case
          <select bind:value={f.case_id}><option value="">No case</option>{#each app.cases as c}<option value={c.id}>{c.name}</option>{/each}</select></label>
      </div>
      <label class="field" style="margin-top:10px">Description<textarea bind:value={f.description} rows="2"></textarea></label>
      <p class="help">You can add end date, coordinates and notes on how the date and place were established on the next screen.</p>
      {#if err}<div class="callout bad" style="margin-bottom:8px">{err}</div>{/if}
      <div class="row"><button class="primary" type="submit" disabled={busy || !f.title.trim()}>{busy ? 'Creating…' : 'Create incident'}</button>
        <button type="button" onclick={() => (creating = false)}>Cancel</button></div>
    </form>
  {/if}

  <div class="row" style="margin-bottom:10px">
    <div class="seg" role="group" aria-label="Filter by status">
      <button class:on={!status} aria-pressed={!status} onclick={() => setQuery({ status: null })}>All</button>
      {#each STATUSES as s}<button class:on={status === s} aria-pressed={status === s} onclick={() => setQuery({ status: s })} title={HELP[s]}>{s}</button>{/each}
    </div>
    <span class="muted small">{num(rows.length)} shown</span>
  </div>

  {#if loading}<Loading />
  {:else if error}<div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
  {:else if !rows.length}
    <Empty title={status ? `No ${status} incidents` : 'No incidents yet'}>
      {status ? 'Try another status filter.' : 'Create an incident, then use “Add to incident” on any post, comment or file to link it.'}
      {#snippet action()}{#if !status}<button class="primary" onclick={open}>＋ New incident</button>{/if}{/snippet}
    </Empty>
  {:else}
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Incident</th><th scope="col">Status</th><th scope="col">Date (UTC)</th><th scope="col">Location</th><th scope="col" class="right">Items</th>{#if cid == null}<th scope="col">Case</th>{/if}</tr></thead>
      <tbody>
        {#each rows as i (i.id)}
          <tr class="click" onclick={() => go(href('incident', i.id))}>
            <td><a href={href('incident', i.id)} onclick={(e) => e.stopPropagation()}><b>{i.title}</b></a>
              {#if i.description}<div class="muted small ellipsis" style="max-width:60ch">{i.description}</div>{/if}</td>
            <td><span class="pill {STATUS_TONE[i.status] || ''}">{i.status}</span></td>
            <td class="nowrap">{day(i.date_start)}{#if i.date_end && i.date_end !== i.date_start} – {day(i.date_end)}{/if}</td>
            <td>{i.location || '–'}</td>
            <td class="right num">{num(i.items)}</td>
            {#if cid == null}<td class="small">{i.case_name || '–'}</td>{/if}
          </tr>
        {/each}
      </tbody>
    </table></div>
  {/if}
</div>
